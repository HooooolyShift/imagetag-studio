"""建立"平行关联"：同一个角色的不同形态（能天使 ↔ 新约能天使、斯卡蒂 ↔ 浊心斯卡蒂）。

用法：
    python tools\\link_parallel_tags.py            # 干跑
    python tools\\link_parallel_tags.py --apply    # 真连线（自动备份数据库）

规则：人物标签 `<base>_<修饰>`（或 `<base>_<修饰>_(作品)`）能找到本体 `<base>` 时，
建一条 relation='parallel' 的无向边，并给关联度（异格/换装高、换衣中）：
  · the_xxx（异格，如 the_corrupting_heart）→ 0.9
  · elite_ii / alter / 1st_costume / 2nd_costume → 0.8
  · swimsuit / bunny / track / small / maid 等换装 → 0.6
图谱里画成紫色虚线；搜索时会先显示关联度高的那个，同时把低的一起列出来。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

WEIGHTS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"the_.+"), 0.9),                 # 异格（the_xxx）
    (re.compile(r"elite_ii|alter|1st_costume|2nd_costume"), 0.8),
    (re.compile(r"swimsuit|bunny|track|small|maid|new_year|xmas|halloween"), 0.6),
]
# 形态后缀：既支持 `_(swimsuit)`，也支持 `alter` / `the_xxx` 这类写法
MOD = (r"(?:the_.+?|\((?:swimsuit|bunny|track|small|maid|alter|elite_ii|1st_costume|2nd_costume"
       r"|new_year|xmas|halloween)\)|alter|elite_ii|1st_costume|2nd_costume)")
PAT = re.compile(rf"^(?P<base>.+?)_(?P<mod>{MOD})(?P<tail>(?:_\([^()]*\))*)$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from app.store import Store

    store = Store()
    rows = store.query("SELECT id, name FROM tags WHERE category='character'")
    by_name = {str(r["name"]): int(r["id"]) for r in store.query("SELECT id, name FROM tags")}
    plan: list[tuple[int, int, float, str, str]] = []
    for r in rows:
        name = str(r["name"])
        m = PAT.match(name)
        if not m:
            continue
        base, mod, tail = m.group("base"), m.group("mod"), m.group("tail") or ""
        weight = 0.5
        for pat, w in WEIGHTS:
            if pat.search(mod):
                weight = w
                break
        cand = None
        for key in (f"{base}{tail}", base, f"{base}_(arknights)", f"{base}_(blue_archive)"):
            if key in by_name and by_name[key] != int(r["id"]):
                cand = by_name[key]
                break
        if cand is None:                       # 再宽松一点：找以 base 开头、但形态更简单的那个
            opts = [n for n in by_name if n.startswith(base) and by_name[n] != int(r["id"])
                    and len(n) < len(name) and "_(" not in n.split(base, 1)[1][:1]]
            if len(opts) == 1:
                cand = by_name[opts[0]]
        if cand:
            plan.append((int(r["id"]), cand, weight, name, str(store.one("SELECT name FROM tags WHERE id=?", (cand,))["name"])))
    print(f"找到 {len(plan)} 组平行关联：")
    for _a, _b, w, n1, n2 in plan:
        print(f"   {n1:<44} ↔ {n2:<34} 关联度 {w}")
    if args.apply and plan:
        db = store.db_path
        backup = db.with_name(f"library_before_parallel_{time.strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db, backup)
        print("已备份：", backup.name)
        for a, b, w, _n1, _n2 in plan:
            store.link_tag_parallel(a, b, w)
        print(f"已建立 {len(plan)} 条平行关联")
    elif plan:
        print("（干跑，加 --apply 才写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
