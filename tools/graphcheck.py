"""图谱页面自检（用真实库副本，只读；会临时改一个标签的中文名再改回来）。

检查：
  0) 库里每个标签都在图谱里（连线一条不缺，节点一个不少）；
  1) 搜索标签能定位到节点：即使它藏在被折叠的分类里，也要画出来并选中；
  2) 定位时会把它的所有祖先分类展开；
  3) 在图谱里改「中文名 / 备注」会写进 tags.zh，并且磁盘命名跟着变（备注=中文名）。

用法： set IMGTAG_DATA=<库副本目录> && python tools\\graphcheck.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.config import Settings
    from app.library import Library
    from app.store import Store
    from app.ui.taxonomy import TaxonomyDialog

    app = QApplication(sys.argv)
    settings = Settings.load()
    store = Store()
    lib = Library(store, settings)
    tax = TaxonomyDialog(lib)
    bad = 0

    # 0) 每个标签都挂进了图谱；默认只显示顶层分类，点「展开全部」后必须一个不少地画出来
    total = store.one("SELECT COUNT(*) c FROM tags")["c"]
    unlinked = store.one("SELECT COUNT(*) c FROM tags t WHERE NOT EXISTS("
                         "SELECT 1 FROM taxonomy_edges e WHERE e.child_kind='tag' AND e.child_id=t.id)")["c"]
    default_tags = len([k for k in tax.canvas.items if k[0] == "tag"])
    tax.set_all_collapsed(False)
    shown = len([k for k in tax.canvas.items if k[0] == "tag"])
    tax.set_all_collapsed(True)
    ok0 = unlinked == 0 and default_tags == 0 and shown >= total
    print(f"[0] 库里标签 {total} ｜ 默认视图标签 {default_tags}（应为 0）｜ 展开后画出 {shown} ｜ "
          f"未连线 {unlinked} {'OK' if ok0 else '不一致'}")
    bad += 0 if ok0 else 1

    tags = store.list_tags()
    print(f"库里标签 {len(tags)} 个，图谱节点 {len(tax.canvas.items)} 个")
    target = tags[len(tags) // 2] if len(tags) > 2 else tags[0]
    tid = int(target["id"])

    # 故意把它的父分类折叠起来，模拟"搜到了但看不见"
    parents = store.parents_of_tag(tid)
    for p in parents:
        store.update_node(int(p["id"]), collapsed=1)
    tax.rebuild()
    hidden_before = ("tag", tid) not in tax.canvas.items

    tax.find_edit.setText(str(target["name"]))
    tax.goto_first_match()
    in_canvas = ("tag", tid) in tax.canvas.items
    focus_ok = tax._focus_tag == tid
    expanded_ok = all(not store.node(int(p["id"]))["collapsed"] or int(p["id"]) in tax._expanded_now
                      for p in parents) if parents else True
    print(f"[1] 搜索「{target['name']}」：折叠时不在画布={hidden_before} → 定位后已画出={in_canvas} "
          f"｜选中={focus_ok}｜祖先已展开={expanded_ok}")
    ok1 = in_canvas and focus_ok and expanded_ok
    bad += 0 if ok1 else 1

    # 中文名 / 备注：在图谱里改，写进 tags.zh，并影响磁盘命名
    old_zh = (store.one("SELECT zh FROM tags WHERE id=?", (tid,))["zh"] or "")
    tax.current = ("tag", tid)
    tax.refresh_detail()
    before_field = tax.node_note.text()
    tax.node_note.setText("图谱改名测试")
    tax.apply_detail()
    now_zh = store.one("SELECT zh, note FROM tags WHERE id=?", (tid,))
    disk = lib.disk_label(str(target["name"]))
    ok2 = now_zh["zh"] == "图谱改名测试" and disk == "图谱改名测试"
    print(f"[2] 字段初值={before_field!r} → 改成「图谱改名测试」：zh={now_zh['zh']!r} 磁盘名={disk!r} "
          f"{'OK' if ok2 else '不一致'}")
    bad += 0 if ok2 else 1

    # 改回去，别留下痕迹
    store.update_tag(tid, zh=old_zh)

    # 拖放：把标签拖到别的分类 = 改类型；把分类拖到别的分类下 = 换父级；禁止拖成环
    try:
        tmp_node = store.ensure_node("拖放测试分类")
        roots = store.node_roots()
        if roots:
            store.link(int(roots[0]["id"]), "node", int(tmp_node))
        store.link(int(tmp_node), "tag", tid)
        tax.on_tag_dropped(tid, int(tmp_node))
        after = store.one("SELECT category FROM tags WHERE id=?", (tid,))
        linked = store.query("SELECT COUNT(*) c FROM taxonomy_edges WHERE child_kind='tag' AND child_id=? "
                             "AND parent_kind='node' AND parent_id=?", (tid, int(tmp_node)))[0]["c"]
        ok5 = bool(after) and linked >= 1
        print(f"[4] 拖标签到分类：类型={after['category'] if after else None} 连线={linked} "
              f"{'OK' if ok5 else '不一致'}")
        bad += 0 if ok5 else 1
        # 环：把 tmp_node 拖到它自己下面应被拒绝
        before_parents = {int(e["parent_id"]) for e in
                          store.query("SELECT parent_id FROM taxonomy_edges WHERE child_kind='node' AND child_id=?",
                                      (int(tmp_node),))}
        tax.on_node_dropped(int(tmp_node), int(tmp_node))
        after_parents = {int(e["parent_id"]) for e in
                         store.query("SELECT parent_id FROM taxonomy_edges WHERE child_kind='node' AND child_id=?",
                                     (int(tmp_node),))}
        ok6 = before_parents == after_parents
        print(f"[5] 分类拖到自己下面是拒绝的：{'OK' if ok6 else '不一致'}")
        bad += 0 if ok6 else 1
        # 清理测试节点
        store.execute("DELETE FROM taxonomy_edges WHERE child_id=? OR parent_id=?", (int(tmp_node), int(tmp_node)))
        store.execute("DELETE FROM nodes WHERE id=?", (int(tmp_node),))
    except Exception as exc:      # noqa: BLE001
        print("  拖放自检失败:", exc)
        bad += 1
    print("结论:", "全部通过" if bad == 0 else f"{bad} 处不一致")
    tax.close()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
