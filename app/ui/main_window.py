"""主窗口：库扫描、缩略图网格、标签筛选/编辑、自动打标、系列合并。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSlider,
    QSplitter, QTabWidget, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import imaging, naming
from ..config import CATEGORY_ORDER, SOURCE_LABELS, TAG_CATEGORIES, Settings
from ..library import EngineHub, Library, prompt_templates
from ..store import Store
from ..workers import Task
from .common import STYLE, colored, label
from .dialogs import (CategoryManagerDialog, ModelsDialog, PersonDialog, PreviewDialog, SeriesDialog,
                      SettingsDialog, TagEditDialog, TagManagerDialog, category_combo)
from .taxonomy import TaxonomyDialog
from .review import ReviewDialog
from .dupes import DuplicateDialog
from .import_dialog import ImportDialog
from .grid import GridItem, GridModel, GridView
from .common import ThumbPool


def _cat_order(store) -> list[str]:
    from .. import categories
    return categories.orders(store)


def _cat_labels(store) -> dict[str, str]:
    from .. import categories
    return categories.label_map(store)


class TagPanel(QWidget):
    """右侧标签面板：批量勾选/取消，添加新标签时可指定类型。"""

    changed = Signal(list)
    requestPreview = Signal(int)
    tagCreated = Signal(str)

    def __init__(self, store: Store, library: Library, parent=None):
        super().__init__(parent)
        self.store = store
        self.library = library
        self.file_ids: list[int] = []
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.header = label("未选择图片", "#c9ccd4", True)
        v.addWidget(self.header)

        add = QHBoxLayout()
        self.new_tag = QLineEdit()
        self.new_tag.setPlaceholderText("新标签…")
        self.new_tag.returnPressed.connect(self.add_new_tag)
        add.addWidget(self.new_tag, 1)
        self.new_cat = category_combo(self.store)
        add.addWidget(self.new_cat)
        b = QPushButton("添加")
        b.clicked.connect(self.add_new_tag)
        add.addWidget(b)
        v.addLayout(add)

        self.search = QLineEdit()
        self.search.setPlaceholderText("筛选标签…")
        self.search.textChanged.connect(self.reload)
        v.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tag_menu)
        v.addWidget(self.tree, 3)
        row0 = QHBoxLayout()
        for text, slot in (("确认全部待审", lambda: self.bulk_status("confirmed")),
                           ("拒绝全部待审", lambda: self.bulk_status("rejected"))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row0.addWidget(b)
        v.addLayout(row0)

        v.addWidget(label("相似图片的标签推荐", "#8f96a3"))
        self.suggest = QListWidget()
        self.suggest.itemDoubleClicked.connect(self.apply_suggestion)
        v.addWidget(self.suggest, 1)
        row = QHBoxLayout()
        b_refresh = QPushButton("推荐标签")
        b_refresh.clicked.connect(self.refresh_suggestions)
        row.addWidget(b_refresh)
        b_apply = QPushButton("应用选中推荐")
        b_apply.clicked.connect(self.apply_suggestion)
        row.addWidget(b_apply)
        v.addLayout(row)

        ext = QHBoxLayout()
        for text, slot in (("打开图片", lambda: self.file_ids and self.requestPreview.emit(self.file_ids[0])),
                           ("资源管理器", self.reveal),
                           ("写回文件名", self.write_back)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            ext.addWidget(b)
        v.addLayout(ext)
        self.learn_status = label("", "#7ddc7d")
        self.learn_status.setWordWrap(True)
        v.addWidget(self.learn_status)

    def show_learning(self, name: str) -> None:
        """告诉用户这个标签现在“学到了多少”：样本越多越稳。"""
        tid = self.store.tag_id(name)
        if not tid:
            return
        info = self.library.probe_confidence(tid)
        if info["n"] == 0:
            self.learn_status.setText(f"「{name}」还没有标注样本：现在只靠 CLIP 零样本（可写提示词提升）")
            return
        bits = [f"已学 {info['n']} 个样本"]
        if info["region"]:
            bits.append("有区域样本")
        if info["logit"]:
            bits.append("已训练分类器")
        if info["extra_threshold"] > 0:
            bits.append(f"阈值提高 {info['extra_threshold']:.2f} 以保证精度")
        bits.append("样本 ≥20 后会明显更准")
        self.learn_status.setText(f"「{name}」" + "；".join(bits))

    # ---------- 审核相关 ----------
    def update_pending(self) -> None:
        s = self.store.pending_summary()
        self.learn_status.setText(f"待审核标签 {s['pending_tags']} 个（{s['pending_files']} 张图）"
                                  if s["pending_tags"] else "")

    def selected_tag_name(self) -> str | None:
        it = self.tree.currentItem()
        data = it.data(0, Qt.UserRole) if it else None
        if data and data[0] == "tag":
            return data[1]
        return None

    def bulk_status(self, status: str) -> None:
        """把选中图片的所有待审标签一次性确认/拒绝。"""
        if not self.file_ids:
            return
        n = 0
        for fid in self.file_ids:
            if status == "confirmed":
                n += self.library.confirm_all_pending(fid)
            else:
                n += self.library.reject_all_pending(fid)
        self.learn_status.setText(f"已{'确认' if status == 'confirmed' else '拒绝'} {n} 个待审标签（已回馈模型）")
        self.changed.emit(list(self.file_ids))
        self.reload()

    def set_status_selected(self, status: str | None, purge: bool = False) -> None:
        name = self.selected_tag_name()
        if not name or not self.file_ids:
            return
        if purge:
            self.library.remove_tags_from_files(self.file_ids, [name], purge=True)
        elif status:
            for fid in self.file_ids:
                self.store.set_tag_status(fid, [name], status)
            self.library.update_probes_for_tags([name])
            self.store.refresh_counts()
        self.changed.emit(list(self.file_ids))
        self.reload()

    def tag_menu(self, pos) -> None:
        name = self.selected_tag_name()
        if not name:
            return
        menu = QMenu(self)
        a_ok = menu.addAction("确认（正样本）")
        a_no = menu.addAction("拒绝（负样本）")
        menu.addSeparator()
        a_edit = menu.addAction("编辑标签（类型/提示词/前置条件）…")
        a_del = menu.addAction("彻底删除该标签（不留样本）")
        act = menu.exec(self.tree.mapToGlobal(pos))
        if act == a_ok:
            self.set_status_selected("confirmed")
        elif act == a_no:
            self.set_status_selected("rejected")
        elif act == a_edit:
            row = self.store.one("SELECT * FROM tags WHERE name=?", (name,))
            if row:
                dlg = TagEditDialog(self, row["name"], row["category"], row["prompt"] or "",
                                    bool(row["auto"]), "编辑标签", row["requires"] or "",
                                    store=self.store)
                if dlg.exec() == dlg.Accepted:
                    v = dlg.values()
                    self.store.update_tag(int(row["id"]), category=v["category"], prompt=v["prompt"],
                                          auto=v["auto"], requires=v.get("requires", ""))
                    self.changed.emit(list(self.file_ids))
                    self.reload()
        elif act == a_del:
            if QMessageBox.question(self, "删除标签", f"彻底删除「{name}」？图片和文件都不受影响。") == QMessageBox.Yes:
                self.set_status_selected(None, purge=True)

    # ---------- 选中变化 ----------
    def set_selection(self, file_ids: list[int]) -> None:
        self.file_ids = list(file_ids)
        if not file_ids:
            self.header.setText("未选择图片")
        elif len(file_ids) == 1:
            row = self.store.one("SELECT name FROM files WHERE id=?", (file_ids[0],))
            self.header.setText(f"{row['name'] if row else ''}")
        else:
            self.header.setText(f"已选 {len(file_ids)} 张（改动会应用到全部）")
        self.reload()
        self.suggest.clear()

    def reload(self) -> None:
        self._loading = True
        expanded = {self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        self.tree.clear()
        if not self.file_ids:
            self._loading = False
            return
        own = self.store.tags_for_files(self.file_ids, statuses=("confirmed", "pending"))
        count: dict[str, tuple[int, str, int]] = {}
        for fid in self.file_ids:
            for t in own.get(fid, []):
                n, cat, pend = count.get(t["name"], (0, t["category"], 0))
                count[t["name"]] = (n + 1, cat, pend + (1 if t["status"] == "pending" else 0))
        by_cat: dict[str, list[tuple[str, int]]] = {}
        needle = self.search.text().strip().lower()
        pend_map: dict[str, int] = {}
        for name, (n, cat, pend) in count.items():
            if needle and needle not in name.lower():
                continue
            by_cat.setdefault(cat, []).append((name, n))
            pend_map[name] = pend
        # 常用标签（图片较多的标签）也列出来，方便快速点选
        if not needle:
            for t in self.store.list_tags(only_used=True)[:400]:
                if t["count"] >= 3 and t["name"] not in count:
                    by_cat.setdefault(t["category"] + "_all", []).append((t["name"], 0))
        cat_order = _cat_order(self.store)
        for cat in cat_order + [c for c in by_cat if c.endswith("_all")]:
            items = by_cat.get(cat)
            if not items:
                continue
            base_cat = cat.replace("_all", "")
            title = _cat_labels(self.store).get(base_cat, base_cat) + ("（全部标签）" if cat.endswith("_all") else "")
            top = QTreeWidgetItem([f"{title} ({len(items)})"])
            top.setData(0, Qt.UserRole, ("cat", cat))
            self.tree.addTopLevelItem(top)
            top.setExpanded(title.split(" (")[0] in expanded or cat.endswith("_all") is False)
            for name, n in sorted(items, key=lambda x: -x[1]):
                pend = pend_map.get(name, 0)
                mark = "？" if pend and pend >= n else ""
                it = QTreeWidgetItem([f"{mark}{name}" + (f"  ×{n}" if 0 < n < len(self.file_ids) else "")
                                      + (f"  待审{pend}" if pend else "")])
                it.setData(0, Qt.UserRole, ("tag", name))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                if pend:
                    it.setForeground(0, QColor("#ffcc66"))
                if n == 0:
                    state = Qt.Unchecked
                elif n >= len(self.file_ids):
                    state = Qt.Checked
                else:
                    state = Qt.PartiallyChecked
                it.setCheckState(0, state)
                top.addChild(it)
        self._loading = False

    def on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading:
            return
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "tag" or not self.file_ids:
            return
        name = data[1]
        state = item.checkState(0)
        self._loading = True
        if state == Qt.Checked:
            self.library.add_tags_to_files(self.file_ids, [name], "manual")
        else:
            # 取消勾选 = 明确“不是这个标签”，作为负样本记下来（不会丢，右键可彻底删除）
            self.library.remove_tags_from_files(self.file_ids, [name])
        self._loading = False
        self.changed.emit(list(self.file_ids))
        self.update_pending()
        QTimer.singleShot(0, self.reload)

    def add_new_tag(self) -> None:
        name = self.new_tag.text().strip()
        if not name:
            return
        if not self.file_ids:
            QMessageBox.information(self, "提示", "先在中间选择图片，再添加标签。")
            return
        cat = self.new_cat.ensure_current(self)
        row = self.store.one("SELECT category FROM tags WHERE name=?", (name,))
        if row is None:
            dlg = TagEditDialog(self, name, cat, title="新建标签（可指定类型）", store=self.store)
            if dlg.exec() != dlg.Accepted:
                return
            v = dlg.values()
            name = v["name"]
            if not name:
                return
            self.store.ensure_tag(name, v["category"], v["prompt"] or None, v["auto"])
            self.store.update_tag(self.store.tag_id(name), category=v["category"], prompt=v["prompt"],
                                  auto=v["auto"], requires=v.get("requires", ""))
            self.tagCreated.emit(name)
        self.library.add_tags_to_files(self.file_ids, [name], "manual")
        self.new_tag.clear()
        self.changed.emit(list(self.file_ids))
        self.reload()
        self.show_learning(name)

    # ---------- 推荐 ----------
    def refresh_suggestions(self) -> None:
        if not self.file_ids:
            return
        names = self.library.suggest_tags(self.file_ids)
        self.suggest.clear()
        for n in names:
            it = QListWidgetItem(n)
            it.setData(Qt.UserRole, n)
            self.suggest.addItem(it)
        if not names:
            self.suggest.addItem(QListWidgetItem("（暂无推荐，多标几张图后会更准）"))

    def apply_suggestion(self, *_) -> None:
        it = self.suggest.currentItem()
        if not it or not self.file_ids:
            return
        name = it.data(Qt.UserRole)
        if not name:
            return
        self.library.add_tags_to_files(self.file_ids, [name], "manual")
        self.suggest.takeItem(self.suggest.row(it))
        self.changed.emit(list(self.file_ids))
        self.reload()
        self.show_learning(name)

    # ---------- 外部操作 ----------
    def reveal(self) -> None:
        if not self.file_ids:
            return
        row = self.store.one("SELECT path FROM files WHERE id=?", (self.file_ids[0],))
        if row:
            subprocess.Popen(["explorer", "/select,", str(row["path"])])

    def write_back(self) -> None:
        if not self.file_ids:
            return
        res = self.library.apply_disk_names(self.file_ids)
        msg = f"已重命名 {res['renamed']} 项"
        if res.get("errors"):
            msg += "\n" + "\n".join(res["errors"][:8])
        QMessageBox.information(self, "写回文件名", msg)
        self.changed.emit(list(self.file_ids))


class MainWindow(QMainWindow):
    def __init__(self, store: Store, settings: Settings):
        super().__init__()
        self.store = store
        self.settings = settings
        self.library = Library(store, settings)
        self.hub = EngineHub(settings)
        self.thumbs = ThumbPool(size=max(320, settings.thumb_size * 2))
        self.task: Task | None = None
        self.current_series: int | None = None
        self.similar_mode: dict | None = None
        self.root_filter: int | None = None
        self.dir_filter: str = ""
        self.setWindowTitle(f"图片标签工坊  v0.1  —  {store.db_path.parent}")
        self.setStyleSheet(STYLE)
        self.resize(1500, 920)
        self._build_ui()
        self.refresh_roots()
        self.refresh_tags()
        self.refresh_files()
        QTimer.singleShot(900, self.check_unfinished)

    # ================================================================= UI
    def _build_ui(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a

        # 主流程：导入 → 打标 → 审核 → 收录 → 整理
        act("导入图片…", self.open_import, "像 Lightroom 那样：选源文件夹 → 预览勾选 → 导入到图库（可顺手打标签和自动打标）")
        act("自动打标", self.run_auto_tag, "对选中图片跑一遍 WD14 + CLIP + 分级识别（分级是必备标签，会自动一起打）")
        self.act_review = QAction("审核待定标签", self)
        self.act_review.triggered.connect(self.open_review)
        tb.addAction(self.act_review)
        act("收录到图库", self.import_to_library,
            "把选中的图片移动到同盘的专用图库目录（源文件随之消失，扫描不会重复）")
        act("查重 / 保留选择", self.open_duplicates, "检测重复与近似图片，选一张保留，其余隔离或删除")
        tb.addSeparator()
        act("合并为系列", self.make_series, "把选中图片合并成一个系列文件夹")
        act("写回文件名", self.write_back, "把标签写入文件名/系列文件夹名")
        act("停止", self.stop_task, "停止当前任务")

        # 更多工具：低频/可选流程收进菜单，避免工具栏太长
        more = QToolButton()
        more.setText("更多 ▾")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        for text, slot, tip in (
                ("添加扫描来源文件夹…", self.add_folder, "把一个文件夹加入为扫描来源（配合「导入图片」使用）"),
                ("重新扫描（库+来源）", self.rescan, "重新扫描全部目录，导入新增文件"),
                (None, None, None),
                ("分级识别（单独补跑）", self.run_rating, "自动打标已包含分级；这里用于只补分级"),
                ("区域精修打标", self.run_region_refine, "用你框选过的区域提升这些标签的准确率"),
                ("CLIP 重打分", self.run_rescore, "标签改动后，用已缓存的图片特征重新打分"),
                ("人脸检测 / 人物聚类", self.run_face, "真人照片专用"),
                (None, None, None),
                ("查找相似图片", self.find_similar, "以选中图片（或它的某个框）在库里找相似图"),
                ("导出框选标注（YOLO）", self.export_regions, "导出成数据集，可用于训练检测器"),
                ("导出 SD 字幕（.txt，给 kohya/WebUI 训练用）", self.export_captions_ui,
                 "给选中的图导出同名 .txt 字幕（逗号分隔，只含已生效标签 + 可选分级）")):
            if text is None:
                menu.addSeparator()
                continue
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            menu.addAction(a)
        more.setMenu(menu)
        tb.addWidget(more)
        tb.addSeparator()
        act("标签管理", self.open_tag_manager)
        act("类型管理", self.open_categories, "新增/改名/删除标签类型，并给类型配 CLIP 提示词模板")
        act("标签体系（图谱）", self.open_taxonomy, "分类节点 + 标签的关系图：多分类、连线、自动排列")
        act("人物管理", self.open_persons)
        act("模型管理", self.open_models)
        act("设置", self.open_settings)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy(), spacer.sizePolicy().verticalPolicy())
        tb.addWidget(spacer)
        act("打开数据目录", lambda: os.startfile(str(self.store.db_path.parent)))

        # 性能挡位快捷切换（工具栏右侧）
        from .. import perf as perf_mod
        tb.addWidget(QLabel("  性能"))
        self.perf_combo = QComboBox()
        for key in perf_mod.ORDER:
            self.perf_combo.addItem(perf_mod.PRESETS[key]["label"], key)
        idx = self.perf_combo.findData(self.settings.perf_mode)
        self.perf_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self.perf_combo.setToolTip("榨干硬件 / 均衡 / 节能 / 只用 CPU —— 切换后立即生效（会重载模型）")
        self.perf_combo.currentIndexChanged.connect(self.on_perf_changed)
        tb.addWidget(self.perf_combo)

        # ---------------- 左侧 ----------------
        dock = QDockWidget("库 / 标签筛选", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.left_tabs = QTabWidget()
        # 筛选
        w_filter = QWidget()
        fl = QVBoxLayout(w_filter)
        fl.setContentsMargins(6, 6, 6, 6)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("文件名搜索，或 tag:标签 语法")
        self.search_box.returnPressed.connect(self.refresh_files)
        self.search_box.textChanged.connect(lambda _t: self._schedule_refresh())
        fl.addWidget(self.search_box)
        self.filter_tree = QTreeWidget()
        self.filter_tree.setHeaderHidden(True)
        self.filter_tree.itemChanged.connect(self.on_filter_changed)
        fl.addWidget(self.filter_tree, 1)
        row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["同时满足 (AND)", "满足任一 (OR)"])
        self.mode_combo.currentIndexChanged.connect(self.refresh_files)
        row.addWidget(self.mode_combo)
        b_clear = QPushButton("清空筛选")
        b_clear.clicked.connect(self.clear_filters)
        row.addWidget(b_clear)
        fl.addLayout(row)
        self.only_unlabeled = QCheckBox("只看没有标签的")
        self.only_unlabeled.stateChanged.connect(self.refresh_files)
        fl.addWidget(self.only_unlabeled)
        self.only_series = QCheckBox("只看系列")
        self.only_series.stateChanged.connect(self.refresh_files)
        fl.addWidget(self.only_series)
        self.only_pending = QCheckBox("只看待审核的图片")
        self.only_pending.stateChanged.connect(self.refresh_files)
        fl.addWidget(self.only_pending)
        self.inc_pending = QCheckBox("检索时包含待审核标签")
        self.inc_pending.stateChanged.connect(self.refresh_files)
        fl.addWidget(self.inc_pending)
        self.library_only = QCheckBox("只检索图库（不含扫描来源）")
        self.library_only.setChecked(True)
        self.library_only.stateChanged.connect(self.refresh_files)
        fl.addWidget(self.library_only)
        self.use_taxonomy = QCheckBox("按分类体系分组（图谱里定义的）")
        self.use_taxonomy.stateChanged.connect(lambda _s: self.refresh_tags())
        fl.addWidget(self.use_taxonomy)
        row_r = QHBoxLayout()
        row_r.addWidget(QLabel("分级"))
        self.rating_filter = QComboBox()
        self.rating_filter.addItem("全部", "")
        from ..config import RATING_LABELS
        for lv, text in RATING_LABELS.items():
            self.rating_filter.addItem(text, lv)
        self.rating_filter.currentIndexChanged.connect(self.refresh_files)
        row_r.addWidget(self.rating_filter, 1)
        fl.addLayout(row_r)
        self.left_tabs.addTab(w_filter, "标签筛选")
        # 文件夹
        w_dirs = QWidget()
        dl = QVBoxLayout(w_dirs)
        dl.setContentsMargins(6, 6, 6, 6)
        self.dir_tree = QTreeWidget()
        self.dir_tree.setHeaderHidden(True)
        self.dir_tree.itemClicked.connect(self.on_dir_clicked)
        dl.addWidget(self.dir_tree, 1)
        self.left_tabs.addTab(w_dirs, "文件夹")
        dock.setWidget(self.left_tabs)
        dock.setMinimumWidth(280)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.dock_left = dock

        # ---------------- 右侧 ----------------
        dock_r = QDockWidget("标签编辑", self)
        self.tag_panel = TagPanel(self.store, self.library)
        self.tag_panel.changed.connect(self.on_tags_changed)
        self.tag_panel.tagCreated.connect(self.maybe_rescore_for_tags)
        self.tag_panel.requestPreview.connect(self.open_preview_by_id)
        dock_r.setWidget(self.tag_panel)
        dock_r.setMinimumWidth(320)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_r)
        self.dock_right = dock_r

        # ---------------- 中间 ----------------
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(6, 6, 6, 6)
        head = QHBoxLayout()
        self.crumb = QLabel("全部图片")
        self.crumb.setStyleSheet("font-size:13px;color:#9fd0ff;")
        head.addWidget(self.crumb, 1)
        self.b_back = QPushButton("← 返回系列列表")
        self.b_back.clicked.connect(self.exit_special)
        self.b_back.setVisible(False)
        head.addWidget(self.b_back)
        head.addWidget(QLabel("缩略图"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(90, 360)
        self.zoom.setValue(self.settings.thumb_size)
        self.zoom.setFixedWidth(150)
        self.zoom.valueChanged.connect(self.on_zoom)
        head.addWidget(self.zoom)
        cl.addLayout(head)
        self.model = GridModel(self.thumbs, self.settings.thumb_size)
        self.model.blur_r18 = bool(self.settings.rating_blur)
        self.grid = GridView(self.model)
        self.grid.itemActivated.connect(self.on_item_activated)
        self.grid.reorderRequested.connect(self.on_reorder)
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self.update_selection())
        self.grid.customContextMenuRequested.connect(self.grid_menu)
        cl.addWidget(self.grid, 1)
        self.setCentralWidget(central)

        # ---------------- 状态栏 ----------------
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.status_label = QLabel("就绪")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)

    # ================================================== 数据刷新
    def refresh_roots(self) -> None:
        self.dir_tree.clear()
        for r in self.store.list_roots():
            is_lib = bool(r["is_library"])
            prefix = "【图库】" if is_lib else "【来源】"
            top = QTreeWidgetItem([f"{prefix}{r['label'] or Path(r['path']).name}"])
            top.setData(0, Qt.UserRole, ("root", int(r["id"]), r["path"]))
            top.setToolTip(0, r["path"] + ("\n（图库：正式存放，检索只查这里）" if is_lib else "\n（来源：扫描用，收录后可清空）"))
            if is_lib:
                top.setForeground(0, QColor("#7ddc7d"))
            self.dir_tree.addTopLevelItem(top)
            dirs = self._dirs_of_root(int(r["id"]))
            for d in dirs:
                depth = d.count("/")
                node = QTreeWidgetItem([Path(d).name])
                node.setData(0, Qt.UserRole, ("dir", int(r["id"]), d))
                if depth == 1:
                    top.addChild(node)
                else:
                    parent_rel = d.rsplit("/", 1)[0]
                    parent = self._find_item(top, parent_rel)
                    (parent or top).addChild(node)
            top.setExpanded(True)

    def _find_item(self, top: QTreeWidgetItem, rel: str) -> QTreeWidgetItem | None:
        stack = [top]
        while stack:
            it = stack.pop()
            data = it.data(0, Qt.UserRole)
            if data and data[0] == "dir" and data[2] == rel:
                return it
            stack.extend([it.child(i) for i in range(it.childCount())])
        return None

    def _dirs_of_root(self, root_id: int) -> list[str]:
        rows = self.store.query("SELECT DISTINCT rel FROM files WHERE root_id=? AND missing=0", (root_id,))
        dirs: set[str] = set()
        for r in rows:
            p = Path(r["rel"])
            parts = p.parts[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        return sorted(dirs)

    def refresh_tags(self) -> None:
        self.store.refresh_counts()
        checked = set(self.selected_filter_tags())
        self._loading_tree = True
        self.filter_tree.clear()
        rows = self.store.list_tags(only_used=True)
        use_tax = self.use_taxonomy.isChecked() if hasattr(self, "use_taxonomy") else False
        tax_groups = self.store.tag_name_groups() if use_tax else {}
        by_cat: dict[str, list] = {}
        for t in rows:
            cats = tax_groups.get(t["name"]) if use_tax else None
            for cat in (cats or [t["category"]]):
                by_cat.setdefault(cat, []).append(t)
        order = _cat_order(self.store)
        labels = _cat_labels(self.store)
        for cat in order + [c for c in by_cat if c not in order]:
            items = by_cat.get(cat)
            if not items:
                continue
            top = QTreeWidgetItem([f"{labels.get(cat, cat)} ({len(items)})"])
            top.setFlags(top.flags() & ~Qt.ItemIsUserCheckable)
            top.setData(0, Qt.UserRole, ("cat", cat))
            self.filter_tree.addTopLevelItem(top)
            for t in items:
                it = QTreeWidgetItem([f"{t['name']} ({t['count']})"])
                it.setData(0, Qt.UserRole, ("tag", t["name"]))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if t["name"] in checked else Qt.Unchecked)
                if t["category"] == "rating":
                    it.setForeground(0, QColor("#a08bd0"))
                top.addChild(it)
        self._loading_tree = False
        if self.filter_tree.topLevelItemCount() <= 12:
            self.filter_tree.expandAll()

    def _filter_args(self):
        required, any_of = [], []
        for name in self.selected_filter_tags():
            (any_of if self.mode_combo.currentIndex() == 1 else required).append(name)
        text = self.search_box.text().strip()
        return required, any_of, text

    def selected_filter_tags(self) -> list[str]:
        out = []
        for i in range(self.filter_tree.topLevelItemCount()):
            top = self.filter_tree.topLevelItem(i)
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.Checked:
                    data = ch.data(0, Qt.UserRole)
                    if data:
                        out.append(data[1])
        return out

    def on_filter_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if getattr(self, "_loading_tree", False):
            return
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if getattr(self, "_pending", None) is None:
            self._pending = QTimer(self)
            self._pending.setSingleShot(True)
            self._pending.timeout.connect(self.refresh_files)
        self._pending.start(220)

    def clear_filters(self) -> None:
        self._loading_tree = True
        for i in range(self.filter_tree.topLevelItemCount()):
            top = self.filter_tree.topLevelItem(i)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.Unchecked)
        self._loading_tree = False
        self.search_box.clear()
        self.only_unlabeled.setChecked(False)
        self.only_series.setChecked(False)
        self.refresh_files()

    def on_dir_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data[0] == "root":
            self.root_filter, self.dir_filter = data[1], ""
        else:
            self.root_filter, self.dir_filter = data[1], data[2]
        self.current_series = None
        self.refresh_files()

    # ------------------------------------------------ 查询 + 网格
    def refresh_files(self) -> None:
        required, any_of, text = self._filter_args()
        kind = "series_page" if self.only_series.isChecked() else ""
        if self.similar_mode:
            scores: dict[int, float] = self.similar_mode["scores"]
            rows = {int(r["id"]): r for r in self.store.files_by_ids(list(scores.keys()))}
            items = []
            for fid, score in scores.items():
                r = rows.get(int(fid))
                if r is None or r["missing"]:
                    continue
                tags = [t["name"] for t in self.store.tags_for_file(int(fid))]
                items.append(GridItem(kind="image", file_id=int(fid), path=r["path"],
                                      name=f"{r['name']}   {score:.3f}", rel=r["rel"], mtime=r["mtime"] or 0,
                                      tags=tags, manual=bool(r["manual"])))
            self.model.set_items(items)
            self.crumb.setText(f"相似图片（以「{self.similar_mode['label']}」为例）  [{len(items)} 项]")
            self.status_label.setText("勾选真正相似的图片 → 在右侧添加同一个标签，就等于批量标注了")
            QTimer.singleShot(50, self.grid._request_visible)
            return
        if self.current_series:
            srows = self.store.series_files(self.current_series)
            ids = [int(r["id"]) for r in srows]
            tmap = self.store.tags_for_files(ids, statuses=("confirmed", "pending"))
            rmap = self.store.region_counts(ids)
            items = []
            for r in srows:
                fid = int(r["id"])
                tags = [t["name"] for t in tmap.get(fid, [])]
                items.append(GridItem(kind="image", file_id=int(r["id"]), path=r["path"], name=r["name"],
                                      rel=r["rel"], mtime=r["mtime"] or 0, series_id=int(r["series_id"]) if r["series_id"] else None,
                                      tags=tags, manual=bool(r["manual"]),
                                      page_no=int(r["page_no"] or 0), rating=(r["rating"] or ""),
                                      regions=rmap.get(fid, 0)))
            self.model.set_items(items)
            s = self.store.one("SELECT * FROM series WHERE id=?", (self.current_series,))
            self.crumb.setText(f"系列：{s['name']}（{len(items)} 页）" if s else "系列")
            self.b_back.setText("← 返回系列列表")
            self.grid.reorder_enabled = True
            self.status_label.setText(f"{len(items)} 页 · 按页码排序 · 直接拖动缩略图即可改顺序（会自动重命名）")
            return
        self.grid.reorder_enabled = False
        lib_roots = [int(r["id"]) for r in self.store.library_roots()]
        if self.root_filter:
            roots = [self.root_filter]
        elif self.library_only.isChecked() and lib_roots:
            roots = lib_roots
        else:
            roots = []
        rows = self.store.search_files(
            required=required, any_of=any_of, text=text, kind=kind,
            only_unlabeled=self.only_unlabeled.isChecked(),
            root_ids=roots,
            rel_prefix=self.dir_filter,
            include_pending=self.inc_pending.isChecked(),
            only_pending=self.only_pending.isChecked(),
            rating=self.rating_filter.currentData() or "")
        region_counts = self.store.region_counts([int(r["id"]) for r in rows[:5000]])
        items: list[GridItem] = []
        seen_series: dict[int, GridItem] = {}
        # 批量取标签，避免一张图一次查询（大库会卡）
        all_ids = [int(r["id"]) for r in rows]
        conf_map = self.store.tags_for_files(all_ids, statuses=("confirmed",))
        pend_map_t = self.store.tags_for_files(all_ids, statuses=("pending",))
        tag_cache: dict[int, list[str]] = {}
        for r in rows:
            fid = int(r["id"])
            if fid in tag_cache:
                tags = tag_cache[fid]
            else:
                conf = [t["name"] for t in conf_map.get(fid, [])]
                pend = [t["name"] for t in pend_map_t.get(fid, [])]
                tags = conf + [f"?{n}" for n in pend]
                tag_cache[fid] = tags
            sid = int(r["series_id"]) if r["series_id"] else None
            if sid and sid in seen_series:
                it = seen_series[sid]
                it.page_count += 1
                continue
            kind_ = "series" if sid else "image"
            name = r["series_name"] or Path(r["series_dir"]).name if sid else r["name"]
            it = GridItem(kind=kind_, file_id=fid, path=r["path"], name=name,
                          rel=r["rel"], mtime=r["mtime"] or 0, series_id=sid,
                          page_count=1, tags=tags, manual=bool(r["manual"]),
                          cover_path=r["path"] if sid is None else r["path"],
                          regions=region_counts.get(fid, 0),
                          pending=sum(1 for x in tags if x.startswith("?")),
                          rating=(r["rating"] or ""))
            items.append(it)
            if sid:
                seen_series[sid] = it
        self.model.set_items(items)
        self.crumb.setText(self._crumb_text(len(items)))
        self.status_label.setText(f"{len(items)} 项 / 共 {self.store.stats()['files']} 张图")
        self.update_pending_button()
        QTimer.singleShot(50, self.grid._request_visible)

    def update_pending_button(self) -> None:
        s = self.store.pending_summary()
        if hasattr(self, "act_review"):
            self.act_review.setText(f"审核待定标签 ({s['pending_tags']})")

    def _crumb_text(self, n: int) -> str:
        bits = []
        if self.root_filter:
            r = self.store.one("SELECT label,path FROM roots WHERE id=?", (self.root_filter,))
            bits.append(Path(r["path"]).name if r else "")
        if self.dir_filter:
            bits.append(self.dir_filter)
        tags = self.selected_filter_tags()
        if tags:
            bits.append(("+" if self.mode_combo.currentIndex() == 1 else "&").join(tags))
        text = " / ".join([b for b in bits if b]) or "全部图片"
        return f"{text}   [{n} 项]"

    def on_zoom(self, value: int) -> None:
        self.model.icon_size = value
        self.grid.set_icon_size(value)
        self.model.cover_path = ""
        self.grid._request_visible()

    # ------------------------------------------------ 选择
    def selected_ids(self) -> list[int]:
        ids = []
        for idx in self.grid.selectionModel().selectedIndexes():
            it = self.model.item_at(idx)
            if it and it.file_id not in ids:
                ids.append(it.file_id)
        return ids

    def selected_series(self) -> int | None:
        for idx in self.grid.selectionModel().selectedIndexes():
            it = self.model.item_at(idx)
            if it and it.series_id:
                return it.series_id
        return None

    def update_selection(self) -> None:
        ids = self.selected_ids()
        self.tag_panel.set_selection(ids)

    def on_tags_changed(self, file_ids: list) -> None:
        for fid in file_ids:
            tags = [t["name"] for t in self.store.tags_for_file(int(fid))]
            row = self.store.one("SELECT manual FROM files WHERE id=?", (int(fid),))
            self.model.update_file_tags(int(fid), tags, bool(row["manual"]) if row else False)
        self.refresh_tags()

    def filter_tree_restore(self) -> None:
        """保留勾选状态刷新标签树（refresh_tags 已处理，这里留作兼容）。"""
        return

    # ------------------------------------------------ 交互
    def on_item_activated(self, item: GridItem) -> None:
        if item.kind == "series" and item.series_id:
            self.enter_series(item.series_id)
        else:
            self.open_preview(item.path)

    def enter_series(self, series_id: int) -> None:
        self.current_series = series_id
        self.b_back.setVisible(True)
        self.refresh_files()

    def exit_series(self) -> None:
        self.current_series = None
        self.b_back.setVisible(False)
        self.refresh_files()

    def exit_special(self) -> None:
        self.current_series = None
        self.similar_mode = None
        self.b_back.setVisible(False)
        self.refresh_files()

    def on_reorder(self, rows: list, target_row: int) -> None:
        """在系列视图里拖动缩略图 → 按新顺序重排页码并重命名文件。"""
        if not self.current_series:
            QMessageBox.information(self, "调整顺序", "顺序调整只在系列内部可用：\n双击一个系列 → 在里面拖动缩略图。")
            return
        items = self.model.items
        ids = [it.file_id for it in items]
        moving = [ids[r] for r in rows if 0 <= r < len(ids)]
        if not moving:
            return
        rest = [i for i in ids if i not in moving]
        insert_at = target_row - sum(1 for r in rows if r < target_row)
        insert_at = max(0, min(len(rest), insert_at))
        new_order = rest[:insert_at] + moving + rest[insert_at:]
        if new_order == ids:
            return
        res = self.library.reorder_series(int(self.current_series), new_order)
        if not res.get("ok"):
            QMessageBox.warning(self, "重排失败", res.get("msg", "未知错误"))
            return
        self.refresh_files()
        msg = f"已按新顺序重排 {res['count']} 页并重命名（P001…）"
        if res.get("errors"):
            msg += "\n有问题的文件：\n" + "\n".join(res["errors"][:5])
        self.status_label.setText(msg.replace("\n", " "))
        if res.get("errors"):
            QMessageBox.warning(self, "部分文件没改成", msg)

    def open_preview(self, path: str) -> None:
        dlg = PreviewDialog(self.store, path, self)
        dlg.tagsChanged.connect(lambda fid: self.on_tags_changed([fid]))
        dlg.exec()

    def open_preview_by_id(self, file_id: int) -> None:
        row = self.store.one("SELECT path FROM files WHERE id=?", (file_id,))
        if row:
            self.open_preview(row["path"])

    def grid_menu(self, pos) -> None:
        menu = QMenu(self)
        n = len(self.selected_ids())
        a_preview = menu.addAction("预览大图（可框选标注）")
        a_write = menu.addAction("写回文件名")
        a_auto = menu.addAction("自动打标（WD14+CLIP）")
        a_face = menu.addAction("人脸检测")
        menu.addSeparator()
        a_series = menu.addAction("合并为系列…")
        a_new_tag = menu.addAction("添加标签…")
        a_similar = menu.addAction("查找相似图片（以这张为例）")
        a_import = menu.addAction("收录到图库")
        a_dup = menu.addAction("查重 / 保留选择")
        menu.addSeparator()
        a_reveal = menu.addAction("在资源管理器中显示")
        a_copy = menu.addAction("复制文件路径")
        action = menu.exec(self.grid.mapToGlobal(pos))
        if action == a_preview:
            ids = self.selected_ids()
            if ids:
                self.open_preview_by_id(ids[0])
        elif action == a_write:
            self.write_back()
        elif action == a_auto:
            self.run_auto_tag()
        elif action == a_face:
            self.run_face()
        elif action == a_series:
            self.make_series()
        elif action == a_new_tag:
            self.tag_panel.new_tag.setFocus()
            self.dock_right.show()
        elif action == a_similar:
            self.find_similar()
        elif action == a_import:
            self.import_to_library()
        elif action == a_dup:
            self.open_duplicates()
        elif action == a_reveal:
            ids = self.selected_ids()
            if ids:
                row = self.store.one("SELECT path FROM files WHERE id=?", (ids[0],))
                if row:
                    subprocess.Popen(["explorer", "/select,", str(row["path"])])
        elif action == a_copy:
            from PySide6.QtWidgets import QApplication
            ids = self.selected_ids()
            rows = self.store.files_by_ids(ids)
            QApplication.clipboard().setText("\n".join(r["path"] for r in rows))

    # ------------------------------------------------ 任务框架
    def run_task(self, name: str, fn, on_done=None, on_item=None) -> Task | None:
        if self.task is not None and self.task.isRunning():
            QMessageBox.information(self, "有任务在跑", f"「{self.task.name}」还没有结束，请稍候或点“停止”。")
            return None
        t = Task(fn, self, name)
        t.progress.connect(self.on_progress)
        if on_item:
            t.item.connect(on_item)
        t.done.connect(lambda r: self.on_task_done(name, r, on_done))
        t.failed.connect(self.on_task_failed)
        self.task = t
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText(f"{name} …")
        t.start()
        return t

    def on_progress(self, msg: str, frac: float) -> None:
        self.status_label.setText(msg)
        if frac < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(max(0.0, min(1.0, frac)) * 100))

    def on_task_done(self, name: str, result, on_done) -> None:
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_label.setText(f"{name} 完成")
        self.refresh_tags()
        self.update_pending_button()
        if on_done:
            on_done(result)

    def on_task_failed(self, msg: str) -> None:
        self.progress.setVisible(False)
        self.status_label.setText("任务失败")
        QMessageBox.warning(self, "任务失败", msg.split("\n\n")[0][:2000])

    def stop_task(self) -> None:
        if self.task is not None and self.task.isRunning():
            self.task.cancel()
            self.status_label.setText("正在停止…")

    # ------------------------------------------------ 目标选择
    def expand_ids(self, ids: list[int]) -> list[int]:
        out = []
        for fid in ids:
            row = self.store.one("SELECT series_id FROM files WHERE id=?", (fid,))
            if row and row["series_id"]:
                for r in self.store.series_files(int(row["series_id"])):
                    if int(r["id"]) not in out:
                        out.append(int(r["id"]))
            if fid not in out:
                out.append(fid)
        return out

    def target_ids(self) -> list[int]:
        ids = self.selected_ids()
        if ids:
            return self.expand_ids(ids)
        total = self.model.rowCount()
        if total == 0:
            QMessageBox.information(self, "没有图片", "当前列表为空，先添加文件夹或放宽筛选条件。")
            return []
        if QMessageBox.question(self, "确认", f"没有选中图片，是否对当前列表的全部 {total} 项执行？") != QMessageBox.Yes:
            return []
        return self.expand_ids([self.model.items[i].file_id for i in range(total)])

    # ------------------------------------------------ 库
    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not d:
            return
        rid = self.store.add_root(d)
        self.refresh_roots()
        self.scan_root(rid, d)

    def rescan(self) -> None:
        roots = self.store.list_roots()
        if not roots:
            self.add_folder()
            return

        def job(progress, cancel, item):
            total = 0
            for r in roots:
                if cancel():
                    break
                res = self.library.scan_root(int(r["id"]), r["path"], progress, cancel)
                total += res["images"]
            self.library.ensure_builtin_tags()
            return total

        self.run_task("重新扫描", job, on_done=lambda n: (self.refresh_roots(), self.refresh_files(),
                                                          self.status_label.setText(f"扫描完成，共 {n} 张图")))

    def scan_root(self, root_id: int, path: str) -> None:
        def job(progress, cancel, item):
            res = self.library.scan_root(root_id, path, progress, cancel)
            self.library.ensure_builtin_tags()
            return res

        self.run_task("扫描文件夹", job,
                      on_done=lambda res: (self.refresh_roots(), self.refresh_files(),
                                           QMessageBox.information(
                                               self, "扫描完成",
                                               f"图片 {res['images']} 张（新增 {res['added']}，丢失 {res['missing']}）")))

    # ------------------------------------------------ 自动打标
    def on_item_tagged(self, fid: int, tags) -> None:
        conf = [t["name"] for t in self.store.tags_for_file(int(fid))]
        pend = [t["name"] for t in self.store.tags_for_file(int(fid), statuses=("pending",))]
        all_tags = conf + [f"?{n}" for n in pend]
        row = self.store.one("SELECT manual, rating FROM files WHERE id=?", (int(fid),))
        self.model.update_file_tags(int(fid), all_tags, bool(row["manual"]) if row else False,
                                    rating=(row["rating"] if row else ""))

    # ------------------------------------------------ 任务（带断电续跑）
    JOB_NAMES = {"wd14": "WD14 打标", "autotag": "自动打标", "clip": "CLIP 打标",
                 "face": "人脸检测", "region": "区域精修打标", "rescore": "CLIP 重新打分",
                 "rating": "分级识别"}

    def launch_job(self, kind: str, ids: list[int], job_id: int | None = None) -> None:
        if not ids:
            return
        if job_id is None:
            job_id = self.library.start_job(kind, ids, {}, note=self._crumb_text(len(ids)))
        name = self.JOB_NAMES.get(kind, kind)
        ids = list(ids)

        def job(progress, cancel, item):
            hub = self.hub
            try:
                if kind in ("wd14", "autotag") and self.settings.wd14_enabled:
                    self.library.run_wd14(ids, hub, progress, cancel, per_file=item, job_id=job_id)
                if kind in ("clip", "autotag") and self.settings.clip_enabled:
                    self.library.ensure_clip_embeddings(ids, hub, progress, cancel, job_id=job_id)
                    self.library.auto_tags_from_clip(hub, ids, progress, cancel)
                if kind == "region":
                    self.library.ensure_region_embeddings(ids, hub, progress, cancel)
                    self.library.region_refine(ids, hub, progress, cancel, job_id=job_id)
                if kind == "face":
                    self.library.run_face(ids, hub, progress, cancel, per_file=item, job_id=job_id)
                    progress("聚类…", -1.0)
                    self.library.cluster_faces(None, progress)
                if kind == "rescore":
                    self.library.auto_tags_from_clip(hub, ids, progress, cancel)
                if kind == "rating" or (kind == "autotag" and self.settings.rating_enabled):
                    self.library.run_rating(ids, hub, progress, cancel,
                                            job_id=job_id if kind == "rating" else None,
                                            per_file=lambda fid, lvl: item(fid, {"rating": lvl}))
            except Exception:
                self.library.finish_job(job_id, "failed")
                raise
            self.library.finish_job(job_id, "done")
            return True

        def done(_r):
            summary = self.store.pending_summary()
            self.status_label.setText(f"{name} 完成；新增待审核标签 {summary['pending_tags']} 个，别忘了审核")
            self.update_pending_button()
            self.refresh_files()

        self.run_task(name, job, on_done=done, on_item=self.on_item_tagged)

    def run_auto_tag(self) -> None:
        ids = self.target_ids()
        if not ids:
            return
        if not self.settings.wd14_enabled and not self.settings.clip_enabled:
            QMessageBox.information(self, "提示", "WD14 与 CLIP 都被关闭了，请在设置里打开至少一个。")
            return
        self.launch_job("autotag", ids)

    def run_rating(self) -> None:
        if not self.settings.rating_enabled:
            QMessageBox.information(self, "提示", "分级识别已在设置里关闭。")
            return
        ids = self.target_ids()
        if not ids:
            return
        self.launch_job("rating", ids)

    # ------------------------------------------------ 审核 / 续跑
    def open_review(self) -> None:
        if self.store.pending_summary()["pending_tags"] == 0:
            QMessageBox.information(self, "审核", "现在没有待审核的标签。\n（自动打标产生的新标签会先进入待审核队列）")
            return
        dlg = ReviewDialog(self.library, self.hub, self)
        dlg.changed.connect(self.update_pending_button)
        dlg.tagCreated.connect(self.maybe_rescore_for_tags)
        dlg.exec()
        self.update_pending_button()
        self.refresh_tags()
        self.refresh_files()
        self.update_selection()
        if self.settings.auto_import_after_review and self.store.pending_summary()["pending_tags"] == 0:
            self.import_to_library(silent=True)

    def import_to_library(self, silent: bool = False) -> None:
        ids = self.expand_ids(self.selected_ids())
        if not ids:
            if self.model.rowCount() == 0:
                QMessageBox.information(self, "收录到图库", "当前列表是空的。")
                return
            if QMessageBox.question(
                    self, "收录到图库",
                    f"没有选中图片，是否把当前列表的 {self.model.rowCount()} 项全部收录到图库？") != QMessageBox.Yes:
                return
            ids = self.expand_ids([self.model.items[i].file_id for i in range(self.model.rowCount())])
        if not silent:
            if QMessageBox.question(
                    self, "收录到图库",
                    f"将把 {len(ids)} 张图片**移动**到同盘的图库目录"
                    f"（默认 <盘符>:\\{self.settings.library_dir_name}），"
                    "源位置的文件会随之消失，因此下次扫描不会重复。\n\n继续吗？") != QMessageBox.Yes:
                return

        def job(progress, cancel, item):
            return self.library.import_to_library(ids, move=True, progress=progress, cancel=cancel)

        def done(res):
            msg = f"已收录 {res['moved']} 张；重写文件名 {res['renamed']} 个"
            if res["dirs"]:
                msg += "\n图库目录：\n" + "\n".join(res["dirs"])
            if res["errors"]:
                msg += "\n\n有问题的文件：\n" + "\n".join(res["errors"][:8])
            if not silent:
                QMessageBox.information(self, "收录完成", msg)
            self.status_label.setText(msg.splitlines()[0])
            self.refresh_roots()
            self.refresh_files()

        if silent and not self.settings.tag_storage == "filename":
            pass
        self.run_task("收录到图库", job, on_done=done)

    def open_duplicates(self) -> None:
        dlg = DuplicateDialog(self.library, self)
        dlg.changed.connect(lambda: (self.refresh_files(), self.update_pending_button()))
        dlg.exec()
        self.refresh_files()

    def maybe_rescore_for_tags(self, name: str) -> None:
        """新建了「带提示词、且参与自动识别」的标签后，自动全库扫一遍，命中的进待审核队列。"""
        if not name:
            return
        if not self.settings.auto_rescore_on_new_tag:
            self.status_label.setText(f"「{name}」已创建（自动扫描已关闭，可点「更多 → CLIP 重打分」手动扫）")
            return
        if not self.settings.clip_enabled:
            return
        row = self.store.one("SELECT * FROM tags WHERE name=?", (name,))
        if not row or not row["auto"]:
            return
        if not (row["prompt"] or "").strip():
            self.status_label.setText(f"「{name}」已创建。想让它能自动找同类，给它填个英文提示词即可全库扫描")
            return
        total = self.store.stats()["files"]
        if total == 0:
            return
        if total > 20000 and QMessageBox.question(
                self, "全库扫描",
                f"「{name}」将用 CLIP 对全部 {total} 张图重新打分（走缓存特征，通常很快），现在开始？") != QMessageBox.Yes:
            return
        ids = [int(r["id"]) for r in self.store.search_files(limit=200000)]
        self.status_label.setText(f"正在用新标签「{name}」扫描全库…")
        self.launch_job("rescore", ids)

    def open_import(self) -> None:
        """Lightroom 式导入：选源文件夹 → 预览勾选 → 导入到图库（可顺手打标/自动打标）。"""
        dlg = ImportDialog(self.library, self)

        def on_imported(ids: list) -> None:
            self.refresh_roots()
            self.refresh_files()
            self.update_pending_button()
            self.status_label.setText(f"已导入 {len(ids)} 张到图库")
            if dlg.cb_autotag.isChecked() and ids:
                self.launch_job("autotag", [int(i) for i in ids])

        dlg.imported.connect(on_imported)
        dlg.exec()
        self.refresh_roots()
        self.refresh_files()
        self.update_pending_button()

    def check_unfinished(self) -> None:
        jobs = self.store.unfinished_jobs()
        if not jobs:
            return
        lines = [self.library.describe_job(int(j["id"])) for j in jobs[:6]]
        if QMessageBox.question(
                self, "继续上次的任务",
                "上次有任务没有跑完（常见原因是断电/关机中断）：\n\n" + "\n".join(lines) +
                "\n\n现在从中断处继续吗？\n（选「否」就标记为放弃，另外可以在标签里补跑）") == QMessageBox.Yes:
            self.resume_job(int(jobs[0]["id"]))
            if len(jobs) > 1:
                self.status_label.setText(f"还有 {len(jobs) - 1} 个未完成任务，下次启动会再问")
        else:
            for j in jobs:
                self.store.finish_job(int(j["id"]), "cancelled", "用户放弃继续")
            self.status_label.setText("已放弃上次未完成的任务")

    def resume_job(self, job_id: int) -> None:
        job = self.store.job(job_id)
        if not job:
            return
        ids = self.library.job_remaining(job_id)
        if not ids:
            self.library.finish_job(job_id, "done")
            self.status_label.setText("该任务其实已经完成了")
            return
        self.status_label.setText(f"继续 {self.JOB_NAMES.get(job['kind'], job['kind'])}：剩余 {len(ids)} 项")
        self.launch_job(job["kind"], ids, job_id)

    def run_rescore(self) -> None:
        if not self.settings.clip_enabled:
            QMessageBox.information(self, "提示", "CLIP 已关闭。")
            return
        target = self.selected_ids()
        ids = self.expand_ids(target) if target else [int(r["id"]) for r in
                                                     self.store.search_files(limit=100000)]
        self.launch_job("rescore", ids)

    def run_face(self) -> None:
        if not self.settings.face_enabled:
            QMessageBox.information(self, "提示", "人脸功能已关闭，可在设置里打开。")
            return
        ids = self.target_ids()
        if not ids:
            return
        if QMessageBox.question(self, "人脸检测", f"对 {len(ids)} 张图片做人脸检测？\n（真人照片适用；二次元图也会检测但意义有限）") != QMessageBox.Yes:
            return

        self.launch_job("face", ids)

    def run_region_refine(self) -> None:
        ids = self.target_ids()
        if not ids:
            return
        if not self.library._region_probes():
            if QMessageBox.question(
                    self, "还没有框选数据",
                    "现在还没有任何框选标注。\n\n框选标注的作用是告诉模型某个 tag 对应画面哪一块。\n"
                    "要先去大图预览里框选（双击图片 → 打开“框选标注”）吗？") == QMessageBox.Yes:
                QMessageBox.information(self, "怎么框选", "双击一张图片 → 点“框选标注：关”打开 → 右上角选标签 → 在图上拖框。")
            return

        self.launch_job("region", ids)

    def find_similar(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            QMessageBox.information(self, "查找相似图片", "请只选中 1 张图片（可先在预览里框选它的一部分）。")
            return
        fid = ids[0]
        row = self.store.one("SELECT name, clip_vec FROM files WHERE id=?", (fid,))
        if not row or not row["clip_vec"]:
            QMessageBox.information(
                self, "查找相似图片",
                "这张图还没有 CLIP 特征（或 CLIP 被关闭）。\n先对它跑一次「自动打标」，或把目标图片加入已算过特征的图片。")
            return
        regions = self.store.regions_for_file(fid)
        kind, region_id, label = "image", None, row["name"]
        if regions:
            options = ["整张图片"] + [f"框选区域：{r['tag_name']}" for r in regions]
            from PySide6.QtWidgets import QInputDialog
            choice, ok = QInputDialog.getItem(self, "查找相似图片", "以什么为查询？", options, 0, False)
            if not ok:
                return
            if choice != "整张图片":
                idx = options.index(choice) - 1
                if not self.store.one("SELECT clip_vec FROM regions WHERE id=?", (int(regions[idx]["id"]),)) or \
                        not self.store.one("SELECT clip_vec FROM regions WHERE id=?", (int(regions[idx]["id"]),))["clip_vec"]:
                    QMessageBox.information(self, "查找相似图片",
                                            "这个框还没有区域特征，先跑一次「区域精修打标」再检索。")
                    return
                kind, region_id = "region", int(regions[idx]["id"])
                label = f"{row['name']} · {regions[idx]['tag_name']}"

        def job(progress, cancel, item):
            return self.library.find_similar(fid, kind, region_id, 200, progress, cancel)

        def done(res):
            if not res:
                QMessageBox.information(self, "查找相似图片", "没有找到（库里可能只有这一张有特征）。")
                return
            self.similar_mode = {"scores": dict(res), "label": label}
            self.current_series = None
            self.b_back.setVisible(True)
            self.refresh_files()
            QMessageBox.information(
                self, "查找相似图片",
                f"找到 {len(res)} 张相似图片（按相似度排序）。\n\n"
                "接下来：选中真正相似的几张 → 在右侧输入标签（可指定类型）→ 添加。\n"
                "样本累计到 5 个以上后，这个标签会自动变得更稳。")

        self.run_task("相似图片检索", job, on_done=done)

    def export_regions(self) -> None:
        stats = self.store.region_counts()
        if not stats:
            QMessageBox.information(self, "导出框选标注", "还没有任何框选标注。")
            return
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not d:
            return
        res = self.library.export_regions_yolo(d)
        QMessageBox.information(self, "导出完成",
                                f"图片 {res['files']} 张 / 框 {res['boxes']} 个 / 类别 {res['classes']} 个\n{res['dir']}")

    def export_captions_ui(self) -> None:
        """导出 SD 训练字幕：默认和图片放一起（kohya/WebUI 都认同名 .txt）。"""
        ids = self.expand_ids(self.selected_ids())
        if not ids:
            if QMessageBox.question(self, "导出字幕",
                                    f"没有选中图片，是否导出当前列表的 {self.model.rowCount()} 项？") != QMessageBox.Yes:
                return
            ids = [self.model.items[i].file_id for i in range(self.model.rowCount())]
        same_dir = QMessageBox.question(
            self, "导出 SD 字幕",
            f"共 {len(ids)} 张。\n\n"
            "「是」= 每张图旁边生成同名 .txt（kohya/WebUI 最常用）\n"
            "「否」= 选一个目录，全部 .txt 集中放进去") == QMessageBox.Yes
        out_dir = None
        if not same_dir:
            out_dir = QFileDialog.getExistingDirectory(self, "选择字幕导出目录")
            if not out_dir:
                return
        res = self.library.export_captions(ids, out_dir)
        QMessageBox.information(self, "导出完成",
                                f"已写 {res['written']} 个字幕文件"
                                + (f"；{res['empty']} 张没有已生效标签，已跳过" if res["empty"] else "")
                                + "\n\n内容示例：1girl, long_hair, white_apron, R15")

    # ------------------------------------------------ 系列 / 写回
    def make_series(self) -> None:
        ids = self.selected_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "合并为系列", "请先选中 2 张以上图片（可框选、Ctrl 多选、Shift 连选）。")
            return
        files = [self.store.one("SELECT id,name,path FROM files WHERE id=?", (i,)) for i in ids]
        files = [dict(f) for f in files if f]
        first = Path(files[0]["path"])
        default_name = first.parent.name
        # 用出现频率≥50%的标签作为默认标签
        from collections import Counter
        cnt: Counter = Counter()
        for f in files:
            for t in self.store.tags_for_file(int(f["id"])):
                cnt[t["name"]] += 1
        common = [t for t, c in cnt.most_common() if c >= max(2, len(files) // 2)]
        dlg = SeriesDialog(files, self, default_name=default_name, default_tags=" ".join(common[:12]),
                           digits=self.settings.page_digits, mode=self.settings.series_move_mode)
        if dlg.exec() != dlg.Accepted:
            return
        v = dlg.values()
        if v["mode"] == "move" and QMessageBox.question(
                self, "确认移动", "将把原文件移动进系列文件夹（原位置不再保留），继续？") != QMessageBox.Yes:
            return

        def job(progress, cancel, item):
            return self.library.create_series(ids, v["name"], v["tags"], v["order"], v["mode"],
                                              v["digits"], v["start"], v["page_names"], progress)

        def done(res):
            if not res.get("ok"):
                QMessageBox.warning(self, "合并失败", res.get("msg", ""))
            else:
                self.status_label.setText(f"已合并 {res['count']} 页 → {res['dir']}")
            self.refresh_roots()
            self.refresh_files()
            self.refresh_tags()

        self.run_task("合并系列", job, on_done=done)

    def write_back(self) -> None:
        ids = self.selected_ids()
        if not ids:
            if QMessageBox.question(self, "写回文件名", "没有选中图片，是否对当前列表全部图片写回？（可能较慢）") != QMessageBox.Yes:
                return
            ids = [self.model.items[i].file_id for i in range(self.model.rowCount())]
        else:
            ids = self.expand_ids(ids)
        if self.settings.tag_storage != "filename":
            QMessageBox.information(self, "提示", "当前标签存储方式为“仅数据库”，不会改动磁盘文件名。\n如需写入文件名，请在设置里切换。")
            return

        def job(progress, cancel, item):
            return self.library.apply_disk_names(ids, progress)

        def done(res):
            msg = f"已重命名 {res.get('renamed', 0)} 项"
            if res.get("errors"):
                msg += "\n前几条错误：\n" + "\n".join(res["errors"][:6])
            QMessageBox.information(self, "写回文件名", msg)
            self.refresh_roots()
            self.refresh_files()

        self.run_task("写回文件名", job, on_done=done)

    # ------------------------------------------------ 对话框
    def open_tag_manager(self) -> None:
        dlg = TagManagerDialog(self.store, self)
        dlg.rescoreRequested.connect(self.run_rescore)
        dlg.tagCreated.connect(self.maybe_rescore_for_tags)
        dlg.exec()
        self.refresh_tags()
        self.refresh_files()
        self.update_selection()

    def open_taxonomy(self) -> None:
        dlg = TaxonomyDialog(self.library, self)
        dlg.changed.connect(lambda: (self.refresh_tags(), self.update_selection()))
        dlg.tagCreated.connect(self.maybe_rescore_for_tags)
        dlg.exec()
        self.refresh_tags()
        self.refresh_files()
        self.update_selection()

    def open_categories(self) -> None:
        dlg = CategoryManagerDialog(self.store, self)
        dlg.changed.connect(lambda: (self.refresh_tags(), self.update_selection()))
        dlg.exec()
        self.refresh_tags()
        self.update_selection()

    def on_perf_changed(self) -> None:
        """工具栏切性能挡位：立即生效（重载引擎，正在跑的任务会在下个批次用新挡位）。"""
        from .. import perf
        key = self.perf_combo.currentData()
        if not key or key == self.settings.perf_mode:
            return
        self.settings.perf_mode = key
        self.settings.save()
        perf.configure(self.settings)
        self.hub.unload()
        self.status_label.setText(f"性能挡位已切到「{perf.label()}」· {perf.describe()}")

    def open_persons(self) -> None:
        dlg = PersonDialog(self.library, self)
        dlg.changed.connect(lambda: (self.refresh_tags(), self.refresh_files()))
        dlg.exec()
        self.refresh_tags()
        self.refresh_files()

    def open_models(self) -> None:
        ModelsDialog(self.settings, self).exec()
        self.hub.unload()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == dlg.Accepted:
            dlg.apply_to(self.settings)
            self.settings.save()
            from .. import perf
            perf.configure(self.settings)
            self.hub.unload()
            self.model.icon_size = self.settings.thumb_size
            self.grid.set_icon_size(self.settings.thumb_size)
            self.status_label.setText(f"设置已保存 · 性能挡位：{perf.label()}")

    # ------------------------------------------------ 关闭
    def closeEvent(self, event) -> None:
        if self.task is not None and self.task.isRunning():
            if QMessageBox.question(self, "正在运行任务", "有任务在运行，确定退出吗？") != QMessageBox.Yes:
                event.ignore()
                return
            self.task.cancel()
            self.task.wait(3000)
        for key in list(self.settings.__dataclass_fields__):
            pass
        self.settings.save()
        event.accept()
