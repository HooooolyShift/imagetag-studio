"""后台任务：把耗时操作放到 QThread 里，通过信号回报进度。"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal


class Task(QThread):
    """通用的耗时任务。

    传入的函数签名：fn(progress=fn(str,float), cancel=fn()->bool, item=fn(int, object)) -> result
    """

    progress = Signal(str, float)
    item = Signal(int, object)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, parent=None, name: str = ""):
        super().__init__(parent)
        self.fn = fn
        self.name = name
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # pragma: no cover - 线程内运行
        try:
            result = self.fn(progress=self.progress.emit, cancel=self.cancelled, item=self.item.emit)
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{self.name}: {exc}\n{traceback.format_exc()}")
