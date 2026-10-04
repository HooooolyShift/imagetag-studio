"""数据库抢救：在副本上诊断 + 逐表抢救，输出一个新库（绝不改动原文件）。

用法： python tools\\recover_db.py [备份目录]
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path.home() / "AppData/Local/ImageTagStudio")


def main() -> int:
    src_db = SRC / "library.db"
    if not src_db.exists():
        print("找不到", src_db)
        return 1
    work = Path(tempfile.mkdtemp(prefix="dbrec_"))
    for f in ("library.db", "library.db-wal", "library.db-shm"):
        p = SRC / f
        if p.exists():
            shutil.copy2(p, work / f)
    db = work / "library.db"
    print("在副本上检查:", db)
    try:
        con = sqlite3.connect(str(db))
        ok = con.execute("PRAGMA integrity_check(5)").fetchall()
        print("  integrity_check:", ok)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {}
        for t in tables:
            try:
                counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception as e:  # noqa: BLE001
                counts[t] = f"ERR {e}"
        print("  各表行数:", counts)
        con.close()
    except Exception as e:  # noqa: BLE001
        print("  打开/检查失败:", e)

    # 抢救：把能读的行逐表复制到新库
    out = SRC / "library_recovered.db"
    if out.exists():
        out.unlink()
    recovered = {}
    try:
        src = sqlite3.connect(str(db))
        dst = sqlite3.connect(str(out))
        for (name, sql) in src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            try:
                dst.execute(sql)
            except Exception:
                continue
        for (name,) in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            try:
                cols = [d[1] for d in src.execute(f"PRAGMA table_info({name})")]
                ph = ",".join("?" * len(cols))
                rows = src.execute(f"SELECT * FROM {name}").fetchall()
                dst.executemany(f"INSERT OR IGNORE INTO {name} VALUES ({ph})", rows)
                recovered[name] = len(rows)
            except Exception as e:  # noqa: BLE001
                recovered[name] = f"部分失败 {e}"
        dst.commit()
        dst.close()
        src.close()
        print("  抢救结果:", recovered)
        print("  新库:", out)
    except Exception as e:  # noqa: BLE001
        print("  抢救失败:", e)
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
