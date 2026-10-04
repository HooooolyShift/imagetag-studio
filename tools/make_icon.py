"""生成应用图标（纯 Pillow 绘制，可复现/可微调）。

设计：深蓝圆角底 + 白色照片卡（山与太阳）+ 珊瑚色标签牌，表达「图片 + 打标签」。
输出：assets/icon.ico（多尺寸，供 exe 与快捷方式）与 assets/icon.png（预览/README）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024                      # 超采样画布
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

BG_TOP = (76, 130, 205)
BG_BOTTOM = (28, 52, 92)
CARD = (255, 255, 255, 255)
SKY = (214, 231, 255, 255)
MOUNT = (122, 163, 226, 255)
MOUNT2 = (92, 133, 200, 255)
SUN = (255, 205, 92, 255)
TAG = (255, 122, 89, 255)
TAG_EDGE = (214, 88, 60, 255)


def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def background() -> Image.Image:
    grad = Image.new("RGB", (1, S))
    px = grad.load()
    for y in range(S):
        t = y / (S - 1)
        px[0, y] = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
    grad = grad.resize((S, S))
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.225), fill=255)
    img.paste(grad, (0, 0), mask)
    hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse([int(-S * 0.25), int(-S * 0.75), int(S * 1.25), int(S * 0.35)],
                               fill=(255, 255, 255, 26))
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.05))
    img.alpha_composite(Image.composite(hl, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
    return img


def with_shadow(layer: Image.Image, blur: int, offset: tuple[int, int], alpha: int = 90) -> Image.Image:
    a = layer.split()[-1]
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, alpha), (0, 0), a)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, offset)
    out.alpha_composite(layer)
    return out


def photo_card() -> Image.Image:
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = int(S * 0.175), int(S * 0.205), int(S * 0.735), int(S * 0.745)
    rounded(d, [x0, y0, x1, y1], int(S * 0.055), fill=CARD)
    inset = int(S * 0.055)
    sx0, sy0, sx1, sy1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    rounded(d, [sx0, sy0, sx1, sy1], int(S * 0.032), fill=SKY)
    w, h = sx1 - sx0, sy1 - sy0
    d.ellipse([sx0 + int(w * 0.62), sy0 + int(h * 0.14),
               sx0 + int(w * 0.86), sy0 + int(h * 0.42)], fill=SUN)
    d.polygon([(sx0, sy1), (sx0 + int(w * 0.42), sy0 + int(h * 0.35)), (sx0 + int(w * 0.78), sy1)], fill=MOUNT)
    d.polygon([(sx0 + int(w * 0.46), sy1), (sx0 + int(w * 0.76), sy0 + int(h * 0.52)), (sx1, sy1)], fill=MOUNT2)
    return with_shadow(layer, int(S * 0.035), (0, int(S * 0.022)), 105)


def tag_badge() -> Image.Image:
    side = int(S * 0.78)
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = int(side * 0.66), int(side * 0.28)
    x0, y0 = int(side * 0.12), int(side * 0.36)
    r = int(h * 0.30)
    rounded(d, [x0, y0, x0 + w, y0 + h], r, fill=TAG, outline=TAG_EDGE, width=max(2, int(h * 0.045)))
    hole = int(h * 0.17)
    cx, cy = x0 + int(w * 0.18), y0 + h // 2
    d.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=(255, 255, 255, 235))
    p1 = (x0 + int(w * 0.42), y0 + int(h * 0.55))
    p2 = (x0 + int(w * 0.53), y0 + int(h * 0.70))
    p3 = (x0 + int(w * 0.76), y0 + int(h * 0.30))
    d.line([p1, p2, p3], fill=(255, 255, 255, 245), width=int(h * 0.10), joint="curve")
    layer = layer.rotate(-22, resample=Image.BICUBIC, expand=False)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.alpha_composite(layer, (int(S * 0.30), int(S * 0.40)))
    return with_shadow(out, int(S * 0.02), (0, int(S * 0.014)), 110)


def build() -> Image.Image:
    img = background()
    img.alpha_composite(photo_card())
    img.alpha_composite(tag_badge())
    return img


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    big = build()
    big.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon.png")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    big.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon.ico", sizes=sizes)
    print("已生成:", ASSETS / "icon.ico", "和", ASSETS / "icon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
