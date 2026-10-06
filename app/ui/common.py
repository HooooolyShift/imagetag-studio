"""界面公共部件：暗色主题、缩略图线程池、通用小控件。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QPushButton

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


def _log_thumb(file_id: int, src: str, why: str) -> None:
    """缩略图失败时记一行日志（pythonw 没有控制台，出问题只能靠这个查）。"""
    try:
        from ..config import data_dir
        p = data_dir() / "thumbs.log"
        if p.exists() and p.stat().st_size > 2_000_000:      # 别无限涨
            p.write_text("", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as fh:
            import time as _t
            fh.write("%s  #%s  %s  -> %s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), file_id, src, why))
    except Exception:
        pass


class _ThumbJob(QRunnable):
    def __init__(self, file_id: int, src: str, mtime: float, size: int, signals: ThumbSignals):
        super().__init__()
        self.file_id, self.src, self.mtime, self.size, self.signals = file_id, src, mtime, size, signals

    def run(self) -> None:  # pragma: no cover - 线程内运行
        try:
            p = imaging.make_thumb(self.src, self.file_id, self.mtime, self.size)
            path = str(p) if p else ""
            if not path:
                _log_thumb(self.file_id, self.src, "make_thumb 返回空（格式不支持或文件损坏）")
        except Exception as exc:
            path = ""
            _log_thumb(self.file_id, self.src, f"{type(exc).__name__}: {exc}")
        try:                      # 对话框可能已关闭 → 信号源被销毁，忽略即可
            self.signals.ready.emit(self.file_id, path)
        except RuntimeError:
            pass


class SeriesOrderList(QListWidget):
    """系列页码排序控件（合并系列 / 重排系列 共用）。

    - 缩略图列表，直接拖动改顺序（InternalMove）
    - 双击看大图
    - F2 或右键「重命名单页文件名」→ 发 renameRequested 信号
    """

    renameRequested = Signal(object)

    def __init__(self, parent=None, size: int = 140):
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractItemView
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(size, size))
        self.setGridSize(QSize(size + 20, size + 50))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Snap)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(self._preview)
        self._rows: dict[int, object] = {}
        self._items: dict[int, QListWidgetItem] = {}
        self._pool = ThumbPool(size=size + 40)
        self._pool.signals.ready.connect(self._on_thumb)

    def set_files(self, rows) -> None:
        """rows: 数据库行，至少要有 id / name / path / mtime / page_no。"""
        self.clear()
        self._rows.clear()
        self._items.clear()
        for r in rows:
            fid = int(r["id"])
            label = (f"{int(r['page_no'] or 0):03d}  {r['name']}"
                     if "page_no" in r.keys() else str(r["name"]))
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, fid)
            it.setToolTip(str(r["path"]))
            self.addItem(it)
            self._rows[fid] = r
            self._items[fid] = it
            try:
                mt = 0.0
                try:
                    mt = float(r["mtime"] or 0)
                except Exception:
                    mt = 0.0
                self._pool.request(fid, str(r["path"]), mt)
            except Exception:
                pass

    def _on_thumb(self, file_id: int, path: str) -> None:
        it = self._items.get(int(file_id))
        if it is None or not path:
            return
        pm = load_pixmap(path)
        if not pm.isNull():
            it.setIcon(QIcon(pm.scaled(self.iconSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def ordered_ids(self) -> list[int]:
        out = []
        for i in range(self.count()):
            fid = self.item(i).data(Qt.UserRole)
            if fid:
                out.append(int(fid))
        return out

    def _key_event(self, e) -> None:
        if e.key() == Qt.Key_F2 and self.currentItem() is not None:
            self.renameRequested.emit(self.currentItem())
        else:
            super().keyPressEvent(e)

    keyPressEvent = _key_event

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        a_pre = menu.addAction("查看大图")
        a_ren = menu.addAction("重命名单页文件名")
        act = menu.exec(self.mapToGlobal(pos))
        if act == a_pre:
            self._preview(item)
        elif act == a_ren:
            self.renameRequested.emit(item)

    def _preview(self, item) -> None:
        fid = int(item.data(Qt.UserRole) or 0)
        r = self._rows.get(fid)
        if r is None:
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle(str(r["name"]))
        dlg.resize(900, 700)
        lay = QVBoxLayout(dlg)
        lab = QLabel()
        lab.setAlignment(Qt.AlignCenter)
        lab.setPixmap(load_pixmap(str(r["path"])).scaled(860, 640, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(lab, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("关闭")
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()


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


def load_pixmap(path: str | Path) -> QPixmap:
    """读一张图（带 EXIF 方向自动旋转，JPG/HEIC 之类 Qt 直读失败时兜底）。

    所有界面显示原图/大图都走这里，不要在各自文件里重复写 QPixmap + QImageReader 兜底。
    """
    pm = QPixmap(str(path))
    if not pm.isNull():
        return pm
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    img = reader.read()
    if not img.isNull():
        return QPixmap.fromImage(img)
    # 最后一道兜底：用 PIL 解（部分 webp/heic、CMYK 的 JPG、超大图 Qt 会读不出来，
    # 但缩略图是 PIL 生成的 → 于是出现"缩略图有、大图空白"这种看着不相干的现象）
    try:
        from PIL import Image, ImageOps
        im = Image.open(str(path))
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        im.thumbnail((4096, 4096))            # 超大图先降到 4K，避免一次性吃掉几百 MB
        data = im.convert("RGBA").tobytes("raw", "RGBA")
        qimg = QImage(data, im.width, im.height, QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(qimg)
    except Exception:
        return QPixmap()


def thumb_pixmap(file_id: int, src: str | Path, mtime: float = 0.0, size: int = 320) -> QPixmap | None:
    """取（必要时先生成）缩略图文件并读成 QPixmap；失败返回 None。"""
    try:
        p = imaging.thumb_path(int(file_id), float(mtime or 0), int(size))
        if not p.exists():
            p = imaging.make_thumb(str(src), int(file_id), float(mtime or 0), int(size)) or p
        pm = load_pixmap(p) if p and Path(p).exists() else QPixmap()
        if pm.isNull():
            pm = load_pixmap(src)          # 缩略图坏了就直接读原图
        if pm.isNull():
            return None
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


def thumb_icon(file_id: int, src: str | Path, mtime: float = 0.0, size: int = 320) -> QIcon | None:
    """缩略图 QIcon（列表/表格里的图标列共用）。"""
    pm = thumb_pixmap(file_id, src, mtime, size)
    return QIcon(pm) if pm is not None else None


def icon_from_path(path: str | Path) -> QIcon | None:
    p = str(path)
    if p in _icon_cache:
        return _icon_cache[p]
    pm = load_pixmap(p)
    if pm.isNull():
        return None
    ic = QIcon(pm)
    _icon_cache[p] = ic
    return ic


def clear_icon_cache() -> None:
    _icon_cache.clear()


def ok_cancel(dialog, parent_layout=None, ok_text: str = "确定", cancel_text: str = "取消"):
    """统一的「确定 / 取消」按钮条（中文文案，避免 Qt 默认显示 OK/Cancel）。

    返回按钮条本身，调用方按需 addWidget / addRow 到自己的布局里。
    """
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.button(QDialogButtonBox.Cancel).setText(cancel_text)
    bb.accepted.connect(dialog.accept)
    bb.rejected.connect(dialog.reject)
    if parent_layout is not None:
        if hasattr(parent_layout, "addRow"):
            parent_layout.addRow(bb)
        else:
            parent_layout.addWidget(bb)
    return bb


def colored(text: str, color: str) -> str:
    return f'<span style="color:{color}">{text}</span>'


def human_size(n: int | None) -> str:
    """文件体积人性化显示（查重/导入等界面共用）。"""
    if not n:
        return "?"
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024.0
    return "?"


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
            base = tag_i18n.display(t["name"], t["zh"] or "")
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
        words.append(tag_i18n.display(name, zh))
    comp = QCompleter(sorted(set(words)), line_edit)
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    comp.setFilterMode(Qt.MatchFlag.MatchContains)
    comp.setMaxVisibleItems(14)
    comp.setCompletionMode(QCompleter.PopupCompletion)
    line_edit.setCompleter(comp)


def tag_words(store, category: str | None = None) -> list[str]:
    """标签联想词条：category 为空/None = 全量；否则只列该分类下的标签。"""
    from .. import tag_i18n
    words: list[str] = []
    try:
        groups = store.tag_name_groups()
        for t in store.list_tags(category=category):
            base = tag_i18n.display(t["name"], t["zh"] or "")
            for cat in (groups.get(t["name"]) or [])[:2]:
                words.append(f"{cat} · {base}")
            words.append(base)
    except Exception:
        pass
    if not category:                       # 只有"全部"才把词典里未入库的也列出来
        for name, zh in tag_i18n.MODEL_DICT.items():
            words.append(tag_i18n.display(name, zh))
    return sorted(set(words))


def suggest_tags(text: str, store, category: str | None = None, limit: int = 200) -> list:
    """按匹配度返回建议（库里有的 + Danbooru 词典里有但库里没有的）。

    排序：完全一样 → 前缀命中 → 包含命中 → 拼音命中；同级按名字长度，短的在前面。
    """
    from .. import tag_i18n
    q = (text or "").strip().lower()
    lib = {str(t["name"]): str(t["zh"] or "") for t in store.list_tags(category=category) if t["name"]}

    def sc(name: str, zh: str):
        n, z = name.lower(), (zh or "").lower()
        if not q:
            return (0, len(n), n)
        if n == q or z == q:
            return (0, 0, n)
        if n.startswith(q) or z.startswith(q):
            return (1, len(n), n)
        if q in n or q in z:
            return (2, len(n), n)
        try:
            p = tag_i18n.pinyin(zh or name).lower()
            if q in p:
                return (3, len(n), n)
        except Exception:
            pass
        return None

    out = []
    for name, zh in lib.items():
        s = sc(name, zh)
        if s:
            out.append((s, name, zh))
    for name, zh in getattr(tag_i18n, "WHOLE", {}).items():
        if name in lib or not isinstance(zh, str):
            continue
        s = sc(str(name), zh)
        if s:
            out.append((s, str(name), zh))
    out.sort(key=lambda x: (x[0], x[1]))
    return out[:limit]


def refresh_tag_completer(line_edit, store, category: str | None = None,
                          query: str | None = None) -> None:
    """按当前分类重建某个输入框的联想词表。"""
    from PySide6.QtCore import QStringListModel, Qt
    from PySide6.QtWidgets import QCompleter
    if query is None:
        words = tag_words(store, category)
    else:
        from .. import tag_i18n
        words = [tag_i18n.display(n, zh) for _s, n, zh in suggest_tags(query, store, category)]
    comp = line_edit.completer()
    if comp is None:
        comp = QCompleter([], line_edit)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setMaxVisibleItems(20)
        line_edit.setCompleter(comp)
    try:
        # 关键：用"不过滤直接展示"模式。默认的 PopupCompletion 会用输入内容再过滤一次，
        # 于是**输入完整标签名时**（或与某项完全相同时）候选列表被过滤空/自动补全并收起，
        # 看起来就是"输入完整了就不联想"。
        from PySide6.QtWidgets import QCompleter as _QC
        comp.setCompletionMode(_QC.CompletionMode.UnfilteredPopupCompletion)
        comp.setMaxVisibleItems(20)
    except Exception:
        pass
    comp.setModel(QStringListModel(words, comp))


def install_tag_suggest(line_edit, store, category=None) -> None:
    """给普通输入框装"边打边联想"：每次按键都按匹配度重排候选（含 Danbooru 独有的词）。

    注意：这里**先把 completer 建好并填上全量候选**，再挂按键刷新 —— 以前只在第一次按键时
    才建，任何一步抛异常都会被 except 吞掉，结果就是"这个输入框压根没联想"。
    """
    def _on_text(txt: str) -> None:
        try:
            refresh_tag_completer(line_edit, store, category, query=txt)
            comp = line_edit.completer()
            if comp is not None and txt and comp.model() is not None and comp.model().rowCount():
                comp.setCompletionPrefix("")
                comp.complete()
        except Exception:
            pass
    try:
        refresh_tag_completer(line_edit, store, category)      # 先建好（不含过滤）
    except Exception:
        pass
    try:
        line_edit.textEdited.connect(_on_text)
    except Exception:
        pass


def fill_tag_combo(combo, store, category: str | None = None, query: str | None = None) -> None:
    """按分类填充一个可编辑下拉框（用于审核台/导入等"点开看标签"的框）。"""
    from .. import tag_i18n
    cur = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    try:
        if query:                       # 边打边筛：按匹配度排，含 Danbooru 独有的词
            for _s, name, zh in suggest_tags(query, store, category):
                combo.addItem(tag_i18n.display(name, zh), name)
        else:
            for t in store.list_tags(category=category):
                combo.addItem(tag_i18n.display(t["name"], t["zh"] or ""), t["name"])
    except Exception:
        pass
    combo.setEditText(cur)
    combo.blockSignals(False)
    try:
        combo.setMaxVisibleItems(20)
    except Exception:
        pass


def bind_category_filter(cat_combo, tag_input, store, combo_mode: bool = False):
    """把「分类」下拉框和标签输入框绑起来，所有能建标签的地方都用它。

    combo_mode=True  → tag_input 是可编辑下拉框（审核台/图谱等"点开看标签"）
    combo_mode=False → tag_input 是普通输入框（靠 QCompleter 联想）
    规则：分类 =「全部」→ 全量；分类 = 具体类型 → 只列该类型下的标签。
    建好后立刻按当前分类刷新一次。
    """
    def _apply(_index: int = -1) -> None:
        cat = cat_combo.currentData() or None
        if combo_mode:
            fill_tag_combo(tag_input, store, cat)
        else:
            refresh_tag_completer(tag_input, store, cat)
            install_tag_suggest(tag_input, store, cat)
    cat_combo.currentIndexChanged.connect(_apply)
    _apply()
    return _apply
