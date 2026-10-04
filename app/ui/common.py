"""界面公共部件：暗色主题、缩略图线程池、通用小控件。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPushButton

from .. import imaging

STYLE = """
QWidget { background: #1e1f24; color: #d8dae0; font-family: "Microsoft YaHei UI","Segoe UI"; font-size: 12px; }
QMainWindow::separator { background: #2c2e35; width: 2px; height: 2px; }
QToolBar { background: #23252b; border: 0; spacing: 4px; padding: 4px; }
QToolButton { padding: 4px 8px; border-radius: 4px; }
QToolButton:hover { background: #34373f; }
QToolButton:checked { background: #3d6ea8; }
QPushButton { background: #2f323a; border: 1px solid #3c404a; border-radius: 4px; padding: 4px 10px; }
QPushButton:hover { background: #3a3e48; }
QPushButton:disabled { color: #6a6e78; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background: #26282f; border: 1px solid #3a3e47; border-radius: 4px; padding: 3px 6px; }
QLineEdit:focus { border-color: #4a7fc1; }
QTreeWidget, QListWidget, QTableWidget, QListView, QTreeView, QTableView {
    background: #1b1c21; border: 1px solid #2c2e35; alternate-background-color: #202127; }
QHeaderView::section { background: #26282f; border: 0; padding: 4px; }
QDockWidget::title { background: #26282f; padding: 4px; }
QTabBar::tab { background: #26282f; padding: 5px 10px; }
QTabBar::tab:selected { background: #3d6ea8; }
QGroupBox { border: 1px solid #2c2e35; border-radius: 4px; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QScrollBar:vertical { background: #1b1c21; width: 10px; }
QScrollBar::handle:vertical { background: #3c404a; border-radius: 5px; min-height: 24px; }
QCheckBox::indicator { width: 13px; height: 13px; }
QMenu { background: #26282f; border: 1px solid #3a3e47; }
QMenu::item:selected { background: #3d6ea8; }
QSplitter::handle { background: #2c2e35; }
"""


class ThumbSignals(QObject):
    ready = Signal(int, str)


class _ThumbJob(QRunnable):
    def __init__(self, file_id: int, src: str, mtime: float, size: int, signals: ThumbSignals):
        super().__init__()
        self.file_id, self.src, self.mtime, self.size, self.signals = file_id, src, mtime, size, signals

    def run(self) -> None:  # pragma: no cover - 线程内运行
        try:
            p = imaging.make_thumb(self.src, self.file_id, self.mtime, self.size)
            self.signals.ready.emit(self.file_id, str(p) if p else "")
        except Exception:
            self.signals.ready.emit(self.file_id, "")


class ThumbPool:
    """后台生成缩略图。"""

    def __init__(self, size: int = 320, workers: int | None = None):
        if not workers:
            try:
                from .. import perf
                workers = perf.thumb_workers()
            except Exception:
                workers = 4
        self.size = size
        self.signals = ThumbSignals()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(workers)

    def request(self, file_id: int, src: str, mtime: float) -> None:
        self.pool.start(_ThumbJob(file_id, src, mtime, self.size, self.signals))


_icon_cache: dict[str, QIcon] = {}


def icon_from_path(path: str | Path) -> QIcon | None:
    p = str(path)
    if p in _icon_cache:
        return _icon_cache[p]
    pm = QPixmap(p)
    if pm.isNull():
        return None
    ic = QIcon(pm)
    _icon_cache[p] = ic
    return ic


def clear_icon_cache() -> None:
    _icon_cache.clear()


def colored(text: str, color: str) -> str:
    return f'<span style="color:{color}">{text}</span>'


class ChipList(QListWidget):
    """小标签列表（可勾选）。"""

    toggled_tag = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.NoSelection)
        self.itemChanged.connect(self._on_changed)

    def _on_changed(self, item: QListWidgetItem) -> None:
        self.toggled_tag.emit(item.data(Qt.UserRole) or item.text(), item.checkState() == Qt.Checked)

    def set_tags(self, tags, checked: set[str] | None = None) -> None:
        checked = checked or set()
        self.blockSignals(True)
        self.clear()
        for t in tags:
            it = QListWidgetItem(str(t))
            it.setData(Qt.UserRole, str(t))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if str(t) in checked else Qt.Unchecked)
            self.addItem(it)
        self.blockSignals(False)


def tool_button(text: str, tip: str = "", checkable: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str, color: str | None = None, bold: bool = False) -> QLabel:
    lb = QLabel(text)
    if color:
        lb.setStyleSheet(f"color:{color};")
    if bold:
        f = lb.font()
        f.setBold(True)
        lb.setFont(f)
    return lb
