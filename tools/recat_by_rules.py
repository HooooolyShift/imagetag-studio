"""按规则把标签重新归到 20 个类型里（只动"其它"和明显归错的，可反复执行）。

用法：
    python tools\\recat_by_rules.py            # 干跑，看会怎么改
    python tools\\recat_by_rules.py --apply    # 真改（自动备份数据库 + 重建图谱连线）
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def wd14_cats() -> dict[str, int]:
    for p in (HERE / "models" / "wd-swinv2-tagger-v3" / "selected_tags.csv",
              *sorted((HERE / "models").rglob("selected_tags.csv"))):
        if p.exists():
            out = {}
            with open(p, encoding="utf-8", errors="replace") as f:
                for row in csv.DictReader(f):
                    try:
                        out[(row.get("name") or "").strip()] = int(row.get("category") or 0)
                    except ValueError:
                        pass
            if out:
                return out
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from app.engines.wd14 import guess_category
    from app.store import Store

    cats = wd14_cats()
    store = Store()
    plan: list[tuple[int, str, str, str]] = []
    for r in store.query("SELECT id, name, category FROM tags"):
        name, cur = str(r["name"]), str(r["category"] or "other")
        if cur in ("character", "series", "rating"):
            continue          # 人物/作品/分级由词表与从属关系管，规则不插手
        want = guess_category(name.lower(), cats.get(name, 0))
        if want != cur and want != "other":
            plan.append((int(r["id"]), name, cur, want))
    print("规则能纠正的类型变化：", Counter(f"{c}→{w}" for _i, _n, c, w in plan))
    for _i, n, c, w in plan[:15]:
        print(f"   {n:<34} {c} → {w}")
    if args.apply and plan:
        db = store.db_path
        backup = db.with_name(f"library_before_recatrules_{time.strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db, backup)
        print("已备份：", backup.name)
        for tid, _n, _c, w in plan:
            store.update_tag(tid, category=w)
        store.refresh_counts()
        from app.config import Settings
        from app.library import Library
        Library(store, Settings.load()).relink_all_categories()
        print(f"已纠正 {len(plan)} 个标签的类型")
    elif plan:
        print("（干跑，加 --apply 才写入）")
    left = store.one("SELECT COUNT(*) c FROM tags WHERE category='other'")["c"]
    print("剩下的「其它」还有:", left)
    if left:
        print("其中图片数最多的 25 个（继续补规则用）：")
        for r in store.query("SELECT name, COUNT(*) n FROM tags t JOIN file_tags f ON f.tag_id=t.id "
                             "WHERE t.category='other' GROUP BY t.id ORDER BY n DESC LIMIT 25"):
            print(f"   {r['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
