"""把"我自己用中文建的标签"合并到库里已有的英文标签上（若存在对应项）。

用法：
    python tools\\merge_zh_tags.py            # 干跑，先看会合并哪些
    python tools\\merge_zh_tags.py --apply    # 真合并（会先备份数据库）

找对应英文标签的三条路：
  1) 内置词典反查：百合 → yuri；
  2) 库里已有标签的中文名正好等于它：芙丽莲? → 无（这时走第 3 条）；
  3) 联网（Wikidata/中文维基）：中文名 → 实体 → 英文名 → 在库里找 frieren / himmel_(...) 这类标签。

合并 = 把源标签的图片、连线、探针都并到目标标签，然后删掉源标签（图片文件不动）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
CACHE = Path(os.environ.get("TEMP", ".")) / "imtag_zh_merge_cache.json"
CJK = re.compile(r"[\u4e00-\u9fff]")
UA = {"User-Agent": "ImageTagStudio/1.3 (zh-tag-merge; local use)"}

# 人工确认过的对应关系（在线源被限流/译名五花八门时的兜底，优先于自动匹配）
CURATED: dict[str, list[str]] = {
    "芙丽莲": ["frieren", "frieren_(sousou_no_frieren)"],
    "芙莉莲": ["frieren", "frieren_(sousou_no_frieren)"],
    "辛美尔": ["himmel", "himmel_(sousou_no_frieren)"],
    "菲亚梅塔": ["fiammetta_(arknights)", "fiammetta"],
    "莫斯提马": ["mostima_(arknights)", "mostima"],
    "明日方舟": ["arknights"],
    "百合": ["yuri"],
}


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def english_name_of(zh: str, cache: dict) -> str:
    """中文名 → 英文名：中文维基搜索 → 取该条目的英文跨语言链接。

    （直接搜 Wikidata 的中文标签命中率很差，而且调用快一点就被限流 429；
      中文维基的搜索+langlinks 更稳，也少一次请求。）
    """
    if zh in cache:
        return cache[zh]
    out = ""
    try:
        q = urllib.parse.quote(zh)
        search = _get_json("https://zh.wikipedia.org/w/api.php?action=query&list=search"
                           f"&srsearch={q}&format=json&srlimit=5")
        time.sleep(0.4)                                  # 温柔一点，别被限流
        titles = [h["title"] for h in search.get("query", {}).get("search", [])]
        for title in titles:
            t = urllib.parse.quote(title)
            ll = _get_json(f"https://zh.wikipedia.org/w/api.php?action=query&titles={t}"
                           f"&prop=langlinks&lllang=en&format=json&redirects=1")
            time.sleep(0.4)
            for page in (ll.get("query", {}).get("pages", {}) or {}).values():
                links = page.get("langlinks") or []
                if links:
                    out = links[0].get("*", "")
                    break
            # 中文条目名本身可能就带英文括号说明，取不到 langlinks 就算了
            if out:
                break
    except Exception:
        out = ""
    cache[zh] = out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from app import tag_i18n
    from app.store import Store
    store = Store()
    zh_rev = tag_i18n.zh_to_name()                      # 百合 → yuri
    cache = _cache()
    all_tags = {str(r["name"]): r for r in store.query("SELECT * FROM tags")}
    by_zh: dict[str, str] = {}
    for name, r in all_tags.items():
        if (r["zh"] or "").strip():
            by_zh.setdefault(str(r["zh"]).strip(), name)

    plan: list[tuple[int, str, int, str, str]] = []      # src_id, src_name, dst_id, dst_name, how
    for r in store.query("SELECT id, name, zh, count FROM tags WHERE name GLOB '*[一-龥]*' "
                         "AND name NOT IN ('全年龄','R15','R18','R18G') ORDER BY count DESC"):
        src = str(r["name"])
        dst = ""
        how = ""
        cand = zh_rev.get(src) or by_zh.get(src)
        for guess in CURATED.get(src, []):
            if guess in all_tags and guess != src:
                dst, cand, how = guess, guess, "人工确认的对应关系"
                break
        if not how and cand and cand != src and cand in all_tags:
            dst, how = cand, "词典/库内中文名"
        elif not how:
            en = english_name_of(src, cache)
            if en:
                key = en.strip().lower().replace(" ", "_")
                if key in all_tags:
                    dst, how = key, f"联网（{en}）"
                else:                                     # frieren_(sousou_no_frieren) 这类
                    m = [n for n in all_tags if n.lower() == key or n.lower().startswith(key + "_")
                         or n.lower().startswith(key + "(")]
                    if len(m) == 1:
                        dst, how = m[0], f"联网（{en}）"
        if dst:
            plan.append((int(r["id"]), src, int(all_tags[dst]["id"]), dst, how))
        else:
            print(f"  · {src:<12} 库里没有对应英文标签，跳过（{int(r['count'])} 张）")

    print(f"\n计划合并 {len(plan)} 组：")
    for _sid, src, _did, dst, how in plan:
        s_cnt = store.one("SELECT COUNT(*) c FROM file_tags WHERE tag_id=?", (_sid,))["c"]
        d_cnt = store.one("SELECT COUNT(*) c FROM file_tags WHERE tag_id=?", (_did,))["c"]
        print(f"  {src:<12}（{s_cnt} 张） → {dst:<28}（{d_cnt} 张）  依据：{how}")

    if not args.apply:
        print("\n（干跑，加 --apply 才会真合并）")
        return 0
    if not plan:
        return 0

    db = store.db_path
    backup = db.with_name(f"library_before_merge_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print(f"\n已备份数据库：{backup.name}")
    for sid, src, did, dst, how in plan:
        store.merge_tags(sid, did)
        print(f"  已合并 {src} → {dst}")
    store.refresh_counts()
    print(f"完成，剩余标签 {store.one('SELECT COUNT(*) c FROM tags')['c']} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
