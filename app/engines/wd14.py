"""WD14 tagger：二次元图片自动打标（ONNX 推理，完全离线）。"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import numpy as np
from PIL import Image

from .. import models
from . import ort_providers, prepare_ort

CATEGORY_MAP = {0: "general", 4: "character", 9: "rating"}

# WD14 的分级：general / sensitive / questionable / explicit -> 对应 pixiv 风格分级
# WD14 的 4 个原始分级类别（映射到最终等级在 library.decide_rating 里做，便于按偏好调整）
WD14_RATING_CLASSES = ("general", "sensitive", "questionable", "explicit")

# 关键词启发式分类，仅用于界面分组
_CLOTHING = ("shirt", "skirt", "dress", "uniform", "school_uniform", "kimono", "sweater", "jacket", "coat",
             "hoodie", "blazer", "suit", "lingerie", "underwear", "bra", "panties", "bikini", "swimsuit",
             "swimwear", "thighhighs", "pantyhose", "socks", "gloves", "hat", "cap", "ribbon", "bowtie",
             "necktie", "scarf", "boots", "shoes", "sneakers", "sandals", "apron", "maid", "nurse", "sailor",
             "shorts", "jeans", "pants", "leggings", "choker", "hairband", "hair_ornament", "eyepatch",
             "glasses", "sunglasses", "earrings", "necklace", "armband", "sarashi", "bandages")
_COUNT = ("solo", "1girl", "1boy", "2girls", "2boys", "3girls", "3boys", "multiple_girls", "multiple_boys",
          "6+girls", "6+boys", "male_focus", "female_focus", "group", "crowd")
_POSE = ("standing", "sitting", "lying", "on_back", "on_stomach", "on_side", "kneeling", "squatting",
         "crouching", "crossed_legs", "legs_up", "legs_apart", "spread_legs", "bent_over", "top-down_bottom-up",
         "masturbation", "sex", "missionary", "doggystyle", "cowgirl_position", "sex_from_behind", "fellatio",
         "paizuri", "handjob", "cum", "hug", "hugging", "kiss", "holding", "carrying", "princess_carry",
         "arm_up", "arms_up", "outstretched_arms")
_BODY = ("breasts", "large_breasts", "small_breasts", "huge_breasts", "nipples", "cleavage", "navel",
         "barefoot", "bare_legs", "bare_shoulders", "midriff", "collarbone", "thighs", "wide_hips",
         "long_hair", "short_hair", "blonde_hair", "black_hair", "brown_hair", "blue_hair", "white_hair",
         "pink_hair", "red_hair", "silver_hair", "green_hair", "purple_hair", "grey_hair", "orange_hair",
         "twintails", "ponytail", "braid", "bun", "bangs", "ahoge", "hair_between_eyes",
         "blue_eyes", "red_eyes", "green_eyes", "brown_eyes", "purple_eyes", "yellow_eyes", "heterochromia")
_SCENE = ("outdoors", "indoors", "sky", "cloud", "tree", "flower", "water", "ocean", "beach", "city",
          "street", "room", "bedroom", "bed", "chair", "window", "night", "day", "sunset", "rain", "snow",
          "forest", "mountain", "classroom", "library", "pool", "bathroom", "onsen", "ruins", "underwater",
          "simple_background", "white_background", "grey_background", "black_background", "gradient_background")
_STYLE = ("masterpiece", "best_quality", "highres", "absurdres", "detailed", "sketch", "monochrome",
          "greyscale", "lineart", "watercolor", "realistic", "3d", "chibi", "anime_style", "photo_background",
          "traditional_media", "resolution", "lowres", "bad_quality", "jpeg_artifacts", "signature", "watermark",
          "text", "artist_name", "logo", "censored", "uncensored", "bar_censor", "mosaic_censoring")
_ACTION = ("holding", "hug", "hugging", "kiss", "carrying", "lifting", "walking", "running", "jumping",
           "dancing", "singing", "eating", "drinking", "reading", "writing", "playing", "sleeping",
           "crying", "laughing", "sitting", "standing", "lying", "kneeling", "squatting", "leaning",
           "bending", "reaching", "pointing", "waving", "clapping", "holding_sword", "holding_gun")
_BODY_PARTS = ("hair", "eyes", "eye", "face", "head", "neck", "shoulder", "arm", "hand", "finger",
               "waist", "hip", "leg", "thigh", "knee", "foot", "feet", "navel", "stomach", "chest",
               "breast", "butt", "skin", "lips", "mouth", "tongue", "teeth", "ear", "tail", "wings",
               "horns", "halo", "mole", "freckles", "blush")
_CLOTHING2 = ("uniform", "dress", "skirt", "shirt", "blouse", "sweater", "jacket", "coat", "suit",
              "tie", "ribbon", "bow", "socks", "stocking", "boot", "shoe", "hat", "cap", "glove",
              "scarf", "apron", "kimono", "yukata", "swimsuit", "bikini", "lingerie", "underwear",
              "bra", "panties", "leotard", "bodysuit", "poncho", "cape", "cloak", "armor", "veil",
              "jewelry", "necklace", "earring", "choker", "collar", "glasses", "mask", "belt", "pants",
              "shorts", "jeans", "leggings", "hoodie", "vest", "cardigan", "sundress", "furoshiki")
_SCENE2 = ("background", "sky", "cloud", "sun", "moon", "star", "tree", "flower", "grass", "leaf",
           "blossom", "petal", "sakura",
           "water", "sea", "ocean", "river", "lake", "beach", "sand", "mountain", "forest", "field",
           "city", "street", "building", "house", "room", "window", "door", "wall", "floor", "ceiling",
           "bed", "chair", "table", "desk", "sofa", "couch", "curtain", "lamp", "bookcase", "classroom",
           "kitchen", "bathroom", "shower", "pool", "onsen", "night", "day", "rain", "snow", "fog")
_STYLE2 = ("style", "art", "painting", "sketch", "lineart", "monochrome", "greyscale", "watercolor",
           "cel", "shading", "lighting", "depth", "blur", "bloom", "flare", "vignette", "texture",
           "realistic", "realism", "cartoon", "chibi", "anime", "manga", "comic", "pixel", "vector",
           "3d", "cg", "render", "photo", "resolution", "quality", "masterpiece", "detailed")
_VIEW = ("from_above", "from_below", "from_behind", "from_side", "looking_at_viewer", "looking_back",
         "looking_away", "looking_down", "looking_up", "eye_contact", "facing_viewer", "profile")

# —— 补充规则（v1.4）：原来这四类全靠"其它"，几千个标签归不进去 ——
_INTERACTION = (
    "gag", "gagged", "ball_gag", "wiffle_gag", "gag_harness", "bit_gag", "ring_gag", "tape_gag",
    "sex", "penetration", "vaginal", "anal", "anal_object_insertion", "vaginal_object_insertion",
    "object_insertion", "urethral", "cum", "cumshot", "cum_on", "ejaculation", "orgasm", "creampie",
    "masturbation", "fingering", "handjob", "fellatio", "cunnilingus", "paizuri", "footjob",
    "irrumatio", "bukkake", "gokkun", "gangbang", "threesome", "group_sex", "orgy", "netorare",
    "sex_from_behind", "missionary", "doggystyle", "cowgirl_position", "mating_press",
    "suspended_congress", "spreader_bar", "cuffs", "shackles", "handcuffs", "bound", "bound_arms",
    "bound_legs", "bondage", "restrained", "shibari", "rope", "chain", "leash", "blindfold",
    "nipple_stimulation", "nipple_pull", "breast_sucking", "breast_press", "groping", "grabbing",
    "rape", "molestation", "tentacles", "vibrator", "dildo", "sex_toy", "onahole", "condom",
    "pussy_juice", "precum", "lactation", "pregnant", "incest", "futanari", "ahegao", "ear_sex",
    "kiss", "kissing", "hug", "hugging", "carrying", "princess_carry", "lap_pillow", "headpat",
)
_BODY_DETAIL = (
    "saliva", "drooling", "spit", "sweat", "sweatdrop", "tears", "tear", "crying", "trembling",
    "blush", "blushing", "freckles", "mole", "mole_under_eye", "scar", "tattoo", "piercing",
    "abs", "muscular", "veins", "skin", "fang", "teeth", "tongue", "lips", "symbol-shaped_pupils",
    "heart-shaped_pupils", "star-shaped_pupils", "sidelocks", "twin_drills", "drill_hair",
    "disembodied_limb", "covered_nipples", "covered_crotch", "bar_censor", "pubic_hair",
    "tail", "wings", "horns", "halo", "animal_ears", "cat_ears", "dog_ears", "fox_ears",
    "rabbit_ears", "elf", "pointy_ears", "animal_ear_fluff", "fang_out", "hair_between_eyes",
)
_TEXT_UI = (
    "speech_bubble", "thought_bubble", "dialogue_box", "text", "watermark", "signature", "logo",
    "artist_name", "username", "twitter_username", "commentary", "border", "frame", "letterboxed",
    "dated", "bad_id", "bad_twitter_id", "translated", "web_address", "url", "qr_code", "barcode",
    "interface", "screenshot", "chart", "graph", "manga_page", "2koma",
    "4koma", "comic", "panel", "caption",
)
_OBJECT2 = (
    "sword", "katana", "gun", "pistol", "rifle", "knife", "dagger", "spear", "axe", "hammer",
    "weapon", "shield", "armor", "wand", "staff", "bow_(weapon)", "arrow", "arrow_(projectile)",
    "phone", "smartphone", "cellphone", "camera", "headphones", "microphone", "guitar", "piano",
    "violin", "drum", "book", "notebook", "bag", "backpack", "handbag", "umbrella", "cup",
    "teacup", "glass", "bottle", "wine_glass", "can", "food", "cake", "ice_cream", "candy",
    "lollipop", "fruit", "apple", "plushie", "doll", "teddy_bear", "balloon", "clock", "watch",
    "calendar", "candle", "basket", "bucket", "towel", "pillow", "blanket", "fan", "parasol",
    "balloon", "kite", "toy", "controller", "game_controller", "syringe", "bottle", "jar",
)
_VIEW2 = (
    "from_above", "from_below", "from_behind", "from_side", "from_outside", "from_front",
    "looking_at_viewer", "looking_back", "looking_away", "looking_down", "looking_up", "eye_contact",
    "facing_viewer", "profile", "portrait", "close-up", "closeup", "upper_body", "lower_body",
    "full_body", "cowboy_shot", "wide_shot", "dutch_angle", "foreshortening", "perspective",
    "pov", "depth_of_field", "blurry", "motion_blur", "cropped", "cropped_torso", "out_of_frame",
    "head_out_of_frame", "feet_out_of_frame", "zoom_layer", "vanishing_point",
)
_SPECIES = (      # 兽娘/种族特征：归到「人物/角色」（描述角色身份）
    "cat_girl", "fox_girl", "wolf_girl", "dog_girl", "rabbit_girl", "bunny_girl", "dragon_girl",
    "demon_girl", "angel_girl", "elf_girl", "orc", "slime_girl", "monster_girl", "spider_girl",
    "cow_girl", "horse_girl", "sheep_girl", "mouse_girl", "bird_girl", "snake_girl", "shark_girl",
    "cat_boy", "fox_boy", "wolf_boy", "rabbit_boy", "dragon_boy", "demon_boy", "angel_boy",
    "otoko_no_ko", "shota", "loli", "kemonomimi", "kemonomimi_mode", "animal_humanoid",
)
_INTERACTION2 = (
    "improvised_gag", "milking_machine", "femdom", "futa_with_female", "futa_with_male",
    "dickgirl", "suspension", "amputee", "mouth_gag", "cleave_gag", "bondage_up",
)


_HIT_PATTERNS: dict[tuple[str, ...], "re.Pattern"] = {}


def _hits(low: str, seq, min_len: int = 0) -> bool:
    """seq 里任意一个"词"在 low 里成词出现就算命中。

    按"词"匹配而不是任意子串：否则 arknights 里的 night 会被当场景、
    unbuttoned 里的 button 会被当界面控件。

    关键：整组词只编译**一条**正则并缓存 —— 以前是对每个词各调一次 re.search，
    等于每轮都在重新编译正则，4000 多个标签能卡死主线程（启动即假死）。
    """
    toks = tuple(t for t in seq if len(t) >= min_len)
    if not toks:
        return False
    pat = _HIT_PATTERNS.get(toks)
    if pat is None:
        alt = "|".join(re.escape(t) for t in toks)
        pat = re.compile(rf"(?:^|[_\-(])(?:{alt})(?:$|[_\-\)])")
        _HIT_PATTERNS[toks] = pat
    return pat.search(low) is not None


def guess_category(name: str, wd_category: int) -> str:
    if wd_category == 4:
        return "character"
    if wd_category == 9:
        return "rating"
    low = name.lower()

    # 先整词匹配，再按关键词包含关系兜底（white_apron、long_hair 这类组合词）
    for seq, cat in ((_COUNT, "count"), (_CLOTHING, "clothing"), (_POSE, "pose"),
                     (_BODY, "body"), (_SCENE, "scene"), (_STYLE, "style")):
        if low in seq:
            return cat
    for seq, cat in ((_CLOTHING, "clothing"), (_POSE, "pose"), (_COUNT, "count")):
        if _hits(low, seq, 4):
            return cat
    # 精确命中新增的五类（性行为/身体细节/文本界面/道具/视角）
    for seq, cat in ((_INTERACTION, "interaction"), (_TEXT_UI, "text_ui"), (_OBJECT2, "object"),
                     (_BODY_DETAIL, "body_detail"), (_VIEW2, "view"), (_SPECIES, "character"),
                     (_INTERACTION2, "interaction")):
        if low in seq:
            return cat
    # 更宽的构词匹配（这两组顺序有讲究：服装/身体优先于场景，避免"bath"被当场景等误判）
    if _hits(low, _VIEW):
        return "pose"
    # 组合词兜底：white_apron 这类，靠关键词包含判断
    for seq, cat in ((_INTERACTION, "interaction"), (_TEXT_UI, "text_ui"), (_OBJECT2, "object"),
                     (_BODY_DETAIL, "body_detail"), (_VIEW2, "view"), (_SPECIES, "character"),
                     (_INTERACTION2, "interaction")):
        if _hits(low, seq, 4):
            return cat
    for seq, cat in ((_CLOTHING2, "clothing"), (_BODY_PARTS, "body"), (_ACTION, "action"),
                     (_POSE, "pose"), (_STYLE2, "style"), (_SCENE2, "scene")):
        if _hits(low, seq, 3):
            return cat
    return "other"


class WD14Tagger:
    def __init__(self, models_dir: Path | None = None, device: str = "auto", model_key: str = "wd14_onnx",
                 tags_key: str = "wd14_tags"):
        self.models_dir = models_dir
        self.device = device
        self.model_key = model_key
        self.tags_key = tags_key
        self.session = None
        self.input_name = None
        self.input_size = 448
        self.names: list[str] = []
        self.cats: list[int] = []
        self.freq: list[int] = []

    # ---------- 加载 ----------
    def load(self, progress=None) -> None:
        if self.session is not None:
            return
        prepare_ort()
        import onnxruntime as ort
        onnx_path = models.ensure(self.model_key, self.models_dir, progress)
        tags_path = models.ensure(self.tags_key, self.models_dir, progress)
        self.names, self.cats, self.freq = [], [], []
        with open(tags_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                self.names.append(row["name"])
                self.cats.append(int(row.get("category", 0)))
                self.freq.append(int(row.get("count", 0)))
        from .. import perf
        opts = perf.ort_session_options()
        providers = ort_providers(self.device)
        self.perf = perf
        try:
            self.session = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=providers)
        except Exception:
            self.session = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        if isinstance(shape[1], int) and shape[1] in (3, 4):
            self.input_size = int(shape[2]) if isinstance(shape[2], int) else 448
        self.chw = False

    # ---------- 预处理 ----------
    def _prep(self, img: Image.Image) -> np.ndarray:
        im = img.convert("RGB")
        w, h = im.size
        side = max(w, h)
        if w != side or h != side:
            canvas = Image.new("RGB", (side, side), (255, 255, 255))
            canvas.paste(im, ((side - w) // 2, (side - h) // 2))
            im = canvas
        im = im.resize((self.input_size, self.input_size), Image.LANCZOS)
        x = np.asarray(im, dtype=np.float32)
        x = x[:, :, ::-1]  # RGB -> BGR
        return x

    # ---------- 推理 ----------
    def predict(self, images: list[Image.Image], threshold: float = 0.35,
                char_threshold: float = 0.85, max_tags: int = 40,
                keep_rating: bool = False, return_rating: bool = False):
        if self.session is None:
            self.load()
        if not images:
            return []
        batch = np.stack([self._prep(im) for im in images]).astype(np.float32)
        try:
            out = self.session.run(None, {self.input_name: batch})[0]
        except Exception:
            out = np.stack([self.session.run(None, {self.input_name: batch[i:i + 1]})[0][0] for i in range(len(batch))])
        probs = np.asarray(out, dtype=np.float32).reshape(len(batch), -1)
        n = min(probs.shape[1], len(self.names))
        results: list[dict[str, float]] = []
        ratings: list[dict[str, float]] = []
        for i in range(len(batch)):
            p = probs[i, :n]
            cand = []
            rating_probs: dict[str, float] = {}
            for j in range(n):
                cat = self.cats[j]
                if cat == 9:
                    if self.names[j] in WD14_RATING_CLASSES:
                        rating_probs[self.names[j]] = float(p[j])
                    if not keep_rating:
                        continue
                th = char_threshold if cat == 4 else threshold
                score = float(p[j])
                if score >= th:
                    cand.append((self.names[j], score, cat))
            cand.sort(key=lambda x: -x[1])
            cand = cand[:max_tags]
            results.append({name: score for name, score, _ in cand})
            if not rating_probs:
                rating_probs = {"general": 0.0}
            ratings.append(rating_probs)
        return (results, ratings) if return_rating else results
