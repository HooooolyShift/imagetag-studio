"""把 WD14 的英文标签翻成中文（审核界面用）。

WD14 的标签是可组合的（颜色 + 部位、动作 + 对象…），所以采用：
1) 整词优先查表；2) 拆词组合翻译；3) 都查不到就原样显示。
用户也可以在标签上手工填「中文名」，会优先使用。
"""
from __future__ import annotations

import re
import json
from pathlib import Path

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
    # —— 扩充：WD14 高频构词（颜色/发眼/体型/服装/动作/场景/画风/镜头/材质）——
    "aqua": "水色", "turquoise": "青绿", "cyan": "青色", "magenta": "品红", "violet": "紫罗兰",
    "navy": "藏青", "beige": "米色", "maroon": "栗色", "teal": "蓝绿", "lavender": "薰衣草色",
    "rainbow": "彩虹", "gradient": "渐变", "streaked": "挑染", "highlighted": "挑染",
    "short": "短", "messy": "凌乱", "wavy": "波浪", "curly": "卷", "straight": "直",
    "sidelocks": "鬓发", "hairband": "发箍", "braid": "辫", "side": "侧", "front": "前",
    "hair": "头发", "ponytail": "马尾", "bun": "发髻", "bangs": "刘海", "ahoge": "呆毛",
    "eyelashes": "睫毛", "eyebrows": "眉毛", "pupils": "瞳孔", "iris": "虹膜",
    "thighhighs": "过膝袜", "thigh": "大腿", "thighs": "大腿", "knee": "膝", "knees": "膝",
    "collar": "领", "lapels": "翻领", "sleeve": "袖", "sleeveless": "无袖", "cuffs": "袖口",
    "hem": "下摆", "frills": "褶边", "lace": "蕾丝", "ruffles": "荷叶边", "pleated": "百褶",
    "striped": "条纹", "plaid": "格纹", "polka": "波点", "dot": "圆点", "checkered": "格纹",
    "leather": "皮革", "denim": "牛仔", "silk": "丝绸", "wool": "羊毛", "fur": "毛皮",
    "wet": "湿身", "transparent": "透明", "see": "透", "through": "透", "sheer": "薄透",
    "cleavage": "乳沟", "underboob": "下乳", "sideboob": "侧乳", "cameltoe": "骆驼趾",
    "panties": "内裤", "bra": "胸罩", "garter": "吊袜带", "stockings": "长袜", "thighband": "大腿环",
    "bikini": "比基尼", "swimsuit": "泳装", "leotard": "紧身衣", "bodysuit": "连体衣",
    "wedding": "婚礼", "nurse": "护士", "police": "警察", "waitress": "女侍",
    "cheerleader": "啦啦队", "idol": "偶像", "witch": "女巫", "knight": "骑士", "ninja": "忍者",
    "sword": "剑", "katana": "武士刀", "gun": "枪", "weapon": "武器", "knife": "刀",
    "holding": "手持", "hug": "拥抱", "carry": "抱", "carrying": "抱着", "lifting": "举起",
    "bed": "床", "sofa": "沙发", "chair": "椅子", "floor": "地板", "ground": "地面",
    "couch": "沙发", "pillow": "枕头", "blanket": "毯子", "towel": "毛巾", "bathtub": "浴缸",
    "shower": "淋浴", "bathroom": "浴室", "toilet": "厕所", "sauna": "桑拿", "onsen": "温泉",
    "fence": "栅栏", "gate": "大门", "wall": "墙", "roof": "屋顶", "bridge": "桥",
    "puddle": "水洼", "river": "河", "lake": "湖", "sea": "海", "horizon": "地平线",
    "sunlight": "阳光", "sunbeam": "光束", "shadow": "阴影", "silhouette": "剪影", "backlighting": "逆光",
    "depth": "景深", "blurry": "模糊", "bloom": "泛光", "lens": "镜头", "flare": "光斑",
    "motion": "动态", "blur": "模糊", "zoom": "变焦", "wide": "广角", "closeup": "特写",
    "profile": "侧脸", "facing": "面向", "turned": "转身", "looking": "看",
    "monochrome": "单色", "sepia": "怀旧", "greyscale": "灰度", "limited": "有限配色",
    "palette": "配色", "spot": "局部", "sketch": "素描", "ink": "墨水", "pencil": "铅笔",
    "brush": "笔刷", "paint": "颜料", "oil": "油画", "pastel": "粉彩", "crayon": "蜡笔",
    "retro": "复古", "pixel": "像素", "vector": "矢量", "cg": "CG", "render": "渲染",
    "photo": "照片", "photorealistic": "照片级", "realistic": "写实", "cartoon": "卡通",
    "expression": "表情", "tears": "泪", "sweat": "汗", "drool": "口水", "tongue": "舌头",
    "fingers": "手指", "hand": "手", "hands": "双手", "arm": "手臂", "arms": "手臂",
    "shoulder": "肩", "shoulders": "双肩", "waist": "腰", "hips": "胯", "butt": "臀",
    "navel": "肚脐", "stomach": "腹部", "chest": "胸口", "neck": "颈部", "throat": "喉咙",
}

