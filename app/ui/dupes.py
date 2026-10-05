"""重复/相似图处理：按组展示，选一张保留，其余移入隔离区或删除；误报可一键反馈。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..workers import Task
from .common import human_size, label, thumb_pixmap
from ._series_helper import _series_of


class DuplicateDialog(QDialog):
    changed = Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.store = library.store
        self.settings = library.settings
        self.groups: list[dict] = []
        self.index = 0
        self.setWindowTitle("重复 / 相似图片处理")
        self.resize(1400, 880)
        self._build()
        self.start_scan()

    # ---------------------------------------------------------------- UI
    def _build(self) -> None:
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = QLabel("准备中…")
        self.title.setStyleSheet("font-size:14px;color:#9fd0ff;")
        top.addWidget(self.title, 1)
        self.cb_clip = QCheckBox("用 CLIP 再兜一层（抓到轻微裁剪/改色的近似图）")
        self.cb_clip.setChecked(self.settings.dup_use_clip)
        top.addWidget(self.cb_clip)
        self.cb_merge = QCheckBox("合并被删图片的标签")
        self.cb_merge.setChecked(True)
        top.addWidget(self.cb_merge)
        b_rescan = QPushButton("重新检测")
        b_rescan.clicked.connect(self.start_scan)
        top.addWidget(b_rescan)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        top.addWidget(b_close)
        v.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.group_list = QListWidget()
        self.group_list.setIconSize(QSize(84, 84))
        self.group_list.setGridSize(QSize(210, 108))
        self.group_list.currentRowChanged.connect(self.on_group_changed)
        split.addWidget(self.group_list)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(label("这一组里的图片（选中你要保留的那张）", "#9fd0ff", True))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["预览", "文件", "大小", "尺寸", "修改时间", "标签"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setIconSize(QSize(96, 96))
        self.table.verticalHeader().setDefaultSectionSize(104)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 330)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 130)
        self.table.itemSelectionChanged.connect(self.on_row_selected)
        cv.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.b_quarantine = QPushButton("保留选中的 → 其余移入隔离区（可恢复）")
        self.b_quarantine.clicked.connect(lambda: self.apply("quarantine"))
        row.addWidget(self.b_quarantine)
        self.b_delete = QPushButton("保留选中的 → 其余永久删除")
        self.b_delete.clicked.connect(lambda: self.apply("delete"))
        row.addWidget(self.b_delete)
        self.b_notdup = QPushButton("不是重复（误报反馈）")
        self.b_notdup.clicked.connect(self.mark_not_dup)
        row.addWidget(self.b_notdup)
        self.b_series = QPushButton("判定为系列（并到同一文件夹）")
        self.b_series.setToolTip("这些图不是重复，而是同一系列的不同页：\n"
                                 "把选中的作为第 1 页，其余按顺序并进同一个系列文件夹")
        self.b_series.clicked.connect(self.judge_series)
        row.addWidget(self.b_series)
        b_skip = QPushButton("跳过这组")
        b_skip.clicked.connect(self.next_group)
        row.addWidget(b_skip)
        cv.addLayout(row)
        split.addWidget(center)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(label("说明", "#9fd0ff", True))
        tip = QLabel(
            "· 检测：感知哈希（缩放/压缩/轻微改动都能抓到）+ CLIP 特征兜底；\n"
            "· 默认选分辨率最高、体积最大的那张作为保留建议，你可以自己改选；\n"
            "· 「隔离区」= 图库目录下的 .removed 文件夹，随时能手动拿回来；\n"
            "· 误报就点「不是重复」，这一对以后不会再提示；\n"
            "· 被删图片的标签与框选区域会自动并入保留的那张。")
        tip.setWordWrap(True)
        rv.addWidget(tip)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        rv.addWidget(self.status)
        rv.addStretch(1)
        split.addWidget(right)
        split.setSizes([300, 900, 320])
        v.addWidget(split, 1)
        self.table.setEnabled(False)

    # ---------------------------------------------------------------- 扫描
    def start_scan(self) -> None:
        self.title.setText("计算图像指纹并查找重复…")
        self.group_list.clear()
        self.table.setRowCount(0)
        self.groups = []
        threshold = self.settings.dup_threshold
        use_clip = self.cb_clip.isChecked()
        roots = [int(r["id"]) for r in self.store.library_roots()]

        def job(progress, cancel, item):
            progress("计算图像指纹…", -1.0)
            self.library.ensure_hashes(None, progress, cancel)
            progress("查找重复…", 0.4)
            return self.library.find_duplicate_groups(threshold=threshold, only_roots=roots,
                                                      use_clip=use_clip, progress=progress, cancel=cancel)

        t = Task(job, self, "重复检测")
        t.progress.connect(lambda msg, frac: self.title.setText(msg))
        t.done.connect(self.on_scanned)
        t.failed.connect(lambda msg: (self.title.setText("检测失败"),
                                      QMessageBox.warning(self, "检测失败", msg[:1500])))
        t.start()
        self._task = t

    def on_scanned(self, groups) -> None:
        self.groups = list(groups)
        self.index = 0
        self.group_list.clear()
        for i, g in enumerate(self.groups):
            cover = g["files"][0]
            it = QListWidgetItem(f"组 {i + 1} · {g['size']} 张 · 差异 {g['max_dist']}/64")
            if g.get("mixed_series"):
                it.setText(it.text() + " · 含系列页")
                it.setForeground(QColor("#ffcc66"))
            pm = self._thumb(cover)
            if pm:
                it.setIcon(QIcon(pm))
            it.setToolTip(Path(cover["path"]).name)
            self.group_list.addItem(it)
        total = sum(g["size"] for g in self.groups)
        self.title.setText(f"发现 {len(self.groups)} 组疑似重复，共 {total} 张图片")
        if self.groups:
            self.group_list.setCurrentRow(0)
        else:
            self.status.setText("没有发现重复图片 🎉")

    # ---------------------------------------------------------------- 展示
    def _thumb(self, row) -> QPixmap | None:
        return thumb_pixmap(int(row["id"]), row["path"], row["mtime"] or 0, 96)

    def on_group_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.groups):
            return
        self.index = row
        files = self.groups[row]["files"]
        self.table.setRowCount(len(files))
        best = max(files, key=lambda f: ((f["width"] or 0) * (f["height"] or 0), f["size"] or 0))
        best_row = 0
        for i, f in enumerate(files):
            it = QTableWidgetItem()
            pm = self._thumb(f)
            if pm:
                it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, int(f["id"]))
            self.table.setItem(i, 0, it)
            name_item = QTableWidgetItem(Path(f["path"]).name)
            name_item.setToolTip(f["path"])
            if int(f["id"]) == int(best["id"]):
                name_item.setText(Path(f["path"]).name + "   ★建议保留")
                name_item.setForeground(QColor("#7ddc7d"))
                best_row = i
            self.table.setItem(i, 1, name_item)
            self.table.setItem(i, 2, QTableWidgetItem(human_size(f["size"])))
            self.table.setItem(i, 3, QTableWidgetItem(f"{f['width'] or '?'}×{f['height'] or '?'}"))
            ts = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M") if f["mtime"] else "?"
            self.table.setItem(i, 4, QTableWidgetItem(ts))
            tags = [t["name"] for t in self.store.tags_for_file(int(f["id"]), statuses=("confirmed",))]
            self.table.setItem(i, 5, QTableWidgetItem(" ".join(tags[:8])))
        self.table.setEnabled(True)
        self.table.selectRow(best_row)
        g = self.groups[self.index]
        extra = ""
        if g.get("mixed_series"):
            n_series = sum(1 for f in files if _series_of(self.store, int(f["id"])))
            extra = (f"　⚠ 其中 {n_series} 张已经在系列里、{len(files) - n_series} 张是单图："
                     f"如果它们其实是同一系列的不同页，用「判定为系列」而不是删掉。")
        self.status.setText(f"第 {self.index + 1}/{len(self.groups)} 组 · {len(files)} 张{extra}")

    def on_row_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.groups[self.index]["files"]):
            return
        f = self.groups[self.index]["files"][row]
        self.status.setText(f"将保留：{Path(f['path']).name}"
                            f"（{f['width'] or '?'}×{f['height'] or '?'}, {human_size(f['size'])}）")

    def keep_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return int(self.groups[self.index]["files"][row]["id"])

    # ---------------------------------------------------------------- 操作
    def apply(self, action: str) -> None:
        if not self.groups:
            return
        g = self.groups[self.index]
        keep = self.keep_id()
        if keep is None:
            QMessageBox.information(self, "重复处理", "先选中要保留的那一张。")
            return
        others = [int(f["id"]) for f in g["files"] if int(f["id"]) != keep]
        if action == "delete":
            if QMessageBox.question(self, "确认删除",
                                    f"将永久删除 {len(others)} 个文件，无法恢复。继续？") != QMessageBox.Yes:
                return
        res = self.library.resolve_duplicate(keep, others, action=action,
                                             merge_tags=self.cb_merge.isChecked())
        if not res.get("ok"):
            QMessageBox.warning(self, "失败", res.get("msg", ""))
            return
        msg = f"已处理：隔离/删除 {res['removed']} 张，合并标签 {res['merged']} 个"
        if res.get("errors"):
            msg += "；错误：" + "; ".join(res["errors"][:3])
        self.status.setText(msg)
        self.changed.emit()
        self.remove_current_group()

    def mark_not_dup(self) -> None:
        if not self.groups:
            return
        g = self.groups[self.index]
        self.library.mark_group_not_duplicate([int(f["id"]) for f in g["files"]])
        self.status.setText("已记录：这一组不是重复，以后不再提示")
        self.remove_current_group()

    def judge_series(self) -> None:
        """这些相似的图其实是同一系列的不同页 → 并进一个系列文件夹（不删文件）。"""
        if not self.groups:
            return
        from .dialogs import SeriesDialog
        g = self.groups[self.index]
        files = [{"id": int(f["id"]), "name": Path(f["path"]).name, "path": f["path"]} for f in g["files"]]
        series_ids = g.get("series_ids") or []
        default_name, default_tags = "", ""
        if len(series_ids) == 1:
            s = self.store.one("SELECT * FROM series WHERE id=?", (int(series_ids[0]),))
            if s:
                default_name = s["name"] or ""
                default_tags = s["tags"] or ""
        if not default_name:
            default_name = Path(files[0]["path"]).parent.name
            from collections import Counter
            cnt: Counter = Counter()
            for f in files:
                for t in self.store.tags_for_file(int(f["id"]), statuses=("confirmed",)):
                    cnt[t["name"]] += 1
            default_tags = " ".join([t for t, c in cnt.most_common() if c >= max(2, len(files) // 2)][:8])
        dlg = SeriesDialog(files, self, default_name=default_name, default_tags=default_tags,
                           digits=self.settings.page_digits, mode="move")
        if dlg.exec() != dlg.Accepted:
            return
        v = dlg.values()
        res = self.library.merge_into_series([int(f["id"]) for f in g["files"]], name=v["name"],
                                             tags=v["tags"], series_id=(series_ids[0] if len(series_ids) == 1 else None),
                                             mode=v["mode"], digits=v["digits"], start=v["start"],
                                             page_names=v["page_names"], order=v["order"])
        if not res.get("ok"):
            QMessageBox.warning(self, "并入系列失败", res.get("msg", ""))
            return
        self.library.mark_group_as_series([int(f["id"]) for f in g["files"]])
        self.status.setText(f"已并入系列：{res['dir']}（移动 {res['moved']} 张，以后不再提示这组）")
        self.changed.emit()
        self.remove_current_group()

    def remove_current_group(self) -> None:
        if not self.groups:
            return
        self.groups.pop(self.index)
        self.group_list.takeItem(self.index)
        self.title.setText(f"还剩 {len(self.groups)} 组疑似重复")
        if self.groups:
            self.group_list.setCurrentRow(min(self.index, len(self.groups) - 1))
        else:
            self.table.setRowCount(0)
            self.status.setText("全部处理完了 🎉")

    def next_group(self) -> None:
        if self.groups:
            self.group_list.setCurrentRow((self.index + 1) % len(self.groups))
