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
    # —— 高频词逐条复核（2026-10-05，按图片使用量排序看下来的错译）——
    "thighhighs": "过膝袜", "white_thighhighs": "白色过膝袜", "black_thighhighs": "黑色过膝袜",
    "pantyhose": "连裤袜", "black_pantyhose": "黑色连裤袜",
    "small_breasts": "小胸", "medium_breasts": "中等胸部", "large_breasts": "大胸",
    "hetero": "异性恋", "ahoge": "呆毛", "bow": "蝴蝶结", "on_back": "仰卧",
    "shibari": "日式绳缚", "gag": "口塞", "gagged": "戴口塞", "ball_gag": "口球",
    "wiffle_gag": "空心口塞", "gag_harness": "口塞束带",
    "pussy_juice": "爱液", "anal": "肛交", "anal_object_insertion": "物体插入肛门",
    "vaginal_object_insertion": "物体插入阴道", "object_insertion": "物体插入",
    "cowboy_shot": "牛仔镜头", "detached_sleeves": "分离式袖子", "hood": "兜帽",
    "soles": "脚底", "bottomless": "下半身赤裸", "cleavage": "乳沟", "collar": "项圈",
    "censored": "打码", "mosaic_censoring": "马赛克遮挡", "open_clothes": "衣服敞开",
    "grin": "咧嘴笑", "suspension": "悬吊", "motion_lines": "动感线",
    "twin_drills": "双钻卷发", "spreader_bar": "撑开器", "futanari": "扶她",
    "cat_girl": "猫娘", "fox_girl": "狐狸娘", "wolf_girl": "狼娘",
    "dragon_girl": "龙娘", "rabbit_girl": "兔娘", "shota": "正太",
    "otoko_no_ko": "伪娘", "fang": "虎牙", "ascot": "领巾", "2koma": "两格漫画",
    "kasane_teto": "重音特托", "looking_at_viewer": "看向观众", "blush": "脸红",
    "animal_ear_fluff": "兽耳绒毛", "torn_clothes": "衣服破损", "1girl": "一个女孩",
    "2girls": "两个女孩", "multiple_girls": "多个女孩", "1boy": "一个男孩",
    # —— 未匹配清单里逐条看出来的错译（Danbooru 词表没有或对不上）——
    "china_dress": "旗袍", "habit": "骑马装", "clitoris_piercing": "阴蒂穿孔",
    "skirt_hold": "掀起裙子", "topless": "上半身赤裸", "lace-trimmed_legwear": "蕾丝边袜",
    "looking_through_legs": "从腿间看", "tail_censor": "尾巴打码",
    "child_on_child": "儿童之间", "basketball": "篮球", "formal": "正装",
    "exercise": "运动", "gym": "健身房", "real_photo": "真实照片",
    "1st_costume": "第一套服装", "female": "女性", "object": "物品",
    "vtuber": "虚拟主播", "weibo_logo": "微博标志", "re:zero": "Re:从零开始的异世界生活",
    "hair_censor": "头发打码", "identity_censor": "身份遮挡", "presenting": "翘起臀部",
    "birthday": "生日", "cup_ramen": "杯面", "doughnut": "甜甜圈", "baozi": "包子",
    "grinding": "磨蹭", "interface_headset": "接口耳机", "test_plugsuit": "测试用驾驶服",
    "plugsuit": "驾驶服", "keyboard_(computer)": "电脑键盘", "mouse_(computer)": "电脑鼠标",
    "nijigasaki_academy_school_uniform": "虹咲学园制服", "night_sky": "夜空",
    "red_curtains": "红色帷幔", "on_grass": "在草地上", "round_image": "圆形构图",
    "see-through": "透视", "see-through_swimsuit": "透视泳装", "superhero": "超级英雄",
    "mechanical_eye": "机械眼", "mechanical_parts": "机械部件", "meme_attire": "梗图装扮",
    "arm_around_waist": "搂腰", "arm_grab": "抓手臂", "ass_grab": "摸屁股",
    "horn_grab": "抓角", "holding_hair": "抓头发", "ear_biting": "咬耳朵",
    "no_headwear": "不戴头饰", "no_tail": "没有尾巴", "roots": "根",
}

SERIES_SUFFIX = {"_(arknights)": ARKNIGHTS, "_(sousou_no_frieren)": FRIEREN}

