"""用 Danbooru 的中英对照词表核对我们的汉化词典（社区维护，比本地小模型可靠）。

数据源：amenorira/danbooru-tags-data-zh（按 character/general/copyright/meta 分类的 CSV，
每行 = tag, category, aliases, zh, count, notes）。

优先级：我们手工确认过的修正（app/tag_zh_corrections.json）> Danbooru 词表 > 现有词典。
对不上的（Danbooru 里没有）单独写到 词典待核_未匹配.txt，留给下一轮人工核。

用法：
    python tools\\import_booru_zh.py            # 干跑，出报告
    python tools\\import_booru_zh.py --apply    # 写入词典 + 历史表 + 同步库内旧译名
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
DICT = HERE / "app" / "tag_zh_dict.json"
CORR = HERE / "app" / "tag_zh_corrections.json"
HIST = HERE / "app" / "tag_zh_fix_history.json"
UNMATCHED = HERE / "词典待核_未匹配.txt"
CACHE = HERE / "app" / "booru_zh"        # 常驻本地资源：随程序一起走，离线也能对照
RAW = "https://raw.githubusercontent.com/amenorira/danbooru-tags-data-zh/main/tags"
FILES = ("character.csv", "general.csv", "copyright.csv", "meta.csv")
UA = {"User-Agent": "ImageTagStudio/1.4 (offline tag lab)"}


def fetch(name: str, refresh: bool = False) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if p.exists() and p.stat().st_size > 1000 and not refresh:
        return p.read_text(encoding="utf-8")
    req = urllib.request.Request(f"{RAW}/{name}", headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8-sig", "replace")
    p.write_text(text, encoding="utf-8")
    return text


def load_booru(refresh: bool = False) -> dict[str, tuple[str, int, str]]:
    """tag → (中文, 类别码, 备注)。character/general/copyright 合并，先出现的优先。"""
    out: dict[str, tuple[str, int, str]] = {}
    for name in FILES:
        try:
            text = fetch(name, refresh)
        except Exception as e:      # noqa: BLE001
            print(f"  （{name} 下载失败：{e}）")
            continue
        for row in csv.DictReader(io.StringIO(text)):
            tag = (row.get("tag") or "").strip()
            zh = (row.get("zh") or "").strip()
            if not tag or not zh:
                continue
            try:
                cat = int(row.get("category") or 0)
            except ValueError:
                cat = 0
            out.setdefault(tag, (zh, cat, (row.get("notes") or "").strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="强制重新下载词表")
    args = ap.parse_args()
    print("下载/读取 Danbooru 中文词表…")
    booru = load_booru(args.refresh)
    print(f"  Danbooru 词表：{len(booru)} 条")
    raw = json.loads(DICT.read_text(encoding="utf-8"))
    ours = json.loads(CORR.read_text(encoding="utf-8")) if CORR.exists() else {}
    try:
        hist = json.loads(HIST.read_text(encoding="utf-8"))
    except Exception:
        hist = {}
    from app.store import Store
    from app import tag_i18n
    store = Store()
    lib_tags = [str(r["name"]) for r in store.query("SELECT name FROM tags")]

    changed: list[tuple[str, str, str]] = []      # (tag, 旧, 新)
    unmatched: list[str] = []
    for tag in lib_tags:
        # Danbooru 为准：连我们手工确认过的修正也一起覆盖（用户要求以社区词表为准）
        hit = booru.get(tag)
        if not hit:
            unmatched.append(tag)
            continue
        new = hit[0]
        old = raw.get(tag, "") or tag_i18n.MODEL_DICT.get(tag, "")
        if new and new != old:
            changed.append((tag, old, new))
    print(f"库里标签 {len(lib_tags)} 个：Danbooru 能对上 {len(lib_tags) - len(unmatched)}，"
          f"其中翻译需要改 {len(changed)}；对不上 {len(unmatched)}")
    for t, o, n in changed[:25]:
        print(f"   {t:<36} {o or '(空)':<16} → {n}")
    if len(changed) > 25:
        print(f"   …其余 {len(changed) - 25} 条")

    # Danbooru 里还有一堆我们词典没收的标签，一并补进来（以后新增标签直接能用）
    added = 0
    for tag, (zh, _cat, _note) in booru.items():
        if tag in ours or tag in raw:
            continue
        raw[tag] = zh
        added += 1

    if args.apply:
        for tag, old, new in changed:
            raw[tag] = new
            hist[tag] = [old, new]
        DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        HIST.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
        UNMATCHED.write_text("\n".join(sorted(unmatched)), encoding="utf-8")
        # 库内已存译名同步：Danbooru 说了算，连用户自己填过的也一起覆盖
        n = 0
        for tag, old, new in changed:
            row = store.one("SELECT id, zh FROM tags WHERE name=?", (tag,))
            if row and (row["zh"] or "").strip() != new:
                store.update_tag(int(row["id"]), zh=new)
                n += 1
        print(f"已写入：词典改动 {len(changed)} 条、新增 {added} 条、库内同步 {n} 条")
        print(f"对不上的 {len(unmatched)} 个标签写到 {UNMATCHED.name}（下一轮单独核）")
    else:
        print(f"（干跑；加 --apply 会写入，并新增 {added} 条 Danbooru 已有的译名）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
