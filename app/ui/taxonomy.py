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

# **色系按"最高级分类"分**：每个顶层分类一个色相（服装蓝、人物青、姿势绿…），
# 同一色系内部再按层级深浅渐变（第 1 层最深、越往下越亮），这样一眼能看出"属于哪个大类、在第几层"
FAMILY_COLORS = [
    "#6f8cff", "#22d3ee", "#4ade80", "#facc15", "#fb923c", "#e879f9", "#f472b6",
    "#a3e635", "#2dd4bf", "#f87171", "#818cf8", "#fbbf24", "#34d399", "#38bdf8",
]


def family_color(idx: int, level: int) -> tuple[QColor, QColor]:
    """按色系 + 层级给出（填充色, 边框色）。层级越深越亮，保证同一大类里也能分层。"""
    base = QColor(FAMILY_COLORS[int(idx) % len(FAMILY_COLORS)])
    depth = max(0, int(level) - 1)
    fill = base.darker(max(135, 330 - 55 * depth))
    border = base if depth == 0 else base.lighter(100 + 10 * depth)
    return fill, border

# 热度色阶（按图片数，对数刻度）：0 → 1 → 5 → 20 → 100 → 400 → 1000+
HEAT_STOPS = [(0, "#3a3f47"), (1, "#2f6fb0"), (5, "#3fa8c9"), (20, "#5fd07a"),
              (100, "#d8c05a"), (400, "#e08a4a"), (1000, "#d05050")]


def heat_color(count: int) -> QColor:
    """图片数 → 热度颜色（对数插值，0/小值深灰蓝，越多越红）。"""
    import math
    c = max(0, int(count or 0))
    pts = [(math.log10(max(1, k)) if k else 0.0, v) for k, v in HEAT_STOPS]
    x = math.log10(c) if c > 0 else 0.0
    if x <= pts[0][0]:
        return QColor(pts[0][1])
    for (x0, c0), (x1, c1) in zip(pts, pts[1:]):
        if x <= x1:
            t = (x - x0) / max(1e-6, x1 - x0)
            a, b = QColor(c0), QColor(c1)
            return QColor(int(a.red() + (b.red() - a.red()) * t),
                          int(a.green() + (b.green() - a.green()) * t),
                          int(a.blue() + (b.blue() - a.blue()) * t))
    return QColor(pts[-1][1])


class NodeItem(QGraphicsRectItem):
    def __init__(self, kind: str, nid: int, name: str, count: int = 0, on_move=None,
                 w: float = NODE_W, h: float = NODE_H, level: int = 2, family: int = 0,
                 cat_label: str = ""):
        super().__init__(0, 0, w, h)
        self.kind = kind            # 'node' | 'tag'
        self.nid = nid
        self.name = name
        self.count = count
        self.on_move = on_move
        self.w, self.h = float(w), float(h)
        self.level = max(1, int(level))
        self.family = int(family)
        self.cat_label = cat_label            # 圈内第一行显示的类型名（服装/人物…）
        self.setToolTip(f"{name}" + (f"（{count} 张）" if count else ""))
        self.edges: list[EdgeItem] = []
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(2)
        self.setAcceptHoverEvents(True)

    def center(self) -> QPointF:
        return self.scenePos() + QPointF(self.w / 2, self.h / 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for e in self.edges:
                e.update_path()
        if change == QGraphicsItem.ItemPositionChange and getattr(self, "spring_home", None):
            # 弹簧拖拽：拖动时给阻力，离原位越远越难拖，松手弹回去
            hx, hy, w = self.spring_home
            nx, ny = float(value.x()), float(value.y())
            dx, dy = nx - hx, ny - hy
            dist = (dx * dx + dy * dy) ** 0.5
            limit = float(getattr(self, "drag_limit", 240.0) or 240.0)
            if dist > limit:
                k = limit / dist
                nx, ny = hx + dx * k, hy + dy * k
            else:
                # 越往外越"沉"（阻尼），拖动手感像拉弹簧
                k = 1.0 - 0.35 * (dist / limit)
                nx, ny = hx + dx * k, hy + dy * k
            value = QPointF(nx, ny)
        if change == QGraphicsItem.ItemPositionChange and self.on_move:
            self.on_move(self)
        return super().itemChange(change, value)

    def boundingRect(self) -> QRectF:
        # 文字全部画在圆内，边界框就是圆本身（留一点余量给描边）
        d = min(self.w, self.h)
        return QRectF(-3, -3, d + 6, d + 6)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        d = min(self.w, self.h)
        r = QRectF(0, 0, d, d)
        fill, border = family_color(self.family, self.level)
        # 拉得很远时圆圈只有几个像素：去掉描边、直接点一个实心点。
        # 4000 多个节点每帧都画描边的话，缩放手感会明显发涩。
        tiny = painter.worldTransform().m11() < 0.22 and not self.isSelected()
        if tiny:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill.lighter(125)))
            if painter.worldTransform().m11() < 0.08:
                # 只有几个像素：关掉抗锯齿、直接填方块，省掉圆形的逐像素计算
                painter.setRenderHint(QPainter.Antialiasing, False)
                painter.drawRect(r)
            else:
                painter.drawEllipse(r)
            return
        if self.kind == "node":
            # 分类：大圆，同样按层级上色（第 1 层），描边更粗更亮
            painter.setBrush(QBrush(fill.darker(115)))
            painter.setPen(QPen(SEL_BORDER if self.isSelected() else border, 4.0))
        else:
            if getattr(self, "heat_mode", False):           # 热度视图：颜色 = 图片数
                hc = heat_color(self.count)
                painter.setBrush(QBrush(hc))
                pen = QPen(SEL_BORDER if self.isSelected() else hc.darker(150), 2.0)
                pen.setStyle(Qt.SolidLine)
                painter.setPen(pen)
            else:
                painter.setBrush(QBrush(fill))
                pen = QPen(SEL_BORDER if self.isSelected() else border, 2.0)
                if self.count == 0:
                    pen.setStyle(Qt.DotLine)                # 没图的标签虚线，一眼区分
                painter.setPen(pen)
        painter.drawEllipse(r)
        # 圈内三行：类型（上） / 名字（中） / 数量（下，仅热度视图）
        if getattr(self, "text_hidden", False):
            return                       # 缩得太小时只画圆，省掉几千段文字
        f = QFont(painter.font())
        kind_txt = "分类" if self.kind == "node" else getattr(self, "cat_label", "")
        if kind_txt:
            fk = QFont(f)
            fk.setPointSizeF(7.0)
            fk.setBold(False)
            painter.setFont(fk)
            painter.setPen(QColor("#aab6c8") if self.kind == "node" else QColor("#9fb0c6"))
            painter.drawText(QRectF(0, d * 0.06, d, d * 0.22), Qt.AlignHCenter | Qt.AlignTop,
                             painter.fontMetrics().elidedText(kind_txt, Qt.ElideRight, int(d - 12)))
        f.setPointSizeF(9.4 if self.kind == "node" else 8.2)
        f.setBold(self.kind == "node" or bool(self.count))
        painter.setFont(f)
        painter.setPen(QColor("#eef2f8") if (self.kind == "node" or self.count) else QColor("#b9bcc5"))
        fm = painter.fontMetrics()
        # 名字按圆的宽度折行（最多两行），放不下就省略号 —— 完整名字在悬停提示里
        per_line = max(2, int((d - 14) / max(6.0, fm.horizontalAdvance("汉"))))
        lines = [self.name[i:i + per_line] for i in range(0, len(self.name), per_line)][:2] or [""]
        if len(self.name) > per_line * 2:
            lines[-1] = lines[-1][:-1] + "…"
        heat = getattr(self, "heat_mode", False) and self.kind == "tag"
        top = d * (0.30 if not kind_txt else 0.30)
        height = d * (0.40 if heat else 0.52)
        painter.drawText(QRectF(0, top, d, height), Qt.AlignHCenter | Qt.AlignVCenter, "\n".join(lines))
        if heat:
            f2 = QFont(f)
            f2.setPointSizeF(7.4)
            f2.setBold(True)
            painter.setFont(f2)
            painter.setPen(QColor("#ffd479"))
            painter.drawText(QRectF(0, d * 0.72, d, d * 0.22), Qt.AlignHCenter | Qt.AlignTop,
                             f"{self.count} 张")


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src: NodeItem, dst: NodeItem, edge_id: int = 0, relation: str = "is_a"):
        super().__init__()
        self.src = src
        self.dst = dst
        self.edge_id = edge_id
        self.relation = relation
        self.setZValue(1)
        # 从属边（tag→tag）用青色，和"分类边"区分开
        # 平行关联（同角色的异格/换装）用紫色虚线，一眼区分
        if relation == "sub_of":
            self.edge_color = QColor("#3fc1c9")
            self.setPen(QPen(self.edge_color, 2.0))
        elif relation == "parallel":
            self.edge_color = QColor("#b57cff")
            pen = QPen(self.edge_color, 1.8)
            pen.setStyle(Qt.DashLine)
            self.setPen(pen)
        else:
            self.edge_color = EDGE_COLOR
            self.setPen(QPen(self.edge_color, 1.6))
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


