"""把人物标签归到对应作品下（tag→tag 从属关系），图谱里就是"作品 ← 人物"的交叉连线。

用法：
    python tools\\link_series_chars.py            # 干跑，先看会连哪些
    python tools\\link_series_chars.py --apply    # 真连（自动备份数据库）

依据：
  1) 人物名里带作品后缀的（mostima_(arknights) → arknights）—— 覆盖大多数；
  2) 少量知名人物没有后缀的，用下面的手工对照表补（初音未来 → VOCALOID 等）。
作品标签不存在就按类型 series 建一个，中文名取内置词典/修正表。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

SUFFIX = re.compile(r"^(.+?)_\(([^()]+)\)$")

# 名字里不带作品的知名人物 → 作品
CURATED: dict[str, str] = {
    "hatsune_miku": "vocaloid", "kagamine_rin": "vocaloid", "kagamine_len": "vocaloid",
    "megurine_luka": "vocaloid", "kaito": "vocaloid", "meiko": "vocaloid",
    "gumi_(vocaloid)": "vocaloid", "ia_(vocaloid)": "vocaloid",
    "izumi_sagiri": "eromanga_sensei", "souryuu_asuka_langley": "evangelion",
    "ayanami_rei": "evangelion", "shikinami_asuka_langley": "evangelion",
    "hakurei_reimu": "touhou", "kirisame_marisa": "touhou", "remilia_scarlet": "touhou",
    "flandre_scarlet": "touhou", "izayoi_sakuya": "touhou", "konpaku_youmu": "touhou",
    "patchouli_knowledge": "touhou", "kochiya_sanae": "touhou", "cirno": "touhou",
    "artoria_pendragon": "fate", "saber_(fate)": "fate", "rin_tohsaka": "fate",
    "sakura_matou": "fate", "illyasviel_von_einzbern": "fate",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from app import tag_i18n
    from app.store import Store
    store = Store()
    chars = store.query("SELECT id, name FROM tags WHERE category='character'")
    by_name = {str(r["name"]): int(r["id"]) for r in store.query("SELECT id, name FROM tags")}
    by_zh = {str(r["zh"]): int(r["id"]) for r in store.query("SELECT id, zh FROM tags")
             if (r["zh"] or "").strip()}

    plan: list[tuple[str, str, str]] = []      # 作品名, 人物名, 依据
    for r in chars:
        name = str(r["name"])
        m = SUFFIX.match(name)
        if m:
            plan.append((m.group(2), name, "名称后缀"))
        elif name in CURATED:
            plan.append((CURATED[name], name, "手工对照"))

    per_series = Counter(s for s, _c, _w in plan)
    print(f"人物 {len(chars)} 个 → 可归类 {len(plan)} 个，涉及 {len(per_series)} 部作品：")
    for s, n in per_series.most_common(20):
        zh = tag_i18n.MODEL_DICT.get(s, "")
        exists = "（已有标签）" if s in by_name else ""
        print(f"   {s:<28} {n:>3} 人  中文名={zh or '（无，稍后补）'}{exists}")

    if not args.apply:
        print("\n（干跑，加 --apply 才会真连）")
        return 0

    db = store.db_path
    backup = db.with_name(f"library_before_series_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print("\n已备份：", backup.name)
    made_tags = made_links = 0
    series_id: dict[str, int] = {}
    for s_name, char_name, how in plan:
        sid = series_id.get(s_name)
        if sid is None:
            sid = by_name.get(s_name)
            if sid is None:                    # 库里可能已有"中文名版本"的作品标签（如 明日方舟）
                zh_name = tag_i18n.MODEL_DICT.get(s_name, "")
                if zh_name and zh_name in by_zh:
                    sid = by_zh[zh_name]
                    store.rename_tag(int(sid), s_name)      # 统一成规范英文名，中文名留在 zh 里
                    store.update_tag(int(sid), zh=zh_name, category="series")
                    by_name[s_name] = int(sid)
                    print(f"   已把库里「{zh_name}」规范成 {s_name}（中文名保留）")
            if sid is None:                    # 作品标签不存在 → 建一个
                zh = tag_i18n.MODEL_DICT.get(s_name, "")
                sid = store.save_tag(s_name, "series")
                if zh:
                    store.update_tag(sid, zh=zh)
                by_name[s_name] = sid
                made_tags += 1
            series_id[s_name] = sid
        cid = by_name.get(char_name)
        if cid and cid != sid:
            store.link_tag_sub(int(sid), int(cid))
            made_links += 1
    store.refresh_counts()
    from app.config import Settings
    from app.library import Library
    lib = Library(store, Settings.load())
    lib.relink_all_categories()                # 分类边照旧，从属边是另建的
    print(f"新建作品标签 {made_tags} 个，建立 作品→人物 从属关系 {made_links} 条")
    print("图谱里看到的形状：作品节点向外连出一堆人物（交叉状），人物仍挂在「人物/角色」分类下。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
