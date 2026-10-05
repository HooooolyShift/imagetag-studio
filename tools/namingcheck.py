"""文件名规则自检（临时库 + 临时图片，不动正式数据）。

验证：
  1) 父子标签同时在图上时，写进文件名的只有最具体的那个；
  2) 默认「只用标签命名」（不保留原文件名）；没有标签的图片不会被改成 untitled；
  3) 手动打开「保留原文件名」后仍按 原名 [标签] 写回；
  4) 重建索引（换机器）时，按从属关系自动补回父标签，且直接生效不进待审；
  5) 关掉「自动补父标签」开关后就不再补；
  6) 检索父标签仍能命中只写了子标签的文件。

用法： python tools\\namingcheck.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def fresh_env():
    """独立的临时数据目录 + 图片目录（每次调用都换新的，互不干扰）。"""
    tmp_data = Path(tempfile.mkdtemp(prefix="imtag_name_"))
    pics = Path(tempfile.mkdtemp(prefix="imtag_pics_"))
    os.environ["IMGTAG_DATA"] = str(tmp_data)
    return tmp_data, pics


def new_lib(pics, **over):
    from app.config import Settings
    from app.library import Library
    from app.store import Store
    store = Store()
    settings = Settings.load()
    settings.library_dir_name = "ImageTags_namingtest"
    for k, v in over.items():
        setattr(settings, k, v)
    lib = Library(store, settings)
    rid = store.add_root(pics)
    lib.scan_root(rid, pics)
    return store, settings, lib, rid


def make_hierarchy(store):
    """服装 → 裙子 → 白裙子（父 → 子）。"""
    top = store.save_tag("服装", "clothing")
    mid = store.save_tag("裙子", "clothing")
    leaf = store.save_tag("白裙子", "clothing")
    store.link_tag_sub(mid, leaf)
    store.link_tag_sub(top, mid)


def main() -> int:
    bad = 0

    # ---------- 场景 A：默认（只用标签命名）+ 父子只留最具体 ----------
    tmp_a, pics_a = fresh_env()
    shutil.copy(HERE / "testdata" / "Anime_Girl.png", pics_a / "IMG_1234.png")
    shutil.copy(HERE / "testdata" / "Wikipe-tan_full_length.png", pics_a / "notag.png")
    store_a, set_a, lib_a, rid_a = new_lib(pics_a)
    ids_a = {Path(r["path"]).name: int(r["id"]) for r in store_a.search_files(root_ids=[rid_a])}
    fid_a, fid_plain = ids_a["IMG_1234.png"], ids_a["notag.png"]
    make_hierarchy(store_a)
    store_a.add_file_tags(fid_a, [("白裙子", "manual", 1.0), ("裙子", "manual", 1.0),
                                  ("服装", "manual", 1.0), ("泳装", "manual", 1.0)], status="confirmed")

    db_tags = [t["name"] for t in store_a.tags_for_file(fid_a)]
    disk_tags = lib_a.disk_tags_for_file(fid_a)
    ok1 = set(disk_tags) == {"白裙子", "泳装"}
    print(f"[1] 库内标签={db_tags} → 写文件名={disk_tags} {'OK' if ok1 else '不一致'}")
    bad += 0 if ok1 else 1

    lib_a.apply_disk_names([fid_a, fid_plain])
    p_a = Path(store_a.one("SELECT path FROM files WHERE id=?", (fid_a,))["path"])
    p_plain = Path(store_a.one("SELECT path FROM files WHERE id=?", (fid_plain,))["path"])
    ok2 = p_a.name == "[白裙子 泳装].png" and p_a.exists() and p_plain.name == "notag.png"
    print(f"[2] 默认只用标签命名：{p_a.name} ｜ 无标签的图保持：{p_plain.name} {'OK' if ok2 else '不一致'}")
    bad += 0 if ok2 else 1

    # ---------- 场景 B：手动打开"保留原文件名" ----------
    tmp_b, pics_b = fresh_env()
    shutil.copy(HERE / "testdata" / "Anime_Girl.png", pics_b / "IMG_1234.png")
    store_b, set_b, lib_b, rid_b = new_lib(pics_b, rename_keep_original=True)
    fid_b = int(store_b.search_files(root_ids=[rid_b])[0]["id"])
    make_hierarchy(store_b)
    store_b.add_file_tags(fid_b, [("白裙子", "manual", 1.0), ("裙子", "manual", 1.0)],
                          status="confirmed")
    lib_b.apply_disk_names([fid_b])
    p_b = Path(store_b.one("SELECT path FROM files WHERE id=?", (fid_b,))["path"])
    ok3 = p_b.name == "IMG_1234 [白裙子].png"
    print(f"[3] 打开保留原名：{p_b.name} {'OK' if ok3 else '不一致'}")
    bad += 0 if ok3 else 1

    # ---------- 场景 C：重建索引（换机器）→ 自动补回父标签 ----------
    tmp_c, pics_c = fresh_env()
    shutil.copy(p_a, pics_c / p_a.name)                    # 只有子标签的文件名
    store_c, set_c, lib_c, rid_c = new_lib(pics_c)
    make_hierarchy(store_c)                                # 从属关系来自学习包/规则生成
    lib_c.scan_root(rid_c, pics_c)                         # 再扫一次，走"从文件名读回 + 推断"
    fid_c = int(store_c.search_files(root_ids=[rid_c])[0]["id"])
    got = {t["name"]: t["status"] for t in store_c.tags_for_file(fid_c)}
    ok4 = {"白裙子", "泳装", "裙子", "服装"} <= set(got) and got.get("裙子") == "confirmed"
    print(f"[4] 重建索引后标签={got} {'OK' if ok4 else '不一致（父标签应补回且已生效）'}")
    bad += 0 if ok4 else 1

    hits = {n: len(store_c.search_files(required=store_c.expand_tag_names([n])))
            for n in ("白裙子", "裙子", "服装")}
    ok5 = all(v >= 1 for v in hits.values())
    print(f"[5] 检索命中：{hits} {'OK' if ok5 else '不一致'}")
    bad += 0 if ok5 else 1

    # ---------- 场景 D：关掉"自动补父标签" ----------
    tmp_d, pics_d = fresh_env()
    shutil.copy(p_a, pics_d / p_a.name)
    store_d, set_d, lib_d, rid_d = new_lib(pics_d, infer_parent_tags=False)
    make_hierarchy(store_d)
    lib_d.scan_root(rid_d, pics_d)
    got_d = {t["name"] for t in store_d.tags_for_file(int(store_d.search_files(root_ids=[rid_d])[0]["id"]))}
    ok6 = "白裙子" in got_d and "裙子" not in got_d
    print(f"[6] 关掉自动补父标签：{sorted(got_d)} {'OK' if ok6 else '不一致'}")
    bad += 0 if ok6 else 1

    # ---------- 场景 E：系列文件夹名也只留最具体的标签 ----------
    tmp_e, pics_e = fresh_env()
    shutil.copy(HERE / "testdata" / "Anime_Girl.png", pics_e / "p1.png")
    shutil.copy(HERE / "testdata" / "Wikipe-tan_full_length.png", pics_e / "p2.png")
    store_e, set_e, lib_e, rid_e = new_lib(pics_e)
    make_hierarchy(store_e)
    ids_e = [int(r["id"]) for r in store_e.search_files(root_ids=[rid_e])]
    res_e = lib_e.merge_into_series(ids_e, name="测试系列", tags=["白裙子", "裙子"], mode="copy")
    dname = Path(res_e.get("dir", "")).name if res_e.get("ok") else f"失败:{res_e.get('msg')}"
    ok7 = res_e.get("ok") and dname == "测试系列 [白裙子]"
    print(f"[7] 系列文件夹名：{dname} {'OK' if ok7 else '不一致'}")
    bad += 0 if ok7 else 1

    for d in (tmp_a, pics_a, tmp_b, pics_b, tmp_c, pics_c, tmp_d, pics_d, tmp_e, pics_e):
        shutil.rmtree(d, ignore_errors=True)
    print("结论:", "全部通过" if bad == 0 else f"{bad} 处不一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
