r"""生成 B 站封面（1920x1080 与 1146x717，各两个方案）。

方案 A：插画完整显示在右侧（**不裁人**）+ 左侧标题 —— 素材取自 `测试图\`
方案 B：软件界面做底 —— 素材取自 `promo\shots\01_main_window.png`（没截图时自动退回 A）

注意：素材一律用文件路径引用，不要把大图贴进对话历史。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "cover"
SHOTS = PROJECT / "promo" / "shots"
IMAGES = PROJECT / "测试图"

FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_R = r"C:\Windows\Fonts\msyh.ttc"
ACCENT = (242, 179, 46)
ACCENT2 = (74, 158, 255)
TEXT = (255, 255, 255)


def font(size: int, bold: bool = True):
    return ImageFont.truetype(FONT_B if bold else FONT_R, size)


def pick_art() -> Path | None:
    """挑一张插画做封面底：优先初音未来、优先大图。"""
    if not IMAGES.exists():
        return None
    cands = [p for p in IMAGES.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    miku = [p for p in cands if "hatsune_miku" in p.name]
    pool = miku or cands
    return max(pool, key=lambda p: p.stat().st_size, default=None)


def cover_fill(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比裁剪填满目标尺寸。"""
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    img = img.resize((int(sw * scale + 0.5), int(sh * scale + 0.5)), Image.LANCZOS)
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    return img.crop((left, top, left + tw, top + th))


def cover_contain(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    """等比缩放到完全放进 box（不裁切）。"""
    bw, bh = box
    sw, sh = img.size
    scale = min(bw / sw, bh / sh)
    return img.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.LANCZOS)


def bottom_scrim(img: Image.Image, strength: int = 210) -> None:
    w, h = img.size
    scrim = Image.new("L", (1, h), 0)
    for y in range(h):
        k = max(0.0, (y - h * 0.35) / (h * 0.65))
        scrim.putpixel((0, y), int(strength * k))
    img.paste(Image.new("RGB", (w, h), (8, 9, 12)), (0, 0), scrim.resize((w, h)))


def _header(icon_size: int, size: tuple[int, int], d: ImageDraw.ImageDraw) -> None:
    w, h = size
    s = h / 1080.0
    icon_path = PROJECT / "assets" / "icon.png"
    x, y = int(w * 0.045), int(h * 0.085)
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
        # 注意：这里用 base 的 paste 由调用方完成，d 只用来写字
    d.text((x + icon_size + int(18 * s), y + icon_size // 2), "ImageTagStudio",
           font=font(int(40 * s)), fill=ACCENT, anchor="lm")


def scheme_a(size: tuple[int, int]) -> Image.Image:
    w, h = size
    base = Image.new("RGB", size, (16, 17, 20))
    art_path = pick_art()
    if art_path:
        art = Image.open(art_path).convert("RGB")
        bg = cover_fill(art, size).filter(ImageFilter.GaussianBlur(26))
        base = Image.blend(bg, Image.new("RGB", size, (10, 11, 14)), 0.55)
        fg = cover_contain(art, (int(w * 0.56), int(h * 0.92)))
        radius = int(min(fg.size) * 0.035)
        mask = Image.new("L", fg.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (fg.width - 1, fg.height - 1)],
                                              radius=radius, fill=255)
        base.paste(fg, (w - fg.width - int(w * 0.035), (h - fg.height) // 2), mask)
        strip = Image.new("L", (int(w * 0.62), 1), 0)
        for x in range(strip.width):
            strip.putpixel((x, 0), int(210 * max(0.0, 1.0 - x / (w * 0.55))))
        base.paste(Image.new("RGB", (int(w * 0.62), h), (10, 11, 14)), (0, 0),
                   strip.resize((int(w * 0.62), h)))
    else:
        bottom_scrim(base, 200)

    s = h / 1080.0
    icon_size = int(112 * s)
    icon_path = PROJECT / "assets" / "icon.png"
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
        base.paste(icon, (int(w * 0.045), int(h * 0.085)), icon)
    d = ImageDraw.Draw(base)
    _header(icon_size, size, d)
    d.text((int(w * 0.045), int(h * 0.28)), "图片标签工坊", font=font(int(104 * s)),
           fill=TEXT, stroke_width=int(3 * s), stroke_fill=(0, 0, 0))
    d.text((int(w * 0.048), int(h * 0.47)), "1.2", font=font(int(72 * s)), fill=ACCENT)
    d.text((int(w * 0.048), int(h * 0.61)), "AI 自动打标 · 人工审核",
           font=font(int(46 * s), False), fill=(235, 238, 245))
    d.text((int(w * 0.048), int(h * 0.685)), "标签写进文件名",
           font=font(int(46 * s), False), fill=(235, 238, 245))
    d.text((int(w * 0.048), int(h * 0.80)), "纯本地离线 · 不联网 · 不上传",
           font=font(int(34 * s), False), fill=(196, 202, 214))
    if art_path is None:
        d.text((int(w * 0.048), int(h * 0.88)), "（没找到插画素材：测试图\\ 为空）",
               font=font(int(28 * s), False), fill=(180, 120, 120))
    return base


def scheme_b(size: tuple[int, int]) -> Image.Image:
    """软件界面做底；没有截图时退回方案 A。"""
    shot = SHOTS / "01_main_window.png"
    if not shot.exists():
        return scheme_a(size)
    base = cover_fill(Image.open(shot).convert("RGB"), size)
    bottom_scrim(base, 225)
    w, h = base.size
    strip = Image.new("L", (int(w * 0.62), 1), 0)
    for x in range(strip.width):
        strip.putpixel((x, 0), int(150 * max(0.0, 1.0 - x / (w * 0.55))))
    base.paste(Image.new("RGB", (int(w * 0.62), h), (8, 9, 12)), (0, 0),
               strip.resize((int(w * 0.62), h)))
    d = ImageDraw.Draw(base)
    s = h / 1080.0
    d.text((int(w * 0.04), int(h * 0.60)), "图片标签工坊", font=font(int(120 * s)),
           fill=TEXT, stroke_width=int(3 * s), stroke_fill=(0, 0, 0))
    d.text((int(w * 0.043), int(h * 0.775)),
           "ImageTagStudio 1.2 · 图库自动打标 + 人工审核",
           font=font(int(45 * s), False), fill=ACCENT)
    d.text((int(w * 0.043), int(h * 0.87)),
           "WD14 · CLIP · 人脸聚类 · 分级 · 系列 · 查重",
           font=font(int(38 * s), False), fill=(220, 224, 232))
    return base


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, size in (("1920x1080", (1920, 1080)), ("1146x717", (1146, 717))):
        for name, fn in (("A", scheme_a), ("B", scheme_b)):
            img = fn(size)
            path = OUT / f"封面_{name}_{tag}.png"
            img.save(path)
            print(f"  {path.name}  {img.size[0]}x{img.size[1]}")
    print("输出目录:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
