"""体检标签库：重复/别名 + 位置可疑的词条。只读，不改数据。

用法： python tools\\audit_tags.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def norm(s: str) -> str:
    """归一化：去掉标点/空格/大小写差异，用于找"写法不同其实是同一个词"。"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\s_\-·・（）()\[\]【】{}<>《》,，。.;；:：!！?？\"'`]+", "", s)


def main() -> int:
    db = Path(os.environ.get("LOCALAPPDATA", "")) / "ImageTagStudio" / "library.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,name,zh,category,count FROM tags").fetchall()
    by_id = {int(r["id"]): r for r in rows}

    parents: dict[int, list[int]] = {}
    pnode: dict[int, list[str]] = {}
    for e in con.execute("SELECT * FROM taxonomy_edges"):
        if e["child_kind"] != "tag":
            continue
        cid = int(e["child_id"])
        if e["parent_kind"] == "tag":
            parents.setdefault(cid, []).append(int(e["parent_id"]))
        else:
            pnode.setdefault(cid, []).append(str(e["parent_id"]))

    print("=" * 78)
    print("一、疑似重复：中文名相同、但标签名不同")
    print("=" * 78)
    by_zh: dict[str, list] = {}
    for r in rows:
        z = str(r["zh"] or "").strip()
        if z:
            by_zh.setdefault(norm(z), []).append(r)
    n1 = 0
    for key, group in sorted(by_zh.items(), key=lambda kv: -max(x["count"] for x in kv[1])):
        if len(group) < 2:
            continue
        n1 += 1
        print(f"  「{group[0]['zh']}」：")
        for r in group:
            pa = [by_id[i]["name"] for i in parents.get(int(r["id"]), []) if i in by_id]
            print(f"      #{r['id']:<6} {r['name']:<34} [{r['category']}] {r['count']:>4} 张  父={pa}")
    if not n1:
        print("  （无）")

    print()
    print("=" * 78)
    print("二、疑似重复：标签名归一化后相同（大小写/下划线/标点差异）")
    print("=" * 78)
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(norm(r["name"]), []).append(r)
    n2 = 0
    for key, group in by_name.items():
        if len(group) > 1:
            n2 += 1
            print("  " + " ｜ ".join(f"#{r['id']} {r['name']}({r['count']}张)" for r in group))
    if not n2:
        print("  （无）")

    print()
    print("=" * 78)
    print("三、位置可疑：人物标签没有挂到任何作品（作品=category 为 series 的标签）")
    print("=" * 78)
    n3 = 0
    for r in sorted(rows, key=lambda x: -int(x["count"] or 0)):
        if str(r["category"]) != "character":
            continue
        ps = parents.get(int(r["id"]), [])
        has_series = any(str(by_id[p]["category"]) == "series" for p in ps if p in by_id)
        if not has_series and int(r["count"] or 0) > 0:
            n3 += 1
            if n3 <= 40:
                print(f"  #{r['id']:<6} {r['name']:<36} {r['zh'] or '':<12} {r['count']:>4} 张")
    print(f"  合计 {n3} 个（只显示前 40）")

    print()
    print("=" * 78)
    print("四、位置可疑：作品标签挂着父标签 / 作品标签没有子人物")
    print("=" * 78)
    n4 = 0
    for r in rows:
        if str(r["category"]) != "series":
            continue
        ps = parents.get(int(r["id"]), [])
        kids = [c for c, plist in parents.items() if int(r["id"]) in plist
                and c in by_id and str(by_id[c]["category"]) == "character"]
        if ps or not kids:
            n4 += 1
            if n4 <= 40:
                print(f"  #{r['id']:<6} {r['name']:<34} 父={[by_id[p]['name'] for p in ps if p in by_id]} 子人物={len(kids)}")
    print(f"  合计 {n4} 个（只显示前 40）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