class TagTree(QTreeWidget):
    """资源管理器式的标签树：可以把标签拖到别的分类里（改类型），也能拖分类换父级。"""

    tagDropped = Signal(int, int)        # (tag_id, 目标分类节点 id)
    nodeDropped = Signal(int, int)       # (被拖的分类节点 id, 新父节点 id；0 表示根）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    @staticmethod
    def _data(item) -> tuple | None:
        d = item.data(0, Qt.UserRole) if item is not None else None
        return tuple(d) if isinstance(d, (tuple, list)) else None

    def _target_node(self, pos) -> int | None:
        """拖到哪个分类节点上（拖到空白 = 根，返回 0）。"""
        item = self.itemAt(pos)
        while item is not None:
            d = self._data(item)
            if d and len(d) >= 2 and d[0] == "node":
                return int(d[1])
            item = item.parent()
        return 0

    def dropEvent(self, event) -> None:
        """接管拖放：不真的移动树项，而是发信号让上层改数据库，再整树重建。"""
        dragged = self.currentItem() or (self.selectedItems() or [None])[0]
        d = self._data(dragged)
        if not d or len(d) < 2:
            event.ignore()
            return
        target = self._target_node(event.position().toPoint())
        if d[0] == "tag":
            self.tagDropped.emit(int(d[1]), int(target or 0))
        elif d[0] == "node":
            if int(d[1]) != int(target or 0):
                self.nodeDropped.emit(int(d[1]), int(target or 0))
        event.accept()


