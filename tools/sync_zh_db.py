"""把汉化词典的"缺口"补齐，并同步到库里，保证各个界面显示的都是同一个中文名。

背景：界面显示中文名的优先级是

    标签自身的 zh（可以手动改的备注） → 词典 → 逐词拼装

所以只要库里 tags.zh 里存了旧值（比如早期模型翻译、或者干脆是英文原名），
界面就会一直显示旧的那个，词典更新了也不生效。这个脚本干两件事：

1. 补词典缺口：只往 app/tag_zh_dict.json 里塞"目前查不到中文"的条目，
   已经有译名的一律不动（以 Danbooru 社区词表为准，不覆盖）。
2. 同步到库：tags.zh 为空、或者存的压根不是中文（英文原名/数字之类）时，用词典值覆盖；
   已经是中文的（无论是词典来的还是自己手填的备注）一律不动。

用法：
    python tools\\sync_zh_db.py            # 干跑，只打印
    python tools\\sync_zh_db.py --apply    # 写入词典 + 库
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DICT = HERE / "app" / "tag_zh_dict.json"

# 词典里查不到、但确实该有中文名的条目（逐条看过）
GAPS: dict[str, str] = {
    # 人物
    "rem_(re:zero)": "雷姆", "ram_(re:zero)": "拉姆",
    "nero_claudius_(fate/extra)": "尼禄·克劳狄乌斯",
    "jack_the_ripper_(fate/apocrypha)": "开膛手杰克",
    "grimm": "格林", "naga_u": "长宇", "kero": "小可",
    "watson_amelia": "阿米莉亚·华生",
    # 作品
    "the_legend_of_luoxiaohei": "罗小黑战记",
    "re:zero": "Re:从零开始的异世界生活",
    "fate/extra": "Fate/EXTRA", "fate/apocrypha": "Fate/Apocrypha",
    "fate_grand_order": "Fate/Grand Order", "fate_kaleid_liner": "魔法少女伊莉雅",
    "gochuumon_wa_usagi_desu_ka": "请问您今天要来点兔子吗",
    "psg": "Panty & Stocking",
    # 普通词
    "basketball": "篮球", "object": "物品", "female": "女性",
    "1st_costume": "第一套服装", "spoken_interrobang": "说话中的叹问号",
    "yes-no_pillow": "是非抱枕", "real_photo": "真人照片",
    # 分级（必备标签，界面上要能看懂）
    "R15": "R15（15禁）", "R18": "R18（18禁）", "R18G": "R18G（18禁·猎奇）",
    "全年龄": "全年龄", "G级": "全年龄",
    # emoji / 颜文字这类不翻译，但给个能看懂的备注
    ":d": "笑脸（:d）", ":p": "吐舌（:p）", ":q": "吐舌（:q）",
    ":o": "惊讶（:o）", ":3": "猫嘴（:3）", ":<": "不悦（:<）",
    ":>": "得意（:>）", ":t": "困惑（:t）", ":i": "无语（:i）",
    "...": "省略号", "+++": "加号", "^^^": "上箭头", ">_<": "闭眼（>_<）",
    "??": "问号", "|_|": "竖线", "0_0": "呆滞（0_0）", ">_o": "歪嘴（>_o）",
}

CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")


def usable(text: str) -> bool:
    return bool(CJK.search(text or ""))


def main() -> int:
    apply = "--apply" in sys.argv
    sys.path.insert(0, str(HERE))
    from app import tag_i18n

    raw = json.loads(DICT.read_text(encoding="utf-8"))
    added = []
    for key, val in GAPS.items():
        cur = raw.get(key)
        if isinstance(cur, str) and usable(cur):
            continue                       # 词典里已经有中文了，不动（Danbooru 为准）
        calc = tag_i18n.label(key, "")
        if usable(calc):
            continue                       # 拼装就能得到中文，不用额外塞
        raw[key] = val
        added.append((key, val))
    if added:
        print("补词典缺口 %d 条：" % len(added))
        for k, v in added:
            print("   %-32s → %s" % (k, v))
        if apply:
            DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
            print("   词典已写入，现在 %d 条" % len(raw))
    else:
        print("词典没有缺口")

    db = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ImageTagStudio", "library.db")
    if not os.path.exists(db):
        print("找不到库：%s" % db)
        return 0
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id,name,zh FROM tags").fetchall()
    plan = []
    for r in rows:
        stored = (r["zh"] or "").strip()
        if usable(stored):
            continue                       # 已经是中文：可能是手填备注，不动
        calc = tag_i18n.label(r["name"], "")
        if not usable(calc) or calc == r["name"]:
            continue
        plan.append((int(r["id"]), r["name"], stored, calc))
    print()
    print("库里需要同步中文名的标签 %d 个：" % len(plan))
    for _tid, name, stored, calc in sorted(plan, key=lambda t: t[1])[:60]:
        print("   %-32s %-18s → %s" % (name, stored or "(空)", calc))
    if apply and plan:
        for tid, _name, _stored, calc in plan:
            con.execute("UPDATE tags SET zh=? WHERE id=?", (calc, tid))
        con.commit()
        print("   已写回 %d 个标签" % len(plan))
    elif not apply:
        print("   （干跑，加 --apply 才写入）")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
