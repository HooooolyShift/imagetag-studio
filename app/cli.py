"""命令行入口：不开界面也能扫描/打标/检索，方便批量处理与自检。

用法示例：
    python -m app.cli models                 # 下载/校验模型
    python -m app.cli add "E:\\图片"          # 加入库并扫描
    python -m app.cli tag --all --wd14       # 给全部图片打 WD14 标签
    python -m app.cli tag --all --clip       # 用 CLIP 给自定义标签打分
    python -m app.cli search 泳装 初音未来     # 多标签检索（同时满足）
    python -m app.cli writeback --all        # 把标签写回文件名
    python -m app.cli stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Settings
from .library import EngineHub, Library
from .store import Store


def _printer(msg: str, frac: float = 0.0) -> None:
    end = "\n" if frac >= 1.0 or frac < 0 else ""
    sys.stdout.write(f"\r  {msg:<60}"[:78] + ("" if end == "" else "\n"))
    sys.stdout.flush()


def build() -> tuple[Settings, Store, Library, EngineHub]:
    settings = Settings.load()
    store = Store()
    library = Library(store, settings)
    hub = EngineHub(settings)
    return settings, store, library, hub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="imtag", description="图片标签工坊 命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="下载/校验模型")
    p_add = sub.add_parser("add", help="加入库并扫描")
    p_add.add_argument("folder")
    p_scan = sub.add_parser("scan", help="重新扫描全部库")

    p_tag = sub.add_parser("tag", help="自动打标")
    p_tag.add_argument("--all", action="store_true")
    p_tag.add_argument("--unlabeled", action="store_true")
    p_tag.add_argument("--wd14", action="store_true")
    p_tag.add_argument("--clip", action="store_true")
    p_tag.add_argument("--face", action="store_true")
    p_tag.add_argument("--limit", type=int, default=0)

    p_search = sub.add_parser("search", help="按标签检索")
    p_search.add_argument("tags", nargs="+")
    p_search.add_argument("--any", action="store_true", help="满足任一（默认是同时满足）")
    p_search.add_argument("--limit", type=int, default=50)

    sub.add_parser("writeback", help="把标签写回文件名")
    sub.add_parser("rescore", help="用缓存的 CLIP 特征重新打分")
    sub.add_parser("cluster", help="人脸聚类")
    p_export = sub.add_parser("export", help="导出标签/框选标注 JSON")
    p_export.add_argument("out", nargs="?", default="")
    sub.add_parser("stats", help="统计信息")

    args = ap.parse_args(argv)
    settings, store, library, hub = build()

    if args.cmd == "models":
        from . import models as model_lib
        model_lib.setup_env(settings.models_path())
        model_lib.ensure_all(settings.models_path(), _printer)
        print("模型就绪：", settings.models_path())
        return 0

    if args.cmd == "add":
        rid = store.add_root(args.folder)
        res = library.scan_root(rid, args.folder, _printer)
        library.ensure_builtin_tags()
        print(f"完成：{res}")
        return 0

    if args.cmd == "scan":
        for r in store.list_roots():
            print(f"扫描 {r['path']}")
            print(" ", library.scan_root(int(r["id"]), r["path"], _printer))
        return 0

    if args.cmd == "tag":
        rows = store.search_files(only_unlabeled=args.unlabeled, limit=args.limit or 20000)
        ids = [int(r["id"]) for r in rows]
        if not args.all and not args.unlabeled:
            print("请指定 --all 或 --unlabeled")
            return 1
        print(f"目标 {len(ids)} 张")
        if args.wd14 or not (args.clip or args.face):
            library.run_wd14(ids, hub, _printer)
        if args.clip:
            library.ensure_clip_embeddings(ids, hub, _printer)
            library.auto_tags_from_clip(hub, ids, _printer)
        if args.face:
            library.run_face(ids, hub, _printer)
            print(" ", library.cluster_faces())
        print("\n完成")
        return 0

    if args.cmd == "search":
        rows = store.search_files(any_of=args.tags if args.any else (), required=() if args.any else args.tags,
                                  limit=args.limit)
        for r in rows:
            print(r["path"])
        print(f"—— 共 {len(rows)} 项")
        return 0

    if args.cmd == "writeback":
        ids = [int(r["id"]) for r in store.search_files(limit=100000)]
        res = library.apply_disk_names(ids, _printer)
        print(f"\n完成：{res}")
        return 0

    if args.cmd == "rescore":
        library.rescore_all_clip(hub, _printer)
        print("\n完成")
        return 0

    if args.cmd == "cluster":
        print(library.cluster_faces(None, _printer))
        return 0

    if args.cmd == "export":
        import json
        from .config import data_dir
        out = Path(args.out) if args.out else data_dir() / "export.json"
        data = {"tags": {r["name"]: {"category": r["category"], "count": r["count"]} for r in store.list_tags()},
                "files": {}, "regions": store.export_regions()}
        for r in store.search_files(limit=200000):
            data["files"][r["path"]] = [t["name"] for t in store.tags_for_file(int(r["id"]))]
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已导出 {out}")
        return 0

    if args.cmd == "stats":
        for k, v in store.stats().items():
            print(f"{k:>8}: {v}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
