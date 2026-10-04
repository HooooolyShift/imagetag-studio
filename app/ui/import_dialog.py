"""导入图片（类似 Lightroom 的导入）：选源文件夹 → 预览勾选 → 设选项 → 导入到图库。

导入 = 把选中的图片移动（或复制）进同盘图库目录，可顺手打上一批标签，
完成后可以直接接着跑自动打标（WD14 + CLIP + 分级）。
"""
from __future__ import annotations

import os
import zlib
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from .. import imaging
from ..config import IMAGE_EXTS
from ..workers import Task
from .common import ThumbPool, label
from .dialogs import category_combo


def human(n: int | None) -> str:
    if not n:
        return "?"
    v = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if v < 1024 or u == "GB":
            return f"{v:.0f} {u}" if u == "B" else f"{v:.1f} {u}"
        v /= 1024.0
    return "?"


class ImportDialog(QDialog):
    imported = Signal(list)          # 导入完成的 file_id 列表

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.store = library.store
        self.settings = library.settings
        self.sources: list[str] = []
        self.candidates: list[dict] = []
        self.thumbs = ThumbPool(size=320, workers=4)
        self.thumbs.signals.ready.connect(self.on_thumb)
        self._item_by_key: dict[int, QListWidgetItem] = {}
        self._key_by_item: dict[int, int] = {}       # id(item) -> thumb key
        self._pending_fill: list[dict] = []
        self._targets_cached: dict[str, tuple[str, bool, str]] = {}
        self._scan_cancel = False
        self._importing = False
        self.setWindowTitle("导入图片到图库")
        self.resize(1360, 860)
        self._build()

    # ---------------------------------------------------------------- UI
    def _build(self) -> None:
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        b_add = QPushButton("选择源文件夹…")
        b_add.clicked.connect(self.pick_folder)
        top.addWidget(b_add)
        self.cb_recursive = QCheckBox("包含子文件夹")
        self.cb_recursive.setChecked(True)
        self.cb_recursive.stateChanged.connect(lambda _s: self.scan_sources())
        top.addWidget(self.cb_recursive)
        b_rescan = QPushButton("重新扫描")
        b_rescan.clicked.connect(self.scan_sources)
        top.addWidget(b_rescan)
        top.addStretch(1)
        b_all = QPushButton("全选")
        b_all.clicked.connect(lambda: self.check_all(True))
        top.addWidget(b_all)
        b_none = QPushButton("全不选")
        b_none.clicked.connect(lambda: self.check_all(False))
        top.addWidget(b_none)
        b_inv = QPushButton("反选")
        b_inv.clicked.connect(self.invert)
        top.addWidget(b_inv)
        b_dup = QPushButton("取消勾选重复项")
        b_dup.clicked.connect(self.uncheck_dups)
        top.addWidget(b_dup)
        b_check = QPushButton("检查/创建图库目录")
        b_check.clicked.connect(self.check_targets)
        top.addWidget(b_check)
        self.b_stop = QPushButton("停止扫描")
        self.b_stop.clicked.connect(self.stop_scan)
        self.b_stop.setVisible(False)
        top.addWidget(self.b_stop)
        v.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(132, 132))
        self.list.setGridSize(QSize(150, 186))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemChanged.connect(lambda _i: self.update_summary())
        self.list.verticalScrollBar().valueChanged.connect(self.request_visible_thumbs)
        split.addWidget(self.list)

        right = QWidget()
        rv = QVBoxLayout(right)
        box1 = QGroupBox("导入方式")
        b1 = QVBoxLayout(box1)
        self.mode = QComboBox()
        self.mode.addItems(["移动到图库（推荐，源文件消失，不会重复扫描）", "复制到图库（源文件保留）"])
        b1.addWidget(self.mode)
        self.lbl_target = QLabel("")
        self.lbl_target.setWordWrap(True)
        b1.addWidget(self.lbl_target)
        rv.addWidget(box1)

        box2 = QGroupBox("导入时给这批图片加标签")
        b2 = QVBoxLayout(box2)
        row = QHBoxLayout()
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("例如：2026春 外拍 泳装（空格分隔多个）")
        row.addWidget(self.tag_edit, 1)
        self.tag_cat = category_combo(self.store)
        row.addWidget(self.tag_cat)
        b2.addLayout(row)
        b2.addWidget(QLabel("这些标签会直接生效（等同于手动打的），并写进文件名"))
        rv.addWidget(box2)

        box3 = QGroupBox("导入后")
        b3 = QVBoxLayout(box3)
        self.cb_autotag = QCheckBox("立刻自动打标（WD14 + CLIP + 分级识别，结果进待审核）")
        self.cb_autotag.setChecked(True)
        b3.addWidget(self.cb_autotag)
        self.cb_skip_dup = QCheckBox("跳过疑似重复的图片（与图库里已有的比对）")
        self.cb_skip_dup.setChecked(True)
        b3.addWidget(self.cb_skip_dup)
        self.cb_writeback = QCheckBox("导入时把标签写进文件名")
        self.cb_writeback.setChecked(self.settings.tag_storage == "filename")
        b3.addWidget(self.cb_writeback)
        rv.addWidget(box3)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        rv.addWidget(self.summary)
        rv.addStretch(1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        rv.addWidget(self.bar)
        row_btn = QHBoxLayout()
        self.b_import = QPushButton("开始导入")
        self.b_import.clicked.connect(self.start_import)
        row_btn.addWidget(self.b_import, 1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row_btn.addWidget(b_close)
        rv.addLayout(row_btn)
        split.addWidget(right)
        split.setSizes([980, 360])
        v.addWidget(split, 1)
        self.update_summary()

    # ---------------------------------------------------------------- 扫描
    def pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择要导入的源文件夹")
        if not d:
            return
        if d not in self.sources:
            self.sources.append(d)
        self.scan_sources()

    def scan_sources(self) -> None:
        if not self.sources:
            return
        self.b_import.setEnabled(False)
        self.b_stop.setVisible(True)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        recursive = self.cb_recursive.isChecked()
        roots: list[tuple[int, str]] = []
        for s in self.sources:
            roots.append((self.store.add_root(s), s))
        # 重复预检最多算这么多张，避免超大目录把扫描拖太久
        dup_limit = 5000
        self._scan_cancel = False

        def job(progress, cancel, item):
            def cancelled() -> bool:
                return self._scan_cancel or bool(cancel())
            out = []
            for rid, path in roots:
                if cancelled():
                    break
                progress(f"扫描 {Path(path).name}…", -1.0)
                self.library.scan_root(rid, path, progress, cancelled)
                for r in self.store.search_files(root_ids=[rid], limit=200000):
                    if not recursive and Path(r["path"]).parent != Path(path):
                        continue
                    out.append(dict(r))
            lib_roots = [int(r["id"]) for r in self.store.library_roots()]
            if lib_roots and out:
                progress("计算图库指纹…", -1.0)
                self.library.ensure_hashes(None, progress, cancelled)
                lib_rows = self.store.hashed_files(lib_roots)
                lib_hashes = [(r["phash"] or "") for r in lib_rows if r["phash"]]
                thr = self.settings.dup_threshold
                todo = out[:dup_limit]
                for c in out[dup_limit:]:
                    c["phash"] = ""
                    c["dup"] = False
                for i, c in enumerate(todo):
                    if cancelled():
                        break
                    h = imaging.dhash(c["path"])
                    c["phash"] = h or ""
                    c["dup"] = any(h and self.library._hamming(h, lh) <= thr for lh in lib_hashes)
                    if i % 10 == 0:
                        progress(f"比对重复 {i + 1}/{len(todo)}", (i + 1) / max(1, len(todo)))
            for c in out:
                if "phash" not in c:
                    c["phash"] = ""
                    c["dup"] = False
            return out

        def done(cands):
            self.bar.setVisible(False)
            self.b_import.setEnabled(True)
            self.b_stop.setVisible(False)
            self.candidates = cands
            if len(cands) > dup_limit:
                self.summary.setText(f"共 {len(cands)} 张（重复预检只跑了前 {dup_limit} 张）")
            self.fill_list_chunked()

        self._task = None

        t = Task(job, self, "导入扫描")
        t.progress.connect(lambda msg, frac: (self.summary.setText(msg),
                                              self.bar.setRange(0, 0) if frac < 0 else self.bar.setRange(0, 100)))
        t.done.connect(done)
        t.failed.connect(lambda msg: (self.bar.setVisible(False), self.b_import.setEnabled(True),
                                      self.b_stop.setVisible(False),
                                      QMessageBox.warning(self, "扫描失败", msg[:1200])))
        t.start()
        self._task = t

    def stop_scan(self) -> None:
        self._scan_cancel = True
        self.b_stop.setEnabled(False)
        self.summary.setText("正在停止扫描…")

    def fill_list(self) -> None:
        """一次性建完（小批量用）；大批量请走 fill_list_chunked()。"""
        self._pending_fill = []
        self.list.blockSignals(True)
        self.list.clear()
        self._item_by_key.clear()
        self._key_by_item.clear()
        for c in self.candidates:
            self._append_item(c)
        self.list.blockSignals(False)
        self.update_summary()
        self.request_visible_thumbs()

    def fill_list_chunked(self, chunk: int = 400) -> None:
        """分块填充列表：几千上万张也不会把界面卡住（每块之间让出事件循环）。"""
        self._pending_fill = list(self.candidates)
        self.list.blockSignals(True)
        self.list.clear()
        self._item_by_key.clear()
        self._key_by_item.clear()
        self.list.blockSignals(False)
        self.update_summary()

        def step() -> None:
            if not self._pending_fill:
                self.update_summary()
                self.request_visible_thumbs()
                return
            self.list.blockSignals(True)
            for c in self._pending_fill[:chunk]:
                self._append_item(c)
            self.list.blockSignals(False)
            del self._pending_fill[:chunk]
            self.update_summary()
            QTimer.singleShot(0, step)

        QTimer.singleShot(0, step)

    def _append_item(self, c: dict) -> None:
            name = Path(c["path"]).name
            extra = "  ⚠疑似重复" if c.get("dup") else ""
            it = QListWidgetItem(f"{name}\n{human(c.get('size'))}{extra}")
            it.setData(Qt.UserRole, c)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            checked = not (c.get("dup") and self.cb_skip_dup.isChecked())
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            it.setToolTip(f"{c['path']}")
            if c.get("dup"):
                it.setForeground(QColor("#ffcc66"))
            self.list.addItem(it)
            key = zlib.crc32(str(c["path"]).encode("utf-8")) & 0x7FFFFFFF
            self._item_by_key[key] = it
            self._key_by_item[id(it)] = key

    # ---------------- 缩略图（后台线程生成，只做可见区域） ----------------
    def request_visible_thumbs(self) -> None:
        if self._importing:            # 导入期间不要再读源文件，避免占用导致移动失败
            return
        rows = self._visible_rows(200)
        for r in rows:
            it = self.list.item(r)
            if it is None or it.icon() is not None and not it.icon().isNull():
                continue
            c = it.data(Qt.UserRole)
            if not c:
                continue
            key = zlib.crc32(str(c["path"]).encode("utf-8")) & 0x7FFFFFFF
            self.thumbs.request(key, c["path"], c.get("mtime") or 0)

    def _visible_rows(self, margin: int = 60) -> list[int]:
        n = self.list.count()
        if n == 0:
            return []
        rect = self.list.viewport().rect()
        top = self.list.indexAt(rect.topLeft())
        bottom = self.list.indexAt(rect.bottomRight())
        first = max(0, (top.row() if top.isValid() else 0) - margin)
        last = min(n - 1, (bottom.row() if bottom.isValid() else min(n - 1, 400)) + margin)
        return list(range(first, last + 1))

    def on_thumb(self, key: int, path: str) -> None:
        if not path:
            return
        it = self._item_by_key.get(int(key))
        if it is None:
            return
        pm = QPixmap(path)
        if pm.isNull():
            return
        try:
            it.setIcon(QIcon(pm.scaled(132, 132, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        except RuntimeError:
            pass

    # ---------------------------------------------------------------- 勾选
    def check_all(self, state: bool) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self.list.blockSignals(False)
        self.update_summary()

    def invert(self) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
        self.list.blockSignals(False)
        self.update_summary()

    def uncheck_dups(self) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            c = self.list.item(i).data(Qt.UserRole)
            if c.get("dup"):
                self.list.item(i).setCheckState(Qt.Unchecked)
        self.list.blockSignals(False)
        self.update_summary()

    def selected_candidates(self) -> list[dict]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def _target_status(self, drive: str) -> tuple[str, bool, str]:
        """目标目录状态有缓存，避免每次勾选都去碰磁盘。"""
        d = (drive or "C:").upper()
        if d not in self._targets_cached:
            self._targets_cached[d] = self.library.check_library_dir(d, create=False)
        return self._targets_cached[d]

    def update_summary(self) -> None:
        if self._pending_fill:            # 还在分块填充：只报进度，不做全表统计
            self.summary.setText(f"正在载入列表… 还有 {len(self._pending_fill)} 张")
            return
        sel_n, dups, total = 0, 0, 0
        drives: set[str] = set()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() != Qt.Checked:
                continue
            c = it.data(Qt.UserRole) or {}
            sel_n += 1
            total += c.get("size") or 0
            if c.get("dup"):
                dups += 1
            drives.add(Path(c.get("path", "")).drive or "C:")
        lines = []
        for d in sorted(drives):
            path, ok, msg = self._target_status(d)
            mark = "✓" if ok else "✗"
            lines.append(f"{mark} {path}（{msg}）")
        self.lbl_target.setText("将存入：\n" + "\n".join(lines) if lines
                                else f"将存入：<盘符>\\{self.settings.library_dir_name}")
        self.lbl_target.setStyleSheet("color:#ffcc66;" if any(
            not self._target_status(d)[1] for d in drives) else "")
        self.summary.setText(f"已选 {sel_n} / {self.list.count()} 张，共 {human(total)}"
                             + (f"（其中 {dups} 张疑似重复）" if dups else ""))
        self.b_import.setEnabled(sel_n > 0)

    def check_targets(self) -> None:
        """按当前勾选涉及的盘，检查并（必要时）真的创建图库目录。"""
        drives = sorted({Path(c["path"]).drive or "C:" for c in self.selected_candidates()})
        if not drives:
            QMessageBox.information(self, "目标目录", "先勾选要导入的图片。")
            return
        lines = []
        bad = 0
        for d in drives:
            path, ok, msg = self.library.check_library_dir(d, create=True)
            if not ok:
                bad += 1
            lines.append(f"{'✓' if ok else '✗'} {path} —— {msg}")
        self._targets_cached.clear()
        self.update_summary()
        self.store.refresh_counts()
        QMessageBox.information(
            self, "目标图库目录",
            "\n".join(lines) + ("\n\n不可写的盘：这些图片会跳过，请把源文件放到可写盘，"
                                "或在设置里把「图库目录名」改成该盘下有权限的路径。" if bad else ""))

    # ---------------------------------------------------------------- 导入
    def do_import(self, progress=None, cancel=None) -> dict:
        """真正的导入逻辑（可被测试直接调用）。"""
        self._importing = True
        try:
            self.thumbs.pool.clear()
            self.thumbs.pool.waitForDone(4000)
        except Exception:
            pass
        sel = self.selected_candidates()
        if self.cb_skip_dup.isChecked():
            sel = [c for c in sel if not c.get("dup")]
        if not sel:
            self._importing = False
            return {"ok": False, "msg": "没有可导入的图片"}
        ids = [int(c["id"]) for c in sel]
        move = self.mode.currentIndex() == 0
        res = self.library.import_to_library(ids, move=move, progress=progress, cancel=cancel,
                                             auto_write_names=False)
        tags = [t for t in self.tag_edit.text().replace(",", " ").split() if t]
        if tags:
            cat = self.tag_cat.ensure_current(self)
            for t in tags:
                self.store.ensure_tag(t, cat)
            self.library.add_tags_to_files(ids, tags, "manual")
            for fid in ids:
                self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (fid,))
        if self.cb_writeback.isChecked() and self.settings.tag_storage == "filename":
            res["renamed"] = self.library.apply_disk_names(ids, progress).get("renamed", 0)
        res["ok"] = True
        res["ids"] = ids
        res["tags"] = tags
        self._importing = False
        return res

    def start_import(self) -> None:
        if not self.selected_candidates():
            QMessageBox.information(self, "导入", "先勾选要导入的图片。")
            return
        # 让正在跑的缩略图线程收尾，并停掉新的读取请求（Windows 下文件被占用就搬不动）
        self._importing = True
        try:
            self.thumbs.pool.clear()
            self.thumbs.pool.waitForDone(4000)
        except Exception:
            pass
        self.b_import.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)

        def job(progress, cancel, item):
            return self.do_import(progress, cancel)

        def done(res):
            self.bar.setVisible(False)
            self.b_import.setEnabled(True)
            self._importing = False
            if not res.get("ok"):
                QMessageBox.warning(self, "导入失败", res.get("msg", ""))
                return
            msg = (f"导入 {len(res['ids'])} 张到图库：{', '.join(res.get('dirs', []))}\n"
                   f"移动 {res.get('moved', 0)}，重写文件名 {res.get('renamed', 0)}")
            if res.get("tags"):
                msg += f"\n已加标签：{' '.join(res['tags'])}"
            if res.get("errors"):
                msg += "\n\n有问题的文件：\n" + "\n".join(res["errors"][:6])
            QMessageBox.information(self, "导入完成", msg)
            self.imported.emit(list(res["ids"]))
            self.accept()

        t = Task(job, self, "导入图片")
        t.progress.connect(lambda msg, frac: (self.summary.setText(msg),
                                              self.bar.setRange(0, 0) if frac < 0 else self.bar.setRange(0, 100)))
        t.done.connect(done)
        t.failed.connect(lambda msg: (self.bar.setVisible(False), self.b_import.setEnabled(True),
                                      setattr(self, "_importing", False),
                                      QMessageBox.warning(self, "导入失败", msg[:1200])))
        t.start()
        self._task = t
