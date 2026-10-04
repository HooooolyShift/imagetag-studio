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
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGraphicsItem,
    QGraphicsPathItem, QGraphicsScene, QGraphicsRectItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..config import CATEGORY_ORDER, TAG_CATEGORIES
from .. import categories as cats
from .common import label
from .dialogs import TagEditDialog, category_combo

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
        self.setPen(QPen(EDGE_COLOR, 1.6))
        src.edges.append(self)
        dst.edges.append(self)
        self.update_path()

    def update_path(self) -> None:
        a = self.src.center()
        b = self.dst.center()
        path = QPainterPath(a)
        mid_x = (a.x() + b.x()) / 2
        path.cubicTo(QPointF(mid_x, a.y()), QPointF(mid_x, b.y()), b)
        self.setPath(path)


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
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
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
        self.cat = category_combo(store)
        form.addRow("类型", self.cat)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("留空即可；填 hatsune_miku 这类英文可让 WD14 直接命中")
        form.addRow("提示词", self.prompt)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

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
        split.addWidget(self.tree)

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
        self.node_note.setPlaceholderText("备注")
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

    # ================= 构建视图 =================
    def rebuild(self) -> None:
        if self.store.one("SELECT COUNT(*) c FROM nodes")["c"] == 0:
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
        # 位置：优先用保存过的坐标，否则先按“分类一列、标签一列”排布（之后可自动排列或手拖）
        y = 40
        for nid, n in nodes.items():
            x, yy = saved.get(("node", nid), (40.0, y))
            y += NODE_H + 18
            self.canvas.add_node("node", nid, n["name"], 0, x, yy)
        y = 40
        y2 = 40
        for tid, t in tag_rows.items():
            if tid not in linked_tags and not self.show_all.isChecked():
                continue
            default = (270.0, y) if tid in linked_tags else (560.0, y2)
            x, yy = saved.get(("tag", tid), default)
            if tid in linked_tags:
                y += NODE_H + 14
            else:
                y2 += NODE_H + 14
            self.canvas.add_node("tag", tid, t["name"], int(t["count"]), x, yy)
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
                sub = QTreeWidgetItem([f"{ch['name']}" + (f"  ({ch['count']})" if ch["count"] else "")])
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
            self.node_note.setText(t["note"] or "")
            for p in self.store.parents_of_tag(nid):
                it = QListWidgetItem(p["name"])
                it.setData(Qt.UserRole, int(p["id"]))
                self.parents_list.addItem(it)
        # 画布选中态
        for key, item in self.canvas.items.items():
            item.setSelected(key == (kind, nid))
        if (kind, nid) in self.canvas.items:
            self.canvas.centerOn(self.canvas.items[(kind, nid)].pos())

    def on_connect_requested(self, src, dst) -> None:
        """连线模式：父必须是分类节点（标签挂在分类下），子可以是分类或标签。"""
        if src is dst:
            return
        if src.kind != "node":
            QMessageBox.information(self, "连线", "父节点必须是分类节点；请从分类连到标签/子分类。")
            return
        self.store.link(src.nid, dst.kind, dst.nid)
        self.changed.emit()
        self.rebuild()
        self.detail.setText(f"已连线：{src.name} → {dst.name}")

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
            self.store.update_tag(nid, note=note)
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
        tid = self.store.ensure_tag(v["name"], v["category"], v["prompt"] or None)
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
            self.changed.emit()
            self.rebuild()
        else:
            QMessageBox.information(self, "提示", "取消关联请选中标签；分类之间的连线可右键删除。")

    def unlink_parent(self) -> None:
        it = self.parents_list.currentItem()
        if not it or not self.current or self.current[0] != "tag":
            return
        self.store.unlink(int(it.data(Qt.UserRole)), "tag", self.current[1])
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

    # ================= 右键菜单 =================
    def canvas_menu(self, pos) -> None:
        item = self.canvas.itemAt(pos)
        while item is not None and not isinstance(item, NodeItem):
            item = item.parentItem()
        menu = QMenu(self)
        a_new = menu.addAction("在此新建分类")
        if isinstance(item, NodeItem):
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
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def new_name(self) -> str:
        return self.edit.text().strip()

    def update_files(self) -> bool:
        return self.cb_files.isChecked()
