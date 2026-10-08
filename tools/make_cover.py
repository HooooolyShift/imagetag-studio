r"""生成更新公告的封面图（B 站专栏头图，16:10，1600x1000）。

用法： python tools\make_cover.py "公告_v1.3\公告_v1.3_封面.png"
素材：assets/icon.png（应用图标）+ manual_shots/*.png（真实界面截图）
字体：Microsoft YaHei（C:/Windows/Fonts）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent.parent

W, H = 1600, 1000
BLUE_DARK = (15, 23, 38)
BLUE_MID = (27, 43, 69)
CORAL = (239, 107, 74)
WHITE = (255, 255, 255)
TEXT_DIM = (150, 168, 192)
TEXT_MID = (201, 214, 232)

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def gradient_bg() -> Image.Image:
    """左上到右下的深蓝渐变 + 右上角一抹冷光。"""
    base = Image.new("RGB", (W, H), BLUE_DARK)
    px = base.load()
    for y in range(H):
        for x in range(0, W, 4):
            t = (x / W * 0.45 + y / H * 0.55)
            r = int(BLUE_DARK[0] + (BLUE_MID[0] - BLUE_DARK[0]) * t)
            g = int(BLUE_DARK[1] + (BLUE_MID[1] - BLUE_DARK[1]) * t)
            b = int(BLUE_DARK[2] + (BLUE_MID[2] - BLUE_DARK[2]) * t)
            for dx in range(4):
                if x + dx < W:
                    px[x + dx, y] = (r, g, b)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([900, -420, 1900, 480], fill=(59, 108, 166, 90))
    gd.ellipse([-300, 620, 600, 1320], fill=(239, 107, 74, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(150))
    return Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")


def rounded(im: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.size[0] - 1, im.size[1] - 1], radius, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def paste_screenshot(canvas: Image.Image, shot_path: Path, box: tuple[int, int, int, int],
                     brighten: float = 1.0) -> None:
    """把界面截图按圆角 + 描边 + 阴影贴到画布上。"""
    x0, y0, x1, y1 = box
    shot = Image.open(shot_path).convert("RGB")
    if brighten != 1.0:
        from PIL import ImageEnhance
        shot = ImageEnhance.Brightness(shot).enhance(brighten)
    tw, th = x1 - x0, y1 - y0
    scale = max(tw / shot.width, th / shot.height)
    shot = shot.resize((int(shot.width * scale + 0.5), int(shot.height * scale + 0.5)),
                       Image.LANCZOS)
    left = (shot.width - tw) // 2
    top = (shot.height - th) // 2
    shot = shot.crop((left, top, left + tw, top + th))
    shot = rounded(shot, 18)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x0 + 6, y0 + 14, x1 + 6, y1 + 14], 18, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(shot, (x0, y0))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x0, y0, x1 - 1, y1 - 1], 18, outline=(74, 108, 152), width=2)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "公告_v1.3" / "公告_v1.3_封面.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    canvas = gradient_bg().convert("RGBA")
    d = ImageDraw.Draw(canvas)

    # 应用图标
    icon = Image.open(HERE / "assets" / "icon.png").convert("RGBA").resize((104, 104), Image.LANCZOS)
    canvas.alpha_composite(rounded(icon, 24), (96, 84))
    d.text((224, 96), "ImageTagStudio", font=font(FONT_BOLD, 30), fill=TEXT_MID)
    d.text((224, 136), "本地离线图片打标与检索", font=font(FONT_REG, 24), fill=TEXT_DIM)

    # ---- v1.5 封面文案（v1.4 及之前的封面走下面 is_v15=False 的旧文案） ----
    is_v15 = "v1.5" in Path(out).name

    # 主标题
    d.text((96, 252), "图片标签工坊", font=font(FONT_BOLD, 88), fill=WHITE)
    d.text((98, 368), "v1.5 更新公告" if is_v15 else "v1.3 更新公告",
           font=font(FONT_BOLD, 54), fill=CORAL)
    d.rounded_rectangle([100, 446, 152, 453], 4, fill=(59, 108, 166))

    lines = ([
        "新增：平板 / 手机 局域网遥控全流程",
        "新增：AI 生图扩展包（换装 / 融合 / 放大）",
        "图谱重做 · 审核可撤销 · 启动开屏",
    ] if is_v15 else [
        "自动打标 WD14 + CLIP + 人脸聚类",
        "人工审核后才正式生效",
        "标签写进文件名，手机也能搜",
    ])
    y = 496
    for text in lines:
        d.ellipse([103, y + 8, 112, y + 17], fill=CORAL)
        d.text((130, y), text, font=font(FONT_REG, 28), fill=TEXT_MID)
        y += 48

    d.text((96, 880),
           "100% 本地运行  ·  不上传图片  ·  算力全在你自己的机器上" if is_v15
           else "100% 本地运行  ·  不上传图片  ·  支持 RTX 显卡加速",
           font=font(FONT_REG, 26), fill=TEXT_DIM)
    d.text((96, 922), "github.com/HooooolyShift/imagetag-studio",
           font=font(FONT_REG, 24), fill=(110, 128, 152))

    paste_screenshot(canvas, HERE / "manual_shots" / "01_主界面.png", (672, 196, 1536, 796),
                     brighten=1.12)
    d.text((672, 812),
           "主界面：标签筛选 / 缩略图 / 标签编辑（另有平板端与 AI 生图扩展包）" if is_v15
           else "主界面：左侧标签筛选 / 中间缩略图 / 右侧标签编辑",
           font=font(FONT_REG, 22), fill=(122, 140, 166))

    canvas.convert("RGB").save(out, quality=95)
    print("封面已生成:", out, canvas.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