# 整词补充（组合翻译覆盖不到、但很常见的）
WHOLE.update({
    "1girl": "一个女孩", "2girls": "两个女孩", "1boy": "一个男孩", "2boys": "两个男孩",
    "solo": "单人", "solo_focus": "单人特写", "male_focus": "以男性为主",
    "female_focus": "以女性为主", "multiple_views": "多视角", "comic": "漫画分格",
    "sketch_page": "草图页", "character_sheet": "角色设定图", "reference_sheet": "参考图",
    "thighhighs": "过膝袜", "thighhighs_only": "只穿过膝袜", "pantyhose": "连裤袜",
    "school_uniform": "校服", "sailor_dress": "水手连衣裙", "gym_uniform": "运动服",
    "casual": "便服", "formal": "正装", "traditional": "传统服饰", "modern": "现代服饰",
    "hourglass_figure": "沙漏身材", "slender": "纤细", "curvy": "丰满", "chubby": "微胖",
    "muscular": "肌肉发达", "toned": "结实", "slim": "苗条", "petite": "娇小",
    "from_above": "俯视", "from_below": "仰视", "from_behind": "从背后", "from_side": "侧面",
    "looking_back": "回头看", "looking_at_viewer": "看向镜头", "eye_contact": "对视",
    "hand_on_hip": "手叉腰", "hands_on_hips": "双手叉腰", "arms_crossed": "双手抱胸",
    "hand_up": "举手", "hands_up": "双手举起", "peace_sign": "比耶", "v_sign": "比V",
    "wink": "眨眼", "one_eye_closed": "单眼闭", "half-closed_eyes": "半眯眼",
    "open_mouth": "张嘴", "closed_mouth": "闭嘴", "parted_lips": "微张唇",
    "sitting_on_bed": "坐在床上", "on_bed": "在床上", "on_chair": "在椅子上",
    "lying_on_back": "仰卧", "lying_on_stomach": "俯卧", "on_side": "侧躺",
    "standing_on_one_leg": "单腿站立", "walking": "行走", "running": "奔跑",
    "jumping": "跳跃", "falling": "下落", "floating": "漂浮", "flying": "飞行",
    "dancing": "跳舞", "singing": "唱歌", "crying": "哭泣", "laughing": "大笑",
    "sleeping": "睡觉", "eyes_closed": "闭眼", "yawning": "打哈欠", "eating": "进食",
    "drinking": "饮水", "reading": "阅读", "writing": "书写", "playing": "演奏",
    "holding_sword": "持剑", "holding_gun": "持枪", "holding_book": "拿书",
    "holding_flower": "拿花", "holding_food": "拿食物", "holding_cup": "拿杯子",
    "white_background": "白底", "black_background": "黑底", "simple_background": "简单背景",
    "gradient_background": "渐变背景", "transparent_background": "透明背景",
    "night_sky": "夜空", "starry_sky": "星空", "blue_sky": "蓝天", "cloudy_sky": "多云天空",
    "sunset": "日落", "sunrise": "日出", "dusk": "黄昏", "dawn": "黎明", "twilight": "微光",
    "cherry_blossoms": "樱花", "petals": "花瓣", "autumn_leaves": "秋叶", "snowing": "下雪",
    "raining": "下雨", "water_drop": "水滴", "steam": "蒸汽", "smoke": "烟雾", "fog": "雾",
    "highres": "高分辨率", "absurdres": "超高分辨率", "official_art": "官方图", "sketch": "草图",
    "lineart": "线稿", "monochrome": "单色", "greyscale": "灰度", "sepia": "怀旧色调",
    "watercolor": "水彩", "oil_painting": "油画", "pixel_art": "像素画", "3d": "3D",
    "chibi": "Q版", "manga": "漫画风", "anime_style": "动漫风格", "realistic": "写实风格",
    "fang": "獠牙", "pointy_ears": "尖耳", "animal_ears": "兽耳", "cat_ears": "猫耳",
    "tail": "尾巴", "wings": "翅膀", "halo": "光环", "horns": "角", "elf": "精灵",
    "glasses": "眼镜", "sunglasses": "墨镜", "eyepatch": "眼罩", "mask": "面罩",
    "medical_mask": "医用口罩", "hairclip": "发夹", "headband": "头带", "crown": "王冠",
    "see-through": "半透明透视", "see-through_clothing": "半透明衣物", "(see-through)": "半透明透视",
    "cherry_blossoms": "樱花", "falling_petals": "飘落花瓣", "sunbeam": "光束", "backlighting": "逆光",
    "pointy_ears": "尖耳朵", "cat_ears": "猫耳", "animal_ears": "兽耳", "from_above": "俯视视角",
    "from_below": "仰视视角", "from_behind": "背面视角", "from_side": "侧面视角",
    "looking_at_viewer": "看向镜头", "looking_back": "回头看", "looking_away": "看向别处",
    "eye_contact": "与观众对视", "half-closed_eyes": "半眯眼", "open_mouth": "张嘴",
})

