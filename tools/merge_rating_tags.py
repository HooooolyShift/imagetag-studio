"""把"同义不同写法"的分级标签合并到规范的四档上（全年龄 / R15 / R18 / R18G）。

背景：从文件名读回来的 `15禁`、`18禁` 这类会在写回文件名时冒充分级标签，
把真正的 `R15`/`R18` 顶掉（首位分级的规则只认规范写法）。

只动数据库：把别名标签名下的图片关联搬到规范标签上，然后删掉别名标签。
用法： python tools\\merge_rating_tags.py [--apply]
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

CANON = {
    "R18G": ("r18g", "r-18g", "r18g（18禁·猎奇）", "18禁猎奇", "r18g猎奇"),
    "R18": ("r18", "r-18", "r18（18禁）", "18禁", "18x", "explicit", "r18向"),
    "R15": ("r15", "r-15", "r15（15禁）", "15禁", "r15向", "sensitive"),
    "全年龄": ("all_ages", "allages", "g级", "g", "健全", "全年龄向", "questionable"),
}


def main() -> int:
    apply = "--apply" in sys.argv
    folder = Path(os.environ.get("LOCALAPPDATA", "")) / "ImageTagStudio"
    db = folder / "library.db"
    if not db.exists():
        print("找不到库:", db)
        return 1
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,name,zh,category,count FROM tags").fetchall()
    by_name = {}
    for r in rows:
        by_name.setdefault(str(r["name"]).strip().lower(), []).append(r)

    plans: list[tuple[str, int, int]] = []       # (别名, 别名id, 规范id)
    for canon, aliases in CANON.items():
        hits = by_name.get(canon.lower())
        if not hits:                              # 规范标签不存在就跳过，不要凭空造
            continue
        cid = int(hits[0]["id"])
        for alias in aliases:
            if alias.lower() == canon.lower():
                continue
            for r in rows:
                if int(r["id"]) == cid:
                    continue
                nm = str(r["name"]).strip().lower()
                zh = str(r["zh"] or "").strip().lower()
                if nm in (alias.lower(), f"{alias.lower()}（{alias.lower()}）") or zh == alias.lower():
                    plans.append((str(r["name"]), int(r["id"]), cid))
    if not plans:
        print("没有需要合并的分级标签")
        return 0
    print("计划合并：")
    for name, src, dst in plans:
        d = con.execute("SELECT name FROM tags WHERE id=?", (dst,)).fetchone()
        n = con.execute("SELECT COUNT(*) c FROM file_tags WHERE tag_id=?", (src,)).fetchone()[0]
        print(f"   {name}  →  {d['name']}   （{n} 条图片关联）")
    if not apply:
        print("（干跑，加 --apply 才写入）")
        return 0

    bak = folder / f"library.db.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(db, bak)
    print("已备份 →", bak.name)
    for _name, src, dst in plans:
        con.execute("INSERT OR IGNORE INTO file_tags(file_id,tag_id,source,score,status,updated_at) "
                    "SELECT file_id,?,source,score,status,updated_at FROM file_tags WHERE tag_id=?",
                    (dst, src))
        con.execute("DELETE FROM file_tags WHERE tag_id=?", (src,))
        con.execute("UPDATE taxonomy_edges SET child_id=? WHERE child_kind='tag' AND child_id=?", (dst, src))
        con.execute("DELETE FROM tags WHERE id=?", (src,))
    con.commit()
    print("合并完成")
    for r in con.execute("SELECT name,count FROM tags WHERE category='rating' ORDER BY count DESC"):
        print("   %-8s %d 张" % (r["name"], r["count"]))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
