"""收尾：把"其实不是人物"的词从人物分类挪走，并把几个中文标签归位。

用法： python tools\\fix_misc_categories.py [--apply]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# 不是人物名 → 正确分类
NOT_CHARACTER = {
    "completely_nude": "body", "clothed_male_nude_female": "interaction",
    "siblings": "count", "sisters": "count", "threesome": "count", "group_sex": "interaction",
    "hetero": "interaction", "office_lady": "body", "loli": "body", "shota": "body",
    "onii-shota": "body", "k-pop": "misc", "1other": "count", "female": "count",
    "?": "misc", "tiger_girl": "character", "cat_girl": "character", "fox_girl": "character",
    "wolf_girl": "character", "dragon_girl": "character", "demon_girl": "character",
    "rabbit_girl": "character", "elf": "character", "kemonomimi": "character",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from app.config import Settings
    from app.library import Library
    from app.store import Store
    s = Store()
    plan = []
    for name, cat in NOT_CHARACTER.items():
        row = s.one("SELECT id, category FROM tags WHERE name=?", (name,))
        if row and str(row["category"]) != cat:
            plan.append((int(row["id"]), name, str(row["category"]), cat))
    # 明日方舟本身是作品，不是人物
    row = s.one("SELECT id, category FROM tags WHERE name='明日方舟'")
    if row and str(row["category"]) != "series":
        plan.append((int(row["id"]), "明日方舟", str(row["category"]), "series"))
    print(f"计划改类型 {len(plan)} 条：")
    for _i, n, c, w in plan[:20]:
        print(f"   {n:<30} {c} → {w}")
    if args.apply and plan:
        db = s.db_path
        backup = db.with_name(f"library_before_misccat_{time.strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db, backup)
        print("已备份：", backup.name)
        for tid, _n, _c, w in plan:
            s.update_tag(tid, category=w)
    # 辛美尔 → 建标准标签 himmel_(sousou_no_frieren) 并合并
    xin = s.one("SELECT id FROM tags WHERE name='辛美尔'")
    him = s.one("SELECT id FROM tags WHERE name='himmel_(sousou_no_frieren)'")
    if xin and not him:
        print("把「辛美尔」并到 himmel_(sousou_no_frieren)")
        if args.apply:
            tid = s.save_tag("himmel_(sousou_no_frieren)", "character")
            s.update_tag(int(tid), zh="辛美尔")
            s.merge_tags(int(xin["id"]), int(tid))
    # 重音特托 归到 VOCALOID（UTAU 通常和 VOCALOID 一起看）
    teto = s.one("SELECT id FROM tags WHERE name='kasane_teto'")
    voc = s.one("SELECT id FROM tags WHERE name='vocaloid'")
    if teto and voc and not s.tag_parents(int(teto["id"])):
        print("把 kasane_teto 挂到 vocaloid 下")
        if args.apply:
            s.link_tag_sub(int(voc["id"]), int(teto["id"]))
    if args.apply:
        Library(s, Settings.load()).relink_all_categories()
        s.refresh_counts()
        print("完成")
    else:
        print("（干跑，加 --apply 才写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
