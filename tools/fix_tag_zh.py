"""修正/补充内置汉化词典里的错译与缺译（可反复执行，幂等）。

用法： python tools\\fix_tag_zh.py [--dry-run]

背景：早期批量翻译是模型跑的，混进了错译（如 frieren→梅琳娜）与脏数据
（/no_think、no think 之类）。脏数据已在加载时过滤，这里补的是"译错的"和"漏译的"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DICT = HERE / "app" / "tag_zh_dict.json"

# 各作品的角色译名（按作品分组，才能正确地扩展出 "角色_(作品)" 这种写法）
ARKNIGHTS: dict[str, str] = {
    "swire": "诗怀雅",
    "ch_en": "陈", "chen_(arknights)": "陈（明日方舟）",
    "miyuki_(arknights)": "深雪", "w": "W（明日方舟）", "w_(arknights)": "W（明日方舟）",
    "exusiai": "能天使", "texas_(arknights)": "德克萨斯", "lappland_(arknights)": "拉普兰德",
    "skadi_(arknights)": "斯卡蒂", "surtr_(arknights)": "史尔特尔", "mudrock": "泥岩",
    "kal'tsit": "凯尔希", "amiya_(arknights)": "阿米娅", "nearl": "临光",
    "blaze_(arknights)": "煌", "weedy": "傀影", "bagpipe": "风笛", "saileach": "琴柳",
    "shining_(arknights)": "闪灵", "nightingale_(arknights)": "夜莺",
    "mostima": "莫斯提马", "fiammetta": "菲亚梅塔", "lemuel": "莱缪尔",
    "angelina_(arknights)": "安洁莉娜", "ifrit_(arknights)": "伊芙利特",
    "eyjafjalla": "艾雅法拉", "saria_(arknights)": "塞雷娅", "hoshiguma": "星熊",
    "siege_(arknights)": "号角", "mountain_(arknights)": "山", "thorns": "棘刺",
    "eunectes": "森蚺", "breeze_(arknights)": "微风", "gravel": "砾",
    "pramanix": "初雪", "matterhorn": "角峰", "gummy": "古米",
}
FRIEREN: dict[str, str] = {
    "frieren": "芙莉莲", "fern_(sousou_no_frieren)": "菲伦",
    "stark_(sousou_no_frieren)": "修塔尔克", "himmel_(sousou_no_frieren)": "欣梅尔",
    "heiter_(sousou_no_frieren)": "海塔", "frieren_(sousou_no_frieren)": "芙莉莲",
    "sousou_no_frieren": "葬送的芙莉莲",
}
OTHER: dict[str, str] = {
    "melina_(elden_ring)": "梅琳娜",
    "gawr_gura": "噶呜·古拉", "watson_amelia": "阿米莉亚·华生",
    # 联网复核里确认过的普通词
    "hatsune_miku": "初音未来", "3d_render": "3D渲染", "manga_page": "漫画页面",
}

SERIES_SUFFIX = {"_(arknights)": ARKNIGHTS, "_(sousou_no_frieren)": FRIEREN}


def build_fixes() -> dict[str, str]:
    """基础修正表 + 按作品的 "角色_(作品)" 扩展（只对同作品的角色展开，避免瞎关联）。"""
    fixes: dict[str, str] = {}
    fixes.update(ARKNIGHTS)
    fixes.update(FRIEREN)
    fixes.update(OTHER)
    for suffix, group in SERIES_SUFFIX.items():
        for key, val in group.items():
            if "_(" not in key:
                fixes.setdefault(key + suffix, val)
    return fixes


def main() -> int:
    dry = "--dry-run" in sys.argv
    raw = json.loads(DICT.read_text(encoding="utf-8"))
    fixes = build_fixes()
    # 清掉上一轮"按所有作品盲目展开"造成的瞎关联（如 watson_amelia_(sousou_no_frieren)）
    valid = set(fixes)
    for suffix, group in SERIES_SUFFIX.items():
        for key, _val in group.items():
            valid.add(key + suffix)
    removed = 0
    bases = {k for k in fixes if "_(" not in k}
    for k in list(raw):
        for suffix, group in SERIES_SUFFIX.items():
            if k.endswith(suffix):
                base = k[: -len(suffix)]
                if base in bases and k not in valid:
                    raw.pop(k)
                    removed += 1
    changed, added = 0, 0
    for k, v in fixes.items():
        old = raw.get(k)
        if old == v:
            continue
        if old is None:
            added += 1
        else:
            changed += 1
        print(f"  {k:34} {old!r} → {v!r}")
        raw[k] = v
    if not dry:
        DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    if removed:
        print(f"清掉误加条目 {removed} 条")
    print(f"修正 {changed} 条、新增 {added} 条" + ("（干跑，未写入）" if dry else f"；词典现在 {len(raw)} 条"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
