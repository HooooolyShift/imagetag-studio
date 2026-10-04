"""查重界面里用到的小工具（单独放，避免循环导入）。"""
from __future__ import annotations


def _series_of(store, file_id: int):
    row = store.one("SELECT series_id FROM files WHERE id=?", (file_id,))
    return int(row["series_id"]) if row and row["series_id"] else None
