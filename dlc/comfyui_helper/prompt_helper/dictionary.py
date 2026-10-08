"""danbooru 词表：加载 / 归一 / 校验（让提示词尽量落在真实存在的 tag 上）。

词表来源（按目录自动扫描，两种列格式都吃）：
  · 项目词表   tag,category,aliases,zh,count,notes     （aliases 用 | 分隔，含中文名）
  · 模型词表   tag,category,count,"alias1,alias2"      （BetaDoggo/danbooru-tag-list 的按模型 tag 表）
文件可以是 .csv 或 .csv.gz。

用法：
    d = BooruDict.load("wordlists")          # 或 BooruDict.load("wordlists", prefer="noobai")
    d.resolve("初音未来")                     # -> 'hatsune_miku'
    d.validate("1girl, hatsune miku, wall with photos")
    # -> ('1girl, hatsune miku, wall with photos', ['wall with photos'])
"""
from __future__ import annotations

import csv
import gzip
import io
import re
from pathlib import Path

# 明明是占位/废话的词，永远不要
JUNK = {
    "bad tag", "bad_tag", "tag", "tags", "unknown", "none", "n/a", "na", "null", "todo",
    "bad tags", "bad_tags", "description", "prompt", "这里写提示词",
}

# 不属于 danbooru 词表、但画图确实想留的质量/技术词
ALLOWED_NON_BOORU = {
    "masterpiece", "best quality", "very aesthetic", "absurdres", "highres", "high quality",
    "newest", "official art", "detailed background", "depth of field", "year 2024", "year 2025",
}

# 词表里存在、但画图时绝对不要的（这些是 danbooru 的元数据/纠错 tag）
BLOCKED_TAGS = {
    "bad_tag", "bad_id", "bad_link", "bad_pixiv_id", "bad_twitter_id", "bad_deviantart_id",
    "bad_metadata", "bad_source", "bad_commentary",
}

# 人工小对照表：构图/表情/光线这类高频中文词，两个词库都可能缺或错配
# （实测主程序词库里"半身"被映射成 newhalf）。命中就优先用它，比任何自动映射都稳。
ZH_OVERRIDES = {
    "半身": "upper_body", "上半身": "upper_body", "全身": "full_body", "特写": "close-up",
    "大头照": "portrait", "侧面": "from_side", "背面": "from_behind", "正面": "facing_viewer",
    "看镜头": "looking_at_viewer", "看向别处": "looking_away", "闭眼": "closed_eyes",
    "微笑": "smiling", "大笑": "grin", "张嘴": "open_mouth", "生气": "angry", "害羞": "blush",
    "坐": "sitting", "站": "standing", "躺": "lying", "趴": "on_stomach", "蹲": "squatting",
    "手牵手": "holding_hands", "牵手": "holding_hands", "举手": "arm_up",
    "单人": "solo", "冷色调": "cool_colors", "暖色调": "warm_colors",
    "逆光": "backlighting", "柔光": "soft_lighting", "强光": "strong_light", "阴影": "shadow",
    "白天": "day", "夜景": "night", "黄昏": "sunset", "清晨": "morning", "海边": "beach",
    "室内": "indoors", "室外": "outdoors", "森林": "forest", "城市": "city",
    "雪": "snow", "雨": "rain", "樱花": "cherry_blossoms", "天空": "sky", "蓝天": "blue_sky",
    "纯色背景": "simple_background", "白背景": "white_background", "简单背景": "simple_background",
}


def normalize(raw: str) -> str:
    """统一成查表键：去权重括号、去转义、下划线/空格统一、小写、压空格。"""
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith("(") and s.endswith(")"):
        m = re.match(r"^\((.*):([\d.]+)\)$", s)
        if m:
            s = m.group(1)
        else:
            s = s[1:-1]
    s = s.replace("\\(", "(").replace("\\)", ")").replace("\\", "")
    s = s.replace("_", " ").replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _open_text(path: Path):
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


