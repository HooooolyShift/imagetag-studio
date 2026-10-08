"""中文描述 → danbooru 提示词（本机 Ollama + 词表校验）。

规则（用户 2026-10-07 定的）：**能匹配上词表就必须用词表里的规范写法**；
匹配不到才保留自造词，并通过 `invented` 字段单独回报，方便前端提示用户。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .dictionary import BooruDict
from .ollama import DEFAULT_HOST, DEFAULT_MODEL, chat, pick_best_model

SYSTEMS = {
    "prompt": (
        "你是 Stable Diffusion / 动漫绘图提示词工程师。把用户的中文描述改写成 danbooru 风格的英文 tag 串"
        "（英文逗号+空格分隔）。要求：1) 只输出 tag 串本身，不要解释、引号、编号；"
        "2) 按重要性排序：主体数量→角色→发型发色→服装→动作姿势→视线表情→镜头构图→背景→光线氛围→画质词；"
        "3) 用 danbooru 常见写法（1girl/2girls、aqua twintails、thighhighs、from side、upper body、medium shot、"
        "detailed background、soft lighting）；知名角色直接用角色 tag（hatsune miku / kasane teto）；"
        "4) 用户没说的不要乱加，可补少量画质词：masterpiece, best quality, very aesthetic, absurdres；"
        "5) 最多 40 个 tag；6) 优先用 danbooru 上真实存在的常见 tag，宁可换个更常见的说法也别拼短语；"
        "7) 光线写 lighting 类、视角写 from side/from above、表情写 smiling/open mouth、视线写 looking at viewer，不要写成句子；"
        "8) 绝对不要输出 bad tag / none / N/A 这类占位词。"
    ),
    "negative": (
        "你是 Stable Diffusion 负向提示词助手。根据用户要画的内容，给出简短的英文负向 tag 串"
        "（英文逗号+空格分隔，20 个以内），只输出 tag 串本身。固定包含：lowres, worst quality, low quality, "
        "bad anatomy, bad hands, extra digits, fewer digits, extra limbs, deformed, watermark, signature, "
        "username, artist name, text, logo, jpeg artifacts, cropped, out of frame。"
        "若提到双人/多人，再加：3girls, 4girls, multiple girls, extra girls, extra person, clone, duplicated。"
    ),
    "outfit": (
        "你是服装设计提示词助手。把用户的中文服装描述转成 danbooru 风格的英文 tag 串（英文逗号+空格分隔），"
        "只输出 tag 串本身：先整体款式（dress / jacket / uniform / leotard 等），再细节（袖子、领口、长度、花纹、材质、颜色），"
        "最后鞋子/袜子/配饰；不要加人物与背景 tag。"
    ),
}

# 一次同时给正负两组（用户要求：不要再手动切模式）
BOTH_SYSTEM = (
    "你是 Stable Diffusion / 动漫绘图提示词工程师。用户会给一句中文描述，"
    "你要**一次性输出正向与负向两组 danbooru 风格英文 tag**，严格输出 JSON，形如："
    '{"positive": "1girl, hatsune miku, smile, upper body, ...", "negative": "lowres, worst quality, ..."}。'
    "要求：1) positive：按重要性排序（主体数量→角色→发型发色→服装→动作姿势→视线表情→镜头构图→背景→光线氛围→画质词），"
    "最多 40 个 tag，用英文逗号+空格分隔，只写真实存在的常见 danbooru tag，宁可换更常见的说法也不要拼短语；"
    "知名角色直接用角色 tag（hatsune miku / kasane teto）；可补画质词 masterpiece, best quality, very aesthetic, absurdres；"
    "2) negative：固定包含 lowres, worst quality, low quality, bad anatomy, bad hands, extra digits, fewer digits, "
    "extra limbs, deformed, watermark, signature, username, artist name, text, logo, jpeg artifacts, cropped, out of frame；"
    "若画面是双人/多人，再加 3girls, 4girls, multiple girls, extra girls, extra person, clone, duplicated；"
    "3) 不要输出 bad tag / none / N/A 这类占位词；4) 只输出 JSON，不要解释、不要代码块标记。"
)


@dataclass
class Result:
    tags: str
    invented: list[str] = field(default_factory=list)
    raw: str = ""
    model: str = ""
    mode: str = "prompt"

    def to_dict(self) -> dict:
        return {"result": self.tags, "invented": ", ".join(self.invented),
                "raw": self.raw, "model": self.model, "mode": self.mode}


def _clean_reply(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip().strip("`").strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    return text


def _needs_comma_repair(reply: str) -> bool:
    """模型偶尔不用逗号（给一串空格分词的标签），需要让它重排一次。"""
    return "," not in reply and len(reply.split()) > 6


REPAIR_SYSTEM = (
    "把用户给的一串 danbooru 标签用英文逗号+空格重新分隔输出，"
    "只输出分隔后的标签串本身，不要增删、不要解释、不要改词。"
)


def generate(text: str, mode: str = "prompt", *, dictionary: BooruDict | None = None,
             host: str = DEFAULT_HOST, model: str = "",
             timeout: float = 300.0) -> Result:
    """中文描述 → 提示词。model 留空 = **自动用本机已有的最合适模型**（不会去下载新的）。
    dictionary 为 None 时跳过词表校验。"""
    if not model:
        model = pick_best_model(host=host)
    system = SYSTEMS.get(mode, SYSTEMS["prompt"])
    hint = ""
    if dictionary is not None:
        hint_tags = dictionary.hints(text)
        if hint_tags:
            hint = "\n\n【参考标签（词表里真实存在，优先使用）】" + ", ".join(hint_tags)
    reply = _clean_reply(chat(system, text + hint, host=host, model=model, timeout=timeout))
    if not reply:
        return Result(tags="", raw="", model=model, mode=mode)
    if _needs_comma_repair(reply):
        fixed = _clean_reply(chat(REPAIR_SYSTEM, reply, host=host, model=model,
                                  temperature=0.0, num_predict=260, timeout=timeout))
        if "," in fixed:
            reply = fixed
    if dictionary is None:
        return Result(tags=reply, raw=reply, model=model, mode=mode)
    clean, invented = dictionary.validate(reply)
    # 一次"自我修复"：如果词表外的占比太高，让模型把那些换成词表里的常见写法（或删掉）
    total = len([t for t in clean.split(",") if t.strip()]) + len(invented)
    if invented and len(invented) >= 3 and total > 0 and len(invented) / total > 0.35:
        fix_user = (
            f"原始中文描述：{text}\n"
            f"上一版标签：{reply}\n"
            f"下面这些不在 danbooru 词表里，请换成词表里真实存在的常见写法（或直接删掉）："
            + ", ".join(invented)
        )
        fixed = _clean_reply(chat(SYSTEMS["prompt"] + "\n\n只输出修正后的完整标签串。", fix_user,
                                  host=host, model=model, timeout=timeout))
        if fixed and "," in fixed:
            clean2, invented2 = dictionary.validate(fixed)
            if clean2 and len(invented2) < len(invented):
                clean, invented, reply = clean2, invented2, fixed
    return Result(tags=clean, invented=invented, raw=reply, model=model, mode=mode)


def _extract_json(reply: str) -> dict | None:
    """从模型回复里抠出 JSON（容忍代码块/前后废话）。"""
    import json
    text = (reply or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:                                        # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for k in ("positive", "negative"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out or None


def generate_both(text: str, *, dictionary: BooruDict | None = None,
                  host: str = DEFAULT_HOST, model: str = "",
                  timeout: float = 300.0) -> dict:
    """一次调用同时给出正向 + 负向（用户要求：不要手动切模式）。
    返回 {"positive": str, "negative": str, "positive_invented": [...], "negative_invented": [...], "raw": str}"""
    if not model:
        model = pick_best_model(host=host)
    hint = ""
    if dictionary is not None:
        hint_tags = dictionary.hints(text)
        if hint_tags:
            hint = "\n\n【参考标签（词表里真实存在，优先使用）】" + ", ".join(hint_tags)
    raw = _clean_reply(chat(BOTH_SYSTEM, text + hint, host=host, model=model,
                            temperature=0.2, num_predict=420, timeout=timeout))
    data = _extract_json(raw)
    if data is None:
        # 兜底一：让它只输出 JSON
        fixed = _clean_reply(chat(BOTH_SYSTEM + "\n\n上次输出不是合法 JSON，请严格只输出 JSON。",
                                  text + hint, host=host, model=model,
                                  temperature=0.0, num_predict=420, timeout=timeout))
        data = _extract_json(fixed) or {}
        raw = fixed or raw
    if not data:
        # 兜底二：拆成两次单模调用（慢一点但稳）
        pos = generate(text, "prompt", dictionary=dictionary, host=host, model=model, timeout=timeout)
        neg = generate(text, "negative", dictionary=dictionary, host=host, model=model, timeout=timeout)
        return {"positive": pos.tags, "negative": neg.tags,
                "positive_invented": pos.invented, "negative_invented": neg.invented,
                "raw": raw, "model": model}
    pos_raw = _clean_reply(data.get("positive", ""))
    neg_raw = _clean_reply(data.get("negative", ""))
    if dictionary is None:
        return {"positive": pos_raw, "negative": neg_raw,
                "positive_invented": [], "negative_invented": [], "raw": raw, "model": model}
    pos, pos_inv = dictionary.validate(pos_raw)
    neg, neg_inv = dictionary.validate(neg_raw)
    return {"positive": pos, "negative": neg,
            "positive_invented": pos_inv, "negative_invented": neg_inv, "raw": raw, "model": model}


def default_wordlist_dir() -> Path:
    """词表目录：DLC 内的 wordlists/。"""
    return Path(__file__).resolve().parent.parent / "wordlists"


def load_dictionary(root: str | Path | None = None) -> BooruDict:
    cache = getattr(load_dictionary, "_cache", None)
    key = str(Path(root or default_wordlist_dir()).resolve())
    if cache is None:
        cache = {}
        load_dictionary._cache = cache
    if key not in cache:
        cache[key] = BooruDict.load(key)
    return cache[key]
