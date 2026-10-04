"""单实例锁：防止两个程序实例（或维护脚本）同时写同一个数据库。

2026-10-04 的数据库损坏事故就是并发写入造成的，这里用文件锁从根上避免：
程序启动时抢占锁；已经有人持锁就提示并退出。维护脚本用 is_running() 自检。
"""
from __future__ import annotations

import atexit
import os
from pathlib import Path

_handle: int | None = None


def lock_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "app.lock"


def _try_lock(fd: int) -> bool:
    try:
        import msvcrt
    except ImportError:          # 非 Windows：退化为不检查
        return True
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def acquire(data_dir: str | Path) -> bool:
    """尝试成为唯一实例；返回 False 表示已有实例在运行。"""
    global _handle
    p = lock_path(data_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    except Exception:
        return True                     # 拿不到锁文件时不阻塞使用
    if not _try_lock(fd):
        try:
            os.close(fd)
        except Exception:
            pass
        return False
    try:
        os.truncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
    except Exception:
        pass
    _handle = fd
    atexit.register(release)
    return True


def release() -> None:
    global _handle
    if _handle is None:
        return
    try:
        import msvcrt
        try:
            os.lseek(_handle, 0, 0)
            msvcrt.locking(_handle, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        os.close(_handle)
    except Exception:
        pass
    _handle = None


def is_running(data_dir: str | Path) -> bool:
    """是否有别的进程正持有锁（维护脚本写库前调用）。"""
    p = lock_path(data_dir)
    if not p.exists():
        return False
    try:
        fd = os.open(str(p), os.O_RDWR)
    except Exception:
        return False
    ok = _try_lock(fd)
    try:
        if ok:
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        os.close(fd)
    except Exception:
        pass
    return not ok