class GraphCanvas(QGraphicsView):
    nodeClicked = Signal(str, int)
    edgeClicked = Signal(int)
    moved = Signal()
    linkRequested = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings_ref = None          # 由对话框注入，用于读取可调参数
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHint(QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontSavePainterState, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)      # 缩放时鼠标指向哪就缩哪
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._zoom = 1.0
        self._panning = False
        self._pan_start = None
        self.setBackgroundBrush(QBrush(QColor("#16171b")))
        self.items: dict[tuple[str, int], NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self.connect_mode = False
        self.connect_from: NodeItem | None = None
        self._pending_save = False
        self.wedges: list = []            # 每块分类扇区的范围（画边界用）
        self.show_bounds = True           # 是否画出分类之间的边界

    # ---------- 缩放 / 平移 ----------
    def wheelEvent(self, event) -> None:
        """滚轮缩放（0.1x ~ 5x），以鼠标位置为锚点。"""
        if event.angleDelta().y() == 0:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if new_zoom < 0.1 or new_zoom > 5.0:
            return
        self._zoom = new_zoom
        self.scale(factor, factor)
        self._schedule_text_visibility()
        event.accept()

    def fit_to_view(self) -> None:
        """适应窗口：把内容缩放到整屏可见。"""
        rect = self.scene_.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.resetTransform()
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        self._zoom = float(self.transform().m11())
        self._apply_text_visibility()

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 1.0
        self._apply_text_visibility()

    def _schedule_text_visibility(self) -> None:
        """缩放变化时节流刷新"圈内文字是否显示"。"""
        if not hasattr(self, "_text_timer"):
            from PySide6.QtCore import QTimer
            self._text_timer = QTimer(self)
            self._text_timer.setSingleShot(True)
            self._text_timer.setInterval(60)
            self._text_timer.timeout.connect(self._apply_text_visibility)
        self._text_timer.start()

    def _apply_text_visibility(self) -> None:
        """缩得太小时不画圈内文字：几千个节点的文字同屏是潜在的卡顿源。"""
        zoom = float(self.transform().m11())
        show = zoom >= 0.55                      # 阈值：超过 55% 才显示文字
        # 拉远时改用 NoCache：DeviceCoordinateCache 会为每个节点缓存一张位图，
        # 缩放过程中几千张缓存都要按新比例重画一遍，这才是"拉远就卡"的主因；
        # 关掉缓存后画的就是个圆圈，反而更快，也不会再拿着旧位图把文字显示出来。
        cache = (QGraphicsItem.DeviceCoordinateCache if zoom >= 0.8
                 else QGraphicsItem.NoCache)
        changed = (show != getattr(self, "_text_visible", None)
                   or cache != getattr(self, "_cache_mode", None))
        self._text_visible = show
        self._cache_mode = cache
        if not changed:
            return
        for it in self.items.values():
            it.text_hidden = not show
            if it.cacheMode() != cache:
                it.setCacheMode(cache)
        # 拉得很远时连线只是糊成一片灰，全部隐藏能省掉每帧几千条路径的绘制
        show_edges = zoom >= 0.25
        if show_edges != getattr(self, "_edges_visible", None):
            self._edges_visible = show_edges
            for ed in self.edges:
                ed.setVisible(show_edges)
        self.viewport().update()                 # 一次刷新，不用几千次 item.update()

    def set_heat_mode(self, on: bool) -> None:
        """切换热度视图：节点颜色改成"图片数"的热度色，并在左下角画图例。"""
        self.heat_mode = bool(on)
        for it in self.items.values():
            it.heat_mode = bool(on)
            it.update()
        self.viewport().update()

    def drawBackground(self, painter, rect) -> None:
        """画分类边界：每块分类扇区铺一层很淡的底色 + 一条描边。

        边界用"该分类真实占到的范围"算，所以是邻簇互相挤压之后的形状，
        不是标准扇形 —— 既看得出分组，又不会把空隙重新画出来。
        """
        super().drawBackground(painter, rect)
        if not getattr(self, "show_bounds", True) or not self.wedges:
            return
        import math
        from PySide6.QtGui import QPainterPath, QPen
        from PySide6.QtCore import QRectF
        heat = bool(getattr(self, "heat_mode", False))
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        for ang_mid, span, radius, family in self.wedges:
            if radius < 80.0 or span <= 1e-4:
                continue
            base = QColor(FAMILY_COLORS[int(family) % len(FAMILY_COLORS)])
            path = QPainterPath()
            path.moveTo(0.0, 0.0)
            box = QRectF(-radius, -radius, radius * 2.0, radius * 2.0)
            # Qt 的弧角是"逆时针为正、y 轴向上"，场景坐标 y 轴向下，所以取负角
            path.arcTo(box, -math.degrees(ang_mid + span / 2.0), math.degrees(span))
            path.closeSubpath()
            fill = QColor(base)
            fill.setAlpha(12 if not heat else 8)
            pen = QPen(QColor(base.red(), base.green(), base.blue(), 105), 2.4)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawPath(path)
        painter.restore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not getattr(self, "heat_mode", False):
            return
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import QRectF
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        w, h, x, y = 232, 96, 16, self.viewport().height() - 112
        p.setBrush(QBrush(QColor(20, 22, 28, 225)))
        p.setPen(QPen(QColor("#3a3e47"), 1))
        p.drawRoundedRect(QRectF(x, y, w, h), 8, 8)
        p.setPen(QColor("#c9d3e0"))
        f = QFont(p.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.drawText(int(x) + 12, int(y) + 22, "热度视图：按图片数")
        bar = QRectF(x + 12, y + 34, w - 24, 14)
        import math
        steps = 60
        for i in range(steps):                      # 渐变条
            cnt = int(round(10 ** (3.0 * i / steps))) if i else 0
            p.setPen(heat_color(cnt))
            p.drawLine(int(bar.left() + bar.width() * i / steps), int(bar.top()),
                       int(bar.left() + bar.width() * i / steps), int(bar.bottom()))
        p.setPen(QColor("#8f96a3"))
        for frac, label in ((0.0, "0"), (0.33, "10"), (0.66, "100"), (1.0, "1000+")):
            p.drawText(int(bar.left() + bar.width() * frac) - 8, int(y) + 66, label)
        p.drawText(int(x) + 12, int(y) + 84, "越红 = 图越多；灰 = 还没有图")
        p.end()

    # ---------- 构建 ----------
    def clear(self) -> None:
        self.scene_.clear()
        self.items.clear()
        self.edges.clear()
        self.connect_from = None

    def add_node(self, kind: str, nid: int, name: str, count: int, x: float, y: float,
                 w: float = NODE_W, h: float = NODE_H, level: int = 2,
                 family: int = 0, cat_label: str = "") -> NodeItem:
        item = NodeItem(kind, nid, name, count, on_move=self._on_move, w=w, h=h, level=level,
                        family=family, cat_label=cat_label)
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

    def radial_layout(self) -> None:
        """**多中心放射布局 v3**：每个分类占一块"扇区"，扇区拼在一起填满整个圆。

        思路：整体是一个大圆，按各分类的节点面积切成若干扇区（像切蛋糕），每个扇区里
        用"环带"一圈圈往外铺满。这样：

        - 分类之间是紧挨着的，不会像一堆圆饼那样留下大片空隙；
        - 形状不强制是圆，扇区之间自然互相挤压变形，谁也不会漂到很远；
        - 父节点（分类/作品/父标签）落在自己那团子节点的重心上，父子距离短；
        - 子节点从圆心方向一路铺到外沿，密度均匀，没有"核心球 + 外圈稀疏"的分层感。

        太小的分类（不足 50 个节点）会合并成一块"杂项扇区"，否则它们会被挤成一根细线。
        参数 `graph_ring_radius` 现在表示"疏密"：900 = 默认，越大节点之间越松。
        """
        import math

        keys = list(self.items.keys())
        if not keys:
            return
        # 1) 父子关系（平行关联不参与布局）
        parents: dict[tuple[str, int], list[tuple[str, int]]] = {}
        children: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for e in self.edges:
            if e.relation == "parallel":
                continue
            sk, dk = (e.src.kind, e.src.nid), (e.dst.kind, e.dst.nid)
            if sk not in self.items or dk not in self.items:
                continue
            parents.setdefault(dk, []).append(sk)
            children.setdefault(sk, []).append(dk)

        spacing = float(getattr(self.settings_ref, "graph_ring_radius", 900.0) or 900.0) / 900.0
        spacing = max(0.5, min(1.8, spacing))
        gap = 12.0 * spacing                    # 节点之间留的空隙

        # 2) 子树半径：叶子=自己的半径；父节点=把子节点的圆盘塞到自己周围之后需要的半径
        #    （等面积折算成一个大圆：rho = √(自己² + Σ子²)）
        rho: dict[tuple[str, int], float] = {}
        busy: set[tuple[str, int]] = set()

        def calc_rho(k) -> float:
            got = rho.get(k)
            if got is not None:
                return got
            it = self.items[k]
            base = max(it.w, it.h) / 2.0 + gap / 2.0
            if k in busy:                   # 有人把节点连成圈：按叶子处理，防止死循环
                return base
            busy.add(k)
            acc = base * base
            for c in children.get(k, []):
                cc = calc_rho(c)
                acc += cc * cc
            busy.discard(k)
            rho[k] = math.sqrt(acc)
            return rho[k]

        for k in keys:
            calc_rho(k)

        # 3) 顶层簇 = 没有父节点的分类节点（根节点"全部标签"不画，所以就是那些最高级分类）
        roots = [k for k in keys if not parents.get(k)]
        if not roots:
            roots = [k for k in keys if self.items[k].kind == "node"] or keys[:1]

        # 4) 分组：按面积给每个分类分一块扇区（像切蛋糕）；扇区太窄的分类（节点太少，
        #    宽度撑不起一个节点）合并成一块「其他小类」扇区，免得被挤成一根细长尖条
        member_of: dict[tuple[str, int], tuple[str, int]] = {}
        big: list[tuple[str, int]] = []
        small: list[tuple[str, int]] = []
        for r in roots:
            members = self._subtree_keys(r, children) + [r]
            for k in members:
                member_of.setdefault(k, r)
            (big if len(members) >= 170 else small).append(r)
        groups: list[tuple[tuple[str, int] | None, list[tuple[str, int]]]] = \
            [(r, [r]) for r in big]
        if small:
            groups.append((None, sorted(small, key=lambda k: -rho[k] ** 2)))
        area = [max(1.0, sum(rho[m] ** 2 for m in members)) for _rep, members in groups]
        total_area = sum(area) or 1.0

        # 5) 环带填满每块扇区：从圆心附近一路均匀铺到外沿，扇区之间紧挨着、互相挤压
        pos: dict[tuple[str, int], list[float]] = {}
        self.wedges = []
        angle = -math.pi / 2.0                                   # 从正上方开始，顺时针铺
        for i in sorted(range(len(groups)), key=lambda n: -area[n]):
            _rep, members = groups[i]
            span = 2.0 * math.pi * area[i] / total_area
            seen_before = set(pos)
            atoms: list[tuple[tuple[str, int], float]] = []
            for m in members:                                    # 同一分类的节点在扇区里连成一段
                kids = [c for c in children.get(m, []) if c in self.items]
                atoms.extend((c, rho[c]) for c in kids or [m])
            for k, x, y in self._fill_ring(0.0, 0.0, 0.0, atoms, gap,
                                           angle + span / 2.0, span):
                if k in pos:
                    continue
                pos[k] = [x, y]
                self._place_subtree(k, pos, children, rho, gap, span)
            for m in members:                                    # 分类本体落在自己那团节点的重心
                mine = [pos[k] for k, o in member_of.items() if o == m and k in pos]
                if mine:
                    pos.setdefault(m, [sum(p[0] for p in mine) / len(mine),
                                       sum(p[1] for p in mine) / len(mine)])
            # 记下这块扇区的外沿，用来画分类边界（形状是"被邻簇挤过"的真实轮廓）
            far = 0.0
            for k in pos:
                if k in seen_before:
                    continue
                p = pos[k]
                far = max(far, math.hypot(p[0], p[1]) + max(self.items[k].w, self.items[k].h) / 2.0)
            if far > 1.0:
                self.wedges.append((angle + span / 2.0, span,
                                    far + 26.0, int(self.items[members[0]].family)))
            angle += span
        for k in keys:                                           # 兜底：万一有连不上的散点
            pos.setdefault(k, [0.0, 0.0])

        # 6) 收尾：分类大圆和个别擦边的节点再分开一点（构造时基本不重叠，这里只是兜底）
        self._separate_overlaps(pos, iterations=40, gap=6.0)
        # 5) 落位（节点中心 → 左上角坐标）；增删过 tag/分类时带过渡动画
        targets = {k: (pos.get(k, [0.0, 0.0])[0] - it.w / 2, pos.get(k, [0.0, 0.0])[1] - it.h / 2)
                   for k, it in self.items.items()}
        self.apply_positions(targets, animate=True)
        self._pending_save = True
        self.scene_.setSceneRect(self.scene_.itemsBoundingRect().adjusted(-160, -160, 160, 160))

    def apply_positions(self, targets: dict, animate: bool = True) -> None:
        """把节点移到目标坐标；视口内、位移较大的那批走缓动动画，其余直接到位。"""
        for k, (tx, ty) in targets.items():          # 记下"原位"，供弹簧拖拽回弹用
            it = self.items.get(k)
            if it is not None:
                it.spring_home = (tx, ty, it.w)
                it.drag_limit = float(getattr(self.settings_ref, "graph_drag_limit", 240.0) or 240.0)
        moved = {k: (it.pos().x(), it.pos().y(), tx, ty)
                 for k, (tx, ty) in targets.items()
                 if (it := self.items.get(k)) is not None
                 and abs(it.pos().x() - tx) + abs(it.pos().y() - ty) > 3}
        if not moved or not animate:
            for k, (_x0, _y0, tx, ty) in moved.items():
                self.items[k].setPos(tx, ty)
            return
        view = self.mapToScene(self.viewport().rect()).boundingRect().adjusted(-200, -200, 200, 200)
        onscreen = [k for k in moved if self.items[k].sceneBoundingRect().intersects(view)]
        instant = [k for k in moved if k not in set(onscreen)]
        for k in instant:
            _x0, _y0, tx, ty = moved[k]
            self.items[k].setPos(tx, ty)
        if not onscreen or len(onscreen) > 900:      # 太多就直接到位，别卡
            for k in onscreen:
                _x0, _y0, tx, ty = moved[k]
                self.items[k].setPos(tx, ty)
            return
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        old = getattr(self, "_move_anim", None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(340)
        anim.setDuration(int(getattr(self.settings_ref, "graph_anim_ms", 340) or 340))
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def step(t: float) -> None:
            for k in onscreen:
                x0, y0, tx, ty = moved[k]
                self.items[k].setPos(x0 + (tx - x0) * t, y0 + (ty - y0) * t)
        anim.valueChanged.connect(step)
        self._move_anim = anim
        anim.start()

    def _place_subtree(self, root, pos: dict, children: dict, rho: dict, gap: float,
                       span: float) -> None:
        """root 已经落位了，把它的子孙一圈圈排在它周围。

        方向顺着"从圆心往外"的方向、宽度和本分类的扇区一致 —— 所以整棵树都待在自己那块
        扇区里，不会甩到隔壁分类的地盘上，父节点也始终待在自己孩子的中间。
        """
        import math
        stack = [(root, 0)]
        while stack:
            k, depth = stack.pop()
            if depth > 16:
                continue
            kids = [c for c in children.get(k, []) if c in self.items and c not in pos]
            if not kids:
                continue
            cx, cy = pos[k]
            out_ang = math.atan2(cy, cx) if (abs(cx) + abs(cy)) > 1e-9 else 0.0
            for c, px, py in self._fill_ring(cx, cy, rho[k] + gap,
                                             [(c, rho[c]) for c in kids], gap, out_ang, span):
                pos[c] = [px, py]
                stack.append((c, depth + 1))

    @staticmethod
    def _fill_ring(cx: float, cy: float, base_r: float, atoms: list, gap: float,
                   ang_mid: float, span: float) -> list:
        """把一批"子圆盘"铺满以 (cx,cy) 为中心、开口 ang_mid、张角 span 的一块扇区。

        一圈一圈往外铺：环内按各子圆盘需要的弧长分配角度（铺满整块扇区），环与环之间按
        "上一环外沿 + 本环最大圆盘"往外挪。密度处处均匀、互不重叠；扇区窄的时候会自动
        从外一点起铺，空出来的只是一条细缝。span = 2π 时就是绕着父节点一圈铺开。
        """
        import math
        items = sorted(atoms, key=lambda a: -a[1])
        out: list = []
        idx, ring = 0, 0
        outer = base_r
        span = max(min(span, 2.0 * math.pi), 1e-6)
        while idx < len(items) and ring < 900:
            ring_max = items[idx][1]
            need_min = 2.0 * ring_max + gap
            radius = max(outer + ring_max + gap, need_min / span)   # 内圈太窄时自动往外挪
            cap = span * radius
            group, used, j = [], 0.0, idx
            while j < len(items):
                need = 2.0 * items[j][1] + gap
                if group and used + need > cap:
                    break
                group.append((items[j][0], items[j][1], need))
                used += need
                j += 1
            ang = ang_mid - span / 2.0
            for key, _r, need in group:
                frac = need / max(used, 1e-6)
                mid = ang + span * frac / 2.0
                out.append((key, cx + radius * math.cos(mid), cy + radius * math.sin(mid)))
                ang += span * frac
            outer = radius + max(r for _k, r, _n in group)
            idx, ring = j, ring + 1
        return out

    def _subtree_keys(self, root, children: dict) -> list:
        """root 自己 + 它的所有子孙（带环保护，多父节点整体平移时用）。"""
        out, seen = [], {root}
        stack = list(children.get(root, []))
        while stack:
            x = stack.pop()
            if x in seen or x not in self.items:
                continue
            seen.add(x)
            out.append(x)
            stack.extend(children.get(x, []))
        return out

    def _separate_overlaps(self, pos: dict, iterations: int = 60, gap: float = 16.0) -> None:
        """保证圆之间不重叠：按网格找邻居，把挨得太近的节点互相推开（几轮就够）。

        用的是"每个节点自身的直径"，所以分类的大圆、标签的小圆都能各按各的尺寸排开。
        """
        items = [(k, pos[k]) for k in pos if k in self.items]
        if len(items) < 2:
            return
        radii = {k: max(it.w, it.h) / 2.0 + gap / 2 for k, it in self.items.items()}
        cell = max(radii.values()) * 2 + 8 if radii else 100.0
        for _ in range(iterations):
            moved_any = False
            grid: dict[tuple[int, int], list[tuple]] = {}
            for k, p in items:
                grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append((k, p))
            for (cx, cy), bucket in grid.items():
                near = []
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        near.extend(grid.get((cx + dx, cy + dy), ()))
                for i, (k1, p1) in enumerate(bucket):
                    r1 = radii.get(k1, 34.0)
                    for k2, p2 in near:
                        if k2 <= k1:
                            continue
                        r2 = radii.get(k2, 34.0)
                        dx, dy = p1[0] - p2[0], p1[1] - p2[1]
                        dist = (dx * dx + dy * dy) ** 0.5
                        need = r1 + r2
                        if dist >= need or dist <= 0.01:
                            if dist <= 0.01:              # 完全重合：随便推开一点
                                p1[0] += 1.0
                            continue
                        push = (need - dist) / 2.0
                        ux, uy = dx / dist, dy / dist
                        if push < 1.0:            # 卡在微小重叠时给点扰动，打破对称僵局
                            ux += 0.05 * ((hash(k1) % 7) - 3)
                            uy += 0.05 * ((hash(k2) % 7) - 3)
                            n = max(1e-6, (ux * ux + uy * uy) ** 0.5)
                            ux, uy = ux / n, uy / n
                        p1[0] += ux * push
                        p1[1] += uy * push
                        p2[0] -= ux * push
                        p2[1] -= uy * push
                        moved_any = True
            if not moved_any:                 # 已经互不重叠，提前收工
                return

    def _legacy_layout(self) -> None:
        """旧版直线层级排版（留着兜底）。"""
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
        # 中键、或空白处左键 = 拖动平移（Ctrl+左键仍是框选）
        if event.button() == Qt.MiddleButton or (
                event.button() == Qt.LeftButton and not (event.modifiers() & Qt.ControlModifier)
                and self.itemAt(event.pos()) is None):
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        # 松开鼠标：被拖动过的节点弹回布局位置（弹簧）
        dragged = [it for it in self.items.values() if getattr(it, "spring_home", None)]
        if dragged and event.button() == Qt.LeftButton:
            self._spring_back(dragged)
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, (NodeItem, EdgeItem)):
            item = item.parentItem()
        if isinstance(item, NodeItem):
            self.nodeClicked.emit(item.kind, item.nid)
        elif isinstance(item, EdgeItem):
            self.edgeClicked.emit(item.edge_id)
        if self._pending_save:
            self.moved.emit()

    def _spring_back(self, items) -> None:
        """松手后把拖动过的节点弹回布局位置。

        注意：QGraphicsItem **不是** QObject，拿它当 QPropertyAnimation 的目标会直接抛
        TypeError（以前就是这个原因，回弹从来没生效）。这里改用 QVariantAnimation 自己插值。
        """
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        moves = []
        for it in items:
            home = getattr(it, "spring_home", None)
            if not home:
                continue
            hx, hy, _w = home
            x0, y0 = it.pos().x(), it.pos().y()
            if abs(x0 - hx) + abs(y0 - hy) < 1.0:
                continue
            moves.append((it, x0, y0, hx, hy))
        if not moves:
            return
        old = getattr(self, "_spring_anim", None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(int(getattr(self.settings_ref, "graph_anim_ms", 340) or 340) + 90)
        anim.setEasingCurve(QEasingCurve.OutBack)          # 稍微过冲一点再收回来，像弹簧

        def step(t: float) -> None:
            for it, x0, y0, hx, hy in moves:
                it.setPos(x0 + (hx - x0) * t, y0 + (hy - y0) * t)

        anim.valueChanged.connect(step)
        self._spring_anim = anim
        anim.start()


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
        self._reset_auto_collapse_once()
        self.setWindowTitle("标签体系（分类图谱）—— 与图片分离，随便改层级都不影响图片")
        self.resize(1460, 900)
        self.current: tuple[str, int] | None = None
        self._collapsed_now: set[int] = set()
        self._expanded_now: set[int] = set()
        self._focus_tag: int | None = None      # 搜索命中的标签：即使超出可见上限也要画出来
        self._auto_collapse_done = False        # 自动折叠只做一次，之后听用户的
        self.connect_source: NodeItem | None = None
        v = QVBoxLayout(self)

        bar = QHBoxLayout()
        for text, slot in (("新建标签", self.new_tag),
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
        b_fit = QPushButton("适应窗口")
        b_fit.setToolTip("把整张图缩放到看得见（滚轮缩放、空白处拖拽平移、Ctrl+拖拽框选）")
        b_fit.clicked.connect(lambda: self.canvas.fit_to_view())
        bar.addWidget(b_fit)
        b_zoom1 = QPushButton("100%")
        b_zoom1.setToolTip("缩放回到 1:1")
        b_zoom1.clicked.connect(lambda: self.canvas.reset_zoom())
        bar.addWidget(b_zoom1)
        self.b_heat = QPushButton("热度视图")
        self.b_heat.setCheckable(True)
        self.b_heat.setToolTip("切换成热度视图：节点颜色 = 该标签下的图片数量（带图例）；\n再点一下回到按层级配色")
        self.b_heat.toggled.connect(self.on_heat_toggled)
        bar.addWidget(self.b_heat)
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
        self.show_bounds = QCheckBox("显示分类边界")
        self.show_bounds.setChecked(True)
        self.show_bounds.setToolTip("在每个分类外围画一层淡色轮廓：看得出分组边界，\n"
                                    "但边界是各分类挤压后的真实形状，不会留出空隙")
        self.show_bounds.toggled.connect(self.on_bounds_toggled)
        bar.addWidget(self.show_bounds)
        bar.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bar.addWidget(b_close)
        v.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.tree = TagTree()                       # 资源管理器式：拖标签改分类、拖分类换父级
        self.tree.tagDropped.connect(self.on_tag_dropped)
        self.tree.nodeDropped.connect(self.on_node_dropped)
        self.tree.setToolTip("把标签拖到别的分类里 = 改它的类型；把分类拖到别的分类上 = 换父级；\n"
                             "拖到空白处 = 移到根目录")
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
        self.canvas.settings_ref = self.library.settings
        self.canvas.nodeClicked.connect(self.on_canvas_clicked)
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
        # 打开就"看得全"：等布局完成后自动缩放到能看见全部节点
        from PySide6.QtCore import QTimer
        QTimer.singleShot(60, self.canvas.fit_to_view)

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
    def on_heat_toggled(self, on: bool) -> None:
        """切换热度视图（工具栏按钮）。"""
        if hasattr(self, "canvas"):
            self.canvas.set_heat_mode(bool(on))
            self.detail.setText("热度视图：节点颜色 = 该标签下的图片数量（左下角有图例）" if on
                                else "已切回按层级配色")

    def on_bounds_toggled(self, on: bool) -> None:
        """显示/隐藏分类边界（工具栏勾选框）。"""
        if hasattr(self, "canvas"):
            self.canvas.show_bounds = bool(on)
            self.canvas.viewport().update()

    def _reset_auto_collapse_once(self) -> None:
        """升级后一次性清掉"上一版自动折叠"留下的标记（用户之后手动折叠的仍会被记住）。

        老版本为了性能会把标签多的分类自动折叠；现在统计查询快了上百倍，
        默认全展开才能满足"每个 tag 都能在图里看到"，所以这里清一次。
        """
        try:
            if getattr(self.library.settings, "graph_expand_reset_done", False):
                return
            self.store.execute("UPDATE nodes SET collapsed=0")
            # 旧版把标签排成了一根几万像素高的长条，这些坐标也一并清掉，改用新的网格排布
            self.store.execute("DELETE FROM layout")
            self.library.settings.graph_expand_reset_done = True
            self.library.settings.save()
        except Exception:
            pass

    def rebuild(self) -> None:
        self.store.prune_dangling_edges()          # 先清悬空连线，否则渲染会崩
        if self.store.one("SELECT COUNT(*) c FROM nodes")["c"] == 0:
            self.library.sync_taxonomy()
        # 图谱里还有没连线的标签时，自动补一次「按分类连线」，避免打开是散的
        unlinked = self.store.one(
            "SELECT COUNT(*) c FROM tags t WHERE NOT EXISTS("
            "  SELECT 1 FROM taxonomy_edges e WHERE e.child_kind='tag' AND e.child_id=t.id)")["c"]
        if self.store.one("SELECT COUNT(*) c FROM tags")["c"] and (unlinked or
                self.store.one("SELECT COUNT(*) c FROM taxonomy_edges")["c"] == 0):
            self.library.sync_taxonomy()
        self.store.refresh_counts()
        self.canvas.clear()
        nodes = {int(n["id"]): n for n in self.store.list_nodes()}
        linked_tags: dict[int, int] = {}
        for e in self.store.edges():
            if e["child_kind"] == "tag":
                linked_tags[int(e["child_id"])] = linked_tags.get(int(e["child_id"]), 0) + 1
        tag_rows = {int(t["id"]): t for t in self.store.list_tags()}
        # 一次性把"标签→子标签"和"标签表"准备好：以前每画一个标签都要单独查一次库，
        # 4000+ 标签就是几千次 SQL，进去就卡住（本次卡死的根因）
        tag_kids: dict[int, list[int]] = {}
        for e in self.store.edges():
            if e["parent_kind"] == "tag" and e["child_kind"] == "tag" and e["relation"] == "sub_of":
                tag_kids.setdefault(int(e["parent_id"]), []).append(int(e["child_id"]))
        self._tag_kids, self._tag_rows = tag_kids, tag_rows

        saved = self.store.positions()
        # 性能：标签很多时，默认折叠"子节点很多"的分类（打开就是轻量的 13 个分类节点），
        # 想看点开那个分类即可；同时给可见标签数设上限，避免一次画上千个节点。
        tag_total = self.store.one("SELECT COUNT(*) c FROM tags")["c"]
        # 自动折叠（只在标签极多且用户没表过态时兜底）：现在统计 SQL 已优化，
        # 4000+ 标签全画也就几百毫秒，所以默认**全展开**，让"每个 tag 都能在图里看到"；
        # 想清爽一点用工具栏「折叠全部」，之后不会再被自动折叠盖回去。
        if tag_total > 20000 and not self._auto_collapse_done:
            self._auto_collapse_done = True
            # 先把"根节点"的折叠状态清掉（根节点被折叠会把所有分类藏起来）
            for nid in list(nodes.keys()):
                if not self.store.one("SELECT 1 FROM taxonomy_edges WHERE child_kind='node' AND child_id=?",
                                      (int(nid),)) and nodes[nid]["collapsed"]:
                    self.store.update_node(int(nid), collapsed=0)
                    self._expanded_now.add(int(nid))
            for nid, n in nodes.items():
                if n["collapsed"] or n["x"] is not None or int(nid) in self._expanded_now:
                    continue                     # 用户手动设过折叠/位置/展开过的不动
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
        VISIBLE_TAG_CAP = 10 ** 9        # 全部标签都画出来（统计 SQL 优化后几千个节点也就百来毫秒）
        # 画哪些标签：**先按图片数从多到少**（常用的、你手工加的都在前面），
        # 再保证每个分类至少露出若干个；超出的用搜索定位（搜索会把视口滑过去并强制画出）。
        by_count = sorted(tag_rows.values(), key=lambda t: (-int(t["count"] or 0), str(t["name"]).lower()))
        draw_order: list[int] = []
        seen_draw: set[int] = set()
        per_cat: dict[str, int] = {}
        for t in by_count:                                  # 每个分类先保底 12 个
            key = str(t["category"] or "other")
            if per_cat.get(key, 0) >= 12:
                continue
            per_cat[key] = per_cat.get(key, 0) + 1
            tid = int(t["id"])
            if tid not in seen_draw:
                seen_draw.add(tid)
                draw_order.append(tid)
        for t in by_count:                                  # 其余按图片数补齐到上限
            tid = int(t["id"])
            if tid in seen_draw:
                continue
            seen_draw.add(tid)
            draw_order.append(tid)
        hidden_tag_count = 0
        # 折叠：把「已折叠」节点的所有子孙藏起来；用 visited 防环（有人乱连成圈也不会死循环）
        # 默认策略：**有子级的分类默认折叠**（图谱一打开只剩最高层分类），你手动展开过的会被记住
        root_ids = {int(r["id"]) for r in self.store.node_roots()}
        hidden: set[tuple[str, int]] = set()
        stack, visited = [], set()
        for nid, n in nodes.items():
            nid = int(nid)
            if nid in root_ids:
                continue                      # 根节点不画也不参与折叠（它的子级就是要露出的顶层分类）
            if nid in self._expanded_now:
                continue
            has_kids = bool(self.store.children_of_node(nid))
            if n["collapsed"] or nid in self._collapsed_now or has_kids:
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
        # 层级（用于配色）：分类=1、直接挂分类下的标签=2、作品下的人物/父标签下的子标签=3…
        # 一个节点同时属于多层时取**更深**的那层（更具体的层级），这样层级颜色才拉得开
        level_of: dict[tuple[str, int], int] = {}
        tag_parent_names = self.store.tag_parent_map()
        for nid in nodes:
            if ("node", int(nid)) not in hidden and int(nid) not in root_ids:
                level_of[("node", int(nid))] = 1
        changed = True
        rounds = 0
        while changed and rounds < 12:     # 迭代传播：父层级 +1（取最深）
            changed = False
            rounds += 1
            for e in self.store.edges():
                pk = (str(e["parent_kind"]), int(e["parent_id"]))
                ck = (str(e["child_kind"]), int(e["child_id"]))
                if pk in level_of:
                    lv = level_of[pk] + 1
                    if ck not in level_of or lv > level_of[ck]:
                        level_of[ck] = lv
                        changed = True
#        for _k in list(level_of): ...
        # 色系：每个"最高级分类"一个色相（服装、人物、姿势…各一色），同一色系内按层级变深浅
        from .. import categories as _cats
        family_of = {str(c["key"]): i for i, c in enumerate(_cats.ordered(self.store))}

        # 节点尺寸：分类最大、作品次之、普通标签更小（用框大小区分"分类"和"节点"）
        def size_of(key) -> tuple[float, float]:
            if key[0] == "node":
                return (118.0, 118.0)          # 分类：大圆
            tid = int(key[1])
            if tag_kids.get(tid):
                return (92.0, 92.0)            # 作品/父标签：中圆
            return (68.0, 68.0)                # 普通标签：小圆

        def family_of_key(key) -> int:
            if key[0] == "node":
                n = nodes.get(int(key[1]))
                k = self.store.category_key_by_label(n["name"]) if n else None
                return family_of.get(str(k), 0)
            t = tag_rows.get(int(key[1]))
            return family_of.get(str(t["category"]) if t else "other", 0)

        # 位置：优先用保存过的坐标；没存过的先给个临时位，稍后统一跑放射布局
        y = 40
        # 根节点（"全部标签"）不画：它的子分类直接悬浮在最上层
        root_ids = {int(r["id"]) for r in self.store.node_roots()}
        for nid, n in nodes.items():
            if ("node", nid) in hidden or nid in root_ids:
                continue
            x, yy = saved.get(("node", nid), (40.0, y))
            y += NODE_H + 18
            w, h = size_of(("node", int(nid)))
            self.canvas.add_node("node", nid, n["name"], 0, x, yy, w=w, h=h,
                                 level=level_of.get(("node", int(nid)), 1),
                                 family=family_of_key(("node", int(nid))))
        # 标签位置稍后统一由「多中心放射布局」算，这里只登记节点
        shown_tags = 0
        for tid in draw_order:
            t = tag_rows.get(tid)
            if t is None:
                continue
            force = (self._focus_tag is not None and tid == self._focus_tag)
            if not force and tid not in linked_tags and not self.show_all.isChecked():
                continue
            if ("tag", tid) in hidden and not force:
                hidden_tag_count += 1
                continue
            if shown_tags >= VISIBLE_TAG_CAP and not force:
                hidden_tag_count += 1
                continue
            x, yy = saved.get(("tag", tid), (0.0, 0.0))
            shown_tags += 1
            from .. import tag_i18n
            w, h = size_of(("tag", tid))
            from .. import categories as _c2
            cat_lbl = _c2.label_of(self.store, str(t["category"])) if t["category"] else ""
            self.canvas.add_node("tag", tid, tag_i18n.label(t["name"], t["zh"] or ""),
                                 int(t["count"]), x, yy, w=w, h=h, level=level_of.get(("tag", tid), 2),
                                 family=family_of_key(("tag", tid)), cat_label=cat_lbl)
        for e in self.store.edges():
            if (e["child_kind"], int(e["child_id"])) not in self.canvas.items:
                continue
            if e["parent_kind"] == "node" and int(e["parent_id"]) in root_ids:
                continue                       # 从根节点出来的连线也不画
            self.canvas.add_edge(e["parent_kind"], int(e["parent_id"]), e["child_kind"], int(e["child_id"]),
                                 int(e["id"]), e["relation"])
        # 多中心放射布局：分类/作品各自是一个中心，孩子绕着它排；交织节点落在中心之间。
        # 用户拖过的坐标（layout 表里有记录的）算数，盖在自动布局上面。
        self.canvas.radial_layout()
        self.canvas.scene_.setSceneRect(self.canvas.scene_.itemsBoundingRect().adjusted(-80, -80, 120, 120))
        self.rebuild_tree()
        self.refresh_detail()
        if hidden_tag_count and not self.show_all.isChecked():
            self.detail.setText(
                f"<span style='color:#8f96a3'>图谱按图片数只画了前 {shown_tags} 个标签，"
                f"还有 {hidden_tag_count} 个长尾标签没画（用左上搜索框搜名字会自动跳过去；"
                f"或勾选「显示未分类标签」）。</span>")

    def rebuild_tree(self) -> None:
        self.tree.clear()
        nodes = {int(n["id"]): n for n in self.store.list_nodes()}
        tag_kids = getattr(self, "_tag_kids", {}) or {}
        tag_rows = getattr(self, "_tag_rows", {}) or {}
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
                row = tag_rows.get(int(ch["cid"]))
                label_txt = (_i18n.display(row["name"], row["zh"] or "") if row
                             else (ch["name"] or f"(已失效标签 #{ch['cid']})"))
                if not label_txt:
                    continue
                sub = QTreeWidgetItem([label_txt + (f"  ({ch['count']})" if ch["count"] else "")])
                sub.setData(0, Qt.UserRole, ("tag", int(ch["cid"])))
                it.addChild(sub)
                add_tag_children(int(ch["cid"]), sub, 0, {int(ch["cid"])})   # 人物挂到作品下面（tag→tag 从属）
            for cid in child_nodes.get(node_id, []):
                add_node(cid, it, depth + 1)

        def add_tag_children(tag_id: int, parent_item: QTreeWidgetItem, depth: int,
                             path: set[int]) -> None:
            """把 tag→tag 从属关系（作品 → 人物、裙子 → 白裙子）铺进树里。

            一个标签可能同时属于多个父节点（既在「人物/角色」分类下，又在某部作品下），
            这种情况**每个父节点下都重复显示一份**；只有"自己出现在自己的子孙里"才跳过（防环）。
            所有副本共用同一个标签 id，所以改名/改类型/删除都会一起同步。
            """
            from .. import tag_i18n
            if depth > 3:
                return
            for cid in tag_kids.get(int(tag_id), []):
                if cid in path:                      # 只防环，不阻止"多父重复显示"
                    continue
                row = tag_rows.get(cid)
                name = str(row["name"]) if row else f"(已失效标签 #{cid})"
                label_txt = tag_i18n.display(name, (row["zh"] if row else "") or "")
                sub = QTreeWidgetItem([label_txt + (f"  ({row['count']})" if row and row["count"] else "")])
                sub.setData(0, Qt.UserRole, ("tag", cid))
                # 作品标签：它下面挂着人物（tag→tag 从属），加粗显示便于区分
                if tag_kids.get(cid):
                    f = sub.font(0)
                    f.setBold(True)
                    sub.setFont(0, f)
                parent_item.addChild(sub)
                add_tag_children(cid, sub, depth + 1, path | {cid})

        for root in self.store.node_roots():
            # 根节点（"全部标签"）不显示：顶层分类直接排在树的最上面，少一层缩进
            for cid in child_nodes.get(int(root["id"]), []):
                add_node(cid, None)
        self.tree.setRootIsDecorated(True)      # 有子项的都带小三角，可折叠
        self.tree.setIndentation(16)
        self.tree.expandToDepth(1)              # 默认展开到"分类 → 标签"，子标签（变体）折叠着
        self.expand_to_current()

    def expand_to_current(self) -> None:
        """把当前选中项在树里的路径展开（搜索定位后直接看到它）。"""
        if not self.current:
            return
        kind, nid = self.current
        want = (kind, int(nid))

        def walk(item: QTreeWidgetItem) -> bool:
            if tuple(item.data(0, Qt.UserRole) or ()) == want:
                p = item.parent()
                while p is not None:
                    p.setExpanded(True)
                    p = p.parent()
                self.tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    return True
            return False

        for i in range(self.tree.topLevelItemCount()):
            if walk(self.tree.topLevelItem(i)):
                return
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
        self.new_group_under(self._selected_node_id())

    def new_group_under(self, parent_node_id: int | None) -> None:
        """新建分类（文件夹）。选中了某个分类 → 建在它下面；没选中 → 建在根下。"""
        name, ok = QInputDialogName(self, "新建分类", "分类名（例如：人物 / 服装 / 体位）")
        if not ok or not name.strip():
            return
        nid = self.store.ensure_node(name.strip())
        parent = int(parent_node_id) if parent_node_id else 0
        if not parent:                                   # 没选中 → 挂到根节点下
            roots = self.store.node_roots()
            parent = int(roots[0]["id"]) if roots else 0
        if parent and parent != nid:
            self.store.link(parent, "node", nid)
        self._expanded_now.add(parent)                    # 建完让父级展开，能看到新分类
        self.changed.emit()
        self.rebuild()
        self.detail.setText(f"已在{'根目录' if parent_node_id is None else '所选分类'}下新建分类「{name.strip()}」")

    # ---------------- 拖放迁移 ----------------
    def on_tag_dropped(self, tag_id: int, node_id: int) -> None:
        """把标签拖到某分类里 = 改它的类型（并重建图谱连线）。"""
        row = self.store.one("SELECT name, category FROM tags WHERE id=?", (tag_id,))
        if not row:
            return
        node = self.store.node(node_id) if node_id else None
        key = self.store.category_key_by_label(node["name"]) if node else None
        if not key:
            self.detail.setText(f"「{row['name']}」没动：目标不是标签类型分类（拖到服装/人物这类分类上才会改类型）")
            return
        self.store.update_tag(tag_id, category=key)
        self.library.relink_all_categories()              # 按新类型重建连线（别用反向同步）
        self.changed.emit()
        self.rebuild()
        from .. import categories as _cats
        self.detail.setText(f"「{row['name']}」已迁移到「{_cats.label_of(self.store, key)}」"
                            f"（原类型：{_cats.label_of(self.store, row['category'])}）")

    def on_node_dropped(self, node_id: int, new_parent: int) -> None:
        """把分类拖到别的分类上 = 换父级（拖到空白 = 移到根下）。"""
        if not new_parent:
            roots = self.store.node_roots()
            new_parent = int(roots[0]["id"]) if roots else 0
        if not new_parent or new_parent == node_id:
            return
        # 防环：不能把节点拖到它自己的子孙下面
        stack, seen = [node_id], set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur == new_parent:
                self.detail.setText("没动：不能把分类拖到它自己的子分类下面")
                return
            for ch in self.store.children_of_node(cur):
                if ch["kind"] == "node":
                    stack.append(int(ch["cid"]))
        self.store.execute("DELETE FROM taxonomy_edges WHERE child_kind='node' AND child_id=?", (node_id,))
        self.store.link(new_parent, "node", node_id)
        self._expanded_now.add(int(new_parent))
        self.changed.emit()
        self.rebuild()
        self.detail.setText("已移动分类")

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

    def auto_link_by_category(self) -> None:
        """按标签的「类型」把标签连到同名分类节点上（幂等，可反复点）。"""
        n = self.library.sync_taxonomy()
        self.changed.emit()
        self.rebuild()
        self.detail.setText(f"已按分类自动连线：补建分类节点 {n} 个；所有标签都已挂到对应分类下")

    def set_all_collapsed(self, flag: bool) -> None:
        for n in self.store.list_nodes():
            if self.store.children_of_node(int(n["id"])):      # 只折叠"有子节点"的
                self.store.update_node(int(n["id"]), collapsed=1 if flag else 0)
                nid = int(n["id"])
                if flag:
                    self._collapsed_now.add(nid)
                    self._expanded_now.discard(nid)
                else:
                    self._expanded_now.add(nid)      # 明确"用户要展开"，别再被自动折叠盖回去
                    self._collapsed_now.discard(nid)
        self.rebuild()
        self.detail.setText("已折叠全部子节点（右键节点可单独展开/折叠）" if flag else "已展开全部")

    def toggle_collapse(self, node_id: int) -> None:
        n = self.store.node(node_id)
        if not n:
            return
        collapsing = bool(n["collapsed"])
        self.store.update_node(node_id, collapsed=0 if collapsing else 1)
        if collapsing:
            self._expanded_now.add(int(node_id))
            self._collapsed_now.discard(int(node_id))
        else:
            self._collapsed_now.add(int(node_id))
            self._expanded_now.discard(int(node_id))
        self.rebuild()
        self.detail.setText(("已折叠该节点下方的所有子节点" if not n["collapsed"] else "已展开该节点"))

    def goto_first_match(self) -> None:
        """搜索标签/分类 → 展开它的所有祖先、在图上选中并把视口跳过去。"""
        from .. import tag_i18n
        q = self.find_edit.text().strip().lower()
        if not q:
            return
        hit = None
        tags = self.store.list_tags()
        # ① 完全同名 > ② 中文名完全一致 > ③ 子串 > ④ 拼音（否则搜 chain 会先命中 chainsaw_man）
        for t in tags:
            if t["name"].lower() == q:
                hit = t
                break
        if hit is None:
            for t in tags:
                if (t["zh"] or "").strip().lower() == q:
                    hit = t
                    break
        if hit is None:
            for t in tags:
                zh = (t["zh"] or tag_i18n.translate(t["name"])).lower()
                if q in t["name"].lower() or q in zh:
                    hit = t
                    break
        if hit is None:
            for t in tags:
                disp = tag_i18n.display(t["name"], t["zh"] or "")
                py = tag_i18n.pinyin(disp)
                if py and (q in py or tag_i18n.pinyin_initials(disp).startswith(q)):
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
        # 平行关联（同一角色的异格/换装）：先显示命中的这个，再把关联度低的也列出来
        parallels = self.store.tag_parallels(tid)
        extra = ""
        if parallels:
            items = [f"{tag_i18n.display(str(p['name']), p['zh'] or '')}"
                     f"（关联度 {float(p['weight'] or 1):.1f}）" for p in parallels]
            extra = "<br><span style='color:#b57cff'>平行关联：</span>" + "、".join(items)
        self.detail.setText(f"已定位：{tag_i18n.display(hit['name'], hit['zh'] or '')}"
                            + self._path_text(tid) + extra)

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
    def move_node_to(self, item) -> None:
        """右键「移动到…」：弹出可搜索的目标选择框，选中后把它挂到新父级下。

        标签多的时候拖拽定位很难（一屏几十上百个节点），这个入口用"搜名字选目标"代替拖拽。
        """
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout
        cur_name = item.name
        dlg = QDialog(self)
        dlg.setWindowTitle(f"把「{cur_name}」移动到…")
        dlg.resize(520, 620)
        v = QVBoxLayout(dlg)
        info = QLabel("选一个目标（分类或标签），它会挂到目标下面；可输入中文/英文/拼音过滤。")
        info.setWordWrap(True)
        info.setStyleSheet("color:#8f96a3;")
        v.addWidget(info)
        search = QLineEdit()
        search.setPlaceholderText("输入名字过滤，例如：明日方舟 / arknights / mingrizhou")
        v.addWidget(search)
        lst = QListWidget()
        v.addWidget(lst, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("移动到这里")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        from .common import suggest_tags

        def fill(text: str = "") -> None:
            lst.clear()
            # 1) 分类节点（作品/服装…都在这类里）
            for n in self.store.list_nodes():
                nm = n["name"]
                if text and text.lower() not in str(nm).lower():
                    continue
                it = QListWidgetItem(f"分类 · {nm}")
                it.setData(Qt.UserRole, ("node", int(n["id"])))
                lst.addItem(it)
            # 2) 标签（含 Danbooru 词典里、库里还没有的 —— 选中会顺手建出来再挂上去）
            from .. import tag_i18n
            for _s, name, zh in suggest_tags(text, self.store, None, 400):
                if (item.kind, int(item.nid)) == ("tag", int(self.store.tag_id(name) or -1)):
                    continue                       # 别把自己挂到自己下面
                it = QListWidgetItem("标签 · " + tag_i18n.display(name, zh))
                it.setData(Qt.UserRole, ("tag", name))
                lst.addItem(it)

        search.textChanged.connect(fill)
        fill("")
        if dlg.exec() != dlg.Accepted or lst.currentItem() is None:
            return
        kind, target = lst.currentItem().data(Qt.UserRole)
        try:
            if kind == "tag":
                tid = self.store.tag_id(str(target))
                if tid is None:
                    tid = self.store.ensure_tag(str(target))
                pid = int(tid)
            else:
                pid = int(target)
            if item.kind == "node":
                self.store.link(pid, "node", int(item.nid), relation="sub_of")
            else:
                self.store.link(pid, "tag", int(item.nid), relation="sub_of")
            self.store.refresh_counts()
            self.rebuild()
            self.detail.setText(f"已把「{cur_name}」移动到所选目标下")
        except Exception as exc:
            QMessageBox.warning(self, "移动失败", f"{type(exc).__name__}: {exc}")

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
            a_move = menu.addAction("移动到…（选目标位置）")
            a_ren = menu.addAction("重命名…")
            a_del = menu.addAction("删除")
        else:
            a_tag = a_link = a_move = a_ren = a_del = None
        act = menu.exec(self.canvas.mapToGlobal(pos))
        if act is None:
            return
        if act is not None and isinstance(item, NodeItem) and act == a_move:
            self.move_node_to(item)
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
        else:
            self.tree.clearSelection()            # 点空白 = 取消选中 → 新分类建在根下
            self.current = None
        menu = QMenu(self)
        a_new = menu.addAction("新建分类（在此分类下）" if item else "新建分类（根目录）")
        a_tag = menu.addAction("新建标签")
        a_link = menu.addAction("关联已有标签…")
        menu.addSeparator()
        a_ren = menu.addAction("重命名…")
        a_del = menu.addAction("删除")
        act = menu.exec(self.tree.mapToGlobal(pos))
        if act == a_new:
            self.new_group_under(self._selected_node_id() if item else None)
        elif act == a_tag:
            self.new_tag()
        elif act == a_link:
            self.link_tags()
        elif act == a_ren:
            self.rename_selected()
        elif act == a_del:
            self.delete_selected()

    def closeEvent(self, event) -> None:
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

