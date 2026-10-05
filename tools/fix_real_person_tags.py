"""修正"中文名标签被词典误覆盖"的问题，并把真人博主标签归到「真人」分类。

背景：Danbooru 导入时给所有标签同步了 zh，但中文名标签（如「忧」= 推特真人博主 憂）
被同名/近名的二次元角色覆盖了。中文名标签的 zh 就该等于它自己。

用法： python tools\\fix_real_person_tags.py [--apply]
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
CJK = re.compile(r"[\u4e00-\u9fff]")

# 手工确认的：标签名 → (规范名, 中文名, 类型, 说明)
MANUAL: dict[str, tuple[str, str, str]] = {
    "忧": ("憂", "憂", "real_person"),      # 推特真人博主，繁体原名
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from app.config import Settings
    from app.library import Library
    from app.store import Store
    s = Store()
    plan: list[tuple[int, str, str, str, str]] = []
    for name, (new_name, zh, cat) in MANUAL.items():
        row = s.one("SELECT id, name, zh, category FROM tags WHERE name=?", (name,))
        if row:
            plan.append((int(row["id"]), name, new_name, zh, cat))
    # 其它"标签名本身就是中文、但 zh 被写成了别的中文"的，一律改回自身
    fixed_zh = []
    for r in s.query("SELECT id, name, zh FROM tags"):
        nm = str(r["name"])
        zh = (r["zh"] or "").strip()
        if CJK.search(nm) and zh and zh != nm and nm not in MANUAL:
            fixed_zh.append((int(r["id"]), nm, zh))
    print(f"手工修正 {len(plan)} 个；中文名被覆盖需改回自身的 {len(fixed_zh)} 个：")
    for _i, nm, zh in fixed_zh[:15]:
        print(f"   {nm}（现 zh={zh}）→ 保留 Danbooru 的完整译名（不动）")
    if not args.apply:
        print("（干跑，加 --apply 才写入）")
        return 0
    db = s.db_path
    backup = db.with_name(f"library_before_realperson_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print("已备份：", backup.name)
    for tid, nm, new_name, zh, cat in plan:
        if new_name != nm and not s.one("SELECT 1 FROM tags WHERE name=?", (new_name,)):
            s.rename_tag(tid, new_name)          # 只改库里的标签名，不动磁盘文件名
        s.update_tag(tid, zh=zh, category=cat)
        print(f"   {nm} → {new_name}（zh={zh}，类型={cat}）")
    # 注意：这些是 Danbooru 给的更完整译名（如「孤独摇滚！」），按"以 Danbooru 为准"保留
    Library(s, Settings.load()).relink_all_categories()
    s.refresh_counts()
    print("完成。下次「写回文件名」会把磁盘上的文件名同步成新名字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
