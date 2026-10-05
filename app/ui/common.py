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


def attach_tag_completer(line_edit, store) -> None:
    """给标签输入框加联想（所有"能建标签"的地方都用它）。

    数据源 = **按图谱分类组织的全量标签列表**：
      - 库里的标签（含它的图谱分类，形如「服装 · 白裙子（white_dress）」）
      - 内置汉化词典里的全部标签（含尚未入库的，形如「裙子（skirt）」）
    键入时按包含关系过滤；选中已有标签会解析成它的规范标签名，直接打上已有 tag。
    """
    from PySide6.QtCore import QStringListModel, Qt
    from PySide6.QtWidgets import QCompleter
    from .. import tag_i18n
    words: list[str] = []
    try:
        groups = store.tag_name_groups()          # tag -> [图谱分类名]
        for t in store.list_tags():
            zh = t["zh"] or tag_i18n.translate(t["name"])
            base = f"{zh}（{t['name']}）" if zh and zh != t["name"] else t["name"]
            for cat in (groups.get(t["name"]) or [])[:2]:
                words.append(f"{cat} · {base}")
            words.append(base)
    except Exception:
        pass
    # 词典里的全部标签（含未入库的），保证"随时新增 tag"也能立刻联想
    from .. import categories as _cats
    cat_label = {}
    try:
        for c in _cats.ordered(store):
            cat_label[c["key"]] = c["label"]
    except Exception:
        pass
    for name, zh in tag_i18n.MODEL_DICT.items():
        words.append(f"{zh}（{name}）" if zh and zh != name else name)
    comp = QCompleter(sorted(set(words)), line_edit)
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    comp.setFilterMode(Qt.MatchFlag.MatchContains)
    comp.setMaxVisibleItems(14)
    comp.setCompletionMode(QCompleter.PopupCompletion)
    line_edit.setCompleter(comp)
