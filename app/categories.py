"""标签类型（category）：存库、可自定义。

类型不只是分组名字，它还决定这个标签在用 CLIP 零样本识别时套什么提示词模板，
例如「服装」用 `wearing {}`、「人物名」用 `the anime character {}`、「道具」用 `a {} object`。
"""
from __future__ import annotations

import re
from typing import Iterable

from .config import DEFAULT_CATEGORY_TEMPLATES, DEFAULT_CATEGORIES

DEFAULT_TEMPLATE = ["{}", "a photo of {}", "{} object"]


def key_from_label(label: str, existing: Iterable[str] = ()) -> str:
    """由中文名生成一个稳定的英文 key（也可以直接输入英文 key）。"""
    label = (label or "").strip()
    if re.fullmatch(r"[a-z0-9_]{2,30}", label.lower()):
        return label.lower()
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not key or re.search(r"[\u4e00-\u9fff]", label):
        key = "c_" + re.sub(r"\W+", "", label)[:20]
    key = key.lower()          # 统一小写，和数据库里的 key 保持一致
    if not key:
        key = "custom"
    if key in existing:
        i = 2
        while f"{key}{i}" in existing:
            i += 1
        key = f"{key}{i}"
    return key


def templates_for(store, key: str) -> list[str]:
    """取某个类型的提示词模板（没有就用内置默认，再没有就用通用模板）。"""
    row = store.category(key) if store else None
    if row:
        try:
            import json
            t = json.loads(row["templates"] or "[]")
            if t:
                return [str(x) for x in t]
        except Exception:
            pass
    return list(DEFAULT_CATEGORY_TEMPLATES.get(key, DEFAULT_TEMPLATE))


def label_of(store, key: str) -> str:
    row = store.category(key) if store else None
    if row:
        return row["label"] or key
    return DEFAULT_CATEGORIES.get(key, key)


def ordered(store) -> list[dict]:
    """按自定义顺序返回全部类型：{key,label,templates,order,builtin,tag_count}。"""
    import json
    out = []
    for r in store.categories():
        try:
            tmpl = json.loads(r["templates"] or "[]")
        except Exception:
            tmpl = []
        out.append({"key": r["key"], "label": r["label"] or r["key"],
                    "templates": tmpl or list(DEFAULT_CATEGORY_TEMPLATES.get(r["key"], DEFAULT_TEMPLATE)),
                    "order": int(r["sort"] or 0), "builtin": bool(r["builtin"]),
                    "count": int(r["count"] or 0)})
    return out


def keys(store) -> list[str]:
    return [c["key"] for c in ordered(store)]


def label_map(store) -> dict[str, str]:
    return {c["key"]: c["label"] for c in ordered(store)}


def orders(store) -> list[str]:
    return [c["key"] for c in ordered(store)]


def add(store, label: str, templates: list[str] | None = None, key: str | None = None) -> str:
    exist = set(keys(store))
    k = (key or "").strip().lower() or key_from_label(label, exist)
    if k in exist:
        return k
    store.add_category(k, label.strip() or k, templates or list(DEFAULT_TEMPLATE))
    return k


def rename(store, key: str, label: str) -> None:
    store.update_category(key, label=label.strip() or key)


def set_templates(store, key: str, templates: list[str]) -> None:
    store.update_category(key, templates=[t for t in templates if t.strip()])


def remove(store, key: str) -> int:
    """删类型：该类型下的标签自动转到「其它」，标签本身不删。"""
    n = store.reassign_category(key, "other")
    store.delete_category(key)
    return n
