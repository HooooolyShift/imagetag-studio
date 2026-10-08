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
    def resolve(self, raw: str) -> str | None:
        key = normalize(raw).replace(" ", "_")
        if not key or key.replace("_", " ") in JUNK or key in JUNK:
            return None
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
        text = chinese_text.lower()
        found: list[tuple[int, int, str]] = []
        for alias, target in self.by_alias.items():
            if len(alias) < 2:
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in alias):
                continue
            if alias in text:
                found.append((len(alias), self.counts.get(target, 0), target))
        found.sort(key=lambda x: (-x[0], -x[1]))
        seen, out = set(), []
        for _, _, tag in found:
            if tag in seen:
                continue
            seen.add(tag)
            out.append(tag.replace("_", " "))
            if len(out) >= limit:
                break
        return out


_cache: dict[str, BooruDict] = {}


def get(root: str | Path) -> BooruDict:
    """带缓存地取词表（同一个目录只加载一次）。"""
    key = str(Path(root).resolve())
    if key not in _cache:
        _cache[key] = BooruDict.load(key)
    return _cache[key]
