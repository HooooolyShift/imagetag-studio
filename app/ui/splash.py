"""启动过渡窗口（开屏）：显示一张随机封面图 + 名称/版本/启动进度。

封面图来源：设置里的「开屏封面文件夹」（留空则用随程序分发的 assets/splash/）。

轮换规则（伪随机 shuffle-bag，而不是每次独立随机）：
  把文件夹里的图洗牌成一份「播放列表」，之后每次启动按顺序取下一张，
  保证一轮之内不会重复；只有这两种情况才会重新洗牌：
    1) 轮换库有改动（增删图片、或图片内容/时间变了）
    2) 列表已经完整走过一轮
  ——这样既不会像真随机那样连着几张撞同一张，也不会出现"总轮不到某些图"。
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

W, H = 560, 340
COVER_H = 236


IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"}


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


def build_pixmap(settings, app_name: str, version: str, status: str = "正在启动…") -> QPixmap:
    from PySide6.QtGui import QFontDatabase
    fam = "Microsoft YaHei UI"
    if fam not in QFontDatabase.families():
        fam = "Microsoft YaHei" if "Microsoft YaHei" in QFontDatabase.families() else ""
    # 按屏幕缩放比渲染（高分屏下直接画 1x 位图会被拉伸 → 文字发虚）
    try:
        dpr = float(QApplication.primaryScreen().devicePixelRatio()) or 1.0
    except Exception:
        dpr = 1.0
    pm = QPixmap(int(W * dpr), int(H * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor("#16171b"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(dpr, dpr)                      # 之后都用"逻辑像素"坐标
    p.setRenderHint(QPainter.TextAntialiasing)
    # 上半部分：封面图（按比例裁切填满）
    cover = pick_cover(getattr(settings, "splash_dir", ""), settings)
    area = QRectF(0, 0, W, COVER_H)
    if cover is not None and not cover.isNull():
        scaled = cover.scaled(int(area.width()), int(area.height()),
                              Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - area.width()) / 2
        y = (scaled.height() - area.height()) / 2
        p.setClipRect(area)
        p.drawPixmap(int(-x), int(-y), scaled)
        p.setClipping(False)
        # 底部渐隐，和下面色块衔接
        grad = QLinearGradient(0, COVER_H - 70, 0, COVER_H)
        grad.setColorAt(0.0, QColor(22, 23, 27, 0))
        grad.setColorAt(1.0, QColor(22, 23, 27, 255))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(0, COVER_H - 70, W, 70))
    else:
        grad = QLinearGradient(0, 0, W, COVER_H)
        grad.setColorAt(0.0, QColor("#1d2434"))
        grad.setColorAt(1.0, QColor("#2a1f2c"))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRect(area)
    # 下半部分：名称 / 版本 / 状态
    f = QFont(fam) if fam else QFont(); f.setPointSizeF(15); f.setBold(True)
    p.setFont(f); p.setPen(QColor("#eef2f8"))
    p.drawText(24, COVER_H + 34, app_name)
    f2 = QFont(fam) if fam else QFont(); f2.setPointSizeF(9.5)
    p.setFont(f2); p.setPen(QColor("#8f96a3"))
    p.drawText(24, COVER_H + 56, f"v{version}　·　完全离线运行")
    p.setPen(QColor("#9fd0ff"))
    p.drawText(24, COVER_H + 82, status)
    p.setPen(QColor("#2a2c33"))
    p.drawLine(24, H - 22, W - 24, H - 22)
    p.end()
    return pm


class AppSplash(QSplashScreen):
    """启动过渡窗口：支持 set_status 更新进度文字。"""

    def __init__(self, settings, app_name: str, version: str):
        self._settings = settings
        self._app_name = app_name
        self._version = version
        super().__init__(build_pixmap(settings, app_name, version))
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        # 不要"忙"光标：悬停在开屏上转圈是启动期间残留的等待光标造成的
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.setCursor(Qt.ArrowCursor)

    def set_status(self, text: str) -> None:
        self.setPixmap(build_pixmap(self._settings, self._app_name, self._version, text))
        QApplication.processEvents()
