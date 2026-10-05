"""收尾归类：角色名 → 人物/角色；坐姿卧姿专名 → 姿势/体位；其余杂项 → 来源/杂项。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import categories as cats   # noqa: E402
from app.config import Settings      # noqa: E402
from app.library import Library      # noqa: E402
from app.store import Store          # noqa: E402

POSE_WORDS = {"wariza", "yokozuwari", "prone_bone", "one_side_up", "two_side_up", "upside-down",
              "out_of_frame", "presenting", "pov", "multiple_views", "dutch_angle", "wading"}


def main() -> int:
    s = Store()
    lib = Library(s, Settings.load())
    rows = s.query("SELECT id,name FROM tags WHERE category IN ('other','')")
    n_char = n_pose = 0
    for r in rows:
        name = r["name"]
        if re.search(r"_\(.+\)$", name):          # manjuu_(azur_lane)、rem_(re:zero)
            s.update_tag(int(r["id"]), category="character")
            n_char += 1
        elif name.lower() in POSE_WORDS:
            s.update_tag(int(r["id"]), category="pose")
            n_pose += 1
    print(f"角色名归类 {n_char} 个，姿势专名归类 {n_pose} 个")

    # 新建「来源/杂项」类型，把剩下的 other 全收进去（其它 = 0）
    if not s.one("SELECT 1 FROM categories WHERE key=?", ("misc",)):
        cats.add(s, "来源/杂项", ["{}", "{} (misc)", "a {} image"], key="misc")
        print("新增类型：来源/杂项(misc)")
    left = s.query("SELECT id FROM tags WHERE category IN ('other','')")
    for r in left:
        s.update_tag(int(r["id"]), category="misc")
    print(f"其余杂项归类 {len(left)} 个 → 来源/杂项")

    s.refresh_counts()
    print("重建分类节点与连线:", lib.sync_taxonomy())
    lib.sync_tag_categories_from_graph()      # 保证 tags.category 与图谱连线一致
    s.prune_dangling_edges()
    s.refresh_counts()
    dist = {r["category"]: r["c"] for r in
            s.query("SELECT category, COUNT(*) c FROM tags GROUP BY category ORDER BY c DESC")}
    print("最终分类分布:", dist)
    print("分类节点:", len(s.list_nodes()), " 连线:", len(s.edges()), " 类型数:", len(s.categories()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
