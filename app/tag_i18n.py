"""把 WD14 的英文标签翻成中文（审核界面用）。

WD14 的标签是可组合的（颜色 + 部位、动作 + 对象…），所以采用：
1) 整词优先查表；2) 拆词组合翻译；3) 都查不到就原样显示。
用户也可以在标签上手工填「中文名」，会优先使用。
"""
from __future__ import annotations

import re

# 常用整词（WD14 词表里高频标签）
WHOLE: dict[str, str] = {
    "1girl": "一个女孩", "2girls": "两个女孩", "3girls": "三个女孩", "multiple_girls": "多个女孩",
    "1boy": "一个男孩", "2boys": "两个男孩", "multiple_boys": "多个男孩", "solo": "单人",
    "male_focus": "以男性为主", "female_focus": "以女性为主", "group": "群像", "crowd": "人群",
    "solo_focus": "单人特写", "couple": "情侣",
    "long_hair": "长发", "short_hair": "短发", "very_long_hair": "超长发", "medium_hair": "中长发",
    "twintails": "双马尾", "ponytail": "马尾", "braid": "辫子", "twin_braids": "双辫",
    "hime_cut": "公主切", "bob_cut": "波波头", "bangs": "刘海", "hair_between_eyes": "眼间发",
    "ahoge": "呆毛", "messy_hair": "凌乱头发", "wavy_hair": "波浪发", "curly_hair": "卷发",
    "hair_ornament": "发饰", "hairband": "发箍", "hair_bow": "蝴蝶结发饰", "hair_ribbon": "发带",
    "blue_eyes": "蓝眼睛", "red_eyes": "红眼睛", "green_eyes": "绿眼睛", "brown_eyes": "棕眼睛",
    "purple_eyes": "紫眼睛", "yellow_eyes": "黄眼睛", "black_eyes": "黑眼睛", "grey_eyes": "灰眼睛",
    "heterochromia": "异色瞳", "closed_eyes": "闭眼", "half-closed_eyes": "半闭眼",
    "looking_at_viewer": "看向观众", "looking_back": "回头看", "looking_away": "看向别处",
    "looking_down": "低头", "looking_up": "抬头", "eye_contact": "对视",
    "open_mouth": "张嘴", "closed_mouth": "闭嘴", "smile": "微笑", "grin": "咧嘴笑",
    "blush": "脸红", "crying": "哭泣", "tears": "眼泪", "angry": "生气", "surprised": "惊讶",
    "school_uniform": "校服", "serafuku": "水手服", "sailor_dress": "水手连衣裙",
    "dress": "连衣裙", "skirt": "裙子", "pleated_skirt": "百褶裙", "shirt": "衬衫",
    "t-shirt": "T恤", "blouse": "女式衬衫", "sweater": "毛衣", "hoodie": "连帽衫",
    "jacket": "夹克", "coat": "外套", "blazer": "西装外套", "suit": "西装", "necktie": "领带",
    "bowtie": "领结", "scarf": "围巾", "gloves": "手套", "hat": "帽子", "cap": "鸭舌帽",
    "beret": "贝雷帽", "boots": "靴子", "shoes": "鞋子", "sneakers": "运动鞋",
    "thighhighs": "过膝袜", "pantyhose": "连裤袜", "socks": "袜子", "barefoot": "光脚",
    "swimsuit": "泳装", "bikini": "比基尼", "school_swimsuit": "学校泳装",
    "lingerie": "内衣", "underwear": "内衣裤", "bra": "胸罩", "panties": "内裤",
    "apron": "围裙", "maid": "女仆装", "kimono": "和服", "yukata": "浴衣", "hanfu": "汉服",
    "collar": "项圈", "choker": "颈圈", "necklace": "项链", "earrings": "耳环",
    "glasses": "眼镜", "sunglasses": "墨镜", "mask": "口罩", "hairclip": "发夹",
    "standing": "站立", "sitting": "坐姿", "lying": "躺卧", "on_back": "仰卧", "on_stomach": "俯卧",
    "on_side": "侧卧", "kneeling": "跪姿", "squatting": "蹲姿", "crouching": "蹲伏",
    "crossed_legs": "翘腿", "legs_apart": "双腿分开", "spread_legs": "张开双腿",
    "bent_over": "弯腰", "arms_up": "举双臂", "arm_up": "举单手", "outstretched_arms": "张开双臂",
    "hugging": "拥抱", "holding": "手持", "carrying": "抱着", "kiss": "接吻",
    "breasts": "胸部", "large_breasts": "大胸", "small_breasts": "小胸", "huge_breasts": "巨乳",
    "cleavage": "乳沟", "nipples": "乳头", "navel": "肚脐", "midriff": "露腰",
    "bare_shoulders": "露肩", "bare_legs": "露腿", "thighs": "大腿", "wide_hips": "宽胯",
    "outdoors": "户外", "indoors": "室内", "sky": "天空", "clouds": "云", "beach": "海滩",
    "ocean": "海洋", "water": "水", "tree": "树", "flower": "花", "grass": "草地",
    "city": "城市", "street": "街道", "room": "房间", "bedroom": "卧室", "bed": "床",
    "chair": "椅子", "window": "窗户", "night": "夜晚", "day": "白天", "sunset": "日落",
    "rain": "雨", "snow": "雪", "forest": "森林", "mountain": "山", "classroom": "教室",
    "simple_background": "简单背景", "white_background": "白色背景", "grey_background": "灰色背景",
    "black_background": "黑色背景", "gradient_background": "渐变背景", "blurry_background": "背景虚化",
    "full_body": "全身", "upper_body": "上半身", "portrait": "肖像", "cowboy_shot": "七分身",
    "from_above": "俯视", "from_below": "仰视", "from_side": "侧视", "from_behind": "背面",
    "masterpiece": "杰作", "best_quality": "最佳画质", "highres": "高分辨率", "absurdres": "超高分辨率",
    "realistic": "写实", "monochrome": "单色", "greyscale": "灰度", "sketch": "草图",
    "lineart": "线稿", "watercolor": "水彩", "chibi": "Q版", "censored": "已打码",
    "uncensored": "无码", "mosaic_censoring": "马赛克", "bar_censor": "黑条打码",
    "photo_background": "照片背景", "real_photo": "真人照片", "anime_style": "动漫风格",
    "white_apron": "白围裙", "blue_dress": "蓝色连衣裙", "white_shirt": "白衬衫",
    "black_hair": "黑发", "brown_hair": "棕发", "blonde_hair": "金发", "blue_hair": "蓝发",
    "pink_hair": "粉发", "red_hair": "红发", "white_hair": "白发", "silver_hair": "银发",
    "green_hair": "绿发", "purple_hair": "紫发", "orange_hair": "橙发", "grey_hair": "灰发",
    "multicolored_hair": "多彩发色", "two-tone_hair": "双色发",
}

