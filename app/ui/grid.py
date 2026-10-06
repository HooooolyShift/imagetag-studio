"""缩略图网格：数据模型 + 自定义绘制（标签、页码角标）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import (QAbstractListModel, QMimeData, QModelIndex, QPoint, QRect, QSize, Qt,
                            QUrl, Signal)
from PySide6.QtGui import (QColor, QDrag, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap)
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
        self._blur_cache: dict[str, QPixmap] = {}      # 真·高斯模糊结果缓存（按缩略图路径）
        self.r18_names: set[str] = set()               # 库里被标记为 R18 的标签名（图谱页可改）
        self.r18_forced_off: set[str] = set()          # 手动取消 R18 的标签名（优先级高于自动词匹配）
        # 缩略图生成完的通知必须在这里接上。之前这行被挤到 blurred() 的 return 之后成了死代码，
        # 模型永远收不到通知 → 所有缩略图只显示"…"（就是这次缩略图集体失效的根因）
        thumbs.signals.ready.connect(self._on_thumb)

    def blurred(self, key: str, pm: QPixmap) -> QPixmap:
        """对缩略图做一次真正的高斯卷积（PIL），结果缓存起来，别每帧重算。

        以前是"缩小再放大"的假模糊，放大后有明显低分辨率插值的颗粒感；
        这里用 PIL 的 GaussianBlur 做卷积，再转回 QPixmap。
        """
        got = self._blur_cache.get(key)
        if got is not None:
            return got
        try:
            from PIL import Image, ImageFilter
            img = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
            ptr = img.bits()
            buf = bytes(ptr)
            pil = Image.frombytes("RGBA", (img.width(), img.height()), buf).convert("RGB")
            radius = max(6.0, min(img.width(), img.height()) / 14.0)
            pil = pil.filter(ImageFilter.GaussianBlur(radius))
            # Qt→PIL→Qt：用 QImage 承载字节
            data = pil.convert("RGBA").tobytes("raw", "RGBA")
            qimg = QImage(data, pil.width, pil.height, QImage.Format_RGBA8888).copy()
            got = QPixmap.fromImage(qimg)
        except Exception:
            got = pm
        if len(self._blur_cache) > 400:                # 别无限涨
            self._blur_cache.clear()
        self._blur_cache[key] = got
        return got

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
        # 少了 ItemIsDragEnabled，Qt 根本不会发起拖拽 —— 这就是"只能框选、拖不动"的原因
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled

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


R18_KEYWORDS_ZH = (
    "性", "裸", "乳", "阴", "阳具", "肛", "口交", "自慰", "爱液", "精液", "插入", "绳缚",
    "拘束", "调教", "触手", "内射", "潮吹", "屁股", "臀", "穴", "勃起", "淫", "情色",
    "开脚", "后入", "骑乘", "玩具", "乳首", "性器", "兽交", "群交", "口内", "尿道",
)
R18_KEYWORDS_EN = (
    "sex", "nude", "naked", "nipple", "areola", "penis", "vagina", "pussy", "anal", "oral",
    "cum", "semen", "dildo", "vibrator", "onahole", "bondage", "shibari", "lewd", "hentai",
    "masturbat", "orgasm", "insertion", "tentacle", "bdsm", "topless", "bottomless",
    "futanari", "ahegao", "clitoris", "urethra", "squirting", "cervix", "condom", "sex_toy",
    "r18", "18禁", "panties", "crotch", "butt", "breast", "lingerie",
)


def is_r18_tag(name: str, zh: str = "") -> bool:
    """R18 相关标签（性行为 / 性玩具 / 裸露…）→ 用来把气泡染成粉色。"""
    if is_rating_tag(name):
        return False                     # 分级标签单独排在最前面，不算"R18 内容标签"
    z = str(zh or "")
    if any(k in z for k in R18_KEYWORDS_ZH):     # 中文按包含匹配（中文没有词边界）
        return True
    # 英文按**词**匹配：analysis / breastfeeding 这种就不会被 anal / breast 误伤
    import re as _re
    toks = [t for t in _re.split(r"[^a-z0-9]+", str(name or "").lower()) if t]
    keys = set(R18_KEYWORDS_EN)
    return any(t in keys or t.rstrip("s") in keys for t in toks)


def is_rating_tag(name: str) -> bool:
    """是不是分级标签（全年龄 / R15 / R18 / R18G 及其别名）。"""
    n = str(name or "").strip().lower().replace("-", "")
    return n in {"r15", "r18", "r18g", "all_ages", "allages", "全年龄", "g级", "健全"} \
        or n.startswith(("r15", "r18", "全年龄"))




class GridDelegate(QStyledItemDelegate):
    """画缩略图 + 标题 + 标签行 + 系列角标。"""

    def __init__(self, model: GridModel, parent=None):
        super().__init__(parent)
        self.model = model

    def sizeHint(self, option, index) -> QSize:
        s = self.model.icon_size
        return QSize(s + 16, s + 108)      # 和 setGridSize 保持一致：标签区约 4 行

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
        # 三种判定，任一命中就遮：数据库里的分级字段 / 分级标签名单 / 文件名里带 R18 字样
        # （最后一条是兜底：很多图还没有分级标签，但文件名里已经写着 R18，用户期望它就该被遮住）
        _name = str(getattr(it, "name", "") or "").lower()
        _hit_name = ("r18" in _name) or ("18禁" in _name)
        blur = bool(self.model.blur_r18) and (
            it.rating in ("r18", "r18g")
            or it.file_id in self.model.blur_ids
            or _hit_name
        )
        if isinstance(pm, QPixmap) and not pm.isNull() and not blur:
            scaled = pm.scaled(img_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = img_rect.left() + (img_rect.width() - scaled.width()) // 2
            y = img_rect.top() + (img_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif blur:
            # 高斯模糊：把缩略图缩到很小再用平滑插值放大回来（等效强模糊，比真卷积快得多）
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(img_rect, 4, 4)
            painter.setClipPath(clip)
            if isinstance(pm, QPixmap) and not pm.isNull():
                # 真·高斯卷积（结果按缩略图缓存），不是"缩小再放大"那种假模糊
                try:
                    key = str(it.thumb_src())
                except Exception:
                    key = str(id(it))
                # 注意：要按比例缩放并居中，直接把整个 pixmap 铺进方形格子会把非 1:1 的图**拉伸变形**
                _bp = self.model.blurred(key, pm)
                _scaled = _bp.scaled(img_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                _x = img_rect.left() + (img_rect.width() - _scaled.width()) // 2
                _y = img_rect.top() + (img_rect.height() - _scaled.height()) // 2
                painter.drawPixmap(_x, _y, _scaled)
            else:
                painter.setBrush(QColor("#241a1e"))
                painter.setPen(Qt.NoPen)
                painter.drawRect(img_rect)
            painter.restore()
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
        # 分级角标：**所有**带分级的图都标（以前只画在"打码"那一支里，
        # 所以只有被打码的图才看得到角标——用户看着就像"分级丢了"）
        _rl = {"r18": "R18", "r18g": "R18G", "r15": "R15", "all_ages": "全年龄"}.get(
            str(getattr(it, "rating", "") or "").lower(), "")
        if not _rl and it.file_id in self.model.blur_ids:
            _rl = "R18"
        if _rl and it.kind != "series":
            badge = QRect(img_rect.left() + 4, img_rect.bottom() - 22, 44, 18)
            # 分级角标配色：全年龄=绿、R15=黄、R18=粉、R18G=红
            _badge_color = {
                "全年龄": QColor(46, 140, 74, 175),
                "R15": QColor(214, 170, 32, 180),
                "R18": QColor(226, 88, 158, 178),
                "R18G": QColor(200, 36, 36, 180),
            }.get(_rl, QColor(120, 120, 130, 170))
            painter.setBrush(_badge_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge, 4, 4)
            # 黄色底配深色字更清楚，其余用白字
            painter.setPen(QColor("#2b2200") if _rl == "R15" else QColor("#ffffff"))
            f = QFont(painter.font())
            f.setPointSizeF(8.0)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(badge, Qt.AlignCenter, _rl)
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
        # 旧的分级角标（黑底 + 彩字，半透明盖在新角标上面）已删除，统一用上面那套四色角标
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
            # 标签用「中文名气泡」逐个画：能排下就同一行，排不下换行，**绝不把标签本身断开**。
            # 每个格子有自己的上下滚动量（鼠标悬停在标签区滚轮即可滚它们，见 GridView.wheelEvent）。
            from .. import tag_i18n
            fm = painter.fontMetrics()
            box = QRect(r.left() + 2, img_rect.bottom() + 17, r.width() - 4,
                        max(18, r.height() - (img_rect.height() + 19)))
            # 标签区自己一层遮罩：滚上去的部分被裁掉，不会盖住上面的文件名，
            # 也不会因为"画到框外又被裁"而突然出现/消失
            painter.save()
            painter.setClipRect(box)
            line_h = fm.height() + 4
            max_lines = max(1, box.height() // line_h)
            it.tag_scroll = max(0, int(getattr(it, "tag_scroll", 0)))
            x, y = box.left(), box.top() - int(getattr(it, "tag_scroll", 0))
            # 显示顺序：分级最前 → 非 R18 的健全标签 → 其余 R18 标签垫底
            ordered = sorted(
                it.tags,
                key=lambda t: (0 if is_rating_tag(t)
                               else (2 if ((t in getattr(self.model, "r18_names", ()))
                                           or is_r18_tag(t, tag_i18n.label(t, ""))) else 1)))
            for name in ordered:
                label = tag_i18n.label(name, "")
                w = fm.horizontalAdvance(label) + 12
                if x + w > box.right() and x > box.left():
                    x = box.left()
                    y += line_h                      # 排不下就整块换行
                if y + line_h < box.top() - line_h:
                    x, y = box.left(), y + line_h     # 滚过头了也要继续往下算（用于裁剪）
                    continue
                # 超出可见区的照样继续排版（只是不画），这样能算出整段标签的总高度
                chip = QRect(x, y, w, line_h - 3)
                if chip.bottom() >= box.top() and chip.top() <= box.bottom():
                    painter.setPen(Qt.NoPen)
                    # 优先用"库里标记过的 R18 标签"（图谱页可勾选修改），没标的再用关键词兜底
                    # 手动标记优先：勾过 = 一定粉；取消过 = 一定不粉（不再被关键词翻回来）；没动过才用词匹配
                    _on = name in getattr(self.model, "r18_names", ())
                    _off = name in getattr(self.model, "r18_forced_off", ())
                    _r18 = True if _on else (False if _off else is_r18_tag(name, label))
                    painter.setBrush(QColor(226, 88, 158, 195) if _r18 else QColor(58, 64, 78, 200))
                    painter.drawRoundedRect(chip, 6, 6)
                    painter.setPen(QColor("#ffffff") if _r18 else QColor("#c9d3e0"))
                    painter.drawText(chip.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
                x += w + 4
            # 记下整段标签的完整高度：滚轮据此夹住上下限，不会滚到"全看不见"
            it.tag_content_h = int(y - box.top() + int(getattr(it, "tag_scroll", 0)) + line_h)
            painter.restore()
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
        self.setGridSize(QSize(size + 16, size + 108))   # 标签区加高到约 4 行（原来只有 2 行）
        self.reset()

    def wheelEvent(self, event) -> None:
        """鼠标悬停在某个格子的**标签区**时，滚轮滚的是那个格子的标签；其它位置正常滚列表。"""
        try:
            pos = event.position().toPoint()
            idx = self.indexAt(pos)
            it = self.grid_model.item_at(idx) if idx.isValid() else None
            if it is not None and self.grid_model.tile_tags and it.tags:
                rect = self.visualRect(idx)
                tag_top = rect.top() + int(self.grid_model.icon_size) + 19
                if pos.y() >= tag_top:
                    # 一次滚一行（以前一次 3px，滚到底很费劲），并且夹在 [0, 内容高-可见高]
                    line_h = self.fontMetrics().height() + 4
                    step = -line_h if event.angleDelta().y() > 0 else line_h
                    box_h = max(1, rect.height() - int(self.grid_model.icon_size) - 19)
                    content_h = int(getattr(it, "tag_content_h", 0) or 0)
                    if content_h <= 0:      # 还没画过（拿不到真实高度）时按标签数估一个上限
                        content_h = box_h + len(it.tags) * 22
                    it.tag_scroll = min(max(0, int(getattr(it, "tag_scroll", 0)) + step),
                                        max(0, content_h - box_h))
                    self.update(idx)
                    event.accept()
                    return
        except Exception:
            pass
        super().wheelEvent(event)

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
