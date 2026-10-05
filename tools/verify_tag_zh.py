"""联网复核内置汉化词典（本地小模型翻错的专有名词，靠在线权威源纠正）。

用法：
    python tools\\verify_tag_zh.py --limit 400            # 只出报告
    python tools\\verify_tag_zh.py --limit 400 --apply   # 应用高置信度修正并写记录

来源：
  1) 专有名词（人名/作品名，如 frieren、swire_(arknights)）→ Wikidata 中文标签 + 中文维基标题；
  2) 普通词 → MyMemory 翻译（低置信度，只作为建议，默认不自动应用）。

结果写两份：字典本身（--apply 时）+ 复核记录 词典复核记录.txt（逐条留痕，方便你回退/查看）。
需要联网（走系统代理）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
DICT = HERE / "app" / "tag_zh_dict.json"
LOG = HERE / "词典复核记录.txt"
CACHE = Path(os.environ.get("TEMP", ".")) / "imtag_zh_verify_cache.json"

CJK = re.compile(r"[\u4e00-\u9fff]")
# 只把"带作品后缀"的当成专有名词：swire_(arknights)、rem_(re:zero)。
# 以前把 long_hair / open_mouth 这种普通复合词也当专有名词，结果匹配到一堆维基百科乱条目
PROPER = re.compile(r"^[a-z0-9][a-z0-9'’\-_. ]*_?\([a-z0-9'’\-_. :]+\)$")
PROPER_CATS = {"character", "real_person", "series"}
GOOD_DESC = ("anime", "manga", "character", "fictional", "film", "television", "series",
             "video game", "light novel", "novel", "comic", "virtual youtuber", "vocaloid")

UA = {"User-Agent": "ImageTagStudio/1.3 (tag-zh-verify; local use)"}


def _get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c: dict) -> None:
    try:
        CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-·・（）()\[\]【】.,'’]+", "", (s or "").lower())


def wikidata_zh(term: str, cache: dict) -> str:
    """英文标签 → Wikidata 中文标签（只在实体看起来是 ACG 相关时才采纳）。"""
    key = f"wd:{term}"
    if key in cache:
        return cache[key]
    out = ""
    try:
        q = urllib.parse.quote(term)
        data = json.loads(_get(f"https://www.wikidata.org/w/api.php?action=wbsearchentities"
                               f"&search={q}&language=en&uselang=en&format=json&limit=6"))
        best = None
        for hit in data.get("search", []):
            label = hit.get("label", "")
            desc = (hit.get("description") or "").lower()
            if _norm(label) != _norm(term):
                continue
            if any(w in desc for w in GOOD_DESC):
                best = hit
                break
            # 说明里看不出是 ACG 相关就不采纳（宁可留空，也别写进莫名其妙的词条）
        if best:
            ids = best["id"]
            ent = json.loads(_get("https://www.wikidata.org/w/api.php?action=wbgetentities"
                                  f"&ids={ids}&props=labels&languages=zh|zh-cn|zh-hans|zh-tw&format=json"))
            labels = ent.get("entities", {}).get(ids, {}).get("labels", {})
            for lang in ("zh", "zh-cn", "zh-hans", "zh-tw"):
                v = (labels.get(lang) or {}).get("value")
                # 严格要求：有中文、长度合理、不是"某某·某某"这种外文人名转写
                if v and CJK.search(v) and len(v) <= 8 and "·" not in v and "（" not in v:
                    out = v
                    break
    except Exception:
        out = ""
    cache[key] = out
    return out


def wikipedia_zh_title(term: str, cache: dict) -> str:
    """中文维基搜索兜底：取标题里带中文的第一个结果。"""
    key = f"wp:{term}"
    if key in cache:
        return cache[key]
    out = ""
    try:
        q = urllib.parse.quote(term)
        data = json.loads(_get(f"https://zh.wikipedia.org/w/api.php?action=query&list=search"
                               f"&srsearch={q}&format=json&srlimit=5"))
        for hit in data.get("query", {}).get("search", []):
            title = re.sub(r"\s*\(.*?\)\s*$", "", hit.get("title", ""))
            if CJK.search(title):
                out = title
                break
    except Exception:
        out = ""
    cache[key] = out
    return out


def mymemory_zh(term: str, cache: dict) -> str:
    key = f"mm:{term}"
    if key in cache:
        return cache[key]
    out = ""
    try:
        q = urllib.parse.quote(term.replace("_", " "))
        data = json.loads(_get(f"https://api.mymemory.translated.net/get?q={q}&langpair=en|zh-CN"))
        v = (data.get("responseData") or {}).get("translatedText") or ""
        out = v if CJK.search(v) and "MYMEMORY WARNING" not in v.upper() else ""
    except Exception:
        out = ""
    cache[key] = out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400, help="最多复核多少个标签（按使用量排序）")
    ap.add_argument("--apply", action="store_true", help="应用高置信度修正并写记录")
    ap.add_argument("--engine", choices=("online",), default="online")
    args = ap.parse_args()

    from app.store import Store
    from app import tag_i18n
    store = Store()
    rows = store.query("SELECT t.name, t.zh, t.category, "
                       "(SELECT COUNT(*) FROM file_tags f WHERE f.tag_id=t.id) AS n "
                       "FROM tags t ORDER BY n DESC, t.name LIMIT ?", (args.limit,))
    cache = _load_cache()
    fixes: list[tuple[str, str, str, str, int]] = []       # name, old, new, source, n
    for i, r in enumerate(rows):
        name = str(r["name"])
        old = (r["zh"] or "") or tag_i18n.MODEL_DICT.get(name.lower(), "")
        proper = bool(PROPER.match(name)) or str(r["category"] or "") in PROPER_CATS
        new, src = "", ""
        if proper and not CJK.search(name):
            base = re.sub(r"_?\(.*?\)$", "", name).replace("_", " ")
            new = wikidata_zh(base, cache)
            src = "wikidata"
            if not new and "--loose" in sys.argv:      # 中文维基那条路误配太多，默认不用
                new = wikipedia_zh_title(base, cache)
                src = "zh-wikipedia(待核)"
        # 中文名（用户自己起的）+ 没有对应译文时，不做机器翻译——那属于"该合并到已有英文标签"
        elif not CJK.search(name) and (not old or not CJK.search(old)):
            new = mymemory_zh(name, cache)
            src = src or "mymemory(低置信)"
        if new and len(new) <= 12 and _norm(new) != _norm(old):
            fixes.append((name, old, new, src, int(r["n"])))
        if i % 25 == 0:
            _save_cache(cache)
            print(f"  ...{i + 1}/{len(rows)}，已找到 {len(fixes)} 条待改")
    _save_cache(cache)

    high = [f for f in fixes if not f[3].startswith("mymemory")]
    low = [f for f in fixes if f[3].startswith("mymemory")]
    print(f"\n复核完成：高置信 {len(high)} 条（权威源），低置信 {len(low)} 条（机器翻译建议）")
    for name, old, new, src, n in high[:25]:
        print(f"  [高] {name:34} {old or '(空)':<14} → {new:<14} ({src}, {n} 张)")
    for name, old, new, src, n in low[:10]:
        print(f"  [低] {name:34} {old or '(空)':<14} → {new:<14} ({src}, {n} 张)")

    if args.apply and high:
        raw = json.loads(DICT.read_text(encoding="utf-8"))
        stamp = time.strftime("%Y-%m-%d %H:%M")
        lines = [f"# 词典联网复核记录（{stamp}）", f"高置信修正 {len(high)} 条，来源：Wikidata / 中文维基", ""]
        for name, old, new, src, n in high:
            raw[name] = new
            lines.append(f"{name}\t{old or '(空)'}\t{new}\t{src}\t图 {n} 张")
        DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已应用 {len(high)} 条，记录写到 {LOG.name}；词典现在 {len(raw)} 条")
    elif fixes:
        print("（未应用，加 --apply 才会写入词典）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
