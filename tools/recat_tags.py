"""按 WD14 词表自带的人物/分级类别纠正标签类型（权威依据，比猜测准）。

用法：
    python tools\\recat_tags.py            # 干跑，报告会改哪些
    python tools\\recat_tags.py --apply    # 真改（改前自动备份数据库）

WD14 的 selected_tags.csv 里 category=4 是人物名（2751 个）、9 是分级（4 个）。
落在这些名单里的标签，类型直接按词表来；不在名单里的保持原样，交给启发式/人工。
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def load_wd14() -> tuple[set[str], set[str]]:
    for p in (HERE / "models" / "wd-swinv2-tagger-v3" / "selected_tags.csv",
              *sorted((HERE / "models").rglob("selected_tags.csv"))):
        if not p.exists():
            continue
        chars, ratings = set(), set()
        with open(p, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                cat = str(row.get("category") or "").strip()
                if not name:
                    continue
                if cat == "4":
                    chars.add(name)
                elif cat == "9":
                    ratings.add(name)
        if chars:
            return chars, ratings
    return set(), set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from app.store import Store
    chars, ratings = load_wd14()
    print(f"WD14 词表：人物 {len(chars)} 个 / 分级 {len(ratings)} 个")
    store = Store()
    plan: list[tuple[int, str, str, str]] = []
    for r in store.query("SELECT id, name, category FROM tags"):
        name, cat = str(r["name"]), str(r["category"] or "other")
        want = "character" if name in chars else ("rating" if name in ratings else None)
        if want and want != cat:
            plan.append((int(r["id"]), name, cat, want))
    from collections import Counter
    print("待纠正：", Counter(f"{c}→{w}" for _i, _n, c, w in plan))
    for _i, n, c, w in plan[:15]:
        print(f"   {n:<34} {c} → {w}")
    if args.apply and plan:
        db = store.db_path
        backup = db.with_name(f"library_before_recat_{time.strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db, backup)
        print("已备份：", backup.name)
        for tid, _n, _c, w in plan:
            store.update_tag(tid, category=w)
        store.refresh_counts()
        from app.library import Library
        from app.config import Settings
        res = Library(store, Settings.load()).relink_all_categories()   # 图谱连线跟着新类型重建
        print("图谱连线已重建：", res)
        print(f"已纠正 {len(plan)} 个标签的类型；其余「其它」还有 "
              f"{store.one(chr(39).join(['SELECT COUNT(*) c FROM tags WHERE category=', chr(39), 'other', chr(39)]))['c']} 个")
    elif plan:
        print("（干跑，加 --apply 才会写入）")

    # 看看剩下的「其它」都是些什么，方便继续收
    print("\n「其它」里图片数最多的 40 个：")
    for r in store.query("SELECT name, zh, count FROM tags WHERE category='other' "
                         "ORDER BY count DESC LIMIT 40"):
        print(f"   {r['name'][:36]:36} zh={r['zh']!r:16} {r['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
