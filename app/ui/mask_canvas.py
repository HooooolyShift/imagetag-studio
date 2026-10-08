"""画遮罩（局部重绘用）：在图上刷出要重绘的区域，导出黑白 mask PNG。

宿主提供给 DLC / 平板遥控共用：
  · 主程序里给 AI 生图 DLC 的窗口嵌进去（`from app.ui.mask_canvas import MaskCanvas`）；
  · 平板端在 PC 上通过 `POST /api/gen/inpaint` 传 base64 mask（那个接口内部也用同一套语义：
    白色=要重绘，黑色=保留）。

交互：左键画、右键（或按住 Alt）擦、滚轮缩放、拖动平移、`[` `]` 调笔刷大小、Ctrl+Z 撤销、C 清空。
导出：`export_mask(path)` 写出**与原图同尺寸**的黑白 PNG；`mask_png_bytes()` 给接口用。
"""
from __future__ import annotations

import io
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
                               QWidget)


class MaskCanvas(QWidget):
    """底图 + 可涂抹的遮罩层（白色=重绘区）。"""

    changed = Signal()

    def __init__(self, image_path: str | None = None, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.base: QPixmap | None = None
        self.mask: QImage | None = None          # 与 base 同尺寸的 ARGB 图（白=重绘）
        self.brush = 40
        self.zoom = 1.0
        self.pan = QPoint(0, 0)
        self._painting = False
        self._erasing = False
        self._last: QPoint | None = None
        self._undo: list[QImage] = []
        if image_path:
            self.load_image(image_path)

    # ---------- 载入 / 导出 ----------
    def load_image(self, path: str | Path) -> bool:
        pm = QPixmap(str(path))
        if pm.isNull():
            return False
        self.base = pm
        self.mask = QImage(pm.size(), QImage.Format_ARGB32)
        self.mask.fill(QColor(0, 0, 0, 0))       # 透明=不重绘
        self._undo.clear()
        self.zoom, self.pan = 1.0, QPoint(0, 0)
        self.update()
        return True

    def has_mask(self) -> bool:
        if self.mask is None:
            return False
        img = self.mask.convertToFormat(QImage.Format_Alpha8)
        w, h = img.width(), img.height()
        for y in range(0, h, max(1, h // 64)):        # 抽样判断，够用
            for x in range(0, w, max(1, w // 64)):
                if img.pixelColor(x, y).alpha() > 0:
                    return True
        return False

    def export_mask(self, path: str | Path) -> bool:
        """写出黑白 PNG（白=重绘、黑=保留），尺寸与原图一致。"""
        if self.mask is None:
            return False
        out = QImage(self.mask.size(), QImage.Format_Grayscale8)
        out.fill(0)
        p = QPainter(out)
        p.drawImage(0, 0, self.mask)
        p.end()
        return bool(out.save(str(path), "PNG"))

    def mask_png_bytes(self) -> bytes:
        """给接口用：白=重绘的黑白 PNG 字节。"""
        if self.mask is None:
            return b""
        buf = io.BytesIO()
        out = QImage(self.mask.size(), QImage.Format_Grayscale8)
        out.fill(0)
        p = QPainter(out)
        p.drawImage(0, 0, self.mask)
        p.end()
        out.save(buf, "PNG")
        return buf.getvalue()

    def clear_mask(self) -> None:
        if self.mask is None:
            return
        self._push_undo()
        self.mask.fill(QColor(0, 0, 0, 0))
        self.update()
        self.changed.emit()

    def undo(self) -> None:
        if self._undo:
            self.mask = self._undo.pop()
            self.update()
            self.changed.emit()

    # ---------- 绘制 ----------
    def _push_undo(self) -> None:
        if self.mask is not None:
            self._undo.append(self.mask.copy())
            if len(self._undo) > 20:
                self._undo.pop(0)

    def _to_image_pos(self, pos: QPoint) -> QPoint:
        """屏幕坐标 → 图像坐标（考虑缩放与平移）。"""
        x = (pos.x() - self.pan.x()) / max(1e-6, self.zoom)
        y = (pos.y() - self.pan.y()) / max(1e-6, self.zoom)
        return QPoint(int(x), int(y))

    def _draw_dot(self, pos: QPoint) -> None:
        if self.mask is None:
            return
        p = QPainter(self.mask)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = max(1.0, self.brush / 2.0)
        if self._erasing:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.setBrush(QColor(0, 0, 0, 0))
        else:
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.setBrush(QColor(255, 255, 255, 255))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(pos.x() - r, pos.y() - r, r * 2, r * 2))
        if self._last is not None and not self._erasing:
            pen = QPen(QColor(255, 255, 255, 255), self.brush)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(self._last, pos)
        p.end()
        self._last = pos
        self.update()
        self.changed.emit()

    # ---------- 事件 ----------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#15171c"))
        if self.base is None:
            p.setPen(QColor("#8f96a3"))
            p.drawText(self.rect(), Qt.AlignCenter, "没有载入图片")
            return
        p.save()
        p.translate(self.pan)
        p.scale(self.zoom, self.zoom)
        p.drawPixmap(0, 0, self.base)
        if self.mask is not None:
            tint = QImage(self.mask.size(), QImage.Format_ARGB32)
            tint.fill(QColor(0, 0, 0, 0))
            tp = QPainter(tint)
            tp.drawImage(0, 0, self.mask)
            tp.setCompositionMode(QPainter.CompositionMode_SourceIn)
            tp.fillRect(tint.rect(), QColor(255, 92, 138, 120))     # 半透明粉=重绘区
            tp.end()
            p.drawImage(0, 0, tint)
        p.restore()
        p.setPen(QColor("#8f96a3"))
        p.drawText(8, self.height() - 8,
                   f"笔刷 {self.brush}px · 左键画/右键擦 · 滚轮缩放 · 拖动平移（空格） · Ctrl+Z 撤销 · C 清空")

    def wheelEvent(self, ev) -> None:
        if self.base is None:
            return
        k = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(0.1, min(8.0, self.zoom * k))
        self.update()

    def mousePressEvent(self, ev) -> None:
        if self.base is None:
            return
        if ev.button() in (Qt.LeftButton, Qt.RightButton):
            if ev.modifiers() & Qt.ControlModifier:
                return                     # Ctrl+左键留给别的用途
            self._push_undo()
            self._painting = True
            self._erasing = (ev.button() == Qt.RightButton) or bool(ev.modifiers() & Qt.AltModifier)
            self._last = None
            self._draw_dot(self._to_image_pos(ev.pos()))

    def mouseMoveEvent(self, ev) -> None:
        if self._painting:
            self._draw_dot(self._to_image_pos(ev.pos()))

    def mouseReleaseEvent(self, _ev) -> None:
        self._painting = False
        self._last = None

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if k == Qt.Key_BracketLeft:
            self.brush = max(4, int(self.brush * 0.8))
            self.update()
        elif k == Qt.Key_BracketRight:
            self.brush = min(400, int(self.brush * 1.25))
            self.update()
        elif k == Qt.Key_C:
            self.clear_mask()
        elif k == Qt.Key_Z and (ev.modifiers() & Qt.ControlModifier):
            self.undo()
        else:
            super().keyPressEvent(ev)


def make_mask_editor(image_path: str | None = None, parent=None) -> QWidget:
    """带工具条的遮罩编辑器（宿主/DLC 直接塞进窗口即可）。

    用法：
        ed = make_mask_editor(图片路径)
        # 用户涂完
        ed.canvas.export_mask("mask.png")   /   ed.canvas.mask_png_bytes()
    """
    box = QWidget(parent)
    v = QVBoxLayout(box)
    canvas = MaskCanvas(image_path, box)
    v.addWidget(canvas, 1)
    row = QHBoxLayout()
    row.addWidget(QLabel("笔刷"))
    sl = QSlider(Qt.Horizontal)
    sl.setRange(4, 400)
    sl.setValue(canvas.brush)
    sl.valueChanged.connect(lambda x: (setattr(canvas, "brush", int(x)), canvas.update()))
    row.addWidget(sl, 1)
    for text, slot in (("撤销", canvas.undo), ("清空", canvas.clear_mask)):
        b = QPushButton(text)
        b.clicked.connect(slot)
        row.addWidget(b)
    v.addLayout(row)
    box.canvas = canvas            # 方便调用方直接拿 canvas
    return box
