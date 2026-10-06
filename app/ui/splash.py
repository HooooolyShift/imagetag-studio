"""启动过渡窗口（开屏）：显示一张随机封面图 + 名称/版本/启动进度。

封面图来源：设置里的「开屏封面文件夹」（每次启动随机取一张）；没设置就用 assets 里的图标兜底。
"""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

W, H = 560, 340
COVER_H = 236


def pick_cover(folder: str | Path | None) -> QPixmap | None:
    """从封面文件夹里随机取一张图，读成 QPixmap（读不出来就返回 None 用内置图）。"""
    if not folder:
        # 没设置就优先用随程序分发的 assets/splash/（把授权允许的图放这里即可）
        here = Path(__file__).resolve().parent.parent.parent / "assets" / "splash"
        if here.is_dir():
            folder = here
    if not folder:
        return None
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return None
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"}
    files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in exts]
    if not files:
        return None
    random.shuffle(files)
    for f in files[:8]:                 # 最多试 8 张，避免个别坏图拖慢启动
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
    cover = pick_cover(getattr(settings, "splash_dir", ""))
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
