"""共享「标签词库服务」：主程序与扩展包（DLC，例如 AI 生图）都用这一份。

**为什么要有它**：以前生图那边的提示词助手自带一套 danbooru 词表，主程序这边也有
`tag_zh_dict.json` + `app/booru_zh/*.csv` + 拼音/联想引擎——两套词表迟早对不上。
现在统一走这里：谁要"中文→标签""标签→中文/分类/热度""父子从属""提示词校验"都调它。

用法（DLC 里通过 host.lexicon() 拿到的就是这个）：

    lex = host.lexicon()
    lex.zh_to_tag("初音未来")        # -> 'hatsune_miku'
    lex.meta("hatsune_miku")        # -> {zh, category, r18, count, parents, children}
    lex.suggest("chu")              # -> ['hatsune_miku', ...]（拼音/中文/英文前缀都能命中）
    lex.hot_tags(30)                # 库里最常用的 30 个标签
    lex.prompt_fix("初音未来 泳装")   # -> ('hatsune_miku, swimsuit', [未命中的词])
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


class TagLex:
    def __init__(self, store=None, library=None):
        self.store = store
        self.library = library
        self._zh_index: dict[str, str] | None = None
        self._ascii_index: dict[str, str] | None = None
        self._child_map: dict[str, list[str]] | None = None
        self._parent_map: dict[str, list[str]] | None = None

    # ---------- 中文 / 拼音 ----------
    def zh_index(self) -> dict[str, str]:
        """中文名 → 规范标签名（首次调用建索引，之后走缓存）。"""
        if self._zh_index is None:
            from . import tag_i18n
            try:
                self._zh_index = dict(tag_i18n.zh_index(self.store))
            except Exception:
                self._zh_index = dict(tag_i18n.zh_to_name())
        return self._zh_index

    def zh_to_tag(self, text: str) -> str:
        """中文/拼音/别名 → 规范标签名（认不出就原样返回）。

        优先用"英文规范名"那份索引（生图友好）：主程序的 `zh_index()` 会优先库里手填的中文名、
        且同类里取最短的名字，于是「初音未来」会被中文垃圾标签「初音」抢走——喂给生图模型就是
        它不认识的词。英文名里找不到时再退回主程序索引（分级标签「全年龄」「R15」这种本来就该是中文）。
        """
        t = (text or "").strip()
        if not t:
            return ""
        hit = self.ascii_index().get(t)
        if hit:
            return hit
        from . import tag_i18n
        try:
            return str(tag_i18n.resolve(t, index=self.zh_index(), store=self.store) or t)
        except Exception:
            return self.zh_index().get(t, t)

    def ascii_index(self) -> dict[str, str]:
        """中文/别名 → **英文规范标签名**（生图/提示词专用）。

        两个来源合并，**以 danbooru 词表优先**：
          1) `app/booru_zh/*.csv`：带**投稿数**，同一个中文名/别名对多个标签时取投稿数最大的
             （所以「初音未来」→ `hatsune_miku`（14 万）而不是 `miku`；「泳装」→ `swimsuit`
             而不是 `mizugi`）；
          2) 内置 `tag_zh_dict.json`：补 CSV 没覆盖的中文名（只在 ASCII 名里挑，避免被中文垃圾名劫持）。
        别名也进索引（如 `初音`/`miku` 也能解析到 `hatsune_miku`）。
        """
        if self._ascii_index is None:
            best: dict[str, tuple[int, str]] = {}
            # 1) danbooru 词表（按投稿数挑）
            try:
                import csv as _csv
                base = Path(__file__).with_name("booru_zh")
                for f in sorted(base.glob("*.csv")):
                    with f.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
                        for i, r in enumerate(_csv.reader(fh)):
                            if i == 0 or len(r) < 5:      # 跳过表头
                                continue
                            name = (r[0] or "").strip()
                            if not name or not name.isascii():
                                continue
                            try:
                                cnt = int((r[4] or "0").strip() or 0)
                            except Exception:
                                cnt = 0
                            keys = [(r[3] or "").strip()] + \
                                   [a.strip() for a in (r[2] or "").split("|") if a.strip()]
                            keys.append(name)
                            for k in keys:
                                old = best.get(k)
                                if old is None or cnt > old[0] or (cnt == old[0] and
                                                                   (name.count("_"), len(name)) <
                                                                   (old[1].count("_"), len(old[1]))):
                                    best[k] = (cnt, name)
            except Exception:
                pass
            # 2) 内置词典补齐
            from . import tag_i18n
            raw = getattr(tag_i18n, "MODEL_DICT", {}) or {}
            idx: dict[str, str] = {}
            for name, zh in raw.items():
                zh_s, name_s = (zh or "").strip(), str(name or "").strip()
                if not zh_s or not name_s or not name_s.isascii():
                    continue
                old = idx.get(zh_s)
                if old is None or (name_s.count("_"), len(name_s), name_s) < \
                        (old.count("_"), len(old), old):
                    idx[zh_s] = name_s
            for k, (_cnt, name) in best.items():      # danbooru 优先覆盖
                idx[k] = name
            self._ascii_index = idx
        return self._ascii_index

    def suggest(self, text: str, limit: int = 12) -> list[str]:
        """联想：中文、拼音（含首字母）、英文前缀都能命中，按匹配度排。"""
        q = (text or "").strip().lower()
        if not q:
            return self.hot_tags(limit)
        from . import tag_i18n
        out: list[str] = []
        for zh, name in self.zh_index().items():
            if name in out:
                continue
            if zh.lower().startswith(q) or q in zh.lower() or name.lower().startswith(q):
                out.append(name)
            elif len(q) >= 2 and tag_i18n.pinyin_initials(zh).lower().startswith(q):
                out.append(name)
            if len(out) >= limit * 3:
                break
        ranked = sorted(out, key=lambda n: (0 if n.lower().startswith(q) else 1, len(n)))
        return ranked[:limit]

    # ---------- 元信息 ----------
    def meta(self, name: str) -> dict:
        """标签的完整信息：中文名、分类、是否 R18、库内图片数、父/子标签。"""
        n = str(name or "")
        row = self.store.one(
            "SELECT t.name, t.zh, t.category, t.r18, "
            "(SELECT COUNT(*) FROM file_tags ft WHERE ft.tag_id=t.id AND ft.status='confirmed') AS n "
            "FROM tags t WHERE t.name=?", (n,)) if self.store is not None else None
        return {"name": n,
                "zh": (row["zh"] if row else "") or "",
                "category": (row["category"] if row else "") or "",
                "r18": bool(row["r18"]) if row is not None and "r18" in row.keys() else False,
                "count": int((row["n"] if row else 0) or 0),
                "parents": self.parents_of(n),
                "children": self.children_of(n)}

    def child_map(self) -> dict[str, list[str]]:
        if self._child_map is None:
            try:
                self._child_map = {k: list(v) for k, v in self.store.tag_child_map().items()}
            except Exception:
                self._child_map = {}
        return self._child_map

    def parent_map(self) -> dict[str, list[str]]:
        if self._parent_map is None:
            try:
                self._parent_map = {k: list(v) for k, v in self.store.tag_parent_map().items()}
            except Exception:
                self._parent_map = {}
        return self._parent_map

    def children_of(self, name: str) -> list[str]:
        return list(self.child_map().get(str(name), []) or [])

    def parents_of(self, name: str) -> list[str]:
        return list(self.parent_map().get(str(name), []) or [])

    def most_specific(self, names: list[str]) -> list[str]:
        """只保留更具体的标签（有"白裙子"就不再要"裙子"）——和写回文件名同一套规则。"""
        try:
            from .library import Library
            return Library.most_specific_tags(list(names), self.child_map())
        except Exception:
            return list(names)

    # ---------- 热度 / 模板 ----------
    def hot_tags(self, limit: int = 50, category: str = "") -> list[str]:
        """库里确认过的标签按图片数排序（生图时的"我常用什么"推荐）。"""
        if self.store is None:
            return []
        args: list = []
        where = "ft.status='confirmed'"
        if category:
            where += " AND t.category=?"
            args.append(category)
        try:
            rows = self.store.query(
                "SELECT t.name n, COUNT(*) c FROM file_tags ft JOIN tags t ON t.id=ft.tag_id "
                f"WHERE {where} GROUP BY t.name ORDER BY c DESC LIMIT ?", (*args, int(limit)))
            return [str(r["n"]) for r in rows]
        except Exception:
            return []

    def prompt_fix(self, text: str, keep_unknown: bool = False,
                   keep_ascii: bool = True) -> tuple[str, list[str]]:
        """把一句（中文或英文）提示词里的词换成**词库里真实存在的规范标签**。

        返回 (规范提示词, 未命中的词列表)。
        - 中文词：必须能在词表里对上才保留（这就是"别自造 tag"的守卫）；
        - 英文词：默认原样保留（`masterpiece`/`absurdres` 这类画质词本来就不在角色词表里）；
          要强制全部校验就传 `keep_ascii=False`；
        - `keep_unknown=True` 时中文未命中的也保留。
        """
        import re
        parts = [p for p in re.split(r"[,，、\s]+", text or "") if p]
        out, unknown = [], []
        for p in parts:
            if p.isascii() and re.fullmatch(r"[\w\-().:']+", p):
                if keep_ascii:
                    out.append(p)
                else:
                    row = self.store.one("SELECT 1 FROM tags WHERE name=?", (p,)) if self.store is not None else None
                    (out if row else unknown).append(p)
                continue
            tag = self.zh_to_tag(p)
            if tag != p or p in self.zh_index() or tag in self.ascii_index().values():
                out.append(tag)
            else:
                (out if keep_unknown else unknown).append(p)
        return ", ".join(out), unknown

    def booru_rows(self, limit: int = 0) -> list[dict]:
        """danbooru 中文词表（app/booru_zh/*.csv）：给提示词助手做"必须用真实 tag"的校验。

        字段：name / category / aliases / zh / cn_name / count / desc。
        `limit>0` 时只取前若干条（调试/预览用）。
        """
        import csv
        from pathlib import Path
        base = Path(__file__).with_name("booru_zh")
        rows: list[dict] = []
        if not base.is_dir():
            return rows
        for f in sorted(base.glob("*.csv")):
            try:
                with f.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
                    for i, r in enumerate(csv.reader(fh)):
                        if not r:
                            continue
                        rows.append({"name": r[0], "category": r[1] if len(r) > 1 else "",
                                     "aliases": r[2] if len(r) > 2 else "",
                                     "zh": r[3] if len(r) > 3 else "",
                                     "cn_name": r[4] if len(r) > 4 else "",
                                     "count": r[5] if len(r) > 5 else "",
                                     "desc": r[6] if len(r) > 6 else ""})
                        if limit and len(rows) >= limit:
                            return rows
            except Exception:
                continue
        return rows


@lru_cache(maxsize=4)
def _cached(_key: int, store=None, library=None) -> TagLex:
    return TagLex(store, library)


def get_lexicon(store=None, library=None) -> TagLex:
    """进程内共享一份（索引建一次就够，别每个窗口都重新读 12 万条词典）。"""
    key = id(store) if store is not None else 0
    return _cached(key, store, library)