_UNDER = re.compile(r"[_\-]+")

# 本地模型批量翻译出来的字典（tools/translate_tags.py 生成，随程序发布）
MODEL_DICT: dict[str, str] = {}
_DICT_PATH = Path(__file__).with_name("tag_zh_dict.json")

_ILLEGAL_LABEL = re.compile(r'[\\/|<>:"*?\[\]]')
_PROMPT_LEAK = re.compile(r"\bno[\s_]?think\b", re.I)


def is_usable_zh(text: str) -> bool:
    """中文名能不能直接拿去显示/当文件名标签：挡住 /no_think、no think 这类脏数据。

    （批量翻译那批词表里混进了几条 LLM 提示词残留，POV / K-pop / 年份这类是正常的，不拦。）
    """
    t = (text or "").strip()
    if not t:
        return False
    if _ILLEGAL_LABEL.search(t) or t.startswith("_"):
        return False
    if _PROMPT_LEAK.search(t):
        return False
    if not re.search(r"[\w\u4e00-\u9fff]", t):      # 纯标点/空白也不可用
        return False
    return True


try:
    if _DICT_PATH.exists():
        MODEL_DICT = {str(k).strip().lower(): str(v).strip()
                      for k, v in json.loads(_DICT_PATH.read_text(encoding="utf-8")).items()
                      if str(v).strip() and is_usable_zh(str(v))}
except Exception:
    MODEL_DICT = {}


