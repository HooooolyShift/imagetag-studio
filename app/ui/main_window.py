"""主窗口：库扫描、缩略图网格、标签筛选/编辑、自动打标、系列合并。"""
from __future__ import annotations

import os
from datetime import datetime
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSlider,
    QTabWidget, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    QDialog, QInputDialog,
)

from ..config import Settings
from ..library import EngineHub, Library
from ..store import Store
from ..workers import Task
from .common import STYLE, ThumbPool, label, ok_cancel
from .dialogs import (CategoryManagerDialog, ModelsDialog, PersonDialog, PreviewDialog, SeriesDialog,
                      SettingsDialog, TagEditDialog, TagManagerDialog, category_combo)
from .taxonomy import TaxonomyDialog
from .review import ReviewDialog
from .dupes import DuplicateDialog
from .import_dialog import ImportDialog
from .grid import GridItem, GridModel, GridView


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
        from .common import attach_tag_completer, bind_category_filter
        attach_tag_completer(self.new_tag, self.store)
        add.addWidget(self.new_tag, 1)
        self.new_cat = category_combo(self.store, "", include_all=True)   # 默认"全部"，可切分类过滤
        add.addWidget(self.new_cat)
        # 分类 ↔ 标签输入框联动：选分类只联想该分类下的标签，选「全部」才全量
        bind_category_filter(self.new_cat, self.new_tag, self.store)
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
        """给选中的图片加标签。类别直接取右边的下拉框，不再多弹一层对话框。"""
        from .. import tag_i18n
        name = tag_i18n.parse_input(self.new_tag.text(), self.store)
        if not name:
            return
        if not self.file_ids:
            QMessageBox.information(self, "提示", "先在中间选择图片，再添加标签。")
            return
        picked = self.new_cat.currentData()
        row = self.store.one("SELECT category FROM tags WHERE name=?", (name,))
        if row is not None:
            cat = row["category"]
        elif picked:                       # 已经在下拉框选好类别 → 直接用它建标签
            cat = self.new_cat.ensure_current(self)
            self.store.save_tag(name, cat)
            self.tagCreated.emit(name)
        else:                              # 停在「全部（不限分类）」→ 不知道类型，才弹窗问
            cat = self.new_cat.ensure_current(self)
            dlg = TagEditDialog(self, name, cat, title="新建标签（可指定类型）", store=self.store)
            if dlg.exec() != dlg.Accepted:
                return
            v = dlg.values()
            name = v["name"]
            if not name:
                return
            self.store.save_tag(name, v["category"], v["prompt"], v["auto"], v.get("requires", ""))
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


class DirTree(QTreeWidget):
    """左侧库/文件夹树：额外支持把网格里选中的图片拖进来 → 移动到该文件夹。"""

    filesDropped = Signal(object, list)          # (目标节点的 UserRole 数据, [绝对路径])

    def __init__(self, parent=None):
        super().__init__(parent)
        # 关键：树默认 dragDropMode = NoDragDrop，等于**不接收任何拖放** →
        # 从图库里拖图片过来时鼠标一直是"禁止"符号。这里显式打开"只接收"模式。
        from PySide6.QtWidgets import QAbstractItemView as _AIV
        self.setAcceptDrops(True)
        self.setDragDropMode(_AIV.DropOnly)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    def _accepts(self, mime) -> bool:
        return bool(mime.hasUrls()) or mime.hasFormat("application/x-imtag-rows")

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            item = self.itemAt(event.position().toPoint())
            self.setCurrentItem(item)
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not self._accepts(event.mimeData()):
            super().dropEvent(event)
            return
        item = self.itemAt(event.position().toPoint())
        data = item.data(0, Qt.UserRole) if item else None
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if data and paths:
            self.filesDropped.emit(data, paths)
            event.acceptProposedAction()
            return
        event.ignore()


