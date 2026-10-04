"""图片读取与缩略图缓存。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

from .config import thumbs_dir

try:
    Image.MAX_IMAGE_PIXELS = None
except Exception:
    pass


def open_image(path: str | Path) -> Image.Image:
    """打开图片并把像素读进内存，随即释放文件句柄（Windows 上别人才动得了这个文件）。"""
    img = Image.open(path)
    try:
        img.load()
    except Exception:
        pass
    out = ImageOps.exif_transpose(img)
    try:
        out.load()
    except Exception:
        pass
    try:
        if out is not img:
            img.close()
    except Exception:
        pass
    return out


def load_rgb(path: str | Path, max_side: int | None = None) -> Image.Image:
    img = open_image(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def image_size(path: str | Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as im:
            w, h = im.size
            if im.getexif():
                o = im.getexif().get(274, 1)
                if o in (6, 8):
                    w, h = h, w
            return int(w), int(h)
    except Exception:
        return None, None


def _thumb_key(file_id: int, mtime: float, size: int) -> str:
    h = hashlib.md5(f"{file_id}|{mtime:.3f}|{size}".encode()).hexdigest()
    return h


def thumb_path(file_id: int, mtime: float, size: int) -> Path:
    key = _thumb_key(file_id, mtime, size)
    d = thumbs_dir() / key[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.jpg"


def make_thumb(src: str | Path, file_id: int, mtime: float, size: int = 320) -> Path | None:
    """生成缩略图（缓存命中则直接返回）。"""
    dst = thumb_path(file_id, mtime, size)
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    try:
        with Image.open(src) as raw:
            raw.load()
            img = ImageOps.exif_transpose(raw)
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, (28, 28, 32))
                img = img.convert("RGBA")
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            tmp = dst.with_suffix(".tmp")
            img.save(tmp, "JPEG", quality=82, optimize=True)
        tmp.replace(dst)
        return dst
    except Exception:
        return None


def clear_thumb_cache() -> int:
    n = 0
    for p in thumbs_dir().rglob("*.jpg"):
        try:
            p.unlink()
            n += 1
        except Exception:
            pass
    return n


# ---------------- 感知哈希（查重用，不依赖额外库） ----------------
def _bits_to_hex(bits: int, size: int = 8) -> str:
    return f"{bits:0{size * size // 4}x}"


def dhash(path: str | Path, size: int = 8) -> str | None:
    """差值哈希：缩放成 (size+1)x size 灰度图，比较相邻像素。对缩放/轻微压缩不敏感。"""
    try:
        img = open_image(path).convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(img.getdata())
        bits = 0
        for y in range(size):
            row = y * (size + 1)
            for x in range(size):
                bits = (bits << 1) | (1 if px[row + x] > px[row + x + 1] else 0)
        return _bits_to_hex(bits, size)
    except Exception:
        return None


def ahash(path: str | Path, size: int = 8) -> str | None:
    """均值哈希：更粗，用来兜住亮度变化大的情况。"""
    try:
        img = open_image(path).convert("L").resize((size, size), Image.LANCZOS)
        px = list(img.getdata())
        avg = sum(px) / len(px)
        bits = 0
        for v in px:
            bits = (bits << 1) | (1 if v >= avg else 0)
        return _bits_to_hex(bits, size)
    except Exception:
        return None
