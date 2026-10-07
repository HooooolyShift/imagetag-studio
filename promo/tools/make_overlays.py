"""生成片头卡、章节标题条和字幕 PNG（带透明通道），供 Premiere 叠在画面上。

产出：
    promo/cards/title.png        片头/片尾卡
    promo/cards/github.png       GitHub 卡
    promo/cards/summary.png      功能一览卡
    promo/overlays/sub_XXX.png   每条字幕一张
    promo/overlays/title_XXX.png 每个章节标题一张

注意：生成前会尽力清掉旧字幕（时间线引用着的时候删不掉，属正常；
时间线只按分镜脚本决定贴不贴字幕，所以旧文件不会串场）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promo_plan import resolve  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT_CARDS = PROJECT / "promo" / "cards"
OUT_OVER = PROJECT / "promo" / "overlays"
W, H = 1920, 1080

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"

BG = (24, 25, 28)
ACCENT = (242, 179, 46)
ACCENT2 = (74, 158, 255)
TEXT = (245, 245, 245)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    return int(draw.textlength(s, font=f))


def draw_center(draw, xy, s, f, fill, stroke=3, stroke_fill=(0, 0, 0, 220), anchor="mm"):
    draw.text(xy, s, font=f, fill=fill, anchor=anchor,
              stroke_width=stroke, stroke_fill=stroke_fill)


def vertical_gradient(bg=BG) -> Image.Image:
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)],
               fill=(int(bg[0] + 14 * k), int(bg[1] + 16 * k), int(bg[2] + 22 * k)))
    return img


def _icon(size: int) -> Image.Image | None:
    p = PROJECT / "assets" / "icon.png"
    if not p.exists():
        return None
    return Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)


def make_card_title() -> None:
    img = vertical_gradient()
    d = ImageDraw.Draw(img)
    size = 260
    icon = _icon(size)
    if icon:
        img.paste(icon, ((W - size) // 2, 250), icon)
    draw_center(d, (W // 2, 590), "图片标签工坊", font(96), TEXT, stroke=0)
    draw_center(d, (W // 2, 700), "ImageTagStudio", font(40, False), ACCENT)
    draw_center(d, (W // 2, 790), "纯本地 · 不联网 · 不上传", font(38, False), (200, 205, 215))
    d.line([(W // 2 - 260, 840), (W // 2 + 260, 840)], fill=(70, 72, 78), width=2)
    draw_center(d, (W // 2, 900), "AI 自动打标  ·  人工审核  ·  标签写进文件名",
                font(32, False), (150, 156, 166))
    img.save(OUT_CARDS / "title.png")


def make_card_github() -> None:
    img = vertical_gradient()
    d = ImageDraw.Draw(img)
    size = 150
    icon = _icon(size)
    if icon:
        img.paste(icon, ((W - size) // 2, 250), icon)
    draw_center(d, (W // 2, 490), "图片标签工坊", font(72), TEXT, stroke=0)
    draw_center(d, (W // 2, 580), "开源 · 免费 · 纯本地", font(40, False), ACCENT)
    draw_center(d, (W // 2, 690), "github.com/HooooolyShift/imagetag-studio",
                font(46), ACCENT2)
    draw_center(d, (W // 2, 790), "装好即离线可用 · 欢迎试用和提 issue",
                font(34, False), (170, 176, 186))
    img.save(OUT_CARDS / "github.png")


def make_card_summary() -> None:
    """收尾用的「功能一览」卡。"""
    img = vertical_gradient()
    d = ImageDraw.Draw(img)
    size = 132
    icon = _icon(size)
    if icon:
        img.paste(icon, (110, 96), icon)
    d.text((270, 120), "图片标签工坊", font=font(60), fill=TEXT)
    d.text((272, 196), "功能一览", font=font(36, False), fill=ACCENT)
    d.line([(110, 272), (W - 110, 272)], fill=(72, 74, 80), width=2)
    items = [
        "WD14 + CLIP 自动打标（含角色名）",
        "人工审核：通过 / 否决 / 框选补标",
        "标签写进文件名，手机文件管理器可搜",
        "标签体系图谱：一个标签可属多分类",
        "系列：多页合并、拖拽改序、自动编号",
        "查重：感知哈希 + pHash + CLIP 三重比对",
        "分级识别 + 敏感内容一键打码",
        "多盘图库：像 Steam 库一样分盘管理",
        "四档性能挡位 · 任务断点续跑",
        "离线完整包 7.2 GB，无需 Python / torch",
    ]
    f = font(34, False)
    for i, text in enumerate(items):
        col, row = divmod(i, 5)
        x = 130 + col * 880
        y = 340 + row * 118
        d.ellipse([(x, y + 12), (x + 16, y + 28)], fill=ACCENT)
        d.text((x + 36, y), text, font=f, fill=(232, 236, 244))
    d.text((110, H - 150), "开源 · 免费 · 纯本地：不联网、不上传、不需要账号",
           font=font(34), fill=ACCENT2)
    img.save(OUT_CARDS / "summary.png")


def wrap(text: str, f, max_w: int, draw) -> list[str]:
    lines: list[str] = []
    for raw in text.split("|"):
        cur = ""
        for ch in raw:
            if text_w(draw, cur + ch, f) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return [ln for ln in lines if ln.strip()]


def make_sub(idx: int, text: str) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(46)
    lines = wrap(text, f, 1500, d)
    line_h = 64
    bottom = H - 92
    top = bottom - line_h * len(lines)
    d.rounded_rectangle([(W // 2 - 810, top - 22), (W // 2 + 810, bottom + 16)],
                        radius=18, fill=(0, 0, 0, 120))
    for i, line in enumerate(lines):
        draw_center(d, (W // 2, top + i * line_h + 30), line, f, (255, 255, 255),
                    stroke=5, stroke_fill=(0, 0, 0, 235))
    img.save(OUT_OVER / f"sub_{idx:03d}.png")


def make_chapter(idx: int, text: str) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(56)
    w = text_w(d, text, f)
    x0, y0 = 96, 84
    d.rounded_rectangle([(x0 - 26, y0 - 18), (x0 + w + 34, y0 + 78)],
                        radius=14, fill=(18, 19, 22, 205))
    d.rectangle([(x0 - 26, y0 - 18), (x0 - 18, y0 + 78)], fill=ACCENT)
    d.text((x0, y0 + 28), text, font=f, fill=(255, 255, 255), anchor="lm")
    img.save(OUT_OVER / f"title_{idx:03d}.png")


def main() -> int:
    OUT_CARDS.mkdir(parents=True, exist_ok=True)
    OUT_OVER.mkdir(parents=True, exist_ok=True)
    for old in list(OUT_OVER.glob("sub_*.png")) + list(OUT_OVER.glob("title_*.png")):
        try:
            old.unlink()
        except OSError:
            pass          # 被 Premiere 引用时删不掉，不碍事
    make_card_title()
    make_card_github()
    make_card_summary()
    n_sub = n_ch = 0
    for i, seg in enumerate(resolve(200.0), 1):
        sub = (seg.get("sub") or "").strip()
        if sub and not seg.get("card_sub"):
            make_sub(i, sub)
            n_sub += 1
        if seg.get("title"):
            make_chapter(i, seg["title"])
            n_ch += 1
    print(f"卡片 3 张，字幕 {n_sub} 张，章节标题 {n_ch} 张 -> {OUT_CARDS} / {OUT_OVER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
