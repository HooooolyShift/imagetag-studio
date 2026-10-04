"""标签命名约定：把标签写进文件名/文件夹名，方便手机文件管理器与其它软件识别。

约定（TagSpaces 风格，纯文件名，跨平台、跨 App 通用）：
    单图:   <原文件名> [tag1 tag2].jpg
    系列:   <系列名> [tag1 tag2]/001.jpg  （文件夹名带标签，内部文件是页码）
文件名里的 [ ] 之外部分保持原样，标签总是放在末尾的方括号里。
"""
from __future__ import annotations

import re
from pathlib import Path

ILLEGAL = set('[]<>:"/\\|?*')
_TAG_BLOCK = re.compile(r"\s*\[([^\[\]]*)\]\s*$")
_ALL_BLOCKS = re.compile(r"\[([^\[\]]*)\]")
MAX_TAG_LEN = 40
MAX_STEM_LEN = 150          # Windows 全路径 260 限制下，留足目录层级的余量
MAX_BASE_WITH_TAGS = 60     # 带标签时，原文件名最多保留多少字符


def sanitize_tag(tag: str) -> str:
    """清掉标签里不能进文件名的字符。"""
    t = (tag or "").strip().replace("\n", " ").replace("\t", " ")
    t = t.replace("[", "(").replace("]", ")")
    out = []
    for ch in t:
        out.append("_" if ch in ILLEGAL else ch)
    t = re.sub(r"\s+", " ", "".join(out)).strip(" .")
    t = t.replace(" ", "_") if " " in t and len(t) <= 20 else t
    return t[:MAX_TAG_LEN]


def parse_tags_from_name(name: str) -> list[str]:
    """从文件名或文件夹名里取出所有 [...] 中的标签。"""
    tags: list[str] = []
    for block in _ALL_BLOCKS.findall(name):
        for part in re.split(r"[\s,;，、]+", block):
            part = part.strip()
            if part and part not in tags:
                tags.append(part)
    return tags


def split_name(stem: str) -> tuple[str, list[str]]:
    """拆成 (基础名, 标签列表)。

    从末尾反复剥离方括号块，兼容 `名字 [tag1] [tag2]` 这种多个方括号的写法；
    若整个名字都是方括号（没有基础名），保留原样，避免把名字吃没了。
    """
    base = (stem or "").strip()
    tags: list[str] = []
    while True:
        m = _TAG_BLOCK.search(base)
        if not m:
            break
        head = base[: m.start()].strip()
        if not head:
            # 整个名字就是 [标签]（没有基础名）→ 保留标签、基础名留空，避免再叠一层
            tags = [t for t in re.split(r"[\s,;，、]+", m.group(1)) if t] + tags
            base = ""
            break
        part = [t for t in re.split(r"[\s,;，、]+", m.group(1)) if t]
        tags = part + tags
        base = head
    return base, tags


def build_name(base: str, tags, trailing: bool = True, max_tags: int | None = None,
               max_len: int = MAX_STEM_LEN, keep_base: bool = True) -> str:
    """由基础名 + 标签拼回文件名（不含后缀）。超长时截断标签，避免 Windows 路径超限。"""
    base = (base or "").strip()
    seen: list[str] = []
    for t in tags:
        t = sanitize_tag(str(t))
        if t and t not in seen:
            seen.append(t)
    if not seen:
        return base[:max_len] or "untitled"
    if not keep_base:
        base = ""            # 只用标签命名
    if max_tags:
        seen = seen[:max_tags]
    # 逐个加标签，直到超出长度预算
    kept: list[str] = []
    for t in seen:
        cand = kept + [t]
        if len(base[:MAX_BASE_WITH_TAGS]) + len(f"[{' '.join(cand)}]") + 1 > max_len and kept:
            break
        kept = cand
    block = f"[{' '.join(kept)}]"
    room = max(20, max_len - len(block))
    base = base[:min(room, MAX_BASE_WITH_TAGS if kept else room)].rstrip()
    if not base:
        return block
    return f"{base} {block}" if trailing else f"{block} {base}"


def replace_tags(path: Path, tags) -> Path:
    """返回写入了标签的新路径（不改动磁盘）。"""
    base, _ = split_name(path.stem)
    new_stem = build_name(base, tags)
    return path.with_name(new_stem + path.suffix.lower())


def build_series_dirname(name: str, tags, max_tags: int = 8) -> str:
    return build_name(name, tags, max_tags=max_tags, max_len=120)


def page_filename(index: int, digits: int = 3, ext: str = ".jpg") -> str:
    return f"{index:0{digits}d}{ext}"
