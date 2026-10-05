"""把「系列/作品」和「人物/角色」两个分类合成一个（作品与人物本来就是一回事的两端）。

用法：
    python tools\\merge_series_character.py            # 干跑
    python tools\\merge_series_character.py --apply    # 真合并（自动备份数据库）

做法：
  1) 标签类型：series → character（合成 key=character，显示名改成「人物 / 作品」）；
  2) 图谱节点：把两个分类节点的子项都挂到合并后的节点上，删掉多余的节点与分类记录；
  3) 从属连线（作品 → 人物）原样保留，所以在这个分类里就是"作品下面挂人物"的样子。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true", help="撤销合并：把作品标签放回「系列/作品」分类")
    args = ap.parse_args()
    from app.config import Settings
    from app.library import Library
    from app.store import Store

    store = Store()
    if args.undo:
        return undo(store, args)
    n_series = store.one("SELECT COUNT(*) c FROM tags WHERE category='series'")["c"]
    n_char = store.one("SELECT COUNT(*) c FROM tags WHERE category='character'")["c"]
    nodes = [(int(n["id"]), str(n["name"])) for n in store.list_nodes()]
    print(f"现在：人物/角色 {n_char} 个标签，系列/作品 {n_series} 个标签")
    print("分类节点:", nodes)
    if not args.apply:
        print("（干跑，加 --apply 才合并）")
        return 0

    db = store.db_path
    backup = db.with_name(f"library_before_mergecat_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print("已备份：", backup.name)

    # 1) 标签类型合并
    store.execute("UPDATE tags SET category='character' WHERE category='series'")
    store.ensure_category("character")
    store.update_category("character", label="人物 / 作品")
    # 类型记录里去掉 series（标签已经搬空了）
    store.delete_category("series")

    # 2) 图谱节点合并：保留一个分类节点，把另一个的子项挪过去
    def node_by_label(label: str) -> int | None:
        for n in store.list_nodes():
            if str(n["name"]) == label:
                return int(n["id"])
        return None

    keep = node_by_label("人物 / 作品") or node_by_label("人物/角色") or node_by_label("人物 / 角色")
    drop = node_by_label("系列/作品") or node_by_label("系列 / 作品")
    if keep is None:
        keep = store.ensure_node("人物 / 作品")
    if drop and int(drop) != int(keep):
        kids = store.children_of_node(int(drop))
        for ch in kids:
            store.link(int(keep), ch["kind"], int(ch["cid"]))
        store.execute("DELETE FROM taxonomy_edges WHERE parent_kind='node' AND parent_id=?", (int(drop),))
        store.execute("DELETE FROM taxonomy_edges WHERE child_kind='node' AND child_id=?", (int(drop),))
        store.execute("DELETE FROM nodes WHERE id=?", (int(drop),))
        print(f"已删除多余的分类节点 #{drop}")
    store.update_node(int(keep), name="人物 / 作品")

    # 3) 重建分类连线（把两个来源的标签都挂到合并后的节点上）
    lib = Library(store, Settings.load())
    lib.relink_all_categories()
    store.refresh_counts()
    print(f"合并完成：人物/角色 {store.one('SELECT COUNT(*) c FROM tags WHERE category=?', ('character',))['c']} 个标签，"
          f"系列/作品 {store.one('SELECT COUNT(*) c FROM tags WHERE category=?', ('series',))['c']} 个")
    return 0


def undo(store, args) -> int:
    """撤销合并：作品标签回到「系列/作品」类型，人物留在「人物/角色」，两者用从属关系相连。"""
    import shutil, time
    # 作品标签 = 某个标签的父级，且子标签名字里有「_(作品名)」
    rows = store.query(
        "SELECT DISTINCT p.id, p.name FROM taxonomy_edges e "
        "JOIN tags p ON p.id=e.parent_id JOIN tags c ON c.id=e.child_id "
        "WHERE e.parent_kind='tag' AND e.child_kind='tag' "
        "  AND instr(lower(c.name), '_(' || lower(p.name) || ')') > 0")
    series_ids = [(int(r["id"]), str(r["name"])) for r in rows]
    # 这些后缀是"限定词/作者名"，不是作品，单独处理
    not_series = {"object", "female", "male", "1st_costume", "2nd_costume", "vtuber", "cosplay",
                  "naga_u", "official_art", "artist_name"}
    series_ids = [(i, n) for i, n in series_ids if n not in not_series]
    leftover = [(int(r["id"]), str(r["name"]))
                for r in store.query("SELECT id, name FROM tags WHERE name IN (%s)"
                                     % ",".join("?" * len(not_series)), tuple(not_series))]
    print(f"识别出作品标签 {len(series_ids)} 个：{ [n for _i, n in series_ids[:10] ] } …")
    if not args.apply:
        print("（干跑，加 --apply 才撤销）")
        return 0
    db = store.db_path
    backup = db.with_name(f"library_before_splitcat_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print("已备份：", backup.name)
    store.ensure_category("series", label="系列/作品")
    for tid, _n in series_ids:
        store.update_tag(tid, category="series")
    for tid, name in leftover:                       # 限定词/作者名：不算作品，也别留在人物里
        store.update_tag(tid, category="misc" if name == "naga_u" else "other")
        store.execute("DELETE FROM taxonomy_edges WHERE child_kind='tag' AND parent_id=?", (tid,))
    store.ensure_category("character")
    store.update_category("character", label="人物/角色")
    from app.config import Settings
    from app.library import Library
    lib = Library(store, Settings.load())
    lib.sync_taxonomy()                 # 补回「系列/作品」分类节点
    lib.relink_all_categories()         # 按类型重建分类连线
    store.refresh_counts()
    # 清掉合并时可能留下的空分类节点（名字带"人物 / 作品"但已经不是合并态）
    for n in store.list_nodes():
        if str(n["name"]).replace(" ", "") == "人物/作品" and not store.children_of_node(int(n["id"])):
            store.execute("DELETE FROM taxonomy_edges WHERE child_kind='node' AND child_id=?", (int(n["id"]),))
            store.execute("DELETE FROM nodes WHERE id=?", (int(n["id"]),))
            print(f"清掉空节点 #{int(n['id'])}")
    print("撤销完成：人物/角色",
          store.one("SELECT COUNT(*) c FROM tags WHERE category='character'")["c"],
          "｜ 系列/作品", store.one("SELECT COUNT(*) c FROM tags WHERE category='series'")["c"],
          "｜ 分类节点", store.one("SELECT COUNT(*) c FROM nodes")["c"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