def label(name: str, zh_hint: str = "") -> str:
    """标签的中文显示名 —— **全项目唯一实现**（界面显示、写文件名、下拉框、图谱节点都用它）。

    优先级：手填/库里的中文名 > 内置词典 > 拆词组合翻译 > 英文原名；
    脏数据（/no_think 这类）在任何一层都会被跳过，不会跑到文件名或界面上。
    """
    if zh_hint and zh_hint.strip() and is_usable_zh(zh_hint):
        return zh_hint.strip()
    if not name:
        return ""
    low = name.strip().lower()
    if re.search(r"[\u4e00-\u9fff]", low):      # 本来就是中文
        return name
    if low in MODEL_DICT:                       # 模型翻译优先（覆盖长尾）
        hit = MODEL_DICT[low]
        if is_usable_zh(hit):
            return hit
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


def translate(name: str, zh_hint: str = "") -> str:
    """兼容旧调用：等同于 label()。"""
    return label(name, zh_hint)


def display(name: str, zh_hint: str = "") -> str:
    """审核界面用：中文（英文原名）。"""
    zh = label(name, zh_hint)
    return zh if zh == name else f"{zh}（{name}）"


_PAREN = re.compile(r"^(.*?)（([^（）]+)）$")

# 输入框里常见的分隔符（中英文标点都算）
_SEPARATORS = ("，", "、", "；", ";", ",", "|", "\n", "\r", "\t")


def parse_list(text: str) -> list[str]:
    """把输入框里的一串标签拆成列表（空格 + 中英文逗号/顿号/分号都算分隔符）。

    所有界面的「粘一串标签进来」都走这里，避免各写一份 split 规则。
    """
    t = text or ""
    for sep in _SEPARATORS:
        t = t.replace(sep, " ")
    return [x for x in t.split() if x]


_ZH_INDEX: dict[str, str] | None = None


def zh_to_name() -> dict[str, str]:
    """内置词典的中文名 → 规范标签名（懒加载，避免每次扫描都遍历 1 万条）。

    一个中文名可能对应多个英文 key（如「泳装」→ swimsuit / holding_swimsuit / print_bikini），
    这里挑更"基础"的那个：下划线少的优先，其次更短的，最后按字母序。
    """
    global _ZH_INDEX
    if _ZH_INDEX is None:
        idx: dict[str, str] = {}
        for name, zh in MODEL_DICT.items():
            if not zh:
                continue
            old = idx.get(zh)
            if old is None or (name.count("_"), len(name), name) < (old.count("_"), len(old), old):
                idx[zh] = name
        _ZH_INDEX = idx
    return _ZH_INDEX


def zh_index(store=None) -> dict[str, str]:
    """中文名 → 规范标签名：库里手填的优先，其次内置词典（同义词取更基础的那个）。"""
    idx = dict(zh_to_name())
    if store is not None:
        for r in store.query("SELECT name, zh FROM tags WHERE IFNULL(zh,'')<>''"):
            if is_usable_zh(r["zh"]):
                idx[str(r["zh"])] = str(r["name"])
    return idx


def resolve(text: str, index: dict[str, str] | None = None, store=None) -> str:
    """把输入框/文件名里的标签还原成规范标签名（唯一实现）。

    - "服装 · 明日方舟（arknights）" → arknights（联想菜单插进来的格式）
    - "明日方舟" → 库里有这个中文名的标签就还原成它的英文名，否则原样返回
    - index 可传入预先建好的 zh_index()，扫描大目录时避免每个标签都重建一次
    """
    t = (text or "").strip()
    if not t:
        return ""
    # 去掉联想菜单里的「分类 · 」前缀
    if " · " in t:
        t = t.split(" · ", 1)[1].strip()
    m = _PAREN.match(t)
    if m:
        return m.group(2).strip() or m.group(1).strip()
    if re.search(r"[\u4e00-\u9fff]", t):
        if index is None:
            index = zh_index(store)
        hit = index.get(t) if index else None
        if hit:
            return hit
    return t


def parse_input(text: str, store=None) -> str:
    """兼容旧调用：等同于 resolve()。"""
    return resolve(text, store=store)
