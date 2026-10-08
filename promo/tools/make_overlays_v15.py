"""v1.5 宣传片的卡片 / 字幕 / 章节标题（输出到 cards_v15 与 overlays_v15）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promo_plan_v15 import resolve  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT_CARDS = PROJECT / "promo" / "cards_v15"
OUT_OVER = PROJECT / "promo" / "overlays_v15"
W, H = 1920, 1080
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_R = r"C:\Windows\Fonts\msyh.ttc"
ACCENT = (242, 179, 46)
ACCENT2 = (74, 158, 255)
TEXT = (245, 245, 245)


def font(size: int, bold: bool = True):
    return ImageFont.truetype(FONT_B if bold else FONT_R, size)


def text_w(draw, s, f):
    return int(draw.textlength(s, font=f))


def center(draw, xy, s, f, fill, stroke=0, stroke_fill=(0, 0, 0, 220)):
    draw.text(xy, s, font=f, fill=fill, anchor="mm", stroke_width=stroke,
              stroke_fill=stroke_fill)


def gradient(bg=(22, 23, 27)):
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)],
               fill=(int(bg[0] + 16 * k), int(bg[1] + 18 * k), int(bg[2] + 26 * k)))
    return img


def icon(size: int):
    p = PROJECT / "assets" / "icon.png"
    if not p.exists():
        return None
    return Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)


def card_title_v15() -> None:
    img = gradient()
    d = ImageDraw.Draw(img)
    ic = icon(230)
    if ic:
        img.paste(ic, ((W - 230) // 2, 240), ic)
    center(d, (W // 2, 570), "图片标签工坊", font(92), TEXT)
    center(d, (W // 2, 672), "v1.5 更新宣传片", font(52), ACCENT)
    center(d, (W // 2, 762), "PC + 安卓端 · 局域网遥控 · AI 生图扩展包", font(38, False), (200, 206, 216))
    d.line([(W // 2 - 300, 820), (W // 2 + 300, 820)], fill=(70, 72, 78), width=2)
    center(d, (W // 2, 878), "纯本地 · 离线可用 · 开源免费", font(32, False), (150, 156, 166))
    img.save(OUT_CARDS / "title_v15.png")


def card_fixes() -> None:
    """修掉的硬 bug 清单（快闪用）。"""
    img = gradient((26, 20, 22))
    d = ImageDraw.Draw(img)
    d.text((120, 96), "顺手修掉的硬 bug", font=font(64), fill=TEXT)
    d.text((122, 178), "v1.4 → v1.5 期间真实踩到并修好的问题", font=font(32, False), fill=(200, 150, 150))
    d.line([(120, 240), (W - 120, 240)], fill=(90, 70, 74), width=2)
    items = [
        "设备发现服务从来没启动过（配对码一直是 0000）",
        "未定级的图首次审核必 409（两端 ver 算法不一致）",
        "SSE 事件名被参数覆盖 → 移动端整条事件丢失",
        "图谱放大后完全拖不动（可平移余量 -203px）",
        "AI 生图窗口最低 1213px，1280×800 笔记本放不下",
        "「停止生成」偶发不生效 → 改成定向取消",
    ]
    f = font(40, False)
    for i, t in enumerate(items):
        y = 300 + i * 108
        d.text((130, y), "✕", font=font(40), fill=(220, 90, 90))
        d.text((196, y), t, font=f, fill=(232, 236, 244))
    d.text((120, H - 120), "这一版不只是加功能，稳定性也补了一轮", font=font(34), fill=ACCENT)
    img.save(OUT_CARDS / "fixes.png")


def card_summary_v15() -> None:
    img = gradient()
    d = ImageDraw.Draw(img)
    ic = icon(120)
    if ic:
        img.paste(ic, (110, 92), ic)
    d.text((256, 110), "图片标签工坊", font=font(56), fill=TEXT)
    d.text((258, 184), "v1.5 功能一览", font=font(34, False), fill=ACCENT)
    d.line([(110, 258), (W - 110, 258)], fill=(72, 74, 80), width=2)
    items = [
        "安卓端（平板/手机）：自动发现 + 双向对码",
        "平板全功能遥控：浏览 / 审核 / 查重 / 图谱 / 框选",
        "移动端图谱重做：关系 + 热度 + 折叠 + 搜索运镜",
        "AI 生图扩展包：中文提示词 → 离线出图",
        "改图四件套：局部重绘 / 参考图 / 姿势 / 图融合",
        "4× 纯放大：1024 → 4096 只要 9 秒",
        "审核可撤销（Ctrl+Z，50 步）+ 多设备乐观并发",
        "启动开屏：14 张自绘封面伪随机轮换",
        "图库浏览模式：缩放 / 平移 / 翻页",
        "图谱、导入、写回、分级全面加固",
    ]
    f = font(32, False)
    for i, t in enumerate(items):
        col, row = divmod(i, 5)
        x = 130 + col * 880
        y = 320 + row * 116
        d.ellipse([(x, y + 12), (x + 16, y + 28)], fill=ACCENT)
        d.text((x + 36, y), t, font=f, fill=(232, 236, 244))
    d.text((110, H - 140), "开源 · 免费 · 纯本地：不联网、不上传、不需要账号",
           font=font(32), fill=ACCENT2)
    img.save(OUT_CARDS / "summary_v15.png")


def card_github_v15() -> None:
    img = gradient()
    d = ImageDraw.Draw(img)
    ic = icon(150)
    if ic:
        img.paste(ic, ((W - 150) // 2, 240), ic)
    center(d, (W // 2, 480), "图片标签工坊 v1.5", font(66), TEXT)
    center(d, (W // 2, 566), "开源 · 免费 · 纯本地", font(38, False), ACCENT)
    center(d, (W // 2, 672), "github.com/HooooolyShift/imagetag-studio", font(44), ACCENT2)
    center(d, (W // 2, 764), "平板 / 手机连上 PC，就能遥控全流程", font(34, False), (176, 182, 192))
    img.save(OUT_CARDS / "github_v15.png")


def wrap(text: str, f, max_w: int, draw) -> list[str]:
    lines = []
    for raw in text.split("|"):
        cur = ""
        for ch in raw:
            if text_w(draw, cur + ch, f) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return [x for x in lines if x.strip()]


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
        center(d, (W // 2, top + i * line_h + 30), line, f, (255, 255, 255),
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
    for old in list(OUT_OVER.glob("*.png")):
        try:
            old.unlink()
        except OSError:
            pass
    card_title_v15()
    card_fixes()
    card_summary_v15()
    card_github_v15()
    n_sub = n_ch = 0
    for i, seg in enumerate(resolve(), 1):
        sub = (seg.get("sub") or "").strip()
        if sub and not seg.get("card_sub"):
            make_sub(i, sub)
            n_sub += 1
        if seg.get("title"):
            make_chapter(i, seg["title"])
            n_ch += 1
    print(f"卡片 4 张，字幕 {n_sub} 张，章节标题 {n_ch} 张 -> {OUT_CARDS} / {OUT_OVER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
