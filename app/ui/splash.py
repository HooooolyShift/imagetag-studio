"""启动过渡窗口（开屏）：封面图（完整显示）+ 下方白底信息条 + Win11 圆角。

封面图来源：设置里的「开屏封面文件夹」（留空则用随程序分发的 assets/splash/）。

轮换规则（伪随机 shuffle-bag，而不是每次独立随机）：
  把文件夹里的图洗牌成一份「播放列表」，之后每次启动按顺序取下一张，
  保证一轮之内不会重复；只有这两种情况才会重新洗牌：
    1) 轮换库有改动（增删图片、或图片内容/时间变了）
    2) 列表已经完整走过一轮

窗口尺寸规则：
  · 宽度固定（默认 560，保证在不同尺寸的自定义图下窗口宽度一致）；
  · 高度随封面比例算——图片区高 = 宽度 / 图宽高比，所以任何比例的图都能完整显示，
    不会被裁切；竖图等极端比例会夹在一个合理区间内等比居中（四周留底色）。
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QWidget

W = 560                  # 窗口宽度（固定，不随图变）
H = 354                  # 兜底总高度（没有封面图时用 236 + 118）
COVER_H = 236            # 默认封面区高度（无图/兜底用）
PANEL_H = 118            # 下方白底信息条高度（应用名 / 版本 / 状态 / 进度条）
RADIUS = 14              # Win11 风格圆角半径
COVER_H_MIN, COVER_H_MAX = 150, 520   # 极端比例时的图片区高度夹取范围
W_DIVISOR = 3            # 窗口宽度 = 屏幕宽度 / 3
W_MIN, W_MAX = 520, 1100  # 宽度夹取范围（小屏/超宽屏都别太夸张）

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"}


# ---------------- 封面文件夹 / 轮换 ----------------
def cover_folder(folder: str | Path | None) -> Path | None:
    """解析封面文件夹：留空就用随程序分发的 assets/splash/。"""
    if not folder:
        here = Path(__file__).resolve().parent.parent.parent / "assets" / "splash"
        if here.is_dir():
            folder = here
    if not folder:
        return None
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return None
    return p


def list_covers(p: Path) -> list[str]:
    return sorted(f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS)


def _signature(p: Path, names: list[str]) -> str:
    """库指纹：文件名 + 大小 + 修改时间。增删/替换图片都会变。"""
    h = hashlib.sha1()
    for n in names:
        try:
            st = (p / n).stat()
            h.update(f"{n}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8", "ignore"))
        except OSError:
            h.update(n.encode("utf-8", "ignore"))
        h.update(b"\n")
    return h.hexdigest()


def next_cover(settings=None, folder: str | Path | None = None) -> Path | None:
    """按「播放列表」取下一张封面；库变了或刚好走完一轮才重新洗牌。"""
    if folder is None and settings is not None:
        folder = getattr(settings, "splash_dir", "")
    p = cover_folder(folder)
    if p is None:
        return None
    names = list_covers(p)
    if not names:
        return None
    sig = _signature(p, names)
    playlist = [n for n in (getattr(settings, "splash_playlist", None) or []) if n in names] if settings else []
    idx = int(getattr(settings, "splash_playlist_index", 0) or 0) if settings else 0
    saved_sig = str(getattr(settings, "splash_playlist_sig", "") or "") if settings else ""
    # 需要重新洗牌的三种情况：库变了 / 列表没了或用光 / 一轮已经走完
    if settings is None or sig != saved_sig or not playlist or idx >= len(playlist):
        playlist = names[:]
        random.shuffle(playlist)
        idx = 0
    name = playlist[idx]
    if settings is not None:
        try:
            settings.splash_playlist = playlist
            settings.splash_playlist_index = idx + 1
            settings.splash_playlist_sig = sig
            settings.save()
        except Exception:
            pass
    return p / name


def pick_cover(folder: str | Path | None, settings=None) -> QPixmap | None:
    """取本次开屏要显示的封面图；读不出来就顺延到列表里的下一张。"""
    p = cover_folder(folder) if folder is not None else None
    if p is None and settings is not None:
        p = cover_folder(getattr(settings, "splash_dir", ""))
    if p is None:
        return None
    for _ in range(8):                  # 最多试 8 张，避免个别坏图拖慢启动
        f = next_cover(settings, p)
        if f is None:
            return None
        try:
            from .common import load_pixmap
            pm = load_pixmap(str(f))
            if not pm.isNull():
                return pm
        except Exception:
            continue
    return None


# ---------------- 绘制 ----------------
def cover_size(cover: QPixmap | None, width: int = W) -> tuple[int, int]:
    """窗口宽度固定，图片区高度按封面比例算 → 图完整显示，不裁切也不拉伸。"""
    ok = cover is not None and not cover.isNull() and cover.height() > 0
    aspect = (cover.width() / cover.height()) if ok else (W / COVER_H)
    ch = int(round(width / aspect)) if aspect > 0 else COVER_H
    ch = max(COVER_H_MIN, min(ch, COVER_H_MAX))
    return width, ch


def prefer_width() -> int:
    """窗口宽度取主屏可用宽度的 1/3（夹在 W_MIN~W_MAX 之间）。"""
    try:
        scr = QApplication.primaryScreen()
        if scr is not None:
            sw = scr.availableGeometry().width()
            if sw > 0:
                return max(W_MIN, min(int(sw / W_DIVISOR), W_MAX))
    except Exception:
        pass
    return W


def _dpr() -> float:
    try:
        d = float(QApplication.primaryScreen().devicePixelRatio())
        return d if d > 0 else 1.0
    except Exception:
        return 1.0


def _ui_fonts() -> tuple[QFont, QFont, QFont]:
    from PySide6.QtGui import QFontDatabase
    have = set(QFontDatabase.families())
    fams = [f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Noto Sans CJK SC")
            if f in have] or ["sans-serif"]

    def mk(size: float, bold: bool = False) -> QFont:
        f = QFont()
        try:
            f.setFamilies(fams)      # 把候选字体都交给 Qt，缺字时自动挑能显示中文的
        except Exception:
            f = QFont(fams[0])
        f.setPointSizeF(size)
        f.setBold(bold)
        return f

    return mk(15.5, True), mk(9.5), mk(9.5)


def draw_splash(p: QPainter, cover: QPixmap | None, app_name: str, version: str,
                status: str, w: int, h: int, cover_h: int,
                step: int = 0, total: int = 0, dpr: float = 1.0) -> None:
    """整块开屏：上方封面（完整）+ 下方独立白底信息条 + 圆角。

    封面按"物理像素"缩放后再画（并设置 devicePixelRatio）——高分屏（125%/150%）
    下如果只按逻辑像素缩放，系统会再放大一次，画面就会发糊、颗粒明显。
    """
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), RADIUS, RADIUS)
    p.setClipPath(path)
    # 上方：封面图（等比缩放到"宽度填满"，高度按图算，所以不裁切）
    p.fillRect(QRectF(0, 0, w, cover_h), QColor("#0f1116"))
    if cover is not None and not cover.isNull():
        scaled = cover.scaled(max(1, int(round(w * dpr))), max(1, int(round(cover_h * dpr))),
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        # setDevicePixelRatio 之后 width()/height() 是"物理像素"，要换回逻辑像素再算居中，
        # 否则高分屏（125%/150%）下会算出负偏移，图就被推到一边、铺不满。
        lw = scaled.width() / dpr
        lh = scaled.height() / dpr
        p.drawPixmap(int(round((w - lw) / 2)), int(round((cover_h - lh) / 2)), scaled)
    else:
        grad = QLinearGradient(0, 0, w, cover_h)
        grad.setColorAt(0.0, QColor("#1d2434"))
        grad.setColorAt(1.0, QColor("#2a1f2c"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRect(QRectF(0, 0, w, cover_h))
    # 下方：白底信息条——窗口向下延伸出来，不盖住图
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(0, cover_h, w, h - cover_h))
    p.setBrush(QColor(0, 0, 0, 18))
    p.drawRect(QRectF(0, cover_h, w, 1))            # 与图的分隔线
    f_name, f_sub, f_status = _ui_fonts()
    p.setFont(f_name)
    p.setPen(QColor("#141821"))
    p.drawText(QRectF(22, cover_h + 14, w - 44, 26), Qt.AlignLeft | Qt.AlignVCenter, app_name)
    p.setFont(f_sub)
    p.setPen(QColor("#6b7280"))
    p.drawText(QRectF(22, cover_h + 42, w - 44, 18), Qt.AlignLeft | Qt.AlignVCenter,
               f"v{version}　·　完全离线运行")
    p.setFont(f_status)
    p.setPen(QColor("#2563eb"))
    p.drawText(QRectF(22, cover_h + 65, w - 44, 18), Qt.AlignLeft | Qt.AlignVCenter, status)
    # 进度条（有 step/total 时才画；右边同时显示"第 n/N 步"）
    bar_y = cover_h + 92
    bar_h = 6.0
    bar_w = w - 44
    if total > 0:
        frac = max(0.0, min(1.0, step / float(total)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e5e7eb"))
        p.drawRoundedRect(QRectF(22, bar_y, bar_w, bar_h), bar_h / 2, bar_h / 2)
        if frac > 0:
            p.setBrush(QColor("#2563eb"))
            p.drawRoundedRect(QRectF(22, bar_y, max(bar_h, bar_w * frac), bar_h), bar_h / 2, bar_h / 2)
        p.setFont(f_sub)
        p.setPen(QColor("#9ca3af"))
        p.drawText(QRectF(22, bar_y - 20, bar_w, 16), Qt.AlignRight | Qt.AlignVCenter,
                   f"第 {step}/{total} 步")
    # 圆角描边（顺带让图的白底之外有个边界）
    p.setClipping(False)
    pen = QPen(QColor(0, 0, 0, 40))
    pen.setWidthF(1.0)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def build_pixmap(settings, app_name: str, version: str, status: str = "正在启动…") -> QPixmap:
    """把开屏画成位图（离屏预览 / 说明书截图用）。"""
    cover = pick_cover(getattr(settings, "splash_dir", ""), settings)
    dpr = _dpr()
    w, cover_h = cover_size(cover, prefer_width())
    h = cover_h + PANEL_H
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.scale(dpr, dpr)
    draw_splash(p, cover, app_name, version, status, w, h, cover_h, 0, 0, dpr)
    p.end()
    return pm


class AppSplash(QWidget):
    """启动过渡窗口：圆角 + 封面完整显示 + 下方白底信息条。"""

    def __init__(self, settings, app_name: str, version: str, status: str = "正在启动…",
                 step: int = 0, total: int = 0):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)    # 圆角外透明
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._settings = settings
        self._app_name = app_name
        self._version = version
        self._status = status
        self._step = step
        self._total = total
        self._cover = pick_cover(getattr(settings, "splash_dir", ""), settings)
        self._dpr = _dpr()
        # 宽度既取屏幕 1/3，又不超过原图像素宽（否则就是把图放大 → 发糊）
        want = prefer_width()
        if self._cover is not None and not self._cover.isNull():
            native = int(self._cover.width() / self._dpr)
            want = min(want, max(W_MIN, native))
        w, cover_h = cover_size(self._cover, want)
        self._w, self._cover_h = w, cover_h
        self._h = cover_h + PANEL_H
        self.setFixedSize(self._w, self._h)
        self._center_on_screen()
        # 不要"忙"光标：悬停在开屏上转圈是启动期间残留的等待光标造成的
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.setCursor(Qt.ArrowCursor)

    def _center_on_screen(self) -> None:
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        x = g.left() + (g.width() - self._w) // 2
        y = g.top() + (g.height() - self._h) // 2 - int(g.height() * 0.05)
        self.move(x, max(g.top(), y))

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        draw_splash(p, self._cover, self._app_name, self._version,
                    self._status, self._w, self._h, self._cover_h,
                    self._step, self._total, self._dpr)
        p.end()

    def set_status(self, text: str, step: int | None = None, total: int | None = None) -> None:
        self._status = text
        if step is not None:
            self._step = step
        if total is not None:
            self._total = total
        self.update()
        QApplication.processEvents()

    def finish(self, _win=None) -> None:
        self.hide()
        self.close()
