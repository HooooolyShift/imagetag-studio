"""v1.5 宣传片的动效引擎：把截图渲染成 3D 倾斜 + 视差 + 景深的 60fps 帧序列。

做法（离线，只依赖 numpy + Pillow）：
  * 背景层：同一张图放大 + 高斯模糊 + 压暗，作为后景（视差 + 景深）
  * 主体层：透视变换把截图摆成 3D 倾斜，带圆角与投影
  * 相机：横向掠过 / 推近特写 / 左右对比 / 横滚
  * 景深：运动两端略虚、中段最实

片段定义见 CLIPS；分镜脚本用 `from v15_motion import CLIPS` 取时长，两边保持一致。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
SHOTS = PROJECT / "promo" / "v15_shots"
OUT = PROJECT / "promo" / "motion_v15"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
W, H = 1920, 1080
FPS = 30          # 渲染用 30fps；pad_motion.py 会按需要补到成片 60fps（动作慢，不糊）

CLIPS: dict[str, dict] = {
    "tab_browse":  dict(img="tablet_01_图库浏览", type="flyby", dur=5.0, dir=1),
    "tab_review":  dict(img="tablet_02_审核台", type="flyby", dur=6.0, dir=-1),
    "tab_gen":     dict(img="tablet_03_遥控生图", type="flyby", dur=5.0, dir=1),
    "tab_viewer":  dict(img="tablet_05_原图", type="flyby", dur=4.5, dir=-1),
    "tab_region":  dict(img="tablet_06_框选区域", type="flyby", dur=4.5, dir=1),
    "tab_region2": dict(img="tablet_07_区域精修完成", type="pushin", dur=4.0,
                        box=(0.55, 0.35, 1.0, 0.95)),
    "tab_tools":   dict(img="tablet_10_工具箱", type="flyby", dur=4.0, dir=-1),
    "tab_dupes":   dict(img="tablet_12_查重", type="flyby", dur=4.5, dir=1),
    "tab_graph":   dict(img="tablet_17_图谱", type="flyby", dur=6.0, dir=-1),
    "tab_sector":  dict(img="tablet_18_图谱分区", type="pushin", dur=4.5,
                        box=(0.0, 0.0, 1.0, 1.0)),
    "tab_devices": dict(img="tablet_19_设备发现", type="pushin", dur=5.0,
                        box=(0.15, 0.25, 0.85, 0.85)),
    "tab_similar": dict(img="tablet_22_相似图", type="flyby", dur=4.5, dir=1),
    "tab_wide":    dict(img="tablet_23_横屏图库", type="flyby", dur=4.5, dir=-1),
    "phone":       dict(img="phone_01_手机浏览", type="flyby", dur=3.5, dir=1),
    "gen_out1":    dict(img="ai_01_出图", type="flyby", dur=4.5, dir=1),
    "gen_out2":    dict(img="ai_04_出图", type="flyby", dur=4.0, dir=-1),
    "gen_hires":   dict(img="ai_05_高分修复", type="pushin", dur=4.5,
                        box=(0.2, 0.1, 0.8, 0.9)),
    "gen_ref":     dict(img="ai_06_参考图", type="flyby", dur=4.0, dir=1),
    "gen_pose":    dict(img="ai_07_姿势控制", type="flyby", dur=4.0, dir=-1),
    "gen_inpaint": dict(img="gen_02_重绘完成", type="flyby", dur=4.5, dir=1),
    "gen_mask":    dict(img="gen_01_涂遮罩", type="pushin", dur=4.0,
                        box=(0.2, 0.15, 0.85, 0.9)),
    "pc_main":     dict(img="pc_01_主界面", type="flyby", dur=5.0, dir=1),
    "pc_browse":   dict(img="pc_14_大图预览与框选", type="flyby", dur=5.0, dir=-1),
    "pc_review":   dict(img="pc_05_审核台", type="pushin", dur=5.5,
                        box=(0.5, 0.1, 1.0, 0.95)),
    "pc_graph":    dict(img="pc_08_标签体系图谱", type="flyby", dur=5.5, dir=1),
    "pc_dup":      dict(img="pc_09_查重与保留选择", type="flyby", dur=4.5, dir=-1),
    "pc_import":   dict(img="pc_10_导入图片", type="flyby", dur=4.5, dir=1),
    "pc_tags":     dict(img="pc_02_标签面板与批量打标", type="pushin", dur=4.5,
                        box=(0.0, 0.0, 0.55, 1.0)),
    "pc_person":   dict(img="pc_12_人物管理", type="flyby", dur=4.0, dir=-1),
    "pc_settings": dict(img="pc_11_设置", type="pushin", dur=4.0,
                        box=(0.1, 0.1, 0.9, 0.9)),
    "splash":      dict(img="splash_01", type="scroll", dur=6.0),
}


def font(size: int):
    return ImageFont.truetype(FONT_B, size)


def load(name: str) -> Image.Image:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = SHOTS / f"{name}{ext}"
        if p.exists():
            return Image.open(p).convert("RGB")
    raise FileNotFoundError(f"缺素材：{SHOTS / (name + '.png')}")


def cover_fill(img: Image.Image, size=(W, H)) -> Image.Image:
    tw, th = size
    sw, sh = img.size
    s = max(tw / sw, th / sh)
    img = img.resize((int(sw * s + 0.5), int(sh * s + 0.5)), Image.LANCZOS)
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    return img.crop((left, top, left + tw, top + th))


def contain(img: Image.Image, box) -> Image.Image:
    bw, bh = box
    s = min(bw / img.width, bh / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def homography(src_pts, dst_pts) -> np.ndarray:
    rows = []
    for (x, y), (u, v) in zip(src_pts, dst_pts):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.array(rows, dtype=np.float64))
    return vt[-1].reshape(3, 3)


def warp_tilt(img: Image.Image, yaw: float, pitch: float, scale: float,
              offset=(0.0, 0.0), roll: float = 0.0):
    """返回（透视后的图, 画布内边距, 画布左上角在图上的位置, 透视系数）。

    系数要交给调用方复用：**遮罩必须用同一个单应一起变换**，
    否则圆角遮罩覆盖的是整块画布，会把画布黑底也贴上去（踩过这个坑）。
    """
    w, h = img.size
    dw, dh = w * scale, h * scale
    kx = math.tan(math.radians(yaw)) * dw * 0.5
    ky = math.tan(math.radians(pitch)) * dh * 0.5
    quad = np.array([[-dw / 2 + kx * 0.6, -dh / 2 + ky],
                     [dw / 2 + kx, -dh / 2 - ky * 0.6],
                     [dw / 2 - kx * 0.6, dh / 2 - ky],
                     [-dw / 2 - kx, dh / 2 + ky * 0.6]], dtype=np.float64)
    if roll:
        c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        quad = quad @ np.array([[c, -s], [s, c]]).T
    quad[:, 0] += W / 2 + offset[0] * W
    quad[:, 1] += H / 2 + offset[1] * H
    pad = 40
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    inv = np.linalg.inv(homography(src, quad))
    coeffs = (inv / inv[2, 2]).flatten()[:8]
    canvas = img.transform((W + pad * 2, H + pad * 2), Image.PERSPECTIVE, coeffs,
                           resample=Image.BICUBIC, fillcolor=(0, 0, 0))
    return canvas, pad, (int(quad[:, 0].min()), int(quad[:, 1].min())), coeffs


def rounded_mask(size, radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)],
                                        radius=radius, fill=255)
    return m


def prep_flyby(img: Image.Image) -> dict:
    """把每帧都要用的重活（背景模糊、圆角遮罩、投影）提前算一次。"""
    big = cover_fill(img, (int(W * 1.25), int(H * 1.25))).filter(ImageFilter.GaussianBlur(22))
    big = Image.blend(big, Image.new("RGB", big.size, (10, 11, 14)), 0.22)
    # 遮罩要按 hero 的实际尺寸做（不是整块画布），否则会把画布黑底一起贴上去
    hero_size = contain(img, (int(W * 0.60), int(H * 0.74))).size
    hero_mask = rounded_mask(hero_size, 26).filter(ImageFilter.GaussianBlur(1.2))
    return {"big": big, "hero_mask": hero_mask}


def compose_flyby(img: Image.Image, t: float, spec: dict, ctx: dict) -> Image.Image:
    dirn = spec.get("dir", 1)
    shift = int(math.sin(dirn * t * math.tau) * 60)
    frame = ctx["big"].crop((int(W * 0.125) + shift, int(H * 0.125),
                             int(W * 0.125) + shift + W, int(H * 0.125) + H))
    hero = contain(img, (int(W * 0.60), int(H * 0.74)))
    x = (-0.42 + 0.84 * t) * dirn
    y = -0.02 * math.sin(t * math.pi)
    yaw = (18 - 36 * t) * dirn
    pitch = 8 - 14 * t
    roll = (-3 + 6 * t) * dirn
    scale = 1.02 + 0.06 * math.sin(t * math.pi)
    warped, pad, _qmin, coeffs = warp_tilt(hero, yaw, pitch, scale, (x, y), roll)
    dof = 2.6 * (abs(t - 0.5) ** 2) * 4
    if dof > 0.35:
        warped = warped.filter(ImageFilter.GaussianBlur(dof))
    # 遮罩按 hero 尺寸画圆角，再套同一个透视 —— 只覆盖图本身，不带画布黑底
    mask_canvas = ctx["hero_mask"].transform(
        (W + pad * 2, H + pad * 2), Image.PERSPECTIVE, coeffs,
        resample=Image.BILINEAR, fillcolor=0)
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    layer.paste(warped, (-pad, -pad))
    inner = mask_canvas.crop((pad, pad, pad + W, pad + H))
    out = frame.copy()
    # 顺序很重要：先铺投影（黑，偏移+模糊），再把主体贴上去。
    # 反过来会把主体自己涂成黑块（这个坑踩过一次）。
    shadow = Image.new("L", (W, H), 0)
    shadow.paste(inner, (12, 16))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    out = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), out, shadow)
    out.paste(layer, (0, 0), inner)
    return out


def compose_pushin(img: Image.Image, t: float, spec: dict) -> Image.Image:
    x0, y0, x1, y1 = spec.get("box", (0.15, 0.15, 0.85, 0.85))
    z0, z1 = spec.get("zoom", (0.92, 1.18))
    z = z0 + (z1 - z0) * t
    big = cover_fill(img, (int(W * 1.3), int(H * 1.3)))
    cw, ch = int(W / z), int(H / z)
    cx = int((x0 + x1) / 2 * big.width)
    cy = int((y0 + y1) / 2 * big.height)
    left = max(0, min(big.width - cw, cx - cw // 2))
    top = max(0, min(big.height - ch, cy - ch // 2))
    crop = big.crop((left, top, left + cw, top + ch)).resize((W, H), Image.LANCZOS)
    dof = 1.2 * abs(t - 0.5) * 2
    return crop.filter(ImageFilter.GaussianBlur(dof)) if dof > 0.3 else crop


def compose_compare(a: Image.Image, b: Image.Image, t: float,
                    label_a: str = "原始 1024", label_b: str = "4× 放大 4096") -> Image.Image:
    za = contain(a, (int(W * 0.44), int(H * 0.72)))
    zb = contain(b, (int(W * 0.44), int(H * 0.72)))
    frame = Image.new("RGB", (W, H), (12, 13, 16))
    d = ImageDraw.Draw(frame)
    f = font(34)
    xa = int(W * 0.03) + (int(W * 0.47) - za.width) // 2
    xb = int(W * 0.50) + (int(W * 0.47) - zb.width) // 2
    ya = (H - za.height) // 2 - 20
    yb = (H - zb.height) // 2 - 20
    frame.paste(za, (xa, ya))
    frame.paste(zb, (xb, yb))
    d.text((xa, ya - 46), label_a, font=f, fill=(210, 214, 222))
    d.text((xb, yb - 46), label_b, font=f, fill=(242, 179, 46))
    xline = int(W * (0.25 + 0.5 * t))
    d.line([(xline, 60), (xline, H - 60)], fill=(242, 179, 46), width=3)
    return frame


def compose_scroll(images: list[Image.Image], t: float) -> Image.Image:
    tile_w = int(W * 0.62)
    tiles = [contain(im, (tile_w, int(tile_w * 0.42))) for im in images]
    gap = 36
    total = sum(x.width for x in tiles) + gap * (len(tiles) - 1)
    strip = Image.new("RGB", (total, H), (12, 13, 16))
    x = 0
    for tile in tiles:
        strip.paste(tile, (x, (H - tile.height) // 2))
        x += tile.width + gap
    off = int(max(0, total - W) * (0.08 + 0.84 * t))
    return strip.crop((off, 0, off + W, H))


def render_clip(name: str, spec: dict) -> float:
    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    dur = float(spec["dur"])
    frames = int(round(dur * FPS))
    kind = spec["type"]
    if kind == "scroll":
        imgs = [load(f"splash_{i:02d}") for i in range(1, 15)]
    else:
        img = load(spec["img"])
    ctx = prep_flyby(img) if kind == "flyby" else None
    for i in range(frames):
        t = i / max(1, frames - 1)
        if kind == "flyby":
            frame = compose_flyby(img, t, spec, ctx)
        elif kind == "pushin":
            frame = compose_pushin(img, t, spec)
        else:
            frame = compose_scroll(imgs, t)
        frame.save(out_dir / f"{name}_{i + 1:05d}.jpg", quality=92)
    print(f"  {name:14} {kind:8} {dur:4.1f}s  {frames} 帧")
    return dur


def main() -> int:
    names = sys.argv[1:] or list(CLIPS)
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for name in names:
        spec = CLIPS.get(name)
        if not spec:
            print(f"  未知片段 {name}")
            continue
        total += render_clip(name, spec)
    (OUT / "clips.json").write_text(
        json.dumps({k: v["dur"] for k, v in CLIPS.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"共 {len(names)} 段，合计 {total:.1f}s -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
