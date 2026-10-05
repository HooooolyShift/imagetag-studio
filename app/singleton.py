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
        # 拿不到锁：看看原来的持有者是不是还活着。进程已经退了的"僵尸锁"直接接管，
        # 否则会出现"没有窗口却提示重复实例"
        try:
            os.lseek(fd, 0, 0)
            raw = os.read(fd, 32).decode("ascii", "ignore").strip()
            old_pid = int(raw) if raw.isdigit() else 0
        except Exception:
            old_pid = 0
        if old_pid and not _pid_alive(old_pid):
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                os.remove(str(p))            # 清掉僵尸锁再抢一次
            except Exception:
                pass
            return acquire(data_dir)
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


def _pid_alive(pid: int) -> bool:
    """判断那个 PID 是否还在跑（Windows 上用 tasklist，其它平台用 os.kill）。"""
    if pid <= 0:
        return False
    import sys
    try:
        if sys.platform == "win32":
            import subprocess
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=8).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False