class MainWindow(QMainWindow):
    def __init__(self, store: Store, settings: Settings):
        super().__init__()
        self.store = store
        self.settings = settings
        self._blank_start = True        # 启动时图库页留空，等用户选条件再加载（见 refresh_files）
        self.library = Library(store, settings)
        self.hub = EngineHub(settings)
        self.thumbs = ThumbPool(size=max(320, settings.thumb_size * 2))
        self.task: Task | None = None
        self.current_series: int | None = None
        self.similar_mode: dict | None = None
        self.root_filter: int | None = None
        self.dir_filter: str = ""
        from ..config import VERSION
        # 标题里带上"这份代码的构建时间"：出问题时一眼就能看出跑的是不是最新代码
        # （以前排查"改了没生效"，先得猜用户到底在跑哪一版）
        try:
            _built = datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%m-%d %H:%M")
        except Exception:
            _built = "?"
        self.setWindowTitle(f"图片标签工坊  v{VERSION}  —  {store.db_path.parent}　[代码 {_built}]")
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
        act("调整系列顺序…", self.open_series_order, "缩略图拖动排序，保存后按新顺序重命名页码")
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
                 "给选中的图导出同名 .txt 字幕（逗号分隔，只含已生效标签 + 可选分级）"),
                (None, None, None),
                ("按规则生成标签从属关系", self.build_hierarchy_ui,
                 "根据「颜色+基础类」等规则自动建立 tag→tag 从属（白裙子→裙子），之后图片界面只显示更具体的标签"),
                ("批量删除标签…", self.bulk_delete_tags_ui,
                 "按标签名批量删除（默认只删索引，可勾选同时从文件名里去掉）"),
                ("导出学习包…", self.export_pack_ui,
                 "把汉化词典、分类体系、从属关系和自训练探针导出，供另一个库/另一台机器导入"),
                ("导入学习包…", self.import_pack_ui,
                 "导入别人（或你自己另一台机器）的学习包并合并——A 库训练出的判断 B 库直接可用"),
                ("清空模型反馈（重新学）", self.clear_feedback_ui,
                 "清掉自训练探针、审核里的否决记录和已审标记，回到干净状态重新积累；"
                 "已确认的标签、类型、连线都不动。中文名错译导致误判时用这个"),
                ("清理失效目录/文件", self.cleanup_missing_ui,
                 "删掉的文件夹/库不再留在界面里：失效的库记录删除，已不存在的文件标记为缺失")):
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
        # 「标签管理」已并入图谱页面的左侧标签页，这里不再单独列出
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
        row_fold = QHBoxLayout()
        for text, slot in (("全部展开", lambda: self.filter_tree.expandAll()),
                           ("全部折叠", lambda: self.filter_tree.collapseAll()),
                           ("只看有内容的", self.fold_empty_groups)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row_fold.addWidget(b)
        fl.addLayout(row_fold)
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
        self.dir_tree = DirTree()
        self.dir_tree.setHeaderHidden(True)
        self.dir_tree.itemClicked.connect(self.on_dir_clicked)
        self.dir_tree.filesDropped.connect(self.move_files_to)
        self.dir_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.dir_tree.customContextMenuRequested.connect(self.dir_menu)
        self.dir_tree.itemDoubleClicked.connect(lambda _i, _c: None)
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
        # 备用开关：设置窗口里那个勾选框在某些缩放/DPI 下点不动（命中偏移），
        # 这里放一个工具栏上的同款开关，随时能拨、立即生效。
        self.blur_switch = QCheckBox("R18打码")
        self.blur_switch.setChecked(bool(self.settings.rating_blur))
        self.blur_switch.setToolTip("勾上后：R18/R18G 的缩略图用高斯模糊遮住，右下角留分级角标\n"
                                    "（和「设置 → R18 打码」是同一个开关，拨动立即生效）")
        self.blur_switch.toggled.connect(self.on_blur_toggled)
        head.addWidget(self.blur_switch)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(90, 360)
        self.zoom.setValue(self.settings.thumb_size)
        self.zoom.setFixedWidth(150)
        self.zoom.valueChanged.connect(self.on_zoom)
        head.addWidget(self.zoom)
        cl.addLayout(head)
        # 地址栏：像资源管理器那样显示「盘符 › 文件夹 › 子文件夹」，每段可点，点哪跳哪
        self.crumb_row = QWidget()
        crumb_lay = QHBoxLayout(self.crumb_row)
        crumb_lay.setContentsMargins(0, 0, 0, 0)
        crumb_lay.setSpacing(4)
        self.crumb_host = QWidget()
        self.crumb_lay = QHBoxLayout(self.crumb_host)
        self.crumb_lay.setContentsMargins(0, 0, 0, 0)
        self.crumb_lay.setSpacing(2)
        crumb_lay.addWidget(self.crumb_host, 1)
        self.b_newdir = QPushButton("新建文件夹")
        self.b_newdir.setToolTip("在当前文件夹里新建一个子文件夹")
        self.b_newdir.clicked.connect(lambda: self.new_folder())
        crumb_lay.addWidget(self.b_newdir)
        self.b_explorer = QPushButton("打开所在文件夹")
        self.b_explorer.setToolTip("在 Windows 资源管理器里打开当前文件夹")
        self.b_explorer.clicked.connect(self.open_in_explorer)
        crumb_lay.addWidget(self.b_explorer)
        self.b_up = QPushButton("↑ 上一级")
        self.b_up.setToolTip("回到上一层文件夹")
        self.b_up.clicked.connect(self.go_up_one_level)
        crumb_lay.addWidget(self.b_up)
        cl.addWidget(self.crumb_row)
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
            exists = Path(r["path"]).exists()
            if not exists:
                prefix = "【已失效】" + prefix       # 目录被删了要显眼标出来，且不再列出子目录
            top = QTreeWidgetItem([f"{prefix}{r['label'] or Path(r['path']).name}"])
            top.setData(0, Qt.UserRole, ("root", int(r["id"]), r["path"]))
            top.setToolTip(0, r["path"] + ("\n（图库：正式存放，检索只查这里）" if is_lib else "\n（来源：扫描用，收录后可清空）"))
            if is_lib:
                top.setForeground(0, QColor("#7ddc7d"))
            if not exists:
                top.setForeground(0, QColor("#ff8a8a"))
            self.dir_tree.addTopLevelItem(top)
            if not exists:
                continue
            dirs = self._dirs_of_root(int(r["id"]))
            for d in dirs:
                # 注意：depth 是"斜杠个数"，**直接子目录是 0**。
                # 以前写的是 depth == 1，于是所有二级目录都被挂到了根上（父子关系丢失、看着是平的）。
                depth = d.count("/")
                node = QTreeWidgetItem([Path(d).name])
                node.setData(0, Qt.UserRole, ("dir", int(r["id"]), d))
                if depth == 0:
                    top.addChild(node)
                else:
                    parent_rel = d.rsplit("/", 1)[0]
                    parent = self._find_item(top, parent_rel)
                    (parent or top).addChild(node)
            # 有子目录的节点自动显示小三角（图库/来源一视同仁），并按需展开
            for i in range(self.dir_tree.topLevelItemCount()):
                it = self.dir_tree.topLevelItem(i)
                if it.childCount():
                    it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            top.setExpanded(True)
        self._update_breadcrumb()

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
        # 光靠库内 rel 会漏掉"刚建好、还没放图片"的空文件夹 —— 磁盘上真实的目录也要列出来
        try:
            root_row = self.store.one("SELECT path FROM roots WHERE id=?", (root_id,))
            base = Path(root_row["path"]) if root_row else None
            # 只走浅层（2 层）就够了，实测整个图库目录 0.05 秒；
            # 但目录多的时候（上千个）仍然要 7 秒，所以结果缓存 10 秒；
            # 新建/改名/删除文件夹的地方会主动清缓存，保证操作完立刻能看到
            import time as _t
            cache = getattr(self, "_dir_disk_cache", None)
            if cache is None:
                cache = self._dir_disk_cache = {}
            hit = cache.get(root_id)
            if hit and _t.time() - hit[0] < 10:
                dirs.update(hit[1])
                return sorted(dirs)
            if base and base.exists():
                found: set[str] = set()
                stack = [(base, 0)]
                while stack and len(dirs) < 3000:
                    cur, depth = stack.pop()
                    if depth >= 2:
                        continue
                    try:
                        for child in cur.iterdir():
                            if not child.is_dir() or child.name.startswith((".", "$")):
                                continue
                            found.add(child.relative_to(base).as_posix())
                            stack.append((child, depth + 1))
                    except Exception:
                        continue
                dirs.update(found)
                cache[root_id] = (_t.time(), found)
        except Exception:
            pass
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
                from .. import tag_i18n
                label = tag_i18n.display(t["name"], t["zh"] or "")
                it = QTreeWidgetItem([f"{label} ({t['count']})"])
                it.setData(0, Qt.UserRole, ("tag", t["name"]))
                it.setToolTip(0, t["name"])
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

    def fold_empty_groups(self) -> None:
        """折叠没有内容的分类组（只展开有标签的那些）。"""
        for i in range(self.filter_tree.topLevelItemCount()):
            top = self.filter_tree.topLevelItem(i)
            top.setExpanded(top.childCount() > 0)

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
        self._update_breadcrumb()

    # ------------------------------------------------ 查询 + 网格
    def refresh_files(self) -> None:
        required, any_of, text = self._filter_args()
        # 启动时不自动列出全部图片：左侧没选任何条件就保持空白（点标签/文件夹/搜索后才加载）
        if getattr(self, "_blank_start", False):
            if not required and not any_of and not text and self.root_filter is None \
                    and self.current_series is None:
                self.model.set_items([])
                self.status_label.setText("请先在左侧选择标签、文件夹，或直接搜索 —— 避免一开就加载几千张")
                return
            self._blank_start = False
        try:      # 库里标记过 R18 的标签名（图谱页可改），图库气泡据此染粉
            self.model.r18_names = {str(r["name"]) for r in self.store.query(
                "SELECT name FROM tags WHERE COALESCE(r18,0)=1")}
            self.model.r18_forced_off = {str(r["name"]) for r in self.store.query(
                "SELECT name FROM tags WHERE COALESCE(r18,0)=-1")}
        except Exception:
            self.model.r18_names = set()
            self.model.r18_forced_off = set()
        # R18 打码：除了已生效的分级，**待审**的 R18/R18G 也要遮住
        # （打标完还没审核时也得挡，否则保护性设置形同虚设）
        try:
            rows_rating = self.store.query(
                "SELECT DISTINCT ft.file_id AS fid, t.name AS nm FROM file_tags ft "
                "JOIN tags t ON t.id=ft.tag_id "
                "WHERE (ft.status='pending' OR ft.status='confirmed') AND t.category='rating'")
            blur_ids = set()
            for r in rows_rating:
                nm = str(r["nm"]).lower().replace("-", "").replace("_", "")
                if "r18" in nm:
                    blur_ids.add(int(r["fid"]))
            self.model.blur_ids = blur_ids
        except Exception:
            self.model.blur_ids = set()
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
        # 筛选时把父标签展开成"它 + 所有子标签"（筛裙子能带出白裙子/黑裙子）
        if required:
            required = [x for x in self.store.expand_tag_names(required)]
        if any_of:
            any_of = [x for x in self.store.expand_tag_names(any_of)]
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
        child_map = self.store.tag_child_map()      # 父→子，用于"只显示最具体的标签"
        tag_cache: dict[int, list[str]] = {}
        for r in rows:
            fid = int(r["id"])
            if fid in tag_cache:
                tags = tag_cache[fid]
            else:
                conf = self.library.most_specific_tags([t["name"] for t in conf_map.get(fid, [])], child_map)
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

    def open_series_order(self, series_id: int | None = None) -> None:
        """打开"调整系列顺序"对话框：缩略图拖动排序，保存后按新顺序重命名页码。"""
        sid = int(series_id or self.current_series or 0)
        if not sid:
            # 没在系列里就按选中的图找它的系列
            ids = self.selected_ids()
            if ids:
                r = self.store.one("SELECT series_id FROM files WHERE id=?", (int(ids[0]),))
                sid = int(r["series_id"] or 0) if r else 0
        if not sid:
            QMessageBox.information(self, "调整系列顺序", "先进入某个系列（或选中系列里的图）。")
            return
        from .dialogs import SeriesOrderDialog
        dlg = SeriesOrderDialog(self.store, sid, self)
        if dlg.exec() != dlg.Accepted:
            return
        res = self.library.set_series_order(sid, dlg.ordered_ids())
        if res.get("ok"):
            self.status_label.setText(f"系列顺序已保存（重排 {res['count']} 页）")
        else:
            QMessageBox.warning(self, "保存失败", res.get("msg", ""))
        self.refresh_roots()
        self.refresh_files()

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
        a_series_order = menu.addAction("调整系列顺序…")
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
        elif action == a_series_order:
            self.open_series_order()
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

        job_ids = list(ids)          # 本次任务处理的图片，done 回调里刷系列要用

        def done(_r):
            summary = self.store.pending_summary()
            self.status_label.setText(f"{name} 完成；新增待审核标签 {summary['pending_tags']} 个，别忘了审核")
            self.update_pending_button()
            self.refresh_files()
            # 打标跑完：这些图若属于某个系列，重新汇总系列标签并刷新系列文件夹名
            try:
                n = self.library.refresh_series_for_files(job_ids)
                if n:
                    self.status_label.setText(self.status_label.text() +
                                              f"；已更新 {n} 个系列的标签与文件夹名")
                    self.refresh_roots()
            except Exception:
                pass

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
        # 审核完也可能给系列图新增了标签 → 重新汇总系列标签并刷新文件夹名
        try:
            n = self.library.refresh_series_for_files(
                [int(r["id"]) for r in self.store.query(
                    "SELECT DISTINCT file_id AS id FROM file_tags WHERE status='confirmed'")])
            if n:
                self.status_label.setText(f"已更新 {n} 个系列的标签与文件夹名")
                self.refresh_roots()
        except Exception:
            pass
        if self.settings.auto_import_after_review:
            # 原来要求"整个待审队列清空"才自动入库 —— 队列里只要还剩别的图就永远不触发，
            # 这就是"我审核完了却没自动入库"的原因。改成：审核一结束，把**所有已审核通过、
            # 但还没进图库**的图收进去（同盘是改名操作，很快）。
            try:
                ids = [int(r["id"]) for r in self.store.query(
                    # 判据必须是 files.reviewed=1（你在审核台点了「审核完毕」的那批）：
                    # 用"有已生效标签"会把文件名读回来的标签也算进去 → 没审的图也被入库（上次就是这么错的）
                    "SELECT DISTINCT f.id FROM files f "
                    "WHERE f.missing=0 AND COALESCE(f.reviewed,0)=1 "
                    "AND f.root_id NOT IN (SELECT id FROM roots WHERE is_library=1)")]
                if ids:
                    res = self.library.import_to_library(
                        ids, progress=None, subdir=str(getattr(self.settings, "import_subdir", "") or ""))
                    self.status_label.setText(f"已自动收录 {res.get('moved', 0)} 张到图库")
                    self.refresh_roots()
                    self.refresh_files()
            except Exception as exc:
                self.status_label.setText(f"自动收录失败：{exc}")

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
            return self.library.import_to_library(
                ids, move=True, progress=progress, cancel=cancel,
                subdir=str(getattr(self.settings, "import_subdir", "") or ""))

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

    def cleanup_missing_ui(self) -> None:
        """清理失效路径：删掉的文件夹/库立刻从界面消失（修复"路径不消失"的 bug）。"""
        def job(progress, cancel, item):
            return self.library.cleanup_missing(progress)

        def done(res):
            msg = f"已移除失效库目录 {len(res['roots_removed'])} 个，标记缺失文件 {res['files_missing']} 个"
            if res["roots_removed"]:
                msg += "\n\n移除的目录：\n" + "\n".join(res["roots_removed"][:6])
            QMessageBox.information(self, "清理失效路径", msg)
            self.refresh_roots()
            self.refresh_files()
            self.refresh_tags()

        self.run_task("清理失效路径", job, on_done=done)

    def build_hierarchy_ui(self) -> None:
        """按规则生成 tag→tag 从属关系（白裙子→裙子 这类），并重建图谱。"""
        def job(progress, cancel, item):
            return self.library.build_tag_hierarchy(progress)

        def done(res):
            self.status_label.setText(f"已生成 {res['links']} 条标签从属关系"
                                      f"（图片界面现在只显示更具体的标签）")
            self.refresh_files()

        self.run_task("生成标签从属关系", job, on_done=done)

    def clear_feedback_ui(self) -> None:
        """清空模型反馈（探针 + 否决记录 + 已审标记）—— 中文名错译导致误判后用它重来。"""
        n_probe = self.store.one("SELECT COUNT(*) c FROM tag_probe")["c"]
        n_rej = self.store.one("SELECT COUNT(*) c FROM file_tags WHERE status='rejected'")["c"]
        n_rev = self.store.one("SELECT COUNT(*) c FROM files WHERE reviewed=1")["c"]
        if QMessageBox.question(
                self, "清空模型反馈",
                f"将清掉：\n· 自训练探针 {n_probe} 条\n· 审核否决记录 {n_rej} 条\n"
                f"· 已审标记 {n_rev} 张\n\n"
                "已确认的标签、标签类型、图谱连线、以及你画的框都不动。\n"
                "清理后会重新积累反馈，确认继续？") != QMessageBox.Yes:
            return
        res = self.library.clear_model_feedback()
        self.status_label.setText(
            f"已清空模型反馈：探针 {res['before']['probes']}→{res['after']['probes']}，"
            f"否决 {res['before']['rejected']}→{res['after']['rejected']}，"
            f"已审标记 {res['before']['reviewed']}→{res['after']['reviewed']}")

    def bulk_delete_tags_ui(self) -> None:
        """批量删除标签（默认只删索引；可勾选同时从文件名里去掉）。"""
        from .. import tag_i18n
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "批量删除标签",
            "要删除的标签名（多个用空格分隔）：\n\n只删库里的标签与其连线，图片文件不动；\n"
            "勾选下面两项可以同时把它们从文件名里去掉。")
        if not ok or not text.strip():
            return
        names = tag_i18n.parse_list(text)
        dlg = QDialog(self)
        dlg.setWindowTitle("删除选项")
        lay = QVBoxLayout(dlg)
        cb_scope = QCheckBox("只处理当前筛选出的图片（不勾=整个库）")
        cb_scope.setChecked(bool(self.selected_ids()))
        cb_name = QCheckBox("同时从文件名里去掉这些标签（会重命名文件）")
        lay.addWidget(cb_scope)
        lay.addWidget(cb_name)
        ok_cancel(dlg, lay)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = None
        if cb_scope.isChecked():
            ids = self.expand_ids(self.selected_ids()) or [
                self.model.items[i].file_id for i in range(self.model.rowCount())]

        def job(progress, cancel, item):
            return self.library.delete_tags_bulk(names, ids, cb_name.isChecked(), progress)

        def done(res):
            QMessageBox.information(self, "批量删除完成",
                                    f"删除标签 {res['tags']} 个，涉及图片 {res['files']} 张，"
                                    f"重命名文件 {res['renamed']} 个")
            self.refresh_tags()
            self.refresh_files()

        self.run_task("批量删除标签", job, on_done=done)

    def export_pack_ui(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "导出学习包", "ImageTagStudio_学习包.json",
                                           "JSON (*.json)")
        if not p:
            return
        res = self.library.export_learning_pack(p)
        QMessageBox.information(self, "导出完成",
                                f"标签 {res['tags']} 个 / 体系连线 {res['edges']} 条 / "
                                f"自训练探针 {res['probes']} 个\n\n{res['file']}\n\n"
                                "把这个文件拷到另一台机器/另一个库，用「导入学习包」合并即可。")

    def import_pack_ui(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "导入学习包", "", "JSON (*.json)")
        if not p:
            return
        if QMessageBox.question(self, "导入学习包",
                                "导入会**合并**（不覆盖本地已有的中文名/探针）：\n"
                                "· 补上别人审核训练出的标签特征中心与分类器\n"
                                "· 补上汉化名、从属关系、类型\n\n继续？") != QMessageBox.Yes:
            return
        res = self.library.import_learning_pack(p)
        QMessageBox.information(self, "导入完成",
                                f"补中文名 {res['tags_zh']} 个 / 从属关系 {res['sub_edges']} 条 / "
                                f"探针 {res['probes']} 个")
        self.refresh_tags()
        self.refresh_files()

    # ------------------------------------------------ 文件夹操作（像资源管理器那样）
    def go_up_one_level(self) -> None:
        """地址栏的「上一级」。"""
        if self.root_filter is None or not self.dir_filter:
            return
        parts = [p for p in Path(self.dir_filter).parts][:-1]
        self.dir_filter = "/".join(parts)
        self.current_series = None
        self.refresh_files()
        self._update_breadcrumb()

    def move_files_to(self, data, paths) -> None:
        """把拖过来的图片移动进某个库/文件夹（拖到左树松手就走这里）。"""
        import shutil
        from PySide6.QtWidgets import QApplication
        dest = self._dir_abs(data)
        if dest is None:
            return
        if not dest.exists():
            QMessageBox.warning(self, "移动图片", f"目标文件夹不在了：\n{dest}")
            return
        plan: list[tuple[Path, Path, bool]] = []      # (源, 目标, 是否改过名)
        skipped = 0
        # 拖的是"系列"时要把整组都带上：以前只拿到封面那一个文件，于是只移动了一页
        expanded: list[str] = []
        for p in paths:
            expanded.append(p)
            try:
                row = self.store.one("SELECT id,series_id FROM files WHERE path=?", (str(p),))
                if row and row["series_id"]:
                    for f in self.store.series_files(int(row["series_id"])):
                        if str(f["path"]) not in expanded:
                            expanded.append(str(f["path"]))
            except Exception:
                pass
        paths = expanded
        for p in paths:
            src = Path(p)
            if not src.exists() or src.parent == dest:
                skipped += 1
                continue
            dst, renamed = dest / src.name, False
            n = 2
            while dst.exists():
                dst = dest / f"{src.stem}_{n}{src.suffix}"
                renamed, n = True, n + 1
            plan.append((src, dst, renamed))
        if not plan:
            self.status_label.setText(f"没有需要移动的图片（跳过 {skipped} 张）")
            return
        if QMessageBox.question(
                self, "移动图片",
                f"把 {len(plan)} 张图片移动到：\n{dest}\n\n"
                + (f"· 其中 {sum(1 for _s, _d, r in plan if r)} 张因为重名会自动改名\n" if any(r for _s, _d, r in plan) else "")
                + (f"· 跳过 {skipped} 张（已在目标目录或文件不存在）\n" if skipped else "")
                + "· 库内索引会跟着更新，不用重新扫描") != QMessageBox.Yes:
            return
        ok = failed = 0
        self.progress.setVisible(True)
        self.progress.setRange(0, len(plan))
        for i, (src, dst, _r) in enumerate(plan, 1):
            try:
                shutil.move(str(src), str(dst))
                self.library.reindex_after_move(src, dst)
                ok += 1
            except Exception:
                failed += 1
            self.progress.setValue(i)
            QApplication.processEvents()
        self.progress.setVisible(False)
        self.status_label.setText(
            f"已移动 {ok} 张到「{dest.name}」"
            + (f"，改名 {sum(1 for _s, _d, r in plan if r)} 张" if any(r for _s, _d, r in plan) else "")
            + (f"，失败 {failed} 张" if failed else "")
            + (f"，跳过 {skipped} 张" if skipped else ""))
        self.refresh_roots()
        self.refresh_files()

    def _dir_abs(self, data) -> Path | None:
        """把树节点（"root"/"dir"）换算成磁盘上的绝对路径。"""
        if not data:
            return None
        row = self.store.one("SELECT path FROM roots WHERE id=?", (int(data[1]),))
        if not row:
            return None
        base = Path(row["path"])
        return base / data[2] if data[0] == "dir" else base

    def _current_dir_abs(self) -> Path | None:
        """当前网格所在目录（没选就返回 None）。"""
        if self.root_filter is None:
            return None
        row = self.store.one("SELECT path FROM roots WHERE id=?", (int(self.root_filter),))
        if not row:
            return None
        base = Path(row["path"])
        return base / self.dir_filter if self.dir_filter else base

    @staticmethod
    def _recycle(target: Path) -> bool:
        """丢进回收站（不直接删）。"""
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x0040, 0x0010, 0x0004
        op = SHFILEOPSTRUCTW(None, FO_DELETE, str(target) + "\0\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT, False, None, None)
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0

    def _select_dir(self, target: Path) -> None:
        """在左树里选中某个绝对路径对应的节点。"""
        want = str(target).lower()
        stack = [self.dir_tree.topLevelItem(i) for i in range(self.dir_tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            p = self._dir_abs(it.data(0, Qt.UserRole))
            if p is not None and str(p).lower() == want:
                self.dir_tree.setCurrentItem(it)
                self.dir_tree.scrollToItem(it)
                return
            stack.extend([it.child(i) for i in range(it.childCount())])

    def new_folder(self, base: Path | None = None) -> None:
        """在当前文件夹下新建一个子文件夹（真实建在磁盘上）。"""
        from PySide6.QtWidgets import QInputDialog
        base = base or self._current_dir_abs()
        if base is None:
            QMessageBox.information(self, "新建文件夹", "先在左边选一个库或文件夹，再新建。")
            return
        name, ok = QInputDialog.getText(self, "新建文件夹", f"在「{base.name}」下新建文件夹：")
        name = (name or "").strip().strip("\\/")
        if not ok or not name:
            return
        target = base / name
        try:
            target.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            QMessageBox.warning(self, "新建文件夹", f"「{name}」已经存在了。")
            return
        except Exception as exc:
            QMessageBox.warning(self, "新建文件夹", f"建不出来：{exc}")
            return
        self.status_label.setText(f"已新建文件夹：{target}")
        self._dir_disk_cache = {}          # 让左树马上出现这个空文件夹（缓存立刻失效）
        self.refresh_roots()
        self._select_dir(target)

    def rename_folder(self, data) -> None:
        """重命名文件夹（磁盘改名 + 库内路径同步）。"""
        from PySide6.QtWidgets import QInputDialog
        src = self._dir_abs(data)
        if src is None or data[0] != "dir":
            return
        name, ok = QInputDialog.getText(self, "重命名文件夹", "新名称：", text=src.name)
        name = (name or "").strip().strip("\\/")
        if not ok or not name or name == src.name:
            return
        dst = src.parent / name
        if dst.exists():
            QMessageBox.warning(self, "重命名", f"「{name}」已经存在了。")
            return
        try:
            src.rename(dst)
        except Exception as exc:
            QMessageBox.warning(self, "重命名", f"改不了：{exc}")
            return
        self.library.reindex_after_move(src, dst)      # 库内路径跟着改，不用重新扫描
        self.status_label.setText(f"已重命名：{src.name} → {name}")
        self.refresh_roots()
        self.refresh_files()

    def delete_folder(self, data) -> None:
        """删除文件夹：连同里面的图片一起丢进回收站（可还原），并清掉库内索引。"""
        target = self._dir_abs(data)
        if target is None:
            return
        n = self.store.one(
            "SELECT COUNT(*) c FROM files WHERE path LIKE ? AND missing=0",
            (str(target) + os.sep + "%",))["c"]
        if QMessageBox.question(
                self, "删除文件夹",
                f"把整个文件夹丢进回收站吗？\n\n{target}\n\n"
                f"· 里面有 {n} 张已索引的图片，会一起进回收站（**可以还原**）\n"
                f"· 库里的索引会一并清掉") != QMessageBox.Yes:
            return
        if not self._recycle(target):
            QMessageBox.warning(self, "删除文件夹", "回收站操作失败，文件没有改动。")
            return
        self.store.execute("UPDATE files SET missing=1 WHERE path LIKE ?",
                           (str(target) + os.sep + "%",))
        self.status_label.setText(f"已把 {target.name} 丢进回收站（可还原）")
        self.refresh_roots()
        self.refresh_files()

    def open_in_explorer(self, target: Path | None = None) -> None:
        """在 Windows 资源管理器里打开当前（或指定）文件夹。"""
        import subprocess
        p = target or self._current_dir_abs()
        if p is None:
            QMessageBox.information(self, "打开文件夹", "先在左边选一个库或文件夹。")
            return
        if not p.exists():
            QMessageBox.warning(self, "打开文件夹", f"这个路径不在了：\n{p}")
            return
        try:
            subprocess.Popen(["explorer", str(p)])
        except Exception as exc:
            QMessageBox.warning(self, "打开文件夹", f"打不开：{exc}")

    def _update_breadcrumb(self) -> None:
        """刷新地址栏：盘符 › 文件夹 › 子文件夹，每段可点。"""
        from PySide6.QtWidgets import QPushButton, QLabel
        while self.crumb_lay.count():
            w = self.crumb_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        cur = self._current_dir_abs()
        if cur is None:
            self.crumb_lay.addWidget(QLabel("（还没选文件夹：点左边任意一个库或文件夹）"))
            self.b_newdir.setEnabled(False)
            self.b_explorer.setEnabled(False)
            return
        self.b_newdir.setEnabled(True)
        self.b_explorer.setEnabled(True)
        row = self.store.one("SELECT id FROM roots WHERE id=?", (int(self.root_filter),))
        base = Path(self.store.one("SELECT path FROM roots WHERE id=?",
                                   (int(self.root_filter),))["path"])
        parts = [p for p in Path(self.dir_filter).parts] if self.dir_filter else []
        segs = [(base.drive or base.name, None)] + [(p, i) for i, p in enumerate(parts)]
        for name, depth in segs:
            btn = QPushButton(str(name))
            btn.setFlat(True)
            btn.setStyleSheet("text-align:left; padding:1px 6px; color:#cfe2ff;")
            if depth is None:
                btn.clicked.connect(lambda _c=False, d=None: self._goto_part(d))
            else:
                btn.clicked.connect(lambda _c=False, d=depth: self._goto_part(d))
            self.crumb_lay.addWidget(btn)
            if depth is not None or len(segs) > 1:
                sep = QLabel("›")
                sep.setStyleSheet("color:#6b7688;")
                self.crumb_lay.addWidget(sep)
        tail = QLabel(f"  （{self.model.rowCount()} 张）")
        tail.setStyleSheet("color:#8f96a3;")
        self.crumb_lay.addWidget(tail)
        self.crumb_lay.addStretch(1)

    def _goto_part(self, depth: int | None) -> None:
        """点地址栏的某一段：跳到那级目录。"""
        row = self.store.one("SELECT path FROM roots WHERE id=?", (int(self.root_filter),))
        if not row:
            return
        base = Path(row["path"])
        if depth is None:
            self.dir_filter = ""
        else:
            parts = [p for p in Path(self.dir_filter).parts][: depth + 1]
            self.dir_filter = "/".join(parts)
        self.current_series = None
        self.refresh_files()
        self._select_dir(base / self.dir_filter if self.dir_filter else base)

    def dir_menu(self, pos) -> None:
        """库/文件夹树右键菜单。"""
        """库/文件夹树右键：移除索引（只删索引，绝不删文件）、清理失效路径。"""
        item = self.dir_tree.itemAt(pos)
        menu = QMenu(self)
        data = item.data(0, Qt.UserRole) if item else None
        # 这几个必须先全部置 None：根节点的分支只会赋值一部分，
        # 下面的 `if act == a_ren:` 一旦读到未定义的名字就会抛 NameError，
        # pythonw 没有控制台 → 异常被吞掉 → 表现就是"右键点了没反应"。
        a_remove = a_new = a_ren = a_del = a_open = None
        if data and data[0] == "root":
            a_remove = menu.addAction(f"移除这个库（只删索引，不删文件）")
            a_new = menu.addAction("在这个库里新建文件夹…")
            a_open = menu.addAction("在资源管理器中打开")
        elif data and data[0] == "dir":
            a_remove = menu.addAction("把这个目录从索引中移除（只删索引，不删文件）")
            a_new = menu.addAction("新建子文件夹…")
            a_ren = menu.addAction("重命名文件夹…")
            a_del = menu.addAction("删除文件夹（丢进回收站，可还原）")
            a_open = menu.addAction("在资源管理器中打开")
            a_mvdir = menu.addAction("移动整个文件夹到…")
        else:
            a_new = a_ren = a_del = a_open = a_mvdir = None
        a_clean = menu.addAction("清理失效目录/文件")
        act = menu.exec(self.dir_tree.mapToGlobal(pos))
        if act is None:
            return
        if act == a_new:
            self.new_folder(self._dir_abs(data))
            return
        if act == a_ren:
            self.rename_folder(data)
            return
        if act == a_del:
            self.delete_folder(data)
            return
        if act == a_open:
            self.open_in_explorer(self._dir_abs(data))
            return
        if a_mvdir is not None and act == a_mvdir:
            self.move_folder_to()
            return
        if act == a_clean:
            self.cleanup_missing_ui()
            return
        if act != a_remove or not data:
            return
        if data[0] == "root":
            row = self.store.one("SELECT path,is_library FROM roots WHERE id=?", (data[1],))
            n = self.store.one("SELECT COUNT(*) c FROM files WHERE root_id=?", (data[1],))["c"]
            tip = "图库目录" if row and row["is_library"] else "扫描来源"
            if QMessageBox.question(
                    self, "移除库",
                    f"要把这个{tip}从库里移除吗？\n\n{data[2]}\n\n"
                    f"· 只删除索引（{n} 条记录），**磁盘上的图片文件一个都不会动**\n"
                    f"· 该库的标签、系列、人脸、框选记录也会一并从库里清掉\n"
                    f"· 之后想恢复，重新「添加文件夹」再扫描即可（文件名里的标签会读回来）") != QMessageBox.Yes:
                return
            try:
                self.store.remove_root(int(data[1]))
            except Exception as exc:                 # 以前这里出错是"静默失败"，看着就像删不掉
                QMessageBox.warning(self, "移除失败", f"移除这个库/来源失败了：\n{exc}")
                return
        else:
            root_id, rel = int(data[1]), data[2]
            if QMessageBox.question(
                    self, "移除目录索引",
                    f"把「{rel}」下的所有图片从索引里移除？\n\n"
                    f"· 只删数据库记录，**磁盘文件不动**\n· 之后可重新扫描恢复") != QMessageBox.Yes:
                return
            try:
                self.store.remove_dir_index(root_id, rel)
            except Exception as exc:
                QMessageBox.warning(self, "移除失败", f"移除这个目录索引失败了：\n{exc}")
                return
        self.refresh_roots()
        self.refresh_tags()
        self.refresh_files()
        self.status_label.setText("已从索引中移除（文件未改动）")

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

    def move_folder_to(self) -> None:
        """把当前选中的整个文件夹移到别处（选一个目标文件夹，整包搬走并同步库内路径）。"""
        import shutil
        src = self._current_dir_abs()
        if src is None or not src.exists():
            QMessageBox.information(self, "移动文件夹", "先在左边选中一个文件夹。")
            return
        if src == Path(src.anchor):
            QMessageBox.information(self, "移动文件夹", "不能移动整个盘符。")
            return
        parent = QFileDialog.getExistingDirectory(self, "选择目标文件夹（把整个文件夹移进去）", str(src.parent))
        if not parent:
            return
        dst = Path(parent) / src.name
        if dst.exists():
            QMessageBox.warning(self, "移动文件夹", f"目标里已经有「{src.name}」了。")
            return
        if QMessageBox.question(self, "移动文件夹",
                                f"把整个文件夹移过去吗？\n\n{src}\n  →  {dst}\n\n"
                                f"· 里面的图片一起搬走\n· 库内路径会自动同步，不用重新扫描") != QMessageBox.Yes:
            return
        try:
            shutil.move(str(src), str(dst))
            self.library.reindex_after_move(src, dst)
            self.status_label.setText(f"已移动文件夹：{src.name} → {dst.parent}")
        except Exception as exc:
            QMessageBox.warning(self, "移动文件夹", f"移不动：{exc}")
            return
        self.refresh_roots()
        self.refresh_files()

    def on_blur_toggled(self, on: bool) -> None:
        """工具栏上的 R18 打码开关：写设置 + 立即刷新（不依赖设置窗口和确定按钮）。"""
        self.settings.rating_blur = bool(on)
        try:
            self.settings.save()                 # 立刻落盘，别等退出
        except Exception:
            pass
        self.apply_settings_live()
        self.status_label.setText("R18 打码：" + ("已开启（R18/R18G 缩略图将模糊处理）" if on else "已关闭"))

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
        # 打开设置窗口时**以磁盘上的 settings.json 为准**，别用内存里那份：
        # 之前主窗口持有的对象如果被别处改成旧副本，就会出现"改完确定、再打开又变回去"的假象。
        from ..config import Settings as _Settings
        fresh = _Settings.load()
        dlg = SettingsDialog(fresh, self)
        dlg.exec()
        # 这里**不再看**对话框的返回值：以前写成 `if exec()==Accepted 才 apply_to+save`，
        # 只要那次比较没成立（点确定/取消/关闭的返回值差异），控件上的改动就整份丢掉 →
        # 表现就是"点了确定打码不生效、要重启"。现在无条件把控件状态落到设置并写盘。
        try:
            dlg.apply_to(fresh)
            fresh.save()
        except Exception:
            pass
        # 不管有没有点「确定」都要以磁盘为准重新载入：
        # 设置窗口现在是"改动即存"，直接关掉窗口也已经写盘了；
        # 如果这里仍抱着旧对象不放，退出时的保存会把刚存的新值整份盖回旧值
        # （日志里出现过 16:51:39 写 False、16:51:40 又被写回 True 就是这个原因）。
        # 关键：**原地**更新主窗口这份对象，不要换成新对象。
        # Library / EngineHub / 缩略图池等都持有同一个引用（self.library.settings 等），
        # 一旦这里换对象，它们就继续看旧值 —— "改了保留原文件名，写回文件名却不改名"就是这么来的。
        back = _Settings.load()
        self.settings.__dict__.clear()
        self.settings.__dict__.update(back.__dict__)
        self.status_label.setText(
            "设置已保存并校验回读：保留原文件名=%s ｜ 只写最具体标签=%s ｜ 缩略图=%dpx ｜ 性能=%s"
            % ("是" if back.rename_keep_original else "否",
               "是" if getattr(back, "tag_most_specific_on_disk", True) else "否",
               int(back.thumb_size), back.perf_mode))
        self.apply_settings_live()

    def apply_settings_live(self) -> None:
        """把「启动时抄下来的那份设置值」重新灌一遍 —— 开关一改就立刻生效，不用关窗口、更不用重启。"""
        from .. import perf
        try:
            perf.configure(self.settings)
        except Exception:
            pass
        try:
            self.model.blur_r18 = bool(self.settings.rating_blur)     # R18 打码：立刻生效
            self.model.icon_size = int(self.settings.thumb_size)
            self.thumbs.size = max(320, int(self.settings.thumb_size) * 2)
            self.zoom.setValue(int(self.settings.thumb_size))
            self.grid.set_icon_size(int(self.settings.thumb_size))
        except Exception:
            pass
        # 打码名单（哪些图要遮）在这里自己重算一遍，不依赖 refresh_files——
        # 一来避免整表重建导致滚动位置跳动，二来 refresh_files 里任何异常都会让上面的
        # try 整段被吞掉，表现就是"点了确定没反应、要重启才生效"。
        try:
            rows_rating = self.store.query(
                "SELECT DISTINCT ft.file_id AS fid, t.name AS nm FROM file_tags ft "
                "JOIN tags t ON t.id=ft.tag_id "
                "WHERE (ft.status='pending' OR ft.status='confirmed') AND t.category='rating'")
            self.model.blur_ids = {int(r["fid"]) for r in rows_rating
                                   if "r18" in str(r["nm"]).lower().replace("-", "").replace("_", "")}
        except Exception:
            pass
        try:
            self.grid.viewport().update()
            self.grid.repaint()
        except Exception:
            pass
        # 留一行日志：以后"点了确定到底有没有触发刷新"不用再猜
        try:
            from ..config import data_dir
            import time as _t
            with open(data_dir() / "blur.log", "a", encoding="utf-8") as fh:
                fh.write("%s  apply_settings_live: 打码开关=%s ｜ 打码名单=%d 张 ｜ 网格条目=%d\n" % (
                    _t.strftime("%Y-%m-%d %H:%M:%S"), bool(self.settings.rating_blur),
                    len(self.model.blur_ids), self.model.rowCount()))
        except Exception:
            pass

    # ------------------------------------------------ 关闭
    def closeEvent(self, event) -> None:
        if self.task is not None and self.task.isRunning():
            if QMessageBox.question(self, "正在运行任务", "有任务在运行，确定退出吗？") != QMessageBox.Yes:
                event.ignore()
                return
            self.task.cancel()
            self.task.wait(3000)
        # 这里**不再**保存设置：设置窗口已经是"改动即存"，而主窗口内存里这份一旦是旧的，
        # 退出时保存就会把用户刚改的值整份盖回去（日志里 16:51:39 写 False、16:51:40 又写回 True 就是它）。
        # 关窗口 = 真正退出（以前会缩到托盘继续活着，于是"我明明重启过了"其实还是旧进程）
        try:
            from PySide6.QtWidgets import QApplication, QSystemTrayIcon
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QSystemTrayIcon):
                    w.hide()
            QApplication.quit()
        except Exception:
            pass
        event.accept()
