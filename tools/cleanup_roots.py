"""整理库根：保留主库，移除冗余/空的库根（只删索引，不动图片文件）。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.store import Store  # noqa: E402
from app import singleton    # noqa: E402

HOME = Path(os.environ["LOCALAPPDATA"]) / "ImageTagStudio"
KEEP = str(sys.argv[1]) if len(sys.argv) > 1 else r"K:\pictures"


def show(store: Store, title: str) -> None:
    print(title)
    for r in store.list_roots():
        n = store.one("SELECT COUNT(*) c FROM files WHERE root_id=?", (int(r["id"]),))["c"]
        print("  [%d] %s  文件 %d  library=%s" % (r["id"], r["path"], n, r["is_library"]))


def main() -> int:
    if singleton.is_running(HOME):
        print("检测到「图片标签工坊」正在运行 —— 为避免并发写坏数据库，请先关闭程序再执行本脚本。")
        return 2
    bk = HOME / ("before_roots_" + time.strftime("%Y%m%d_%H%M%S"))
    bk.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(HOME / "library.db"))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    shutil.copy2(HOME / "library.db", bk / "library.db")
    print("已备份:", bk.name)

    s = Store()
    show(s, "处理前库根:")
    removed = 0
    for r in list(s.list_roots()):
        p = str(r["path"])
        n = int(s.one("SELECT COUNT(*) c FROM files WHERE root_id=?", (int(r["id"]),))["c"])
        low, keep = p.lower(), KEEP.lower()
        if low.startswith(keep + os.sep) or (low == "k:\\imagetags" and n == 0):
            s.remove_root(int(r["id"]))
            removed += 1
            print("  移除:", p)
    print("移除", removed, "个")
    show(s, "剩余库根:")
    print("统计:", s.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
