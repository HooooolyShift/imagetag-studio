"""给还没有作品归属的人物标签补上"作品 → 人物"从属关系。

数据来源：本地 Danbooru 词表 app/booru_zh/
  · character.csv 的 notes 字段常写着「《作品名》的…」，从这里取作品；
  · copyright.csv 是作品表（tag + 中文名），用它把中文作品名映射回英文标签名。

用法：
    python tools\\fill_character_series.py            # 干跑
    python tools\\fill_character_series.py --apply    # 真建（自动备份数据库）
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
CACHE = HERE / "app" / "booru_zh"

# 中文作品名 → 我们库里已有的作品标签名（词表对不上的少数几个手工兜底）
MANUAL: dict[str, str] = {
    "物语系列": "monogatari", "轻音少女": "k-on", "斩服少女": "kill_la_kill",
    "魔法禁书目录": "toaru_majutsu_no_index", "莉可丽丝": "lycoris_recoil",
    "Lycoris Recoil": "lycoris_recoil", "擅长捉弄人的高木同学": "teasing_master_takagi-san",
    "请问您今天要来点兔子吗？": "gochuumon_wa_usagi_desu_ka", "偶像大师 灰姑娘女孩": "idolmaster_cinderella_girls",
    "鬼灭之刃": "kimetsu_no_yaiba", "咒术回战": "jujutsu_kaisen", "原神": "genshin_impact",
    "蔚蓝档案": "blue_archive", "明日方舟": "arknights", "赛马娘": "umamusume",
    "葬送的芙莉莲": "sousou_no_frieren", "电锯人": "chainsaw_man", "间谍过家家": "spy_x_family",
    "东方Project": "touhou", "VOCALOID": "vocaloid", "新世纪福音战士": "evangelion",
}


def load(name: str) -> list[dict]:
    p = CACHE / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    chars = {r["tag"]: r for r in load("character.csv") if r.get("tag")}
    copyr = load("copyright.csv")
    zh2series: dict[str, str] = {}
    for r in copyr:
        tag = (r.get("tag") or "").strip()
        zh = (r.get("zh") or "").strip()
        if tag:
            zh2series.setdefault(zh or tag, tag)
    for zh, tag in MANUAL.items():
        zh2series.setdefault(zh, tag)
    from app.store import Store
    s = Store()
    by_name = {str(r["name"]): int(r["id"]) for r in s.query("SELECT id, name FROM tags")}
    by_zh = {str(r["zh"]): int(r["id"]) for r in s.query("SELECT id, zh FROM tags")
             if (r["zh"] or "").strip()}
    plan: list[tuple[str, str, str]] = []          # 人物, 作品名(中/英), 依据
    for r in s.query("SELECT id, name FROM tags WHERE category='character'"):
        name = str(r["name"])
        if s.tag_parents(int(r["id"])):
            continue
        row = chars.get(name)
        w = ""
        if row:
            m = re.search(r"《([^》]{2,20})》", row.get("notes") or "")
            if m:
                w = m.group(1)
        if not w:
            continue
        plan.append((name, w, "词表 notes"))
    print(f"能补上作品的人物：{len(plan)} 个")
    for n, w, how in plan[:20]:
        tag = zh2series.get(w, "")
        print(f"   {n:<34} 《{w}》 → {tag or '（词表里没有对应作品标签，将新建）'}")
    if not args.apply:
        print("（干跑，加 --apply 才写入）")
        return 0
    db = s.db_path
    backup = db.with_name(f"library_before_seriesfill_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print("已备份：", backup.name)
    made_tag = made_link = 0
    for name, w, _how in plan:
        tag = zh2series.get(w) or re.sub(r"[^\w]+", "_", w).strip("_").lower()
        sid = by_name.get(tag)
        if sid is None:
            sid = s.save_tag(tag, "series")
            s.update_tag(int(sid), zh=w)
            by_name[tag] = int(sid)
            made_tag += 1
        cid = by_name.get(name)
        if cid:
            s.link_tag_sub(int(sid), int(cid))
            made_link += 1
    from app.config import Settings
    from app.library import Library
    Library(s, Settings.load()).relink_all_categories()
    s.refresh_counts()
    print(f"新建作品标签 {made_tag} 个，建立 作品→人物 关系 {made_link} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
