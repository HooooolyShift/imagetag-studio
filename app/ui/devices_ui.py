"""「已连接设备」管理窗口：本机信息 + 局域网里发现到的设备。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..devices import local_ip


class DevicesDialog(QDialog):
    """设备管理：给平板/手机端"选中即连接"用（连接 API 后续接入，这里先做发现与配对码）。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("已连接设备 / 局域网设备")
        self.resize(720, 420)
        v = QVBoxLayout(self)
        info = service.self_info() if service is not None else {
            "name": "本机", "ip": local_ip(), "port": "-", "code": "----"}
        head = QLabel(
            f"本机：<b>{info['name']}</b>　{info['ip']}:{info['port']}　"
            f"配对码 <b style='color:#f59e0b'>{info['code']}</b>")
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)
        tip = QLabel("平板 / 手机端与 PC 在同一局域网时会自动出现在下表；在移动端点选设备即连接。\n"
                     "配对码用于首次连接授权（移动端会让你核对这 4 位数字）。")
        tip.setStyleSheet("color:#8f96a3;")
        v.addWidget(tip)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["设备名", "类型", "地址", "版本"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_refresh = QPushButton("刷新")
        b_refresh.clicked.connect(self.reload)
        row.addWidget(b_refresh)
        b_copy = QPushButton("复制配对码")
        b_copy.clicked.connect(self.copy_code)
        row.addWidget(b_copy)
        b_copy_ip = QPushButton("复制本机地址")
        b_copy_ip.clicked.connect(lambda: QGuiApplication.clipboard().setText(str(info["ip"])))
        row.addWidget(b_copy_ip)
        row.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)
        if service is not None:
            try:
                service.changed.connect(self.reload)
            except Exception:
                pass
        self.reload()

    def reload(self) -> None:
        peers = self.service.peers() if self.service is not None else []
        self.table.setRowCount(len(peers))
        for r, p in enumerate(peers):
            role = {"pc": "PC", "tablet": "平板", "phone": "手机"}.get(str(p.get("role")), str(p.get("role")))
            for c, text in enumerate((p.get("name", "?"), role,
                                      f"{p.get('ip', '')}:{p.get('port', '')}",
                                      p.get("version", ""))):
                self.table.setItem(r, c, QTableWidgetItem(str(text)))

    def copy_code(self) -> None:
        code = self.service.self_info()["code"] if self.service is not None else "----"
        QGuiApplication.clipboard().setText(str(code))
        QMessageBox.information(self, "已复制", f"配对码 {code} 已复制到剪贴板。")