# 作品名（tag→tag 从属关系里的"父"），给人物归类用
SERIES_ZH: dict[str, str] = {
    "arknights": "明日方舟", "blue_archive": "蔚蓝档案", "genshin_impact": "原神",
    "honkai:_star_rail": "崩坏：星穹铁道", "honkai_impact_3rd": "崩坏3",
    "kancolle": "舰队Collection", "azur_lane": "碧蓝航线", "girls'_frontline": "少女前线",
    "fate": "Fate系列", "fate/grand_order": "Fate/Grand Order", "pokemon": "宝可梦",
    "girls_und_panzer": "少女与战车", "chainsaw_man": "电锯人",
    "re:zero": "Re:从零开始的异世界生活", "sousou_no_frieren": "葬送的芙莉莲",
    "cyberpunk": "赛博朋克", "cyberpunk:_edgerunners": "赛博朋克：边缘行者",
    "spy_x_family": "间谍过家家", "vocaloid": "VOCALOID", "konosuba": "为美好的世界献上祝福",
    "nier": "尼尔", "nier:automata": "尼尔：机械纪元", "sao": "刀剑神域",
    "umamusume": "赛马娘", "majo_no_tabitabi": "魔女之旅", "shoujo_shuumatsu_ryokou": "少女终末旅行",
    "touhou": "东方Project", "love_live": "LoveLive!", "idolmaster": "偶像大师",
    "one_piece": "海贼王", "naruto": "火影忍者", "bleach": "死神", "jujutsu_kaisen": "咒术回战",
    "kimetsu_no_yaiba": "鬼灭之刃", "boku_no_hero_academia": "我的英雄学院",
    "attack_on_titan": "进击的巨人", "evangelion": "新世纪福音战士", "neon_genesis_evangelion": "新世纪福音战士",
    "eromanga_sensei": "情色漫画老师", "oreshura": "我的妹妹不可能那么可爱",
    "dragon_ball": "龙珠", "sailor_moon": "美少女战士", "cardcaptor_sakura": "魔卡少女樱",
    "puella_magi_madoka_magica": "魔法少女小圆", "steins;gate": "命运石之门",
    "hololive": "hololive", "nijisanji": "彩虹社", "genshin": "原神",
    "arknights:_operators": "明日方舟", "blue_archive_(game)": "蔚蓝档案",
    "project_sekai": "世界计划", "bang_dream": "邦邦", "osomatsu_san": "阿松",
    "touken_ranbu": "刀剑乱舞", "kantai_collection": "舰队Collection",
}


def build_fixes() -> dict[str, str]:
    """基础修正表 + 按作品的 "角色_(作品)" 扩展（只对同作品的角色展开，避免瞎关联）。"""
    fixes: dict[str, str] = {}
    fixes.update(ARKNIGHTS)
    fixes.update(FRIEREN)
    fixes.update(OTHER)
    fixes.update(SERIES_ZH)
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
    stale: list[tuple[str, str, str]] = []      # (标签名, 旧译名, 新译名)
    for k, v in fixes.items():
        old = raw.get(k)
        if old == v:
            continue
        if old is None:
            added += 1
        else:
            changed += 1
            stale.append((k, old, v))
        print(f"  {k:34} {old!r} → {v!r}")
        raw[k] = v
    if not dry:
        DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        hist_path = HERE / "app" / "tag_zh_fix_history.json"
        try:                                    # 记录"旧译名 → 新译名"，供库里同步旧值用
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:
            hist = {}
        for k, old_v, new_v in stale:
            hist[k] = [old_v, new_v]
        for k, v in fixes.items():
            hist.setdefault(k, ["", v])
        hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"修正历史表已更新（{len(hist)} 条）")
    if removed:
        print(f"清掉误加条目 {removed} 条")
    print(f"修正 {changed} 条、新增 {added} 条" + ("（干跑，未写入）" if dry else f"；词典现在 {len(raw)} 条"))

    # 库里以前"把词典译名存进了 tags.zh"的标签不会跟着词典更新，这里同步掉
    if not dry and stale and "--no-db" not in sys.argv:
        sys.path.insert(0, str(HERE))
        try:
            from app.store import Store
            store = Store()
            n = 0
            for name, old_zh, new_zh in stale:
                row = store.one("SELECT id, zh FROM tags WHERE name=?", (name,))
                if row and (row["zh"] or "").strip() == old_zh.strip():
                    store.update_tag(int(row["id"]), zh=new_zh)
                    n += 1
            if n:
                print(f"库里同步更新了 {n} 个标签已存的旧译名")
        except Exception as e:      # 库不在本机/被占用都不影响词典本身
            print("（跳过库内同步：%s）" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
