"""自检脚本：用几张小样本图跑通 扫描 → WD14 → CLIP → 框选区域 → 系列 → 写回文件名。

用法： python tools\\selftest.py [图片目录]
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("IMGTAG_HOME", str(HERE))
os.environ["IMGTAG_DATA"] = str(HERE / ".selftest_home")

from app.config import Settings           # noqa: E402
from app.library import EngineHub, Library  # noqa: E402
from app.store import Store               # noqa: E402


def show(msg: str, frac: float = 0.0) -> None:
    if frac < 0 or frac >= 1.0:
        print(f"  {msg}", flush=True)


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "testdata"
    work = HERE / "testdata_work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work)
    data_home = Path(os.environ["IMGTAG_DATA"])
    if data_home.exists():
        shutil.rmtree(data_home)
    # 上次运行若中途失败，测试图库目录可能残留，先清掉，保证页码从 001 开始
    for leftover in ("ImageTags_selftest", "ImageTags_selftest2"):
        p = Path(f"E:\\{leftover}")
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    settings = Settings.load()
    settings.models_dir = str(HERE / "models")
    settings.tag_storage = "db"        # 先只写数据库，最后单独测写文件名
    settings.wd14_keep_rating = True
    settings.clip_threshold = 0.6
    store = Store()
    lib = Library(store, settings)
    hub = EngineHub(settings)

    print("== 1) 扫描 ==")
    rid = store.add_root(work)
    print("  ", lib.scan_root(rid, work, show))
    lib.ensure_builtin_tags()

    print("== 2) 建立带类型的自定义标签 ==")
    for name, cat, prompt in [("泳装", "clothing", "swimsuit"), ("围裙", "clothing", "apron"),
                              ("眼镜", "clothing", "glasses"), ("西装", "clothing", "business suit"),
                              ("蓝头发", "body", "blue hair"), ("单人", "count", "one person"),
                              ("二次元插画", "style", "anime illustration"),
                              ("真人照片", "style", "photograph of a real person"),
                              ("初音未来", "character", "hatsune_miku")]:
        tid = store.ensure_tag(name, cat, prompt)
        store.update_tag(tid, category=cat, prompt=prompt)
    # 「西装」加前置条件：必须同时是真人照片，否则不打（这正是防止“领带状物体”到处命中的机制）
    store.update_tag(store.tag_id("西装"), requires="真人照片")
    ids = [int(r["id"]) for r in store.search_files()]

    print("== 3) WD14 二次元打标（GPU/ONNX）==")
    import time
    t0 = time.time()
    lib.run_wd14(ids, hub, show)
    print(f"  耗时 {time.time() - t0:.1f}s")

    print("== 4) CLIP 特征 + 零样本打标 ==")
    t0 = time.time()
    lib.ensure_clip_embeddings(ids, hub, show)
    lib.auto_tags_from_clip(hub, ids, show)
    print(f"  耗时 {time.time() - t0:.1f}s")

    print("== 5) 框选区域标注（不靠人眼，是给模型的区域监督） ==")
    wt = store.file_by_path(work / "Wikipe-tan_full_length.png")
    ob = store.file_by_path(work / "Barack_Obama.jpg")
    mk = store.file_by_path(work / "Angela_Merkel_2019_cropped.jpg")
    if wt:
        store.add_region(int(wt["id"]), "围裙", (0.30, 0.50, 0.42, 0.16), category="clothing")
    if ob:
        store.add_region(int(ob["id"]), "西装", (0.15, 0.72, 0.70, 0.27), category="clothing")
    if mk:
        store.add_region(int(mk["id"]), "西装", (0.18, 0.70, 0.64, 0.28), category="clothing")
    n = lib.ensure_region_embeddings(ids, hub, show)
    print(f"  区域特征 {n} 个，区域中心标签 {lib.update_region_probes()} 个")
    print("  区域精修：", lib.region_refine(ids, hub, show))

    print("== 5.5) 审核（AI 结果先待审，人工确认后才生效）==")
    print("  待审核：", store.pending_summary())
    rejected_demo = 0
    for r in store.search_files():
        fid = int(r["id"])
        pending = [t["name"] for t in store.tags_for_file(fid, statuses=("pending",))]
        # 演示：把明显错的“泳装”在动漫图上拒绝掉，其它全部确认
        decisions = {}
        for name in pending:
            if name == "泳装" and "Anime_Girl" in r["name"]:
                decisions[name] = "rejected"
                rejected_demo += 1
            else:
                decisions[name] = "confirmed"
        if decisions:
            lib.review_file(fid, decisions)
    print(f"  审核完成，拒绝了 {rejected_demo} 个错标签；剩余待审：{store.pending_summary()}")
    print("  泳装 探针状态：", lib.probe_confidence(store.tag_id("泳装")))

    print("== 5.6) 断点续跑（模拟跑到一半断电）==")
    store.execute("UPDATE files SET wd_done=0 WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
    job = lib.start_job("wd14", ids, note="selftest")
    lib.run_wd14(ids[:2], hub, show, job_id=job)
    left = lib.job_remaining(job)
    print(f"  中断后：{lib.describe_job(job)}")
    lib.run_wd14(left, hub, show, job_id=job)
    lib.finish_job(job)
    print(f"  续跑完成，剩余 {len(lib.job_remaining(job))} 项；未完成任务数 {len(store.unfinished_jobs())}")

    print("== 6) 结果 ==")
    for r in store.search_files():
        tags = store.tags_for_file(int(r["id"]))
        manual = [t["name"] for t in tags if t["source"] == "manual"]
        wd = [t["name"] for t in tags if t["source"] == "wd14"]
        cl = [f"{t['name']}({t['score']:.2f})" for t in tags if t["source"] == "clip"]
        rg = [f"{t['name']}({t['score']:.2f})" for t in tags if t["source"] == "clip_region"]
        print(f"\n  ● {r['name']}")
        print(f"    手动: {manual}")
        print(f"    区域精修: {rg}")
        print(f"    CLIP: {cl}")
        print(f"    WD14: {wd[:18]}")

    print("\n== 7) 标签重命名（自动同步到所有图片 + 可选改文件名）==")
    tid = store.tag_id("围裙")
    print("  ", lib.rename_tag_global(tid, "apron_围裙", update_filenames=False))
    settings.tag_storage = "filename"
    print("  写回文件名：", lib.apply_disk_names(ids, show))
    for p in sorted(work.rglob("*")):
        if p.is_file():
            print("   ", p.relative_to(work))

    print("\n== 8) 系列合并 ==")
    series_ids = [int(r["id"]) for r in store.search_files() if r["name"].endswith(".jpg")]
    res = lib.create_series(series_ids, "测试系列", ["真人", "测试"], order=series_ids, mode="copy",
                            digits=3, start=1, page_names={}, progress=show)
    print("  ", res)
    for p in sorted(work.rglob("*")):
        if p.is_file():
            print("   ", p.relative_to(work))
    print("\n== 9) 检索 ==")
    print("  标签 西装 →", [r["path"].split("\\")[-1] for r in store.search_files(required=["西装"])])
    print("  系列 →", [(r["name"], r["series_name"], r["series_pages"]) for r in store.search_files(kind="series_page")][:4])
    print("\n统计：", store.stats())

    print("\n== 10) 收录到图库（同盘专用目录，源文件消失）==")
    settings.library_dir_name = "ImageTags_selftest"
    import_ids = [int(r["id"]) for r in store.search_files(limit=100000)]
    res = lib.import_to_library(import_ids, move=True, progress=show)
    print("  ", {k: v for k, v in res.items() if k != "errors"}, res.get("errors"))
    print("  图库目录内容：")
    for p in sorted(Path(res["dirs"][0]).rglob("*")) if res["dirs"] else []:
        if p.is_file():
            print("   ", p.relative_to(res["dirs"][0]))
    print("  图纸库根：", [(r["path"], r["is_library"]) for r in store.list_roots()])
    lib_roots = [int(r["id"]) for r in store.library_roots()]
    print("  只查图库：", len(store.search_files(root_ids=lib_roots, limit=1000)), "项")

    print("\n== 11) 查重 / 保留选择 / 误报反馈 ==")
    from PIL import Image as PILImage
    lib_root = Path(res["dirs"][0]) if res["dirs"] else work
    base_img = None
    for cand in lib_root.rglob("*"):
        if cand.is_file() and cand.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            base_img = cand
            break
    if base_img and base_img.is_file():
        im = PILImage.open(base_img)
        small = base_img.with_name("DUP_small.jpg")
        small2 = base_img.with_name("DUP_small2.jpg")
        im.resize((im.width // 2, im.height // 2)).save(small, quality=88)
        im.resize((im.width // 2 + 3, im.height // 2)).save(small2, quality=70)
        lib.scan_root(int(store.one("SELECT id FROM roots WHERE is_library=1")["id"]), lib_root, show)
        print("  指纹计算：", lib.ensure_hashes(None, show), "张")
        base = store.file_by_path(base_img)
        dup1 = store.file_by_path(small)
        dup2 = store.file_by_path(small2)
        groups = lib.find_duplicate_groups(threshold=8, only_roots=lib_roots, use_clip=False)
        print("  发现重复组：", [(g["size"], g["max_dist"]) for g in groups])
        if base and dup1:
            lib.mark_group_not_duplicate([int(f["id"]) for g in groups for f in g["files"]], "selftest")
            groups2 = lib.find_duplicate_groups(threshold=8, only_roots=lib_roots, use_clip=False)
            same = any({int(f["id"]) for f in g["files"]} >= {int(base["id"]), int(dup1["id"])} for g in groups2)
            print(f"  误报反馈后，这一对还会出现吗：{same}（期望 False）")
        if base and dup2:
            r = lib.resolve_duplicate(int(base["id"]), [int(dup2["id"])], action="quarantine", merge_tags=True)
            print("  保留并隔离：", r)
            print("  隔离区文件：", [p.name for p in (lib_root / ".removed").glob("*")] if (lib_root / ".removed").exists() else [])
        shutil.rmtree(Path(res["dirs"][0]), ignore_errors=True)

    print("\n== 12) 分级识别（全年龄 / R15 / R18 / R18G）==")
    from app.library import decide_rating
    ids2 = [int(r["id"]) for r in store.search_files(limit=100000)]
    print("  ", lib.run_rating(ids2, hub, show))
    for r in store.search_files(limit=100000):
        row = store.one("SELECT rating, rating_scores FROM files WHERE id=?", (int(r["id"]),))
        print(f"   {r['name'][:34]:36} → {row['rating']}")
    # 用合成概率验证融合/阈值规则（不依赖测试图内容）
    print("  规则自检：")
    cases = [
        ({"general": 0.98, "sensitive": 0.02}, {"all_ages": 0.9, "r15": 0.1}, False, "all_ages"),
        ({"general": 0.05, "sensitive": 0.2, "explicit": 0.95}, None, False, "r18"),
        ({"general": 0.05, "questionable": 0.9}, None, False, "r15"),
        ({"general": 0.05, "questionable": 0.9}, None, True, "r18"),
        (None, {"all_ages": 0.1, "r15": 0.7, "r18": 0.2}, False, "r15"),
        (None, {"all_ages": 0.1, "r15": 0.2, "r18": 0.1, "r18g": 0.8}, False, "r18g"),
    ]
    for wd, cp, strict, expect in cases:
        got, _ = decide_rating(wd, cp, questionable_as_r18=strict)
        print(f"    wd14={wd} clip={cp} 严格={strict} → {got} （期望 {expect}）{'✓' if got == expect else '✗'}")

    print("\n== 13) 新标签自动扫描 / 并入系列 / 同系列不报警 ==")
    # 清理前面步骤留下的陈旧记录（测试脚本手动删过目录，DB 里还留着）
    store.execute("UPDATE files SET missing=1 WHERE path LIKE ?", (f"%{settings.library_dir_name}%",))
    store.execute("DELETE FROM series")
    store.execute("UPDATE files SET series_id=NULL,page_no=NULL,kind='image'")
    from PIL import Image as _PIL
    src2 = HERE / "testdata_series"
    shutil.rmtree(src2, ignore_errors=True)
    src2.mkdir(parents=True)
    im = _PIL.open(HERE / "testdata" / "Anime_Girl.png")
    im.save(src2 / "page_a.png")
    im.resize((max(1, im.width // 2), max(1, im.height // 2))).save(src2 / "page_b.png")
    shutil.copy(HERE / "testdata" / "Barack_Obama.jpg", src2 / "solo.jpg")
    shutil.copy(HERE / "testdata" / "Wikipe-tan_full_length.png", src2 / "apron.png")
    settings.library_dir_name = "ImageTags_selftest2"
    lib.scan_root(store.add_root(src2), src2, show)
    rid_src = int(store.one("SELECT id FROM roots WHERE path=?", (str(src2),))["id"])
    ids2 = [int(r["id"]) for r in store.search_files(root_ids=[rid_src], limit=1000)]

    # (a) 新建带提示词的标签 → 全库扫描 → 命中进待审核
    tid_new = store.ensure_tag("白围裙", "clothing", "white apron")
    store.update_tag(tid_new, prompt="white apron", auto=1, category="clothing")
    lib.ensure_clip_embeddings(ids2, hub, show)
    before = store.pending_summary()["pending_tags"]
    lib.auto_tags_from_clip(hub, ids2, show)
    after = store.pending_summary()["pending_tags"]
    hits = [r["name"] for r in store.query(
        "SELECT f.name FROM files f JOIN file_tags ft ON ft.file_id=f.id JOIN tags t ON t.id=ft.tag_id "
        "WHERE t.name='白围裙' AND ft.status='pending'")]
    print(f"  新标签「白围裙」全库扫描：待审 {before} → {after}；命中待审图片：{hits}")
    print("  期望命中 apron.png：", any("apron" in h for h in hits))

    # (b) 先收进图库，再把三张并成一个新系列
    imp = lib.import_to_library(ids2, move=True, auto_write_names=False, progress=show)
    print("  收进图库:", {k: v for k, v in imp.items() if k != 'errors'})
    ids_lib = [int(r["id"]) for r in store.search_files(root_ids=imp["roots"], limit=1000)]
    res2 = lib.merge_into_series(ids_lib, name="测试连载", tags=["连载", "测试"], mode="move",
                                 digits=3, progress=show)
    print("  并入系列:", {k: v for k, v in res2.items() if k != 'errors'})
    if res2.get("dir"):
        print("  系列目录:", sorted(p.name for p in Path(res2["dir"]).iterdir()))
        pages = store.query("SELECT name,page_no FROM files WHERE series_id=? ORDER BY page_no",
                            (int(res2["series_id"]),))
        print("  页码:", [(r["name"], r["page_no"]) for r in pages])
    # (c) 同一系列内部的相似图不再当作重复报警
    lib.ensure_hashes(ids2, show)
    lib_roots2 = [int(r["id"]) for r in store.library_roots()]
    groups3 = lib.find_duplicate_groups(threshold=10, only_roots=lib_roots2, use_clip=False)
    still = [g["size"] for g in groups3 if g["size"] > 1]
    print(f"  同系列相似图还报警吗：{still}（期望空列表）")
    # (c2) 拖动改顺序 → 自动重命名页码
    if res2.get("series_id"):
        sid = int(res2["series_id"])
        before = [(int(r["id"]), Path(r["path"]).name, r["page_no"]) for r in store.series_files(sid)]
        rev = [x[0] for x in reversed(before)]
        rr = lib.reorder_series(sid, rev, progress=show)
        after = [(int(r["id"]), Path(r["path"]).name, r["page_no"]) for r in store.series_files(sid)]
        print("  重排前:", [x[1] for x in before])
        print("  重排后:", [x[1] for x in after], "｜ok=", rr.get("ok"))
        print("  顺序已按拖动结果重命名：", [x[0] for x in after] == rev,
              "｜页码连续：", [x[2] for x in after] == list(range(1, len(after) + 1)))
    # (d) 相似组判定为系列：先造两张单图，再整组并进系列
    stray = HERE / "testdata_stray"
    shutil.rmtree(stray, ignore_errors=True)
    stray.mkdir(parents=True)
    im.save(stray / "stray1.png")
    im.resize((max(1, im.width // 2 + 3), max(1, im.height // 2))).save(stray / "stray2.png")
    lib.scan_root(store.add_root(stray), stray, show)
    rid_s = int(store.one("SELECT id FROM roots WHERE path=?", (str(stray),))["id"])
    ids_s = [int(r["id"]) for r in store.search_files(root_ids=[rid_s], limit=100)]
    lib.import_to_library(ids_s, move=True, auto_write_names=False)
    lib.ensure_hashes(ids_s, show)
    groups_s = lib.find_duplicate_groups(threshold=10, only_roots=lib_roots2, use_clip=False)
    target = next((g for g in groups_s if {int(f["id"]) for f in g["files"]} >= set(ids_s)), None)
    if target:
        r3 = lib.merge_into_series(ids_s, series_id=int(res2["series_id"]), mode="move", progress=show)
        lib.mark_group_as_series(ids_s)
        groups_s2 = lib.find_duplicate_groups(threshold=10, only_roots=lib_roots2, use_clip=False)
        again = any({int(f["id"]) for f in g["files"]} >= set(ids_s) for g in groups_s2)
        print("  判定为系列后还会提示吗：", again, "（期望 False）；并入结果:", r3.get("ok"), r3.get("moved"))
    for p in [src2, stray]:
        if p.parent == HERE:
            shutil.rmtree(p, ignore_errors=True)
    for d in store.library_roots():
        if d["path"].endswith("ImageTags_selftest2"):
            shutil.rmtree(Path(d["path"]), ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