class BooruDict:
    def __init__(self) -> None:
        self.by_name: dict[str, str] = {}      # 规范键 -> 规范键（下划线形式）
        self.by_alias: dict[str, str] = {}     # 别名/中文 -> 规范键
        self.counts: dict[str, int] = {}       # 规范键 -> 热度
        self.sources: list[str] = []
        # 按词表来源分组的 tag 集合，供"按模型过滤"用
        self.groups: dict[str, set[str]] = {}
        # tag(下划线形式) -> 中文名，用于界面悬浮气泡
        self.zh_by_tag: dict[str, str] = {}
        # 共享的人工别名表（主程序 app/tag_zh_aliases.json）：中文口头说法 → 规范 tag，优先级最高
        self.zh_aliases: dict[str, str] = {}
        # 宿主共享词库（app/taglex.TagLex）。中文 ↔ 英文映射以它为准，本文件只负责"模型认不认这个 tag"。
        self.lexicon = None

    def attach_lexicon(self, lexicon) -> None:
        """挂上宿主的共享词库（host.lexicon()）。挂了之后中文一律先问它。"""
        self.lexicon = lexicon

    # ---------- 加载 ----------
    @classmethod
    def load(cls, root: str | Path) -> "BooruDict":
        self = cls()
        root = Path(root)
        for path in sorted(root.rglob("*.csv")) + sorted(root.rglob("*.csv.gz")):
            self._load_file(path)
        return self

    def _load_file(self, path: Path) -> None:
        group = path.stem.replace(".csv", "")
        # 只有项目词典（booru_zh，人工整理过、别名是中文/常用写法）才用来建别名；
        # 模型词表里的"别名"质量很差（例如 bad_tag 的别名里有 colors/background），用它反而会纠错成坏的 tag。
        use_aliases = "booru_zh" in path.parts
        names = set()
        with _open_text(path) as fh:
            for row in csv.reader(fh):
                if len(row) < 2:
                    continue
                tag = (row[0] or "").strip()
                if not tag or tag.lower() == "tag":
                    continue
                key = normalize(tag).replace(" ", "_")
                if not key:
                    continue
                if key in BLOCKED_TAGS:
                    continue
                names.add(key)
                if key not in self.by_name:
                    self.by_name[key] = key
                # 两种格式：项目词表(6 列，第三列别名、第五列 count) / 模型词表(4 列，第三列 count)
                if len(row) >= 6 and not row[2].strip().lstrip("-").isdigit():
                    aliases_raw, zh_raw, count_raw = row[2], row[3], row[4]
                    count = int(count_raw) if count_raw.strip().lstrip("-").isdigit() else 0
                    alias_items = re.split(r"[|,]", aliases_raw)
                    if zh_raw.strip():
                        alias_items.append(zh_raw)
                        if key not in self.zh_by_tag:
                            self.zh_by_tag[key] = zh_raw.strip()
                else:
                    count_raw = row[2] if len(row) > 2 else "0"
                    count = int(count_raw) if count_raw.strip().lstrip("-").isdigit() else 0
                    alias_items = re.split(r"[|,]", row[3]) if len(row) > 3 else []
                self.counts[key] = max(self.counts.get(key, 0), count)
                if not use_aliases:
                    continue
                for alias in alias_items:
                    akey = normalize(alias).replace(" ", "_")
                    if len(akey) > 1:
                        old = self.by_alias.get(akey)
                        if old in BLOCKED_TAGS or key in BLOCKED_TAGS:
                            continue
                        if old is None or self.counts.get(key, 0) >= self.counts.get(old, 0):
                            self.by_alias[akey] = key
        self.groups[group] = names
        self.sources.append(f"{path.name}({len(names)})")

    # ---------- 查询 ----------
    def zh_for(self, tag: str) -> str | None:
        """英文 tag → 中文名（没有就返回 None）。悬浮气泡用这个。"""
        key = normalize(tag).replace(" ", "_")
        if not key:
            return None
        if key in self.zh_by_tag:
            return self.zh_by_tag[key]
        # 也允许传中文反查（返回规范 tag 的中文名，用于确认）
        hit = self.resolve(tag)
        if hit and hit in self.zh_by_tag:
            return self.zh_by_tag[hit]
        return None

    def load_zh_map(self, path: str | Path) -> int:
        """合并一份额外的 tag→中文 表（JSON，形如 {"1girl": "一个女孩", ...}）。
        主程序的 app/tag_zh_dict.json 就是这个格式（12.8 万条）。返回新增条数。"""
        import json
        p = Path(path)
        if not p.exists():
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            return 0
        added = 0
        for name, zh in data.items():
            if not isinstance(zh, str) or not zh.strip():
                continue
            key = normalize(str(name)).replace(" ", "_")
            if key and key not in self.zh_by_tag:
                self.zh_by_tag[key] = zh.strip()
                added += 1
        return added

    def load_zh_aliases(self, path: str | Path) -> int:
        """合并主程序的人工别名表：{"看镜头": "looking_at_viewer", ...}（优先级高于一切自动映射）。"""
        import json
        p = Path(path)
        if not p.exists():
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            return 0
        added = 0
        for zh, tag in data.items():
            if str(zh).startswith("_"):
                continue
            if not isinstance(tag, str) or not tag.strip():
                continue
            key = normalize(str(zh))
            if key and key not in self.zh_aliases:
                self.zh_aliases[key] = tag.strip()
                added += 1
        return added

    def resolve(self, raw: str) -> str | None:
        key = normalize(raw).replace(" ", "_")
        if not key or key.replace("_", " ") in JUNK or key in JUNK:
            return None
        # 中文（或含中文）先问宿主词库：它按 danbooru 投稿数取规范英文名
        if self.lexicon is not None and any("\u4e00" <= ch <= "\u9fff" for ch in raw):
            try:
                hit_zh = self.lexicon.zh_to_tag(raw.strip())
            except Exception:                                # noqa: BLE001
                hit_zh = None
            if hit_zh:
                return str(hit_zh).strip().lower().replace(" ", "_")
        if key in self.by_name:
            return self.by_name[key]
        if key in self.by_alias:
            return self.by_alias[key]
        plain = key.replace("_", " ")
        if plain in ALLOWED_NON_BOORU:
            return plain.replace(" ", "_")
        alt = key[:-1] if key.endswith("s") else key + "s"
        if alt in self.by_name:
            return self.by_name[alt]
        if alt in self.by_alias:
            return self.by_alias[alt]
        return None

    def validate(self, tag_string: str) -> tuple[str, list[str]]:
        """返回 (最终串, 词表外的条目)。命中词表的一律换成规范写法；命中不了的保留原样。"""
        kept: list[str] = []
        invented: list[str] = []
        seen = set()
        for raw in re.split(r"[,\n]", tag_string):
            raw = raw.strip()
            if not raw:
                continue
            if normalize(raw) in JUNK:
                continue
            hit = self.resolve(raw)
            if hit is None:
                display = normalize(raw).replace("_", " ")
                invented.append(display)
            else:
                display = hit.replace("_", " ")
            if display and display not in seen:
                seen.add(display)
                kept.append(display)
        return ", ".join(kept), invented

    def stats(self) -> dict:
        return {
            "tags": len(self.by_name),
            "aliases": len(self.by_alias),
            "sources": self.sources,
            "groups": {k: len(v) for k, v in self.groups.items()},
        }

    # ---------- 给模型的"参考标签"提示 ----------
    def hints(self, chinese_text: str, limit: int = 12) -> list[str]:
        """从中文输入里挑出词表里真实存在的标签，作为提示交给模型（提高命中率）。
        做法：扫描所有别名/中文名，看是否出现在输入里；优先长词、按热度排序。"""
        if not chinese_text:
            return []
        out: list[str] = []
        # 1) 宿主共享词库优先：**按中文片段逐段查**（zh_to_tag 更准）
        #    注意：不要用 lex.prompt_fix() 当提示源——它会做整体替换，可能把"半身/泳装"这类
        #    段落错配成 newhalf 之类的怪 tag（2026-10-08 实测），逐段 zh_to_tag 稳得多。
        if self.lexicon is not None:
            for seg in re.split(r"[，,。、；;：:\s]+", chinese_text):
                seg = seg.strip()
                if not seg or not re.search(r"[\u4e00-\u9fff]", seg):
                    continue
                # -1) 主程序共享的人工别名表（最优先，长词优先）
                shared = [t for zh, t in self.zh_aliases.items() if zh and zh in seg]
                if shared:
                    out.extend(t.replace("_", " ") for t in shared[:2])
                    continue
                # 0) 人工小对照表最优先（构图/表情/光线这类高频词，两个词库都可能错）
                hit_override = [tag for zh_key, tag in ZH_OVERRIDES.items() if zh_key in seg]
                if hit_override:
                    for tag in hit_override[:2]:
                        out.append(tag.replace("_", " "))
                    continue
                # 先查我们项目词典里的人工中文别名（准）：例如 半身→upper_body、初音未来→hatsune_miku
                own = self.by_alias.get(normalize(seg).replace(" ", "_"))
                if own:
                    out.append(own.replace("_", " "))
                    continue
                try:
                    hit = self.lexicon.zh_to_tag(seg)
                except Exception:                            # noqa: BLE001
                    hit = None
                if not hit:
                    continue
                hit = str(hit).strip()
                # 一致性校验：共享词库对**长短语**会错配（实测"微笑看镜头"→newhalf、"半身"→asahina mirai），
                # 所以只接受"英文 tag + 它自己的中文名跟这一段对得上"的结果。
                if re.search(r"[\u4e00-\u9fff]", hit):
                    continue
                zh = self.zh_by_tag.get(hit.lower().replace(" ", "_"), "")
                if zh:
                    if zh not in seg and seg not in zh:
                        continue
                elif len(seg) > 4 or hit.lower().replace(" ", "_") not in self.by_name:
                    continue
                out.append(hit)
        # 2) 没有宿主词库时，才用自带的别名扫描兜底
        #    （扫描是"子串命中"，容易把"未来"这种词误配到 asahina mirai，所以挂了词库就不用它）
        text = chinese_text.lower()
        found: list[tuple[int, int, str]] = []
        if self.lexicon is None:
            for alias, target in self.by_alias.items():
                if len(alias) < 2:
                    continue
                if not any("\u4e00" <= ch <= "\u9fff" for ch in alias):
                    continue
                if alias in text:
                    found.append((len(alias), self.counts.get(target, 0), target))
        found.sort(key=lambda x: (-x[0], -x[1]))
        seen = {t.lower().replace(" ", "_") for t in out}
        for _, _, tag in found:
            if tag in seen:
                continue
            seen.add(tag)
            out.append(tag.replace("_", " "))
            if len(out) >= limit:
                break
        return out[:limit]


_cache: dict[str, BooruDict] = {}


def get(root: str | Path) -> BooruDict:
    """带缓存地取词表（同一个目录只加载一次）。"""
    key = str(Path(root).resolve())
    if key not in _cache:
        _cache[key] = BooruDict.load(key)
    return _cache[key]
