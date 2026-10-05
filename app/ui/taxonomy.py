"""标签体系编辑器：分类节点 + 标签节点的关系图。

设计要点（与用户需求对应）：
- 一个标签可以同时属于多个分类（多对多连线），所以用图而不是树；
- 分类本身还能挂到更大的分类下面，节点可随时增删、自动排版或手动拖动；
- 这一层完全独立于图片：图片只存 tag，改层级不会动到图片；
- 在这里改名标签（如「初音未来」→「hatsune miku」）可一键同步改掉图片文件名。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QGraphicsItem,
    QGraphicsPathItem, QGraphicsScene, QGraphicsRectItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
    QTableWidget, QTabWidget,
    QTableWidgetItem, QInputDialog,
    QHeaderView,
)

from .. import categories as cats
from .common import label, ok_cancel
from .dialogs import category_combo

GROUP_FILL = QColor("#2b3d52")
GROUP_BORDER = QColor("#4a7fc1")
TAG_FILL = QColor("#2a2c33")
TAG_BORDER = QColor("#4a4e58")
TAG_FILL_USED = QColor("#31343d")
TAG_BORDER_USED = QColor("#5f6572")
SEL_BORDER = QColor("#ffb347")
EDGE_COLOR = QColor("#5a6270")

NODE_W = 168.0
NODE_H = 38.0


class NodeItem(QGraphicsRectItem):
    def __init__(self, kind: str, nid: int, name: str, count: int = 0, on_move=None):
        super().__init__(0, 0, NODE_W, NODE_H)
        self.kind = kind            # 'node' | 'tag'
        self.nid = nid
        self.name = name
        self.count = count
        self.on_move = on_move
        self.edges: list[EdgeItem] = []
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(2)
        self.setAcceptHoverEvents(True)

    def center(self) -> QPointF:
        return self.scenePos() + QPointF(NODE_W / 2, NODE_H / 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for e in self.edges:
                e.update_path()
        if change == QGraphicsItem.ItemPositionChange and self.on_move:
            self.on_move(self)
        return super().itemChange(change, value)

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, NODE_W + 4, NODE_H + 4)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, NODE_W, NODE_H)
        path = QPainterPath()
        path.addRoundedRect(r, 7, 7)
        if self.kind == "node":
            painter.setBrush(QBrush(GROUP_FILL))
            painter.setPen(QPen(SEL_BORDER if self.isSelected() else GROUP_BORDER, 2))
        else:
            used = self.count > 0
            painter.setBrush(QBrush(TAG_FILL_USED if used else TAG_FILL))
            painter.setPen(QPen(SEL_BORDER if self.isSelected() else (TAG_BORDER_USED if used else TAG_BORDER), 2))
        painter.drawPath(path)
        f = QFont(painter.font())
        f.setPointSizeF(9.0)
        f.setBold(self.kind == "node")
        painter.setFont(f)
        painter.setPen(QColor("#e8eaf0") if self.kind == "node" or self.count else QColor("#b9bcc5"))
        text = self.name + (f"  ({self.count})" if self.kind == "tag" and self.count else "")
        painter.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight, int(NODE_W - 14)))


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src: NodeItem, dst: NodeItem, edge_id: int = 0, relation: str = "is_a"):
        super().__init__()
        self.src = src
        self.dst = dst
        self.edge_id = edge_id
        self.relation = relation
        self.setZValue(1)
        # 从属边（tag→tag）用青色，和"分类边"区分开
        self.edge_color = QColor("#3fc1c9") if relation == "sub_of" else EDGE_COLOR
        self.setPen(QPen(self.edge_color, 2.0 if relation == "sub_of" else 1.6))
        src.edges.append(self)
        dst.edges.append(self)
        self.update_path()

    def update_path(self) -> None:
        a = self.src.center()
        b = self.dst.center()
        path = QPainterPath(a)
        # 直线代替三次贝塞尔：连线上千条时开销小得多
        path.lineTo(b)
        self.setPath(path)

    def paint(self, painter, option, widget=None) -> None:
        """画连线 + 末端箭头（箭头指向子节点，直观表示从属关系）。"""
        super().paint(painter, option, widget)
        path = self.path()
        if path.isEmpty():
            return
        import math
        end = path.pointAtPercent(1.0)
        before = path.pointAtPercent(0.9)
        ang = math.atan2(end.y() - before.y(), end.x() - before.x())
        size = 10.0
        pts = [end,
               QPointF(end.x() - size * math.cos(ang - 0.42), end.y() - size * math.sin(ang - 0.42)),
               QPointF(end.x() - size * math.cos(ang + 0.42), end.y() - size * math.sin(ang + 0.42))]
        painter.setBrush(QBrush(self.edge_color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF(pts))


class GraphCanvas(QGraphicsView):
    nodeClicked = Signal(str, int)
    edgeClicked = Signal(int)
    moved = Signal()
    linkRequested = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontSavePainterState, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setBackgroundBrush(QBrush(QColor("#16171b")))
        self.items: dict[tuple[str, int], NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self.connect_mode = False
        self.connect_from: NodeItem | None = None
        self._pending_save = False

    # ---------- 构建 ----------
    def clear(self) -> None:
        self.scene_.clear()
        self.items.clear()
        self.edges.clear()
        self.connect_from = None

    def add_node(self, kind: str, nid: int, name: str, count: int, x: float, y: float) -> NodeItem:
        item = NodeItem(kind, nid, name, count, on_move=self._on_move)
        item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        item.setPos(x, y)
        self.scene_.addItem(item)
        self.items[(kind, nid)] = item
        return item

    def add_edge(self, parent_kind: str, parent_id: int, child_kind: str, child_id: int,
                 edge_id: int = 0, relation: str = "is_a") -> None:
        src = self.items.get((parent_kind, parent_id))
        dst = self.items.get((child_kind, child_id))
        if src is None or dst is None:
            return
        e = EdgeItem(src, dst, edge_id, relation)
        self.scene_.addItem(e)
        self.edges.append(e)

    def _on_move(self, item: NodeItem) -> None:
        self._pending_save = True

    def save_positions(self, store) -> None:
        if not self._pending_save:
            return
        for (kind, nid), item in self.items.items():
            store.set_pos(kind, nid, item.pos().x(), item.pos().y())
        self._pending_save = False

    def auto_layout(self) -> None:
        """按层级自动排版（多父节点取最深的一层）。"""
        depths: dict[tuple[str, int], int] = {}
        parents: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for e in self.edges:
            key = (e.dst.kind, e.dst.nid)
            parents.setdefault(key, []).append((e.src.kind, e.src.nid))

        def depth(key, guard=0):
            if key in depths:
                return depths[key]
            ps = parents.get(key) or []
            if not ps or guard > 30:
                depths[key] = 0
                return 0
            depths[key] = max(depth(p, guard + 1) for p in ps) + 1
            return depths[key]

        buckets: dict[int, list[NodeItem]] = {}
        for key, item in self.items.items():
            buckets.setdefault(depth(key), []).append(item)
        for d in sorted(buckets):
            items = buckets[d]

            def sort_key(it: NodeItem):
                ps = [self.items[k] for k in parents.get((it.kind, it.nid), []) if k in self.items]
                return (min([p.center().y() for p in ps]) if ps else it.pos().y(), it.name)

            items.sort(key=sort_key)
            for i, it in enumerate(items):
                it.setPos(40 + d * 230, 40 + i * (NODE_H + 18))
        self._pending_save = True
        self.scene_.setSceneRect(self.scene_.itemsBoundingRect().adjusted(-60, -60, 80, 80))

    # ---------- 交互 ----------
    def mousePressEvent(self, event) -> None:
        if self.connect_mode:
            item = self.itemAt(event.pos())
            while item is not None and not isinstance(item, NodeItem):
                item = item.parentItem()
            if isinstance(item, NodeItem):
                if self.connect_from is None:
                    self.connect_from = item
                    item.setSelected(True)
                else:
                    self.linkRequested.emit(self.connect_from, item)
                    for it in self.items.values():
                        it.setSelected(False)
                    self.connect_from = None
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, (NodeItem, EdgeItem)):
            item = item.parentItem()
        if isinstance(item, NodeItem):
            self.nodeClicked.emit(item.kind, item.nid)
        elif isinstance(item, EdgeItem):
            self.edgeClicked.emit(item.edge_id)
        if self._pending_save:
            self.moved.emit()


class LinkTagsDialog(QDialog):
    """给某个分类挂上若干标签（多选）。"""

    def __init__(self, store, parent=None, exclude: set[int] | None = None):
        super().__init__(parent)
        self.store = store
        self.exclude = exclude or set()
        self.setWindowTitle("关联标签到该分类")
        self.resize(560, 620)
        v = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标签…（可多选，Ctrl/Shift）")
        self.search.textChanged.connect(self.reload)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(self.list, 1)
        info = QLabel("提示：一个标签可以同时挂在多个分类下，重复关联不会被拦截。")
        info.setWordWrap(True)
        v.addWidget(info)
        ok_cancel(self, v)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for t in self.store.list_tags(self.search.text().strip()):
            if int(t["id"]) in self.exclude:
                continue
            it = QListWidgetItem(f"{t['name']}   [{cats.label_of(self.store, t['category'])}]  ({t['count']})")
            it.setData(Qt.UserRole, int(t["id"]))
            self.list.addItem(it)

    def selected_ids(self) -> list[int]:
        return [int(i.data(Qt.UserRole)) for i in self.list.selectedItems()]


class NewTagForNodeDialog(QDialog):
    """在体系中新建标签：名称 + 类型 + 提示词（类型决定 CLIP 提示词模板）。"""

    def __init__(self, store, parent=None, prefill: str = ""):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("新建标签")
        self.resize(480, 260)
        form = QFormLayout(self)
        self.name = QLineEdit(prefill)
        form.addRow("标签名", self.name)
        from .common import attach_tag_completer, bind_category_filter
        attach_tag_completer(self.name, store)
        self.cat = category_combo(store, "", include_all=True)
        form.addRow("类型", self.cat)
        # 分类 ↔ 标签名联想联动：选「全部」列全部标签，选具体类型只看该类
        bind_category_filter(self.cat, self.name, store)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("留空即可；填 hatsune_miku 这类英文可让 WD14 直接命中")
        form.addRow("提示词", self.prompt)
        ok_cancel(self, form)

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "category": self.cat.ensure_current(self),
                "prompt": self.prompt.text().strip()}


class TaxonomyDialog(QDialog):
    """标签体系总界面：左边结构化树 + 中间关系图 + 右边属性。"""

    changed = Signal()
    tagCreated = Signal(str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.store = library.store
        self.setWindowTitle("标签体系（分类图谱）—— 与图片分离，随便改层级都不影响图片")
        self.resize(1460, 900)
        self.current: tuple[str, int] | None = None
        self._collapsed_now: set[int] = set()
        self._expanded_now: set[int] = set()
        self._focus_tag: int | None = None      # 搜索命中的标签：即使超出可见上限也要画出来
        self.connect_source: NodeItem | None = None
        v = QVBoxLayout(self)

        bar = QHBoxLayout()
        for text, slot in (("新建分类", self.new_group), ("新建标签", self.new_tag),
                           ("关联标签…", self.link_tags), ("取消关联", self.unlink_selected),
                           ("重命名…", self.rename_selected), ("删除", self.delete_selected)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addSpacing(16)
        self.b_connect = QPushButton("连线模式：关")
        self.b_connect.setCheckable(True)
        self.b_connect.toggled.connect(self.toggle_connect)
        bar.addWidget(self.b_connect)
        b_layout = QPushButton("自动排列")
        b_layout.clicked.connect(self.do_layout)
        bar.addWidget(b_layout)
        b_link = QPushButton("按分类自动连线")
        b_link.setToolTip("把库里所有标签按它们的「类型」连到对应分类节点上（已连过的不会重复），"
                          "这样图谱从一开始就是连通的")
        b_link.clicked.connect(self.auto_link_by_category)
        bar.addWidget(b_link)
        b_collapse_all = QPushButton("折叠全部")
        b_collapse_all.setToolTip("把所有分类节点折叠起来（只显示顶层），节点多的时候很有用")
        b_collapse_all.clicked.connect(lambda: self.set_all_collapsed(True))
        bar.addWidget(b_collapse_all)
        b_expand_all = QPushButton("展开全部")
        b_expand_all.clicked.connect(lambda: self.set_all_collapsed(False))
        bar.addWidget(b_expand_all)
        self.show_all = QCheckBox("显示未分类标签")
        self.show_all.toggled.connect(self.rebuild)
        bar.addWidget(self.show_all)
        bar.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bar.addWidget(b_close)
        v.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self.on_tree_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        find_row = QHBoxLayout()
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("搜索标签并定位（中文/英文都行，回车跳转）")
        self.find_edit.returnPressed.connect(self.goto_first_match)
        find_row.addWidget(self.find_edit, 1)
        b_find = QPushButton("定位")
        b_find.clicked.connect(self.goto_first_match)
        find_row.addWidget(b_find)
        left_wrap = QWidget()
        lw = QVBoxLayout(left_wrap)
        lw.setContentsMargins(0, 0, 0, 0)
        lw.addWidget(self.tree, 1)
        lw.addLayout(find_row)
        # 左侧两个标签页：体系树 / 标签管理（原来独立的"标签管理"窗口并到这里）
        left_tabs = QTabWidget()
        left_tabs.addTab(left_wrap, "体系树")
        left_tabs.addTab(self._build_tag_tab(), "标签管理")
        split.addWidget(left_tabs)

        self.canvas = GraphCanvas()
        self.canvas.nodeClicked.connect(self.on_canvas_clicked)
        self.canvas.moved.connect(lambda: self.canvas.save_positions(self.store))
        self.canvas.linkRequested.connect(self.on_connect_requested)
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self.canvas_menu)
        split.addWidget(self.canvas)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 6, 6, 6)
        rv.addWidget(label("属性", "#9fd0ff", True))
        self.detail = QLabel("选中左侧树或中间的节点")
        self.detail.setWordWrap(True)
        rv.addWidget(self.detail)
        self.node_name = QLineEdit()
        self.node_name.setPlaceholderText("显示名称")
        rv.addWidget(self.node_name)
        self.node_note = QLineEdit()
        self.node_note.setPlaceholderText("中文名 / 备注（手填优先，会用于文件名和界面显示）")
        self.node_note.setToolTip("标签：这一栏就是它的中文名（写进文件名、界面显示都用它，留空则用内置词典）\n"
                                  "分类节点：这一栏是分类的备注说明")
        rv.addWidget(self.node_note)
        b_apply = QPushButton("保存名称/备注")
        b_apply.clicked.connect(self.apply_detail)
        rv.addWidget(b_apply)
        rv.addWidget(label("所属分类（一个标签可以有多个）", "#8f96a3"))
        self.parents_list = QListWidget()
        rv.addWidget(self.parents_list, 1)
        b_unlink = QPushButton("取消选中的关联")
        b_unlink.clicked.connect(self.unlink_parent)
        rv.addWidget(b_unlink)
        rv.addStretch(1)
        rv.addWidget(label("说明", "#9fd0ff", True))
        rv.addWidget(label("图片里只存 tag；分类、层级、连线都只存在于本界面。\n"
                           "在「重命名…」里改名标签，可选择顺手改掉图片文件名。", "#8f96a3"))
        split.addWidget(right)
        split.setSizes([330, 820, 310])
        v.addWidget(split, 1)
        self.rebuild()

    # ---------------- 左侧「标签管理」标签页（与图谱联动） ----------------
    def _build_tag_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        tip = QLabel("① 直接点下面这个框就能看到全部已有标签（也可输入中文/英文过滤）")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8f96a3;")
        v.addWidget(tip)
        # 分类过滤：默认「全部（不限分类）」= 全量；选具体类型则只列该类型下的标签
        from .. import categories as _cats0
        row_cat = QHBoxLayout()
        row_cat.addWidget(QLabel("分类"))
        self.tag_cat_filter = QComboBox()
        self.tag_cat_filter.addItem("全部（不限分类）", "")
        for c in _cats0.ordered(self.store):
            self.tag_cat_filter.addItem(f"{c['label']} ({c['key']})"
                                       + (f"  〔{c['count']}〕" if c["count"] else ""), c["key"])
        self.tag_cat_filter.setToolTip("选一个分类 → 下面只列该分类下的标签；选「全部」→ 列全部标签")
        self.tag_cat_filter.currentIndexChanged.connect(lambda _i: self.reload_tag_table())
        row_cat.addWidget(self.tag_cat_filter, 1)
        v.addLayout(row_cat)
        # 用可编辑下拉框当搜索框：不输入也能展开看到全部标签，且宽度足够不截断中文
        self.tag_search = QComboBox()
        self.tag_search.setEditable(True)
        self.tag_search.setInsertPolicy(QComboBox.NoInsert)
        self.tag_search.setMinimumWidth(320)
        self.tag_search.lineEdit().setPlaceholderText("点这里看全部标签，或输入中文/英文过滤")
        self.tag_search.setMaxVisibleItems(30)
        self.tag_search.editTextChanged.connect(self.reload_tag_table)
        self.tag_search.lineEdit().returnPressed.connect(self.locate_from_table)
        self.tag_search.activated.connect(lambda _i: self.locate_from_table())
        v.addWidget(self.tag_search)
        self.tag_table = QTableWidget(0, 3)
        self.tag_table.setHorizontalHeaderLabels(["标签（中文备注 / 英文原名）", "类型", "图片数"])
        self.tag_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tag_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tag_table.horizontalHeader().setStretchLastSection(False)
        self.tag_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)   # 第一列自适应，不再截断
        self.tag_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tag_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tag_table.setSortingEnabled(False)
        self.tag_table.cellDoubleClicked.connect(lambda *_: self.locate_from_table())
        self.tag_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tag_table.customContextMenuRequested.connect(self._tag_table_menu)
        v.addWidget(self.tag_table, 1)
        hint = QLabel("② 先在上面表里点一行选中标签，再点下面的按钮；右键行也有同样菜单")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8f96a3;")
        v.addWidget(hint)
        row = QHBoxLayout()
        for text, slot in (("定位到图上", self.locate_from_table),
                           ("改类型…", self.change_category_selected),
                           ("删除标签", self.delete_tag_selected)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        self.tag_note = QLabel("")
        self.tag_note.setWordWrap(True)
        self.tag_note.setStyleSheet("color:#8f96a3;")
        v.addWidget(self.tag_note)
        self.reload_tag_table()
        return w

    def _tag_table_menu(self, pos) -> None:
        row = self.tag_table.rowAt(pos.y())
        if row < 0:
            return
        self.tag_table.selectRow(row)
        menu = QMenu(self)
        a_locate = menu.addAction("在图上定位这个标签")
        a_cat = menu.addAction("改类型…")
        a_del = menu.addAction("删除这个标签")
        act = menu.exec(self.tag_table.mapToGlobal(pos))
        if act == a_locate:
            self.locate_from_table()
        elif act == a_cat:
            self.change_category_selected()
        elif act == a_del:
            self.delete_tag_selected()

    def reload_tag_table(self) -> None:
        from .. import tag_i18n, categories as _cats
        if not hasattr(self, "tag_table"):
            return
        LIMIT = 500
        # 分类过滤（默认「全部」= 全量）
        try:
            cat_key = self.tag_cat_filter.currentData() if hasattr(self, "tag_cat_filter") else ""
        except Exception:
            cat_key = ""
        cat_key = cat_key or ""
        # 下拉框里同步"全部标签"（不输入也能点开挑），并按当前输入过滤表格
        try:
            q = self.tag_search.currentText().strip()
        except Exception:
            q = ""
        all_rows = self.store.list_tags(q, category=cat_key or None)
        if hasattr(self, "tag_search"):
            from .common import fill_tag_combo
            fill_tag_combo(self.tag_search, self.store, cat_key or None)
        rows = all_rows[:LIMIT]
        self.tag_table.setRowCount(len(rows))
        for i, t in enumerate(rows):
            it = QTableWidgetItem(tag_i18n.display(t["name"], t["zh"] or ""))
            it.setData(Qt.UserRole, int(t["id"]))
            it.setToolTip(t["name"])
            self.tag_table.setItem(i, 0, it)
            cat = QTableWidgetItem(_cats.label_of(self.store, t["category"]))
            cat.setData(Qt.UserRole, t["category"])
            self.tag_table.setItem(i, 1, cat)
            self.tag_table.setItem(i, 2, QTableWidgetItem(str(t["count"])))
        if hasattr(self, "tag_note"):
            self.tag_note.setText(
                f"共 {len(all_rows)} 个标签" + (f"（只列出前 {LIMIT} 个，用搜索框定位）" if len(all_rows) > LIMIT else "")
                + "　·　双击 = 定位到图上；改动会立即同步图谱")

    def _selected_tag_id(self) -> int | None:
        row = self.tag_table.currentRow() if hasattr(self, "tag_table") else -1
        item = self.tag_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.UserRole)) if item else None

    def locate_from_table(self) -> None:
        tid = self._selected_tag_id()
        row = self.store.one("SELECT name FROM tags WHERE id=?", (tid,)) if tid else None
        if not row:
            from .. import tag_i18n
            q = self.tag_search.text().strip()
            if q:
                self.find_edit.setText(q)
                self.goto_first_match()
            return
        self.find_edit.setText(row["name"])
        self.goto_first_match()

    def change_category_selected(self) -> None:
        tid = self._selected_tag_id()
        if not tid:
            return
        from .dialogs import category_combo
        from PySide6.QtWidgets import QInputDialog
        combo = category_combo(self.store)
        cur = self.store.one("SELECT category FROM tags WHERE id=?", (tid,))
        idx = combo.findData(cur["category"] if cur else "other")
        if idx >= 0:
            combo.setCurrentIndex(idx)
        dlg = QDialog(self)
        dlg.setWindowTitle("改类型")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("选择新的类型（图谱里的分类连线会同步更新）"))
        lay.addWidget(combo)
        ok_cancel(dlg, lay)
        if dlg.exec() != QDialog.Accepted:
            return
        self.store.update_tag(tid, category=combo.currentData())
        self.library.sync_taxonomy()                 # 补分类节点
        self.library.relink_all_categories()         # 按新分类重建连线（别用反向同步）
        self.changed.emit()
        self.rebuild()
        self.reload_tag_table()
        self.detail.setText("已改类型并同步图谱连线")

    def delete_tag_selected(self) -> None:
        tid = self._selected_tag_id()
        if not tid:
            return
        row = self.store.one("SELECT name FROM tags WHERE id=?", (tid,))
        if QMessageBox.question(self, "删除标签",
                                f"删除标签「{row['name'] if row else tid}」？\n"
                                f"只从库里移除该标签及其连线，图片文件不受影响。") != QMessageBox.Yes:
            return
        self.store.delete_tag(int(tid))
        self.changed.emit()
        self.rebuild()
        self.reload_tag_table()
        self.detail.setText("已删除标签及其连线")

    # ================= 构建视图 =================
    def rebuild(self) -> None:
        self.store.prune_dangling_edges()          # 先清悬空连线，否则渲染会崩
        if self.store.one("SELECT COUNT(*) c FROM nodes")["c"] == 0:
            self.library.sync_taxonomy()
        # 图谱里还有没连线的标签时，自动补一次「按分类连线」，避免打开是散的
        if self.store.one("SELECT COUNT(*) c FROM tags")["c"] and \
                self.store.one("SELECT COUNT(*) c FROM taxonomy_edges")["c"] == 0:
            self.library.sync_taxonomy()
        self.store.refresh_counts()
        self.canvas.clear()
        nodes = {int(n["id"]): n for n in self.store.list_nodes()}
        linked_tags: dict[int, int] = {}
        for e in self.store.edges():
            if e["child_kind"] == "tag":
                linked_tags[int(e["child_id"])] = linked_tags.get(int(e["child_id"]), 0) + 1
        tag_rows = {int(t["id"]): t for t in self.store.list_tags()}

        saved = self.store.positions()
        # 性能：标签很多时，默认折叠"子节点很多"的分类（打开就是轻量的 13 个分类节点），
        # 想看点开那个分类即可；同时给可见标签数设上限，避免一次画上千个节点。
        tag_total = self.store.one("SELECT COUNT(*) c FROM tags")["c"]
        if tag_total > 200:
            # 先把"根节点"的折叠状态清掉（根节点被折叠会把所有分类藏起来）
            for nid in list(nodes.keys()):
                if not self.store.one("SELECT 1 FROM taxonomy_edges WHERE child_kind='node' AND child_id=?",
                                      (int(nid),)) and nodes[nid]["collapsed"]:
                    self.store.update_node(int(nid), collapsed=0)
                    self._expanded_now.add(int(nid))
            for nid, n in nodes.items():
                if n["collapsed"] or n["x"] is not None:
                    continue                     # 用户手动设过折叠/位置的不动
                kids = self.store.children_of_node(int(nid))
                if not kids:
                    continue
                is_root = not self.store.one(
                    "SELECT 1 FROM taxonomy_edges WHERE child_kind='node' AND child_id=?", (int(nid),))
                tag_ratio = sum(1 for c in kids if c["kind"] == "tag") / len(kids)
                # 只折叠"分类节点"（有父级）且子项以标签为主、数量多的；根节点永不折叠
                if (not is_root) and tag_ratio >= 0.8 and len(kids) >= 15:
                    self.store.update_node(int(nid), collapsed=1)
                    self._collapsed_now.add(int(nid))     # sqlite3.Row 只读，用集合记本次折叠
        VISIBLE_TAG_CAP = 600
        # 折叠：把「已折叠」节点的所有子孙藏起来；用 visited 防环（有人乱连成圈也不会死循环）
        hidden: set[tuple[str, int]] = set()
        stack, visited = [], set()
        for nid, n in nodes.items():
            if (n["collapsed"] or int(nid) in self._collapsed_now) and int(nid) not in self._expanded_now:
                stack.append(int(nid))
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            for ch in self.store.children_of_node(nid):
                key = (ch["kind"], int(ch["cid"]))
                hidden.add(key)
                if ch["kind"] == "node":
                    stack.append(int(ch["cid"]))
        # 位置：优先用保存过的坐标，否则先按“分类一列、标签一列”排布（之后可自动排列或手拖）
        y = 40
        for nid, n in nodes.items():
            if ("node", nid) in hidden:
                continue
            x, yy = saved.get(("node", nid), (40.0, y))
            y += NODE_H + 18
            self.canvas.add_node("node", nid, n["name"], 0, x, yy)
        y = 40
        y2 = 40
        shown_tags = 0
        for tid, t in tag_rows.items():
            force = (self._focus_tag is not None and tid == self._focus_tag)
            if not force and tid not in linked_tags and not self.show_all.isChecked():
                continue
            if ("tag", tid) in hidden and not force:
                continue
            if shown_tags >= VISIBLE_TAG_CAP and not force:
                continue
            default = (270.0, y) if tid in linked_tags else (560.0, y2)
            x, yy = saved.get(("tag", tid), default)
            if tid in linked_tags:
                y += NODE_H + 14
            else:
                y2 += NODE_H + 14
            shown_tags += 1
            from .. import tag_i18n
            self.canvas.add_node("tag", tid, tag_i18n.translate(t["name"], t["zh"] or ""),
                                 int(t["count"]), x, yy)
        for e in self.store.edges():
            if (e["child_kind"], int(e["child_id"])) not in self.canvas.items:
                continue
            self.canvas.add_edge(e["parent_kind"], int(e["parent_id"]), e["child_kind"], int(e["child_id"]),
                                 int(e["id"]), e["relation"])
        self.canvas.scene_.setSceneRect(self.canvas.scene_.itemsBoundingRect().adjusted(-80, -80, 120, 120))
        self.rebuild_tree()
        self.refresh_detail()

    def rebuild_tree(self) -> None:
        self.tree.clear()
        nodes = {int(n["id"]): n for n in self.store.list_nodes()}
        child_nodes: dict[int, list[int]] = {}
        for e in self.store.edges():
            if e["child_kind"] == "node":
                child_nodes.setdefault(int(e["parent_id"]), []).append(int(e["child_id"]))

        def add_node(node_id: int, parent_item: QTreeWidgetItem | None, depth: int = 0) -> None:
            n = nodes.get(node_id)
            if n is None:
                return
            it = QTreeWidgetItem([n["name"]])
            it.setData(0, Qt.UserRole, ("node", node_id))
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            if parent_item is None:
                self.tree.addTopLevelItem(it)
            else:
                parent_item.addChild(it)
            for ch in self.store.children_of_node(node_id):
                if ch["kind"] == "node":
                    continue
                from .. import tag_i18n as _i18n
                row = self.store.one("SELECT name,zh FROM tags WHERE id=?", (int(ch["cid"]),))
                label_txt = (_i18n.display(row["name"], row["zh"] or "") if row
                             else (ch["name"] or f"(已失效标签 #{ch['cid']})"))
                if not label_txt:
                    continue
                sub = QTreeWidgetItem([label_txt + (f"  ({ch['count']})" if ch["count"] else "")])
                sub.setData(0, Qt.UserRole, ("tag", int(ch["cid"])))
                it.addChild(sub)
            for cid in child_nodes.get(node_id, []):
                add_node(cid, it, depth + 1)

        for root in self.store.node_roots():
            add_node(int(root["id"]), None)
        self.tree.expandToDepth(1)

    # ================= 选择 / 详情 =================
    def on_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole)
        if data:
            self.current = (data[0], int(data[1]))
            self.refresh_detail()
            if (data[0], int(data[1])) in self.canvas.items:      # 点树 → 缓动运镜过去
                self.smooth_center(self.canvas.items[(data[0], int(data[1]))])

    def on_canvas_clicked(self, kind: str, nid: int) -> None:
        if self.b_connect.isChecked():
            return
        self.current = (kind, nid)
        self.refresh_detail()

    def refresh_detail(self) -> None:
        self.parents_list.clear()
        if not self.current:
            self.detail.setText("选中左侧树或中间的节点")
            return
        kind, nid = self.current
        if kind == "node":
            row = self.store.node(nid)
            if row is None:
                return
            children = self.store.children_of_node(nid)
            n_tags = sum(1 for c in children if c["kind"] == "tag")
            n_nodes = sum(1 for c in children if c["kind"] == "node")
            self.detail.setText(f"<b>分类节点</b>：{row['name']}<br>子分类 {n_nodes} 个 / 标签 {n_tags} 个")
            self.node_name.setText(row["name"])
            self.node_note.setText(row["note"] or "")
        else:
            t = self.store.one("SELECT * FROM tags WHERE id=?", (nid,))
            if t is None:
                return
            self.detail.setText(f"<b>标签</b>：{t['name']}<br>类型：{cats.label_of(self.store, t['category'])}"
                                f"<br>图片数：{t['count']}<br>提示词：{t['prompt'] or '（用标签名）'}")
            self.node_name.setText(t["name"])
            self.node_note.setText((t["zh"] or t["note"] or ""))     # 中文名/备注（二合一）
            for p in self.store.parents_of_tag(nid):
                it = QListWidgetItem(p["name"])
                it.setData(Qt.UserRole, int(p["id"]))
                self.parents_list.addItem(it)
        # 画布选中态
        for key, item in self.canvas.items.items():
            item.setSelected(key == (kind, nid))
        pan = getattr(self, "_pan_anim", None)
        from PySide6.QtCore import QAbstractAnimation
        animating = pan is not None and pan.state() == QAbstractAnimation.State.Running
        if not animating and (kind, nid) in self.canvas.items:
            self.canvas.centerOn(self.canvas.items[(kind, nid)].pos())   # 重绘时保持原位，不运镜

    def on_connect_requested(self, src, dst) -> None:
        """连线模式：父必须是分类节点（标签挂在分类下），子可以是分类或标签。"""
        if src is dst:
            return
        if src.kind != "node":
            QMessageBox.information(self, "连线", "父节点必须是分类节点；请从分类连到标签/子分类。")
            return
        self.store.link(src.nid, dst.kind, dst.nid)
        if dst.kind == "tag":      # 连线即改分类（图谱反过来可视化编辑分类）
            self.library.sync_tag_categories_from_graph(dst.nid)
        self.changed.emit()
        self.rebuild()
        self.detail.setText(f"已连线：{src.name} → {dst.name}"
                            + ("（该标签的分类已同步更新）" if dst.kind == "tag" else ""))

    def apply_detail(self) -> None:
        if not self.current:
            return
        kind, nid = self.current
        name = self.node_name.text().strip()
        note = self.node_note.text().strip()
        if kind == "node":
            self.store.update_node(nid, name=name, note=note)
        else:
            if name:
                res = self.library.rename_tag_global(nid, name)
                if res.get("ok") and (res.get("renamed") or res.get("old") != res.get("new")):
                    self.detail.setText(self.detail.text() + f"<br><span style='color:#7ddc7d'>已同步 {res.get('renamed', 0)} 个文件名</span>")
            # 备注与中文名合并成一个：手填的优先，写进文件名与所有中文显示
            self.store.update_tag(nid, zh=note, note=note)
            self.library.clear_label_cache()
        self.changed.emit()
        self.rebuild()

    # ================= 操作 =================
    def _selected_node_id(self) -> int | None:
        if self.current and self.current[0] == "node":
            return self.current[1]
        # 若选中的是标签，用它的第一个父分类
        if self.current and self.current[0] == "tag":
            parents = self.store.parents_of_tag(self.current[1])
            if parents:
                return int(parents[0]["id"])
        roots = self.store.node_roots()
        return int(roots[0]["id"]) if roots else None

    def new_group(self) -> None:
        name, ok = QInputDialogName(self, "新建分类", "分类名（例如：人物 / 服装 / 体位）")
        if not ok or not name.strip():
            return
        nid = self.store.ensure_node(name.strip())
        parent = self.store.node_roots()
        if parent and int(parent[0]["id"]) != nid:
            self.store.link(int(parent[0]["id"]), "node", nid)
        self.changed.emit()
        self.rebuild()

    def new_tag(self) -> None:
        parent = self._selected_node_id()
        dlg = NewTagForNodeDialog(self.store, self)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v["name"]:
            return
        tid = self.store.save_tag(v["name"], v["category"], v["prompt"])
        if parent:
            self.store.link(parent, "tag", tid)
        self.tagCreated.emit(v["name"])
        self.changed.emit()
        self.rebuild()

    def link_tags(self) -> None:
        parent = self._selected_node_id()
        if not parent:
            QMessageBox.information(self, "提示", "先选中一个分类节点。")
            return
        dlg = LinkTagsDialog(self.store, self)
        if dlg.exec() == QDialog.Accepted:
            for tid in dlg.selected_ids():
                self.store.link(parent, "tag", tid)
            self.changed.emit()
            self.rebuild()

    def unlink_selected(self) -> None:
        if not self.current:
            return
        kind, nid = self.current
        if kind == "tag":
            for p in self.store.parents_of_tag(nid):
                self.store.unlink(int(p["id"]), "tag", nid)
            self.library.sync_tag_categories_from_graph(nid)
            self.changed.emit()
            self.rebuild()
        else:
            QMessageBox.information(self, "提示", "取消关联请选中标签；分类之间的连线可右键删除。")

    def unlink_parent(self) -> None:
        it = self.parents_list.currentItem()
        if not it or not self.current or self.current[0] != "tag":
            return
        self.store.unlink(int(it.data(Qt.UserRole)), "tag", self.current[1])
        self.library.sync_tag_categories_from_graph(self.current[1])
        self.changed.emit()
        self.rebuild()

    def rename_selected(self) -> None:
        if not self.current:
            return
        kind, nid = self.current
        if kind == "node":
            row = self.store.node(nid)
            name, ok = QInputDialogName(self, "重命名分类", "新的分类名", row["name"] if row else "")
            if ok and name.strip():
                self.store.update_node(nid, name=name.strip())
                self.rebuild()
            return
        t = self.store.one("SELECT * FROM tags WHERE id=?", (nid,))
        if t is None:
            return
        dlg = RenameTagDialog(self, t["name"], int(t["count"]))
        if dlg.exec() != QDialog.Accepted:
            return
        res = self.library.rename_tag_global(nid, dlg.new_name(), dlg.update_files())
        if not res.get("ok"):
            QMessageBox.warning(self, "重命名失败", res.get("msg", ""))
            return
        QMessageBox.information(
            self, "已重命名",
            f"「{res['old']}」→「{res['new']}」\n影响图片 {res['files']} 张，"
            f"重写文件名 {res.get('renamed', 0)} 个。")
        self.changed.emit()
        self.rebuild()

    def delete_selected(self) -> None:
        if not self.current:
            return
        kind, nid = self.current
        if kind == "node":
            if QMessageBox.question(self, "删除分类", "删除该分类节点？（子项会接到它的父级上）") != QMessageBox.Yes:
                return
            self.store.delete_node(nid, reparent=True)
        else:
            if QMessageBox.question(self, "删除标签", "删除这个标签？图片本身不会被删除，但该标签会被移除。") != QMessageBox.Yes:
                return
            self.store.delete_tag(nid)
        self.current = None
        self.changed.emit()
        self.rebuild()

    def toggle_connect(self, on: bool) -> None:
        self.canvas.connect_mode = on
        self.canvas.connect_from = None
        self.b_connect.setText("连线模式：开（依次点两个节点）" if on else "连线模式：关")
        self.canvas.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag)
        if on:
            QMessageBox.information(self, "连线模式", "依次点击「父节点」和「子节点」即可建立连线；\n"
                                                      "标签也可以被连线（例如：某个标签属于某个体位分类）。")

    def do_layout(self) -> None:
        self.canvas.auto_layout()
        self.canvas.save_positions(self.store)
        self.canvas.scene_.setSceneRect(self.canvas.scene_.itemsBoundingRect().adjusted(-80, -80, 120, 120))

    def auto_link_by_category(self) -> None:
        """按标签的「类型」把标签连到同名分类节点上（幂等，可反复点）。"""
        n = self.library.sync_taxonomy()
        self.changed.emit()
        self.rebuild()
        self.do_layout()
        self.detail.setText(f"已按分类自动连线：补建分类节点 {n} 个；所有标签都已挂到对应分类下")

    def set_all_collapsed(self, flag: bool) -> None:
        for n in self.store.list_nodes():
            if self.store.children_of_node(int(n["id"])):      # 只折叠"有子节点"的
                self.store.update_node(int(n["id"]), collapsed=1 if flag else 0)
        self.rebuild()
        self.detail.setText("已折叠全部子节点（右键节点可单独展开/折叠）" if flag else "已展开全部")

    def toggle_collapse(self, node_id: int) -> None:
        n = self.store.node(node_id)
        if not n:
            return
        self.store.update_node(node_id, collapsed=0 if n["collapsed"] else 1)
        self.rebuild()
        self.detail.setText(("已折叠该节点下方的所有子节点" if not n["collapsed"] else "已展开该节点"))

    def goto_first_match(self) -> None:
        """搜索标签/分类 → 展开它的所有祖先、在图上选中并把视口跳过去。"""
        from .. import tag_i18n
        q = self.find_edit.text().strip().lower()
        if not q:
            return
        hit = None
        for t in self.store.list_tags():
            zh = (t["zh"] or tag_i18n.translate(t["name"])).lower()
            if q in t["name"].lower() or q in zh:
                hit = t
                break
        if hit is None:
            nodes = [n for n in self.store.list_nodes() if q in (n["name"] or "").lower()]
            if nodes:
                nid = int(nodes[0]["id"])
                self.expand_ancestors(nid)
                self.current = ("node", nid)
                self.rebuild()
                self.jump_to(("node", nid))
                self.detail.setText(f"已定位分类：{nodes[0]['name']}")
                return
            self.detail.setText(f"没找到匹配「{q}」的标签或分类")
            return
        tid = int(hit["id"])
        self._focus_tag = tid                     # 即使超出可见上限也把它画出来
        for p in self.store.parents_of_tag(tid):  # 沿 子→父 一路展开（含被折叠/自动折叠的）
            self.expand_ancestors(int(p["id"]))
        self.current = ("tag", tid)
        self.rebuild()
        self.jump_to(("tag", tid))
        self.detail.setText(f"已定位：{tag_i18n.display(hit['name'], hit['zh'] or '')}"
                            + self._path_text(tid))

    def expand_ancestors(self, node_id: int) -> None:
        """把这个节点本身和它的所有祖先都展开（带 visited 防环）。"""
        stack, visited = [int(node_id)], set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            self.store.update_node(nid, collapsed=0)
            self._collapsed_now.discard(nid)
            self._expanded_now.add(nid)
            for p in self.store.parents_of_node(nid):
                stack.append(int(p["id"]))

    def _path_text(self, tag_id: int) -> str:
        """给定位结果补一句「在哪个分类下」，方便确认位置。"""
        names = []
        for p in self.store.parents_of_tag(int(tag_id)):
            names.append(str(p["name"]))
        return f"　（位于：{' / '.join(names)}）" if names else ""

    def jump_to(self, key: tuple[str, int]) -> None:
        """把画布视口跳到某个节点上（重建后立刻调用，带缓动）。"""
        item = self.canvas.items.get(key)
        if item is None:
            return
        for k, it in self.canvas.items.items():
            it.setSelected(k == key)
        self.smooth_center(item)

    def smooth_center(self, item, duration: int = 520) -> None:
        """带缓动的运镜：起步即最高速、一路减速滑到目标节点（不是瞬间跳过去）。

        速度曲线用 OutQuart —— 没有加速段，一开始就是峰值速度，越接近目标越慢。
        """
        from PySide6.QtCore import QEasingCurve, QPointF, QTimer, QVariantAnimation
        canvas = self.canvas
        viewport = canvas.viewport()
        if viewport is None or viewport.width() <= 0 or not canvas.isVisible():
            canvas.centerOn(item)                     # 离屏/测试环境直接定位
            return
        end = item.mapToScene(item.boundingRect().center())

        def _animate() -> None:
            start = canvas.mapToScene(canvas.viewport().rect().center())
            if abs(start.x() - end.x()) < 1.5 and abs(start.y() - end.y()) < 1.5:
                canvas.centerOn(end)                  # 已经在目标位置，不用动
                return
            old = getattr(self, "_pan_anim", None)
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    pass
            anim = QVariantAnimation(canvas)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(duration)
            anim.setEasingCurve(QEasingCurve.OutQuart)

            def step(t: float) -> None:
                canvas.centerOn(QPointF(start.x() + (end.x() - start.x()) * t,
                                        start.y() + (end.y() - start.y()) * t))
            anim.valueChanged.connect(step)
            anim.finished.connect(lambda: canvas.ensureVisible(item, 120, 120))
            self._pan_anim = anim
            anim.start()

        QTimer.singleShot(0, _animate)                # 等布局/重建完成再开始运镜

    # ================= 右键菜单 =================
    def canvas_menu(self, pos) -> None:
        item = self.canvas.itemAt(pos)
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        menu = QMenu(self)
        a_new = menu.addAction("在此新建分类")
        a_fold = None
        if isinstance(item, NodeItem):
            if item.kind == "node":
                has_child = bool(self.store.children_of_node(item.nid))
                a_fold = menu.addAction("折叠/展开其下方所有子节点") if has_child else None
            a_tag = menu.addAction("在此新建标签")
            a_link = menu.addAction("关联已有标签…")
            a_ren = menu.addAction("重命名…")
            a_del = menu.addAction("删除")
        else:
            a_tag = a_link = a_ren = a_del = None
        act = menu.exec(self.canvas.mapToGlobal(pos))
        if act is None:
            return
        if isinstance(item, NodeItem):
            self.current = (item.kind, item.nid)
            self.refresh_detail()
        if act == a_new:
            nid = self.store.ensure_node(f"新分类{self.store.one('SELECT COUNT(*) c FROM nodes')['c'] + 1}")
            if isinstance(item, NodeItem):
                self.store.link(item.nid, "node", nid)
            self.rebuild()
        elif a_fold is not None and act == a_fold:
            self.toggle_collapse(item.nid)
        elif act == a_tag:
            self.new_tag()
        elif act == a_link:
            self.link_tags()
        elif act == a_ren:
            self.rename_selected()
        elif act == a_del:
            self.delete_selected()

    def tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item:
            self.on_tree_clicked(item, 0)
        menu = QMenu(self)
        a_new = menu.addAction("新建子分类")
        a_tag = menu.addAction("新建标签")
        a_link = menu.addAction("关联已有标签…")
        menu.addSeparator()
        a_ren = menu.addAction("重命名…")
        a_del = menu.addAction("删除")
        act = menu.exec(self.tree.mapToGlobal(pos))
        if act == a_new:
            name, ok = QInputDialogName(self, "新建子分类", "分类名")
            parent = self._selected_node_id()
            if ok and name.strip() and parent:
                nid = self.store.ensure_node(name.strip())
                self.store.link(parent, "node", nid)
                self.rebuild()
        elif act == a_tag:
            self.new_tag()
        elif act == a_link:
            self.link_tags()
        elif act == a_ren:
            self.rename_selected()
        elif act == a_del:
            self.delete_selected()

    def closeEvent(self, event) -> None:
        self.canvas.save_positions(self.store)
        event.accept()


def QInputDialogName(parent, title: str, label_text: str, default: str = ""):
    from PySide6.QtWidgets import QInputDialog
    return QInputDialog.getText(parent, title, label_text, text=default)


class RenameTagDialog(QDialog):
    """重命名标签：可选择是否同步改掉图片文件名。"""

    def __init__(self, parent=None, old: str = "", count: int = 0):
        super().__init__(parent)
        self.setWindowTitle("重命名标签")
        self.resize(460, 220)
        v = QVBoxLayout(self)
        self.edit = QLineEdit(old)
        form = QFormLayout()
        form.addRow("新名称", self.edit)
        v.addLayout(form)
        v.addWidget(label(f"该标签当前用在 {count} 张图片上。图片只存标签引用，改名后所有图片会自动跟着变。", "#8f96a3"))
        self.cb_files = QCheckBox("同时把新名字写进图片文件名/文件夹名")
        self.cb_files.setChecked(True)
        v.addWidget(self.cb_files)
        v.addStretch(1)
        ok_cancel(self, v)

    def new_name(self) -> str:
        return self.edit.text().strip()

    def update_files(self) -> bool:
        return self.cb_files.isChecked()