# 拆词表（用于组合式标签）
TOKEN: dict[str, str] = {
    "hair": "头发", "eyes": "眼睛", "eye": "眼", "skin": "皮肤", "face": "脸",
    "long": "长", "short": "短", "medium": "中", "very": "超", "big": "大", "small": "小",
    "large": "大", "huge": "巨大", "closed": "闭合", "open": "张开", "half": "半",
    "blue": "蓝", "red": "红", "green": "绿", "brown": "棕", "black": "黑", "white": "白",
    "yellow": "黄", "purple": "紫", "pink": "粉", "orange": "橙", "grey": "灰", "gray": "灰",
    "silver": "银", "golden": "金", "blonde": "金", "multicolored": "多彩", "two": "双",
    "tone": "色", "light": "浅", "dark": "深", "pale": "苍白",
    "shirt": "衬衫", "skirt": "裙子", "dress": "连衣裙", "sleeves": "袖子", "sleeve": "袖子",
    "collar": "领子", "neck": "颈部", "tie": "领带", "bow": "蝴蝶结", "ribbon": "丝带",
    "socks": "袜子", "shoe": "鞋", "boots": "靴子", "pants": "裤子", "shorts": "短裤",
    "hat": "帽子", "cap": "帽", "glove": "手套", "glasses": "眼镜", "earring": "耳环",
    "standing": "站立", "sitting": "坐", "lying": "躺", "kneeling": "跪", "crossed": "交叉",
    "arm": "手臂", "arms": "手臂", "hand": "手", "hands": "双手", "leg": "腿", "legs": "腿",
    "head": "头", "body": "身体", "back": "背", "side": "侧", "front": "正面",
    "background": "背景", "sky": "天空", "flower": "花", "flowers": "花", "tree": "树",
    "water": "水", "sun": "太阳", "moon": "月亮", "star": "星", "cloud": "云",
    "room": "房间", "school": "学校", "swim": "游泳", "bath": "浴室", "pool": "泳池",
    "white": "白", "apron": "围裙", "maid": "女仆", "outfit": "装束", "uniform": "制服",
    "pose": "姿势", "view": "视角", "shot": "镜头", "body": "身体", "up": "向上",
    "down": "向下", "away": "别处", "viewer": "观众", "smile": "微笑", "mouth": "嘴",
}

_UNDER = re.compile(r"[_\-]+")


def translate(name: str, zh_hint: str = "") -> str:
    """返回中文显示名；查不到就返回原名。zh_hint 是用户在标签上手工填的中文名。"""
    if zh_hint and zh_hint.strip():
        return zh_hint.strip()
    if not name:
        return ""
    low = name.strip().lower()
    if re.search(r"[\u4e00-\u9fff]", low):      # 本来就是中文
        return name
    if low in WHOLE:
        return WHOLE[low]
    parts = [p for p in _UNDER.split(low) if p]
    if not parts:
        return name
    if len(parts) > 1:
        joined = "_".join(parts)
        if joined in WHOLE:
            return WHOLE[joined]
    zh_parts = []
    for p in parts:
        if p in TOKEN:
            zh_parts.append(TOKEN[p])
        elif p.isdigit():
            zh_parts.append(p)
        else:
            zh_parts = None
            break
    if zh_parts:
        # 颜色在前、名词在后时读起来更自然
        if len(zh_parts) == 2 and zh_parts[0].endswith(("色", "")) and parts[0] in ("blue", "red", "green",
                                                                                  "brown", "black", "white",
                                                                                  "yellow", "purple", "pink",
                                                                                  "orange", "grey", "gray",
                                                                                  "silver", "golden", "blonde"):
            return f"{zh_parts[0]}{zh_parts[1]}"
        return "".join(zh_parts)
    return name


def display(name: str, zh_hint: str = "") -> str:
    """审核界面用：中文（英文原名）。"""
    zh = translate(name, zh_hint)
    return zh if zh == name else f"{zh}（{name}）"
