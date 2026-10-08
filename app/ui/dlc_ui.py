"""DLC（可选扩展包）管理窗口：启用/停用、安装 zip、配置（如生图输出路径）、卸载。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QFrame, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import dlc as dlc_mod


class DlcManagerDialog(QDialog):
    """已装 DLC 一览 + 安装/卸载 + 配置表单（清单里声明的 settings 自动渲染）。"""

    def __init__(self, settings, parent=None, on_changed=None):
        super().__init__(parent)
        self.settings = settings
        self.on_changed = on_changed
        self.setWindowTitle("扩展包（DLC）")
        self._fit_to_screen()
        v = QVBoxLayout(self)
        tip = QLabel("DLC 是可选安装的功能包（例如 AI 生图那套）。启用后会在工具栏「更多 ▾」里多出对应菜单；"
                     "配置项（如生图输出路径）在这里改，随主程序设置一起保存。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8f96a3;")
        v.addWidget(tip)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "版本", "状态", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.reload_config)
        v.addWidget(self.table, 1)
        self.config_box = QWidget()
        self.config_form = QFormLayout(self.config_box)
        # 配置项是照 DLC 清单自动渲染的，数量只会越来越多（生图那套已经 7 项）。
        # 包一层滚动区，以后再长也顶不破窗口。
        self.config_scroll = QScrollArea()
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setFrameShape(QFrame.NoFrame)
        self.config_scroll.setWidget(self.config_box)
        self.config_scroll.setMaximumHeight(280)
        v.addWidget(self.config_scroll)
        row = QHBoxLayout()
        for text, slot in (("启用 / 停用", self.toggle),
                           ("安装 zip…", self.install),
                           ("打开 DLC 目录", self.open_dir),
                           ("卸载（进回收站）", self.uninstall)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)
        self._editors: dict[str, QLineEdit] = {}
        self.reload()

    # ---------- 列表 ----------
    def _fit_to_screen(self) -> None:
        """按屏幕可用区域定初始尺寸（小屏笔记本也能整个放下）。"""
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        w, h = 760, 460
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() > 0 and avail.height() > 0:
                w = min(w, max(560, avail.width() - 80))
                h = min(h, max(360, avail.height() - 80))
        self.resize(w, h)
        self.setMinimumSize(520, 340)

    def reload(self) -> None:
        self.dlcs = dlc_mod.scan_dlcs()
        self.table.setRowCount(len(self.dlcs))
        for i, d in enumerate(self.dlcs):
            state = "已启用" if dlc_mod.is_enabled(self.settings, d.id) else "已安装（未启用）"
            if not d.ok:
                state = "清单有问题"
            for c, text in enumerate((d.name, d.version or "-", state,
                                      (d.error or d.description)[:90])):
                it = QTableWidgetItem(str(text))
                if c == 0:
                    it.setData(Qt.UserRole, d.id)
                self.table.setItem(i, c, it)
        if self.dlcs and self.table.currentRow() < 0:
            self.table.selectRow(0)
        self.reload_config()

    def _current(self):
        r = self.table.currentRow()
        return self.dlcs[r] if 0 <= r < len(self.dlcs) else None

    # ---------- 配置表单 ----------
    def reload_config(self) -> None:
        while self.config_form.rowCount():
            self.config_form.removeRow(0)
        self._editors.clear()
        d = self._current()
        if d is None:
            return
        cfg = dlc_mod.dlc_config(self.settings, d)
        for item in d.settings:
            key = str(item.get("key") or "")
            if not key:
                continue
            lab = str(item.get("label") or key)
            edit = QLineEdit(str(cfg.get(key, "")))
            if str(item.get("type")) == "dir":
                wrap = QWidget()
                h = QHBoxLayout(wrap)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(edit, 1)
                b = QPushButton("选择…")
                b.clicked.connect(lambda _c=False, e=edit, k=key: self._pick_dir(e, k))
                h.addWidget(b)
                self.config_form.addRow(lab, wrap)
            else:
                self.config_form.addRow(lab, edit)
            edit.editingFinished.connect(lambda k=key, e=edit: self.save_config(k, e.text()))
            self._editors[key] = edit

    def _pick_dir(self, edit: QLineEdit, key: str) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
            self.save_config(key, d)

    def save_config(self, key: str, value: str) -> None:
        d = self._current()
        if d is None or not key:
            return
        cfg = dlc_mod.dlc_config(self.settings, d)
        cfg[key] = value
        dlc_mod.save_dlc_config(self.settings, d.id, cfg)

    # ---------- 操作 ----------
    def toggle(self) -> None:
        d = self._current()
        if d is None or not d.ok:
            QMessageBox.information(self, "扩展包", "先选一个清单正常的 DLC。")
            return
        on = not dlc_mod.is_enabled(self.settings, d.id)
        dlc_mod.set_enabled(self.settings, d.id, on)
        self.reload()
        if self.on_changed:
            self.on_changed()

    def install(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 DLC 压缩包", "", "Zip 包 (*.zip)")
        if not path:
            return
        ok, msg = dlc_mod.install_zip(path)
        QMessageBox.information(self, "安装 DLC", ("安装成功：" + msg) if ok else ("安装失败：" + msg))
        self.reload()
        if self.on_changed:
            self.on_changed()

    def open_dir(self) -> None:
        import os
        os.startfile(str(dlc_mod.dlcs_dir()))

    def uninstall(self) -> None:
        d = self._current()
        if d is None:
            return
        if QMessageBox.question(self, "卸载 DLC",
                                f"把「{d.name}」移入回收站？（可还原；主程序本体不动）") != QMessageBox.Yes:
            return
        ok, msg = dlc_mod.uninstall(d.id, self.settings)
        QMessageBox.information(self, "卸载 DLC", msg)
        self.reload()
        if self.on_changed:
            self.on_changed()
