"""审核界面：AI 打的标签先在这里过一遍人工，确认后才正式生效。

- 左边是待审核图片队列（按待审标签数排序）
- 中间是大图，选中某个标签会**高亮它对应的区域框**；框不准可以重新框选覆盖
- 右边逐个标签接受 / 拒绝 / 删除
- 审核结论会立刻回馈模型（确认=正样本，拒绝=负样本）
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QRadioButton, QVBoxLayout, QWidget,
)

from ..config import SOURCE_LABELS
from .common import label
from .dialogs import ImageCanvas, TagEditDialog, category_combo

CONFIRMED = QColor("#7ddc7d")
REJECTED = QColor("#ff8a8a")
PENDING = QColor("#ffcc66")


class ReviewDialog(QDialog):
    changed = Signal()
    tagCreated = Signal(str)

    def __init__(self, library, hub, parent=None):
        super().__init__(parent)
        self.library = library
        self.hub = hub
        self.store = library.store
        self.setWindowTitle("审核待定标签（确认后才正式生效）")
        self.resize(1500, 900)
        self.queue: list[dict] = []          # [{'id':..,'name':..,'path':..,'pending':[tag rows]}]
        self.index = 0
        self.decisions: dict[str, str] = {}
        self.pending_names: list[str] = []
        self.cards: dict[str, tuple] = {}
        self._q_items: dict[int, QListWidgetItem] = {}
        from .common import ThumbPool
        self.thumbs = ThumbPool(size=200)
        self.thumbs.signals.ready.connect(self.on_queue_thumb)
        self.selected_tag: str | None = None
        self.box_mode = False
        self.box_target: str | None = None
        self._build()
        self.reload_queue()

    # ------------------------------------------------------------ UI
    def _build(self) -> None:
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = QLabel("")
        self.title.setStyleSheet("font-size:14px;color:#9fd0ff;")
        top.addWidget(self.title, 1)
        self.only_pending = QCheckBox("只看还有待审标签的图片")
        self.only_pending.setChecked(True)
        self.only_pending.stateChanged.connect(self.reload_queue)
        top.addWidget(self.only_pending)
        for text, slot in (("全部接受 (A)", self.accept_all), ("全部拒绝 (R)", self.reject_all),
                           ("保存并下一张 (空格)", self.save_and_next), ("跳过", self.next_file)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        v.addLayout(top)
        bottom_bar = QHBoxLayout()
        hint = QLabel("「审核完毕」= 按你的判断通过/否决，<b>其余未决的标签直接丢弃</b>"
                      "（丢弃不参与模型训练，只有通过/否决才算反馈）")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8f96a3;")
        bottom_bar.addWidget(hint, 1)
        self.b_finish = QPushButton("✔ 审核完毕（并通过当前图）")
        self.b_finish.setStyleSheet("background:#2f6b46;color:#e8fff0;font-weight:bold;padding:6px 14px;")
        self.b_finish.clicked.connect(self.finish_current)
        bottom_bar.addWidget(self.b_finish)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bottom_bar.addWidget(b_close)
        v.addLayout(bottom_bar)

        split = QSplitter(Qt.Horizontal)
        self.queue_list = QListWidget()
        self.queue_list.setIconSize(QSize(96, 96))
        self.queue_list.setGridSize(QSize(104, 128))
        self.queue_list.setViewMode(QListWidget.IconMode)
        self.queue_list.setResizeMode(QListWidget.Adjust)
        self.queue_list.currentRowChanged.connect(self.on_queue_changed)
        self.queue_list.verticalScrollBar().valueChanged.connect(lambda _v: self._queue_thumbs())
        split.addWidget(self.queue_list)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        self.canvas = ImageCanvas()
        self.canvas.regionDrawn.connect(self.on_region_drawn)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        cv.addWidget(scroll, 1)
        hint = QLabel("提示：在上面的标签表里点一行 → 图上会高亮它对应的框；点「重新框选」后直接在图上拖框即可修正偏差。"
                      "滚轮缩放，拖框时的坐标会按比例保存。\n"
                      "右下角可以随时补上 AI 漏掉的标签（输入名字回车即可，直接生效；也可以新建带类型/提示词的标签），"
                      "已生效标签同样能选中并框选/改框。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8f96a3;")
        cv.addWidget(hint)
        split.addWidget(center)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(label("这张图的待审标签", "#9fd0ff", True))
        # —— 年龄分级专列（默认显示自动结果，可手动改）——
        rate_box = QGroupBox("年龄分级（默认为自动识别结果，可手动改）")
        rb = QHBoxLayout(rate_box)
        self.rating_group = QButtonGroup(self)
        self.rating_radios: dict[str, QRadioButton] = {}
        from ..config import RATING_LABELS
        for key in ("all_ages", "r15", "r18", "r18g"):
            r = QRadioButton(RATING_LABELS[key].split("（")[0])
            r.setToolTip(RATING_LABELS[key])
            rb.addWidget(r)
            self.rating_group.addButton(r)
            self.rating_radios[key] = r
        r_cancel = QRadioButton("保持/不定级")
        r_cancel.setToolTip("不修改分级；若原本没有分级就不写")
        rb.addWidget(r_cancel)
        self.rating_group.addButton(r_cancel)
        self.rating_radios[""] = r_cancel
        rv.addWidget(rate_box)
        self.rating_hint = QLabel("")
        self.rating_hint.setWordWrap(True)
        self.rating_hint.setStyleSheet("color:#8f96a3;")
        rv.addWidget(self.rating_hint)
        self.table = QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels(["通过 / 否决　（显示中文名，双击某一标签可给它设中文名）"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self.on_row_selected)
        self.table.cellDoubleClicked.connect(self.edit_tag_zh)
        rv.addWidget(self.table, 2)

        rv.addWidget(label("已生效的标签（灰掉的不参与文件名写回）", "#9fd0ff", True))
        self.confirmed_list = QListWidget()
        self.confirmed_list.itemSelectionChanged.connect(self.on_confirmed_selected)
        rv.addWidget(self.confirmed_list, 1)

        rv.addWidget(label("补一个 AI 漏掉的标签", "#9fd0ff", True))
        add_row = QHBoxLayout()
        self.new_tag = QLineEdit()
        self.new_tag.setPlaceholderText("输入标签名后回车（直接生效）")
        self.new_tag.returnPressed.connect(self.add_manual_tag)
        from .common import attach_tag_completer
        attach_tag_completer(self.new_tag, self.store)
        add_row.addWidget(self.new_tag, 1)
        self.new_cat = category_combo(self.store)
        add_row.addWidget(self.new_cat)
        b_add = QPushButton("添加")
        b_add.clicked.connect(self.add_manual_tag)
        add_row.addWidget(b_add)
        rv.addLayout(add_row)
        add_row2 = QHBoxLayout()
        b_full = QPushButton("新建标签（类型/提示词/前置条件）…")
        b_full.clicked.connect(self.new_tag_full)
        add_row2.addWidget(b_full)
        add_row2.addStretch(1)
        rv.addLayout(add_row2)

        rv.addWidget(label("相似图片的标签推荐（双击添加）", "#9fd0ff", True))
        self.suggest_list = QListWidget()
        self.suggest_list.itemDoubleClicked.connect(lambda _i: self.add_suggested())
        rv.addWidget(self.suggest_list, 1)
        b_sug = QPushButton("刷新推荐")
        b_sug.clicked.connect(self.load_suggestions)
        rv.addWidget(b_sug)

        rv.addWidget(label("已有系列（可把当前图片并进去）", "#9fd0ff", True))
        self.series_list = QListWidget()
        self.series_list.setViewMode(QListWidget.IconMode)
        self.series_list.setIconSize(QSize(64, 64))
        self.series_list.setGridSize(QSize(150, 96))
        self.series_list.setResizeMode(QListWidget.Adjust)
        self.series_list.setMaximumHeight(210)
        rv.addWidget(self.series_list, 1)
        row_s = QHBoxLayout()
        for text, slot, tip in (
                ("加入选中系列", self.add_to_series, "把当前图片按下一页并入选中的系列文件夹"),
                ("用当前图片新建系列", self.new_series_from_current, "以这张图为第 1 页建一个新系列"),
                ("移出系列", self.leave_series, "把当前图片从它所属的系列里移出来（文件不动）")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row_s.addWidget(b)
        rv.addLayout(row_s)
        row = QHBoxLayout()
        for text, slot in (("确认选中", self.confirm_selected_confirmed),
                           ("拒绝选中", self.reject_selected_confirmed)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        rv.addLayout(row)
        row2 = QHBoxLayout()
        self.b_box = QPushButton("重新框选选中的标签")
        self.b_box.clicked.connect(self.start_box)
        row2.addWidget(self.b_box)
        self.b_delbox = QPushButton("删除该标签的框")
        self.b_delbox.clicked.connect(self.delete_box)
        row2.addWidget(self.b_delbox)
        rv.addLayout(row2)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        rv.addWidget(self.status)
        split.addWidget(right)
        split.setSizes([260, 830, 400])
        v.addWidget(split, 1)

    # ------------------------------------------------------------ 队列
    def reload_queue(self) -> None:
        rows = self.store.pending_files(400)
        self.queue = [dict(r) for r in rows]
        self._q_items: dict[int, QListWidgetItem] = {}
        self._q_pending = 0
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        for r in self.queue:
            it = QListWidgetItem(Path(r["path"]).name)
            it.setData(Qt.UserRole, int(r["id"]))
            it.setToolTip(f"{r['path']}\n待审标签 {r['n_pending']} 个"
                          + ("\n（未定级）" if r["no_rating"] else ""))
            self.queue_list.addItem(it)
            self._q_items[int(r["id"])] = it
        self.queue_list.blockSignals(False)
        self.title.setText(f"待审核：{self.store.pending_summary()['pending_tags']} 个标签 / "
                           f"{len(self.queue)} 张图片（缩略图后台生成中…）")
        self._queue_thumbs()          # 缩略图交给后台线程池，不在界面线程里解原图
        summary = self.store.pending_summary()
        self.title.setText(f"待审核：{summary['pending_tags']} 个标签 / {summary['pending_files']} 张图片")
        if self.queue:
            self.index = min(self.index, len(self.queue) - 1)
            self.queue_list.setCurrentRow(self.index)
            self.load_current()
        else:
            self.canvas.set_pixmap(QPixmap())
            self.table.setRowCount(0)
            self.confirmed_list.clear()
            self.status.setText("没有待审核的标签了 🎉")

    def _queue_thumbs(self) -> None:
        """只给可见区域请求缩略图（以前是同步解 400 张原图，必然卡死）。"""
        n = self.queue_list.count()
        if not n:
            return
        rect = self.queue_list.viewport().rect()
        top = self.queue_list.indexAt(rect.topLeft())
        bottom = self.queue_list.indexAt(rect.bottomRight())
        first = max(0, (top.row() if top.isValid() else 0) - 20)
        last = min(n - 1, (bottom.row() if bottom.isValid() else min(n - 1, 60)) + 20)
        for i in range(first, last + 1):
            r = self.queue[i]
            fid = int(r["id"])
            it = self._q_items.get(fid)
            if it is None or not it.icon().isNull():
                continue
            self.thumbs.request(fid, r["path"], r.get("mtime") or 0)

    def on_queue_thumb(self, file_id: int, path: str) -> None:
        it = self._q_items.get(int(file_id))
        if it is None or not path:
            return
        pm = QPixmap(path)
        if not pm.isNull():
            try:
                it.setIcon(QIcon(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            except RuntimeError:
                pass

    def on_queue_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.queue):
            return
        self.save_decisions(silent=True)
        self.index = row
        self.load_current()

    def load_current(self) -> None:
        if not self.queue:
            return
        r = self.queue[self.index]
        self.path = r["path"]
        pm = QPixmap(self.path)
        if pm.isNull():
            from PySide6.QtGui import QImageReader
            rd = QImageReader(self.path)
            rd.setAutoTransform(True)
            pm = QPixmap.fromImage(rd.read())
        self.canvas.set_pixmap(pm)
        self.decisions = {}
        self.selected_tag = None
        self.box_mode = False
        self.canvas.annotate = False
        self.reload_table()
        self.reload_regions()
        self.reload_confirmed()
        self.load_suggestions()
        self.reload_series()
        self.reload_rating()
        self.new_tag.clear()

    def reload_table(self) -> None:
        """每个标签一行：左边是单独的「通过 / 否决」按钮，中间显示中文名（附英文原名）。"""
        from .. import tag_i18n
        fid = int(self.queue[self.index]["id"]) if self.queue else None
        rows = self.store.tags_for_file(fid, statuses=("pending",)) if fid else []
        self.table.setRowCount(0)
        self.cards.clear()
        self.pending_names = [t["name"] for t in rows]
        for i, t in enumerate(rows):
            name = t["name"]
            zh_hint = self.library.tag_zh(name)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)
            b_ok = QPushButton("✓ 通过")
            b_ok.setFixedWidth(64)
            b_ok.setStyleSheet("background:#25543a;color:#b6f5cd;")
            b_ok.clicked.connect(lambda _c=False, n=name: self.decide(n, "confirmed", None))
            b_no = QPushButton("✗ 否决")
            b_no.setFixedWidth(64)
            b_no.setStyleSheet("background:#5a2a2a;color:#ffc9c9;")
            b_no.clicked.connect(lambda _c=False, n=name: self.decide(n, "rejected", None))
            h.addWidget(b_ok)
            h.addWidget(b_no)
            lab = QLabel(tag_i18n.display(name, zh_hint))
            lab.setStyleSheet("font-size:12px;")
            lab.setToolTip(f"{name}\n来源：{SOURCE_LABELS.get(t['source'], t['source'])}"
                           f"　分数：{t['score']:.2f}\n双击可设置中文名")
            h.addWidget(lab, 1)
            src = QLabel(f"{SOURCE_LABELS.get(t['source'], t['source'])} {t['score']:.2f}")
            src.setStyleSheet("color:#8f96a3;font-size:10px;")
            h.addWidget(src)
            self.cards[name] = (row, lab)
            self.table.insertRow(i)
            self.table.setCellWidget(i, 0, row)
            self.table.setRowHeight(i, 30)
        self.status.setText(f"第 {self.index + 1}/{len(self.queue)} 张 · 待审 {len(rows)} 个标签"
                            + ("　（双击标签可设中文名）" if rows else ""))
        if not rows:
            # 队列里也会列出"没有待审标签但还没定级"的图（兜底，避免漏审）——这里必须说清楚
            cur = self.store.one("SELECT rating FROM files WHERE id=?", (fid,)) if fid else None
            if cur is not None and not (cur["rating"] or ""):
                self.status.setText(
                    f"第 {self.index + 1}/{len(self.queue)} 张 · 这张图**没有待审标签**，"
                    "它出现在队列里是因为还没有「年龄分级」——请在上面的分级单选里选一档，"
                    "然后点右下角「审核完毕」；右边的「已生效标签」可以看到它现有的标签。")
            else:
                self.status.setText(
                    f"第 {self.index + 1}/{len(self.queue)} 张 · 这张图的 AI 标签已经审完了"
                    "（没有新的待审标签）——直接点「跳过」或「下一张」继续。"
                    "已生效的标签列在右侧「已生效标签」里。")

    def reload_confirmed(self) -> None:
        from .. import tag_i18n
        fid = int(self.queue[self.index]["id"]) if self.queue else None
        self.confirmed_list.clear()
        if not fid:
            return
        for t in self.store.tags_for_file(fid, statuses=("confirmed",)):
            it = QListWidgetItem(f"{tag_i18n.display(t['name'], self.library.tag_zh(t['name']))}"
                                 f"   [{SOURCE_LABELS.get(t['source'], t['source'])}]")
            it.setData(Qt.UserRole, t["name"])
            it.setForeground(CONFIRMED)
            self.confirmed_list.addItem(it)

    def reload_regions(self) -> None:
        fid = int(self.queue[self.index]["id"]) if self.queue else None
        regions = self.store.regions_for_file(fid) if fid else []
        self.canvas.regions = [(r["tag_name"] or "", r["x"], r["y"], r["w"], r["h"]) for r in regions]
        self.canvas.highlight = self.selected_tag
        self.canvas.update()

    # ------------------------------------------------------------ 决定
    def decide(self, name: str, action: str, row: int | None = None) -> None:
        self.decisions[name] = action
        card = self.cards.get(name)
        if card:
            w, lab = card
            w.setStyleSheet("background:#1f3a2a;" if action == "confirmed" else "background:#3a2020;")
            lab.setText(("✓ " if action == "confirmed" else "✗ ") + lab.text().lstrip("✓✗ "))
            lab.setStyleSheet("font-size:12px;color:%s;" %
                              ("#b6f5cd" if action == "confirmed" else "#ffc9c9"))

    def accept_all(self) -> None:
        for name in list(self.pending_names):
            self.decide(name, "confirmed")

    def reject_all(self) -> None:
        for name in list(self.pending_names):
            self.decide(name, "rejected")

    def edit_tag_zh(self, row: int, _col: int) -> None:
        """双击某一行 → 给这个标签设中文名（以后审核界面都显示它）。"""
        if row < 0 or row >= len(self.pending_names):
            return
        name = self.pending_names[row]
        from PySide6.QtWidgets import QInputDialog
        cur = self.library.tag_zh(name)
        text, ok = QInputDialog.getText(self, "设置中文名", f"「{name}」显示成：", text=cur)
        if ok:
            self.library.set_tag_zh(name, text)
            self.reload_table()
            self.reload_confirmed()

    def save_decisions(self, silent: bool = False) -> int:
        if not self.queue or not self.decisions:
            return 0
        fid = int(self.queue[self.index]["id"])
        n = self.library.review_file(fid, dict(self.decisions))
        self.decisions = {}
        self.changed.emit()
        if not silent:
            self.status.setText(f"已保存 {n['changed']} 条审核结论（模型已据此更新）")
        return n["changed"]

    def save_and_next(self) -> None:
        self.save_decisions()
        self.next_file()

    def finish_current(self) -> None:
        """审核完毕：通过/否决按当前判断写库，其余未决标签直接丢弃（不参与模型反馈）。"""
        if not self.queue:
            return
        fid = int(self.queue[self.index]["id"])
        confirmed = [n for n, a in self.decisions.items() if a == "confirmed"]
        rejected = [n for n, a in self.decisions.items() if a == "rejected"]
        drop = [n for n in self.pending_names if n not in self.decisions]
        rating = self.chosen_rating()
        res = self.library.finish_review(fid, confirmed, rejected, drop, rating=rating)
        self.decisions = {}
        self.changed.emit()
        self.status.setText(f"审核完毕：通过 {res['confirmed']}｜否决 {res['rejected']}｜"
                            f"丢弃 {res['dropped']}（丢弃不参与模型反馈）"
                            + ("｜已定级" if rating else "｜保持原分级"))
        self.next_file()

    def reload_rating(self) -> None:
        """分级专列：默认选中自动打上的等级；没定级则提示手工选。"""
        from ..config import RATING_LABELS
        fid = int(self.queue[self.index]["id"]) if self.queue else None
        row = self.store.one("SELECT rating FROM files WHERE id=?", (fid,)) if fid else None
        cur = (row["rating"] or "") if row else ""
        for key, r in self.rating_radios.items():
            r.setChecked(key == cur)
        if cur:
            self.rating_hint.setStyleSheet("color:#8f96a3;")
            self.rating_hint.setText(f"自动识别：{RATING_LABELS.get(cur, cur)}（改档位后点「审核完毕」生效）")
        else:
            self.rating_hint.setStyleSheet("color:#ffcc66;")
            self.rating_hint.setText("⚠ 这张图还没定级（WD14/CLIP 都没给结论）——请手动选一档，"
                                     "否则它会一直留在待审核列表里。")

    def chosen_rating(self) -> str | None:
        for key, r in self.rating_radios.items():
            if r.isChecked():
                return key or None
        return None

    def next_file(self) -> None:
        self.save_decisions(silent=True)
        if not self.queue:
            self.reload_queue()
            return
        if self.index + 1 < len(self.queue):
            self.queue_list.setCurrentRow(self.index + 1)
        else:
            self.reload_queue()

    # ------------------------------------------------------------ 已生效标签
    def confirm_selected_confirmed(self) -> None:
        it = self.confirmed_list.currentItem()
        if not it:
            return
        self._apply_to_confirmed(it.data(Qt.UserRole), "confirmed")

    def reject_selected_confirmed(self) -> None:
        it = self.confirmed_list.currentItem()
        if not it:
            return
        self._apply_to_confirmed(it.data(Qt.UserRole), "rejected")

    def _apply_to_confirmed(self, name: str, action: str) -> None:
        fid = int(self.queue[self.index]["id"])
        self.library.review_file(fid, {name: action})
        self.changed.emit()
        self.reload_confirmed()
        self.reload_regions()
        self.status.setText(f"「{name}」已改为 {action}")

    # ------------------------------------------------------------ 新增标签
    def add_manual_tag(self) -> None:
        """审核时补一个 AI 漏掉的标签（直接生效，并立刻回馈模型）。"""
        from .. import tag_i18n
        name = tag_i18n.parse_input(self.new_tag.text(), self.store)
        if not name:
            self.status.setText("先输入标签名再按回车")
            return
        if not self.queue:
            return
        fid = int(self.queue[self.index]["id"])
        cat = self.new_cat.ensure_current(self)
        if self.store.tag_id(name) is None:
            self.store.ensure_tag(name, cat)
        self.library.add_tags_to_files([fid], [name], "manual")
        self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (fid,))
        self.new_tag.clear()
        self.reload_confirmed()
        self.reload_table()
        self.changed.emit()
        info = self.library.probe_confidence(self.store.tag_id(name))
        from .. import categories as cats
        self.status.setText(f"已补上「{name}」（{cats.label_of(self.store, cat)}），该标签已学 {info['n']} 个样本")

    def new_tag_full(self) -> None:
        """新建带类型/提示词/前置条件的标签，适合以后要靠 CLIP 去找的标签。"""
        if not self.queue:
            return
        dlg = TagEditDialog(self, self.new_tag.text().strip(), self.new_cat.ensure_current(self),
                            store=self.store)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v["name"]:
            return
        tid = self.store.ensure_tag(v["name"], v["category"], v["prompt"] or None, v["auto"])
        self.store.update_tag(tid, category=v["category"], prompt=v["prompt"], auto=v["auto"],
                              requires=v.get("requires", ""))
        fid = int(self.queue[self.index]["id"])
        self.library.add_tags_to_files([fid], [v["name"]], "manual")
        self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (fid,))
        self.tagCreated.emit(v["name"])
        self.new_tag.clear()
        self.reload_confirmed()
        self.changed.emit()
        self.status.setText(f"已新建并打上「{v['name']}」"
                            + ("（有提示词，可点工具栏「CLIP 重打分」全库找同类）" if v["prompt"] else ""))

    def load_suggestions(self) -> None:
        self.suggest_list.clear()
        if not self.queue:
            return
        fid = int(self.queue[self.index]["id"])
        for n in self.library.suggest_tags([fid]):
            it = QListWidgetItem(n)
            it.setData(Qt.UserRole, n)
            self.suggest_list.addItem(it)
        if self.suggest_list.count() == 0:
            self.suggest_list.addItem(QListWidgetItem("（暂无推荐，多标几张后会更准）"))

    def add_suggested(self) -> None:
        it = self.suggest_list.currentItem()
        if not it or not self.queue:
            return
        name = it.data(Qt.UserRole)
        if not name:
            return
        fid = int(self.queue[self.index]["id"])
        if self.store.tag_id(name) is None:
            self.store.ensure_tag(name, "other")
        self.library.add_tags_to_files([fid], [name], "manual")
        self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (fid,))
        self.suggest_list.takeItem(self.suggest_list.row(it))
        self.reload_confirmed()
        self.changed.emit()
        self.status.setText(f"已采纳推荐标签「{name}」")

    # ------------------------------------------------------------ 选中项
    def selected_tag_name(self) -> str | None:
        """当前选中的标签：待审表 或 已生效列表，取其一。"""
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            return self.table.item(row, 0).data(Qt.UserRole)
        it = self.confirmed_list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def on_confirmed_selected(self) -> None:
        it = self.confirmed_list.currentItem()
        if not it:
            return
        self.selected_tag = it.data(Qt.UserRole)
        self.canvas.highlight = self.selected_tag
        self.canvas.update()
        has = any((r[0] or "") == self.selected_tag for r in self.canvas.regions)
        self.status.setText(f"已选中「{self.selected_tag}」"
                            + ("，图上高亮的是它的框（可点「重新框选」修正）" if has
                               else "，这张图还没有它的框（可点「重新框选」补一个）"))

    # ------------------------------------------------------------ 系列
    def reload_series(self) -> None:
        from .. import imaging
        self.series_list.clear()
        cur_fid = int(self.queue[self.index]["id"]) if self.queue else None
        cur_series = None
        if cur_fid:
            row = self.store.one("SELECT series_id FROM files WHERE id=?", (cur_fid,))
            cur_series = int(row["series_id"]) if row and row["series_id"] else None
        for s in self.store.series_list():
            it = QListWidgetItem(f"{s['name']}  [{s['page_count']}页]")
            it.setData(Qt.UserRole, int(s["id"]))
            it.setToolTip(s["dir"])
            if int(s["id"]) == cur_series:
                it.setText(it.text() + " ←当前")
                it.setForeground(QColor("#7ddc7d"))
            first = self.store.one("SELECT id,path,mtime FROM files WHERE series_id=? ORDER BY page_no,id LIMIT 1",
                                   (int(s["id"]),))
            if first:
                p = imaging.thumb_path(int(first["id"]), first["mtime"] or 0, 320)
                if not p.exists():
                    p = imaging.make_thumb(first["path"], int(first["id"]), first["mtime"] or 0, 320) or p
                pm = QPixmap(str(p))
                if not pm.isNull():
                    it.setIcon(QIcon(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.series_list.addItem(it)
        if self.series_list.count() == 0:
            self.series_list.addItem(QListWidgetItem("（还没有系列）"))

    def add_to_series(self) -> None:
        if not self.queue:
            return
        it = self.series_list.currentItem()
        sid = it.data(Qt.UserRole) if it else None
        if not sid:
            QMessageBox.information(self, "加入系列", "先在下面选一个系列。")
            return
        fid = int(self.queue[self.index]["id"])
        res = self.library.merge_into_series([fid], series_id=int(sid), mode="move")
        if not res.get("ok"):
            QMessageBox.warning(self, "加入系列失败", res.get("msg", ""))
            return
        self.changed.emit()
        self.reload_series()
        self.reload_confirmed()
        self.status.setText(f"已把当前图片并入系列：{res['dir']}")

    def new_series_from_current(self) -> None:
        if not self.queue:
            return
        fid = int(self.queue[self.index]["id"])
        conf = [t["name"] for t in self.store.tags_for_file(fid, statuses=("confirmed",))]
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建系列", "系列名（会写进文件夹名）：",
                                        text=Path(self.queue[self.index]["path"]).parent.name)
        if not ok or not name.strip():
            return
        tags_text, ok2 = QInputDialog.getText(self, "新建系列", "系列标签（空格分隔，可留空）：",
                                              text=" ".join(conf[:8]))
        if not ok2:
            return
        tags = [t for t in tags_text.replace(",", " ").split() if t]
        res = self.library.merge_into_series([fid], name=name.strip(), tags=tags, mode="move")
        if not res.get("ok"):
            QMessageBox.warning(self, "新建系列失败", res.get("msg", ""))
            return
        self.changed.emit()
        self.reload_series()
        self.reload_confirmed()
        self.status.setText(f"已新建系列：{res['dir']}")

    def leave_series(self) -> None:
        if not self.queue:
            return
        fid = int(self.queue[self.index]["id"])
        row = self.store.one("SELECT series_id FROM files WHERE id=?", (fid,))
        sid = int(row["series_id"]) if row and row["series_id"] else None
        if not sid:
            self.status.setText("当前图片不在任何系列里")
            return
        self.store.execute("UPDATE files SET series_id=NULL,page_no=NULL,kind='image' WHERE id=?", (fid,))
        self.store.update_series_stats(sid)
        self.changed.emit()
        self.reload_series()
        self.status.setText("已把当前图片移出系列（文件留在原文件夹，可之后整理）")

    # ------------------------------------------------------------ 区域框
    def on_row_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self.selected_tag = self.table.item(row, 0).data(Qt.UserRole)
        self.canvas.highlight = self.selected_tag
        self.canvas.update()
        has = any((r[0] or "") == self.selected_tag for r in self.canvas.regions)
        self.status.setText(f"已选中「{self.selected_tag}」"
                            + ("，图上高亮的是它对应的框；可点「重新框选」修正" if has
                               else "，这张图还没有它的框；点「重新框选」在图上拖一个"))

    def start_box(self) -> None:
        name = self.selected_tag_name()
        if not name and self.new_tag.text().strip():
            # 允许“先输入标签名 → 直接框选”，自动把它加到这张图上
            name = self.new_tag.text().strip()
            cat = self.new_cat.ensure_current(self)
            if self.store.tag_id(name) is None:
                self.store.ensure_tag(name, cat)
            fid = int(self.queue[self.index]["id"])
            self.library.add_tags_to_files([fid], [name], "manual")
            self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (fid,))
            self.new_tag.clear()
            self.reload_confirmed()
            self.changed.emit()
        if not name:
            QMessageBox.information(self, "重新框选", "先选一个标签（或在上面输入新标签名）。")
            return
        self.box_target = name
        self.box_mode = True
        self.canvas.annotate = True
        self.canvas.setCursor(Qt.CrossCursor)
        self.reload_table()

    def on_region_drawn(self, x: float, y: float, w: float, h: float) -> None:
        if not self.box_mode or not self.box_target:
            return
        fid = int(self.queue[self.index]["id"])
        res = self.library.replace_region(fid, self.box_target, (x, y, w, h), self.hub)
        self.box_mode = False
        self.canvas.annotate = False
        self.canvas.setCursor(Qt.ArrowCursor)
        self.status.setText(f"已把「{self.box_target}」的框更新为当前框（旧框已替换，模型已重新学习）" if res
                            else "框太小，已忽略")
        self.selected_tag = self.box_target
        self.reload_regions()
        self.reload_table()
        self.changed.emit()

    def delete_box(self) -> None:
        name = self.selected_tag_name()
        if not name:
            return
        fid = int(self.queue[self.index]["id"])
        for r in self.store.regions_for_file(fid):
            if (r["tag_name"] or "") == name:
                self.store.delete_region(int(r["id"]))
        self.library.update_region_probes()
        self.reload_regions()
        self.status.setText(f"已删除「{name}」的框（标签本身还在）")

    # ------------------------------------------------------------ 快捷键
    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_A:
            self.accept_all()
        elif key == Qt.Key_R:
            self.reject_all()
        elif key in (Qt.Key_Space, Qt.Key_Right):
            self.save_and_next()
        elif key == Qt.Key_Left:
            if self.index > 0:
                self.queue_list.setCurrentRow(self.index - 1)
        elif Qt.Key_1 <= key <= Qt.Key_9:
            i = key - Qt.Key_1
            if i < len(self.pending_names):
                name = self.pending_names[i]
                cur = self.decisions.get(name)
                self.decide(name, "confirmed" if cur != "confirmed" else "rejected")
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.save_decisions(silent=True)
        self.changed.emit()
        event.accept()


def path_for_thumb(store, file_id: int):
    """队列缩略图：直接用已有缩略图缓存。"""
    from .. import imaging
    row = store.one("SELECT path, mtime FROM files WHERE id=?", (file_id,))
    if not row:
        return ""
    p = imaging.thumb_path(file_id, row["mtime"] or 0, 320)
    if p.exists():
        return p
    return row["path"]
