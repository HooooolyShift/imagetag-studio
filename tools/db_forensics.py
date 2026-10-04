"""数据库取证：只拷主库（跳过 WAL）尝试读取，并把能读的数据抢救到新库。"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

SRC = Path.home() / "AppData/Local/ImageTagStudio"
OUT = SRC / "library_recovered.db"


def try_open(db: Path) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(str(db))
        print("  integrity_check:", con.execute("PRAGMA integrity_check(3)").fetchall())
        return con
    except Exception as e:  # noqa: BLE001
        print("  打开失败:", e)
        return None


def dump(con: sqlite3.Connection) -> None:
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    print("  表:", tables)
    if OUT.exists():
        OUT.unlink()
    dst = sqlite3.connect(str(OUT))
    for name in tables:
        sql_row = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not sql_row or not sql_row[0]:
            continue
        try:
            dst.execute(sql_row[0])
        except Exception:
            continue
        try:
            cols = [d[1] for d in con.execute("PRAGMA table_info(%s)" % name)]
            ph = ",".join("?" * len(cols))
            rows = con.execute("SELECT * FROM %s" % name).fetchall()
            dst.executemany("INSERT OR IGNORE INTO %s VALUES (%s)" % (name, ph), rows)
            print("   %-12s %d 行 ✓" % (name, len(rows)))
        except Exception as e:  # noqa: BLE001
            print("   %-12s 读失败: %s" % (name, e))
    dst.commit()
    dst.close()
    print("  抢救库:", OUT)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="mainonly_"))
    shutil.copy2(SRC / "library.db", work / "library.db")
    print("只拷主库（跳过 WAL）→", work)
    con = try_open(work / "library.db")
    if con:
        dump(con)
        con.close()
    else:
        print("主库单独也读不出，需要按页抢救（sqlite3 .recover）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
