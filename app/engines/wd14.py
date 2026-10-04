"""WD14 tagger：二次元图片自动打标（ONNX 推理，完全离线）。"""
from __future__ import annotations

import csv
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
        if any(tok in low for tok in seq if len(tok) > 3):
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
