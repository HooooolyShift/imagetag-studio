"""磁盘操作的小工具（PC 端 UI 与局域网 API 共用，别再各写一份）。

- `roots_paths` / `inside_roots`：**安全护栏**——所有来自移动端的路径都必须落在已登记的
  库/来源根里面，防止一个越界路径把机器上别的东西删了。
- `recycle`：丢进回收站（不真删）。PC 的文件夹删除本来就在 `main_window._recycle` 里，
  这里抽出来给 API 复用。
"""
from __future__ import annotations

import shutil
from pathlib import Path


def roots_paths(store) -> list[Path]:
    return [Path(r["path"]) for r in store.list_roots()]


def inside_roots(path, roots) -> bool:
    """路径是否在某个根里面（同盘比较，大小写不敏感）。"""
    try:
        p = Path(path).resolve()
    except Exception:
        return False
    for r in roots:
        try:
            rp = Path(r).resolve()
        except Exception:
            continue
        try:
            p.relative_to(rp)
            return True
        except ValueError:
            continue
    return False


def recycle(target) -> bool:
    """丢进回收站（Windows）；失败就退化为普通删除（调用方负责只在确认过时用）。"""
    import sys
    if sys.platform != "win32":
        try:
            shutil.rmtree(str(target)) if Path(target).is_dir() else Path(target).unlink()
            return True
        except Exception:
            return False
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
