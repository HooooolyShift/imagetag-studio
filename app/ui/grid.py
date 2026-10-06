"""缩略图网格：数据模型 + 自定义绘制（标签、页码角标）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import (QAbstractListModel, QMimeData, QModelIndex, QPoint, QRect, QSize, Qt,
                            QUrl, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QListView, QStyle, QStyledItemDelegate

from .common import ThumbPool, load_pixmap


@dataclass
class GridItem:
    kind: str           # image / series
    file_id: int
    path: str
    name: str
    rel: str = ""
    mtime: float = 0.0
    series_id: int | None = None
    page_count: int = 0
    tags: list[str] = field(default_factory=list)
    manual: bool = False
    cover_path: str = ""
    regions: int = 0
    pending: int = 0
    rating: str = ""
    page_no: int = 0

    @property
    def thumb_src(self) -> str:
        return self.cover_path or self.path


class GridModel(QAbstractListModel):
    ItemRole = Qt.UserRole + 1

    def __init__(self, thumbs: ThumbPool, size: int = 170, parent=None):
        super().__init__(parent)
        self.items: list[GridItem] = []
        self.thumbs = thumbs
        self.icon_size = size
        self.tile_tags = True
        self._pending: set[int] = set()
        self._icons: dict[int, QPixmap] = {}
        self.blur_r18 = False
        # 还没审核通过的 R18/R18G 也算进来（打标完没审的时候就先遮住，保护性功能不能等审核）
        self.blur_ids: set[int] = set()
        thumbs.signals.ready.connect(self._on_thumb)

    # ---- 基础接口 ----
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        it = self.items[index.row()]
        if role == self.ItemRole:
            return it
        if role == Qt.DisplayRole:
            text = it.name
            if it.kind == "series":
                text = f"{it.name}  ({it.page_count}页)"
            return text
        if role == Qt.ToolTipRole:
            lines = [it.name, f"{it.path}"]
            if it.tags:
                lines.append("标签：" + " ".join(it.tags[:60]))
            return "\n".join(lines)
        if role == Qt.DecorationRole:
            self.ensure_thumb(it)
            pm = self._icons.get(it.file_id)
            return pm
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    # ---- 缩略图 ----
    def ensure_thumb(self, it: GridItem) -> None:
        if it.file_id in self._icons or it.file_id in self._pending:
            return
        self._pending.add(it.file_id)
        self.thumbs.request(it.file_id, it.thumb_src, it.mtime)

    def _on_thumb(self, file_id: int, path: str) -> None:
        self._pending.discard(file_id)
        if not path:
            return
        pm = load_pixmap(path)
        if pm.isNull():
            return
        self._icons[file_id] = pm
        for row, it in enumerate(self.items):
            if it.file_id == file_id:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.DecorationRole])
                break

    def clear_cache(self) -> None:
        self._icons.clear()
        self._pending.clear()

    # ---- 数据装载 ----
    def set_items(self, items: list[GridItem]) -> None:
        self.beginResetModel()
        self.items = items
        self._pending.clear()
        self.endResetModel()

    def item_at(self, index: QModelIndex) -> GridItem | None:
        if index.isValid() and index.row() < len(self.items):
            return self.items[index.row()]
        return None

    def update_file_tags(self, file_id: int, tags: list[str], manual: bool, rating: str | None = None) -> None:
        for row, it in enumerate(self.items):
            if it.file_id == file_id:
                it.tags = tags
                it.manual = manual
                if rating is not None:
                    it.rating = rating
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx)

    def bump_series(self, series_id: int) -> None:
        for row, it in enumerate(self.items):
            if it.series_id == series_id:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx)


class GridDelegate(QStyledItemDelegate):
    """画缩略图 + 标题 + 标签行 + 系列角标。"""

    def __init__(self, model: GridModel, parent=None):
        super().__init__(parent)
        self.model = model

    def sizeHint(self, option, index) -> QSize:
        s = self.model.icon_size
        return QSize(s + 16, s + 52)

    def paint(self, painter: QPainter, option, index) -> None:
        it: GridItem = index.data(GridModel.ItemRole)
        if it is None:
            return
        painter.save()
        r = option.rect.adjusted(4, 2, -4, -2)
        s = self.model.icon_size
        img_rect = QRect(r.left(), r.top(), r.width(), s)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.setBrush(QColor("#3d6ea8"))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.addRoundedRect(r, 6, 6)
            painter.drawPath(path)
        else:
            painter.setBrush(QColor("#26282f"))
            painter.setPen(Qt.NoPen)
            path = QPainterPath()
            path.addRoundedRect(img_rect, 4, 4)
            painter.drawPath(path)
        pm = index.data(Qt.DecorationRole)
        blur = self.model.blur_r18 and (it.rating in ("r18", "r18g")
                                       or it.file_id in self.model.blur_ids)
        if isinstance(pm, QPixmap) and not pm.isNull() and not blur:
            scaled = pm.scaled(img_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = img_rect.left() + (img_rect.width() - scaled.width()) // 2
            y = img_rect.top() + (img_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif blur:
            painter.setBrush(QColor("#2a1c20"))
            painter.setPen(Qt.NoPen)
            painter.drawRect(img_rect)
            painter.setPen(QColor("#ff8a5c"))
            f = QFont(painter.font())
            f.setPointSizeF(11)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(img_rect, Qt.AlignCenter, "R18\n已打码")
        else:
            painter.setPen(QColor("#5a5f6b"))
            painter.drawText(img_rect, Qt.AlignCenter, "…")
        # 页码角标
        if it.kind == "series":
            badge = QRect(img_rect.right() - 46, img_rect.top() + 4, 42, 18)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#7fd0ff"))
            f = QFont(painter.font())
            f.setPointSizeF(8)
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, f"{it.page_count}页")
        if it.regions:
            badge = QRect(img_rect.left() + 4, img_rect.top() + 4, 40, 18)
            painter.setBrush(QColor(30, 90, 60, 200))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#8ff0b0"))
            f = QFont(painter.font())
            f.setPointSizeF(8)
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, f"框{it.regions}")
        if it.pending:
            badge = QRect(img_rect.right() - 46, img_rect.top() + 26, 42, 18)
            painter.setBrush(QColor(150, 110, 20, 210))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#ffd54a"))
            f = QFont(painter.font())
            f.setPointSizeF(8)
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, f"待审{it.pending}")
        if it.rating:
            from ..config import RATING_COLOR, RATING_TAG
            badge = QRect(img_rect.left() + 4, img_rect.bottom() - 22, 52, 18)
            painter.setBrush(QColor(0, 0, 0, 190))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor(RATING_COLOR.get(it.rating, "#cccccc")))
            f = QFont(painter.font())
            f.setPointSizeF(8)
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, RATING_TAG.get(it.rating, it.rating))
        if it.page_no:
            badge = QRect(img_rect.left() + 4, img_rect.bottom() - 44, 52, 18)
            painter.setBrush(QColor(20, 40, 70, 200))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#9fd0ff"))
            f = QFont(painter.font())
            f.setPointSizeF(8)
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, f"P{it.page_no:03d}")
        # 标题
        painter.setPen(QColor("#e6e8ee") if it.manual else QColor("#b9bcc5"))
        f = QFont(painter.font())
        f.setPointSizeF(8.5)
        painter.setFont(f)
        text_rect = QRect(r.left() + 2, img_rect.bottom() + 2, r.width() - 4, 16)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(it.name, Qt.ElideRight, text_rect.width()))
        if self.model.tile_tags and it.tags:
            painter.setPen(QColor("#8f96a3"))
            painter.drawText(QRect(r.left() + 2, img_rect.bottom() + 17, r.width() - 4, 30),
                             Qt.AlignLeft | Qt.TextWordWrap,
                             " ".join(it.tags[:6]) + (" …" if len(it.tags) > 6 else ""))
        painter.restore()


class GridView(QListView):
    """缩略图视图（只请求可见区域的缩略图）。"""

    itemActivated = Signal(object)
    reorderRequested = Signal(list, int)      # (拖动的行号列表, 目标插入位置)

    def __init__(self, model: GridModel, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.grid_model = model
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListView.ExtendedSelection)
        self.setSpacing(2)
        self.setWordWrap(True)
        self.setItemDelegate(GridDelegate(model, self))
        self.set_icon_size(model.icon_size)
        self.verticalScrollBar().valueChanged.connect(self._request_visible)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.reorder_enabled = False
        # 允许把选中的图片拖到别处（左侧文件夹树等）；拖的是真实文件路径
        self.setDragEnabled(True)
        self.setDragDropMode(QListView.DragOnly)
        self._press_pos: QPoint | None = None
        self._press_row: int | None = None
        self._drop_row: int | None = None
        self.setMouseTracking(True)

    def set_icon_size(self, size: int) -> None:
        self.grid_model.icon_size = size
        self.setIconSize(QSize(size, size))
        self.setGridSize(QSize(size + 16, size + 52))
        self.reset()

    def _request_visible(self) -> None:
        for idx in self.visible_indexes():
            it = self.grid_model.item_at(idx)
            if it:
                self.grid_model.ensure_thumb(it)

    def mimeData(self, indexes) -> QMimeData:
        """把选中的图片打包成拖动数据：既有真实文件路径（资源管理器式），
        也带一份行号（万一以后要拖出去做别的用途）。"""
        md = QMimeData()
        rows = sorted({i.row() for i in indexes if i.isValid()})
        md.setData(self.MIME, (",".join(str(r) for r in rows)).encode("ascii"))
        urls = []
        for r in rows:
            it = self.grid_model.item_at(self.model().index(r, 0))
            if it is not None and getattr(it, "path", ""):
                urls.append(QUrl.fromLocalFile(str(it.path)))
        if urls:
            md.setUrls(urls)
        return md

    def startDrag(self, actions) -> None:
        """默认拖拽动作是「移动」：拖到左侧文件夹松手就是移动文件。"""
        md = self.mimeData(self.selectedIndexes())
        if md is None or not md.hasUrls():
            return
        drag = QDrag(self)
        drag.setMimeData(md)
        pix = self.grid_model.item_at(self.currentIndex())
        drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)

    def visible_indexes(self) -> list[QModelIndex]:
        """当前可见区域对应的行索引（含上下各几行余量）。"""
        first, last = self.visible_rows()
        if last < first:
            return []
        return [self.model().index(r, 0) for r in range(first, last + 1)]

    def _range_for_rect(self, rect: QRect) -> tuple[int, int]:
        n = self.model().rowCount()
        if n == 0:
            return 0, -1
        return self.visible_rows(rect)

    def visible_rows(self, rect: QRect | None = None) -> tuple[int, int]:
        """按滚动位置 + 网格尺寸算可见行区间（唯一实现）。

        老实现用 indexAt(视口右下角)，图标网格下这个角经常落在空隙里 → 判定失败后
        回退成"前 8~48 行"，于是图库图片一多，往下翻就再也不请求缩略图（一片空白）。
        """
        n = self.model().rowCount()
        if n == 0:
            return 0, -1
        rect = rect or self.viewport().rect()
        grid = self.gridSize()
        gw = max(1, grid.width())
        gh = max(1, grid.height())
        cols = max(1, rect.width() // gw)
        bar = self.verticalScrollBar()
        first = max(0, (bar.value() // gh - 1) * cols)
        last = min(n - 1, ((bar.value() + max(1, rect.height())) // gh + 2) * cols)
        mid = self.indexAt(rect.center())          # 交叉校验（布局异常时兜底）
        if mid.isValid():
            row = int(mid.row())
            first = min(first, max(0, row - cols * 2))
            last = max(last, min(n - 1, row + cols * 2))
        return first, max(first, last)

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self.indexAt(event.pos())
        it = self.grid_model.item_at(idx)
        if it is not None:
            self.itemActivated.emit(it)
        super().mouseDoubleClickEvent(event)

    # ---------------- 拖动改顺序（只在系列内视图开启） ----------------
    MIME = "application/x-imtag-rows"

    def mousePressEvent(self, event) -> None:
        if self.reorder_enabled and event.button() == Qt.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            self._press_row = idx.row() if idx.isValid() else None
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (self.reorder_enabled and self._press_row is not None and self._press_pos is not None):
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        rows = sorted({i.row() for i in self.selectionModel().selectedIndexes()} or [self._press_row])
        mime = QMimeData()
        mime.setData(self.MIME, (",".join(str(r) for r in rows)).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        idx = self.model().index(rows[0], 0)
        rect = self.visualRect(idx)
        if rect.isValid():
            pm = self.grab(QRect(rect.topLeft() - self.viewport().pos(), rect.size()))
            drag.setPixmap(pm)
            drag.setHotSpot(event.position().toPoint() - rect.topLeft())
        self._press_row = None
        drag.exec(Qt.MoveAction)
        self._drop_row = None
        self.viewport().update()

    def _row_at(self, pos: QPoint) -> int:
        idx = self.indexAt(pos)
        rect = self.visualRect(idx) if idx.isValid() else QRect()
        if not idx.isValid():
            return max(0, self.model().rowCount() - 1)
        return idx.row() + (1 if pos.x() > rect.center().x() else 0)

    def dragEnterEvent(self, event) -> None:
        if self.reorder_enabled and event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self.reorder_enabled and event.mimeData().hasFormat(self.MIME):
            self._drop_row = self._row_at(event.position().toPoint())
            self.viewport().update()
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if self.reorder_enabled and event.mimeData().hasFormat(self.MIME):
            try:
                rows = [int(x) for x in bytes(event.mimeData().data(self.MIME)).decode("ascii").split(",") if x]
            except Exception:
                rows = []
            target = self._drop_row if self._drop_row is not None else self._row_at(event.position().toPoint())
            self._drop_row = None
            self.viewport().update()
            event.acceptProposedAction()
            if rows:
                self.reorderRequested.emit(rows, int(target))
        else:
            super().dropEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row is None or not self.reorder_enabled:
            return
        n = self.model().rowCount()
        if n == 0:
            return
        row = min(max(0, self._drop_row), n - 1)
        rect = self.visualRect(self.model().index(row, 0))
        p = QPainter(self.viewport())
        pen = QPen(QColor("#7fd0ff"), 3)
        p.setPen(pen)
        x = rect.left() if self._drop_row <= row else rect.right()
        p.drawLine(x, rect.top(), x, rect.bottom())
        p.end()
