"""SQLite 索引：库根目录、文件、标签、系列、人脸。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS roots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    label TEXT,
    is_library INTEGER NOT NULL DEFAULT 0,     -- 1=专用图库(正式存放) 0=扫描来源
    drive TEXT,
    added_at REAL
);
CREATE TABLE IF NOT EXISTS tags(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    prompt TEXT,
    requires TEXT,                            -- 前置条件标签（空格分隔）：例如「领带」需要「人物」
    zh TEXT,                                  -- 用户填的中文名（审核界面优先显示）
    note TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    auto INTEGER NOT NULL DEFAULT 1,
    count INTEGER NOT NULL DEFAULT 0,
    created_at REAL,
    updated_at REAL
);
-- 标签类型（可自定义）：类型决定 CLIP 提示词模板与界面分组
CREATE TABLE IF NOT EXISTS categories(
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    templates TEXT,
    sort INTEGER DEFAULT 100,
    color TEXT,
    builtin INTEGER DEFAULT 0,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS series(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id INTEGER,
    dir TEXT NOT NULL,
    name TEXT,
    tags TEXT,
    page_count INTEGER DEFAULT 0,
    first_file_id INTEGER,
    created_at REAL,
    updated_at REAL,
    UNIQUE(root_id, dir)
);
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id INTEGER NOT NULL,
    path TEXT UNIQUE NOT NULL,
    rel TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT,
    size INTEGER,
    mtime REAL,
    width INTEGER,
    height INTEGER,
    quick_hw TEXT,
    phash TEXT,
    ahash TEXT,
    phash2 TEXT,
    dup_checked INTEGER DEFAULT 0,
    series_id INTEGER,
    page_no INTEGER,
    kind TEXT DEFAULT 'image',
    rating TEXT,
    rating_scores TEXT,
    auto_json TEXT,
    clip_model TEXT,
    clip_vec BLOB,
    wd_done INTEGER DEFAULT 0,
    clip_done INTEGER DEFAULT 0,
    face_done INTEGER DEFAULT 0,
    region_done INTEGER DEFAULT 0,
    manual INTEGER DEFAULT 0,
    reviewed INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    added_at REAL,
    scanned_at REAL
);
CREATE TABLE IF NOT EXISTS file_tags(
    file_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    score REAL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'confirmed',
    updated_at REAL,
    PRIMARY KEY(file_id, tag_id)
);
CREATE TABLE IF NOT EXISTS faces(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    person_id INTEGER,
    bbox TEXT,
    det_score REAL,
    emb BLOB,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS persons(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    tag_id INTEGER,
    count INTEGER DEFAULT 0,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS regions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    tag_id INTEGER,
    tag_name TEXT,
    x REAL NOT NULL,
    y REAL NOT NULL,
    w REAL NOT NULL,
    h REAL NOT NULL,
    note TEXT,
    clip_vec BLOB,
    clip_vec_ctx BLOB,
    clip_model TEXT,
    created_at REAL
);
-- ============ 标签体系（与图片分离的一层：图片只存 tag，层级/分组存在这里）============
CREATE TABLE IF NOT EXISTS nodes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'group',      -- group=大类/分类节点
    note TEXT,
    color TEXT,
    x REAL, y REAL,                          -- 画布位置（手动拖动后保存）
    sort INTEGER DEFAULT 0,
    collapsed INTEGER DEFAULT 0,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS taxonomy_edges(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_kind TEXT NOT NULL,               -- 'node'
    parent_id INTEGER NOT NULL,
    child_kind TEXT NOT NULL,                -- 'node' | 'tag'
    child_id INTEGER NOT NULL,
    relation TEXT NOT NULL DEFAULT 'is_a',
    created_at REAL,
    UNIQUE(parent_kind, parent_id, child_kind, child_id, relation)
);
-- 自训练：用你确认过的标注在线更新每个标签的“特征中心/分类器”
CREATE TABLE IF NOT EXISTS tag_probe(
    tag_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                      -- centroid | logit
    data BLOB,
    n_pos INTEGER DEFAULT 0,
    n_neg INTEGER DEFAULT 0,
    updated_at REAL,
    PRIMARY KEY(tag_id, kind)
);
-- 图谱布局（分类节点和标签节点都可以记位置）
CREATE TABLE IF NOT EXISTS layout(
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    x REAL,
    y REAL,
    PRIMARY KEY(kind, ref_id)
);
-- 任务持久化：断电/关机后再次启动可继续
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    params TEXT,
    ids TEXT NOT NULL,
    total INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    note TEXT,
    created_at REAL,
    updated_at REAL
);
-- 重复图：误报反馈（标记过“这不是重复”的组合以后不再提示）
CREATE TABLE IF NOT EXISTS dup_feedback(
    pair_key TEXT PRIMARY KEY,
    action TEXT,
    note TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_id);
CREATE INDEX IF NOT EXISTS idx_files_series ON files(series_id, page_no);
CREATE INDEX IF NOT EXISTS idx_ft_tag ON file_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_ft_file ON file_tags(file_id);
CREATE INDEX IF NOT EXISTS idx_faces_file ON faces(file_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
CREATE INDEX IF NOT EXISTS idx_regions_file ON regions(file_id);
CREATE INDEX IF NOT EXISTS idx_edge_parent ON taxonomy_edges(parent_kind, parent_id);
CREATE INDEX IF NOT EXISTS idx_edge_child ON taxonomy_edges(child_kind, child_id);
/* 统计"某标签有多少张图"要 join files 判断 missing，而 files 行里带 CLIP 特征 BLOB，
   没有覆盖索引就得回表读十几万次（实测 1.1 秒）→ 这个索引让统计只走索引 */
CREATE INDEX IF NOT EXISTS idx_files_id_missing ON files(id, missing);
"""


class Store:
    """线程安全的轻量封装：每线程一个连接。"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else data_dir() / "library.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema','1')")
        self.ensure_default_categories()
        self.sync_categories_from_tags()

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """老库升级：补齐后加的列。"""
        wanted = {
            "regions": [("clip_vec", "BLOB"), ("clip_vec_ctx", "BLOB"), ("clip_model", "TEXT")],
            "files": [("rating", "TEXT"), ("auto_json", "TEXT"), ("phash2", "TEXT"),
                      ("rating_scores", "TEXT"),
                      ("region_done", "INTEGER DEFAULT 0"), ("reviewed", "INTEGER DEFAULT 0"),
                      ("phash", "TEXT"), ("ahash", "TEXT"), ("dup_checked", "INTEGER DEFAULT 0")],
            "tags": [("requires", "TEXT"), ("zh", "TEXT")],
            "file_tags": [("status", "TEXT NOT NULL DEFAULT 'confirmed'")],
            "roots": [("is_library", "INTEGER NOT NULL DEFAULT 0"), ("drive", "TEXT")],
            "taxonomy_edges": [("weight", "REAL DEFAULT 1.0")],
        }
        for table, cols in wanted.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                    if table == "file_tags" and name == "status":
                        # 老库升级：以前 AI 直接写的标签改为「待审核」，让你补审一遍
                        c.execute("UPDATE file_tags SET status='pending' "
                                  "WHERE source NOT IN ('manual','series','face')")

    # ---------------- 基础 ----------------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def execute(self, sql: str, args: Sequence[Any] = ()) -> sqlite3.Cursor:
        c = self.conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur

    def query(self, sql: str, args: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, args).fetchall()

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        c = self.conn()
        c.executemany(sql, seq)
        c.commit()

    def one(self, sql: str, args: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, args).fetchone()

    # ---------------- 库根目录 ----------------
    def add_root(self, path: str | Path, label: str | None = None) -> int:
        p = str(Path(path).resolve())
        row = self.one("SELECT id FROM roots WHERE path=?", (p,))
        if row:
            return int(row["id"])
        drive = Path(p).drive
        is_lib = 1 if Path(p).name.lower() in ("imagetags", "图片标签库") else 0
        cur = self.execute("INSERT INTO roots(path,label,is_library,drive,added_at) VALUES(?,?,?,?,?)",
                           (p, label or Path(p).name, is_lib, drive, time.time()))
        return int(cur.lastrowid)

    def list_roots(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM roots ORDER BY id")

    def library_roots(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM roots WHERE is_library=1 ORDER BY id")

    def set_root_library(self, root_id: int, is_library: bool = True) -> None:
        self.execute("UPDATE roots SET is_library=? WHERE id=?", (1 if is_library else 0, root_id))

    def root_for_drive(self, drive: str, create: bool = False, dir_name: str = "ImageTags") -> int | None:
        """找一个盘符上的专用图库（Steam 式多库）；不存在的盘可以现场创建。"""
        drive = (drive or "").upper()
        # 优先匹配当前设置的目录名，其次复用该盘上已有的图库
        want = Path(f"{drive}\\{dir_name}")
        row = self.one("SELECT id FROM roots WHERE is_library=1 AND drive=? AND path=?", (drive, str(want)))
        if row:
            return int(row["id"])
        row = self.one("SELECT id FROM roots WHERE is_library=1 AND drive=?", (drive,))
        if row:
            return int(row["id"])
        if not create or not drive:
            return None
        rid = self.add_root(want)
        self.set_root_library(rid, True)
        return rid

    def remove_root(self, root_id: int) -> None:
        self.execute("DELETE FROM file_tags WHERE file_id IN (SELECT id FROM files WHERE root_id=?)", (root_id,))
        self.execute("DELETE FROM files WHERE root_id=?", (root_id,))
        self.execute("DELETE FROM series WHERE root_id=?", (root_id,))
        self.execute("DELETE FROM roots WHERE id=?", (root_id,))

    def remove_dir_index(self, root_id: int, rel_prefix: str) -> int:
        """把一个子目录从索引里移除（只删数据库记录，磁盘文件不动）。"""
        rel = rel_prefix.replace("\\", "/").rstrip("/")
        like = rel + "/%"
        ids = [int(r["id"]) for r in self.query(
            "SELECT id FROM files WHERE root_id=? AND (rel LIKE ? OR rel=?)", (root_id, like, rel))]
        if not ids:
            return 0
        ph = ",".join("?" * len(ids))
        self.execute(f"DELETE FROM file_tags WHERE file_id IN ({ph})", ids)
        self.execute(f"DELETE FROM faces WHERE file_id IN ({ph})", ids)
        self.execute(f"DELETE FROM regions WHERE file_id IN ({ph})", ids)
        self.execute(f"DELETE FROM files WHERE id IN ({ph})", ids)
        for s in self.query("SELECT id FROM series WHERE root_id=? AND (dir LIKE ? OR dir=?)",
                            (root_id, like, rel)):
            self.update_series_stats(int(s["id"]))
        self.refresh_counts()
        return len(ids)

    # ---------------- 文件 ----------------
    def upsert_file(self, **kw) -> int:
        """按 path 插入或更新一条文件记录，返回 id。"""
        path = str(kw["path"])
        row = self.one("SELECT id FROM files WHERE path=?", (path,))
        now = time.time()
        if row:
            fid = int(row["id"])
            allowed = ("root_id", "rel", "name", "ext", "size", "mtime", "width", "height",
                       "series_id", "page_no", "kind", "manual", "missing", "scanned_at", "rating")
            sets, args = [], []
            for k in allowed:
                if k in kw:
                    sets.append(f"{k}=?")
                    args.append(kw[k])
            sets.append("scanned_at=?")
            args.append(now)
            if sets:
                args.append(fid)
                self.execute(f"UPDATE files SET {','.join(sets)} WHERE id=?", args)
            return fid
        cols = ["root_id", "path", "rel", "name", "ext", "size", "mtime", "width", "height",
                "series_id", "page_no", "kind", "added_at", "scanned_at", "manual", "missing"]
        vals = [kw.get("root_id"), path, kw.get("rel"), kw.get("name"), kw.get("ext"), kw.get("size"),
                kw.get("mtime"), kw.get("width"), kw.get("height"), kw.get("series_id"), kw.get("page_no"),
                kw.get("kind", "image"), now, now, kw.get("manual", 0), kw.get("missing", 0)]
        cur = self.execute(f"INSERT INTO files({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        return int(cur.lastrowid)

    def file_by_path(self, path: str | Path):
        return self.one("SELECT * FROM files WHERE path=?", (str(Path(path).resolve()),))

    def files_by_ids(self, ids: Iterable[int]) -> list[sqlite3.Row]:
        ids = list(ids)
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        return self.query(f"SELECT * FROM files WHERE id IN ({ph})", ids)

    def mark_missing(self, root_id: int, seen: set[str]) -> int:
        rows = self.query("SELECT id,path FROM files WHERE root_id=?", (root_id,))
        gone = [int(r["id"]) for r in rows if r["path"] not in seen]
        if gone:
            ph = ",".join("?" * len(gone))
            self.execute(f"UPDATE files SET missing=1 WHERE id IN ({ph})", gone)
        return len(gone)

    def clear_missing(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        self.execute(f"UPDATE files SET missing=0 WHERE id IN ({ph})", ids)

    # ---------------- 标签 ----------------
    def ensure_tag(self, name: str, category: str = "other", prompt: str | None = None, auto: int = 1) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("标签名不能为空")
        self.ensure_category(category)
        row = self.one("SELECT id FROM tags WHERE name=?", (name,))
        now = time.time()
        if row:
            return int(row["id"])
        cur = self.execute(
            "INSERT INTO tags(name,category,prompt,enabled,auto,count,created_at,updated_at) VALUES(?,?,?,1,?,0,?,?)",
            (name, category, prompt, auto, now, now))
        tid = int(cur.lastrowid)
        # 新增标签时自动带上中文名：内置词典来自 Danbooru 社区词表（本地 app/booru_zh/），
        # 以后你新建的标签只要能对上，中文名就直接填好
        self.fill_zh_from_dict(tid, name)
        return tid

    def fill_zh_from_dict(self, tag_id: int, name: str) -> str:
        """从本地词典给标签补中文名（只补空的，不动你手填的）。返回补上的名字。"""
        import re
        from . import tag_i18n
        row = self.one("SELECT zh FROM tags WHERE id=?", (int(tag_id),))
        if not row or (row["zh"] or "").strip():
            return ""
        zh = tag_i18n.label(name or "", "")
        if not zh or zh == name or not re.search(r"[\u4e00-\u9fff]", zh):
            return ""
        self.update_tag(int(tag_id), zh=zh)
        return zh

    # ---------------- 标签类型 ----------------
    def categories(self) -> list[sqlite3.Row]:
        self.ensure_default_categories()
        return self.query(
            "SELECT c.*, (SELECT COUNT(*) FROM tags t WHERE t.category=c.key) AS count "
            "FROM categories c ORDER BY c.sort, c.key")

    def category(self, key: str):
        return self.one("SELECT * FROM categories WHERE key=?", (key,))

    def ensure_default_categories(self) -> int:
        """首次运行时把内置类型写进库；已存在的不动（用户改过就保留用户的）。"""
        from .config import DEFAULT_CATEGORIES, DEFAULT_CATEGORY_TEMPLATES
        n = 0
        now = time.time()
        for i, (key, label) in enumerate(DEFAULT_CATEGORIES.items()):
            if self.one("SELECT 1 FROM categories WHERE key=?", (key,)):
                continue
            self.execute(
                "INSERT INTO categories(key,label,templates,sort,builtin,created_at) VALUES(?,?,?,?,1,?)",
                (key, label, json.dumps(DEFAULT_CATEGORY_TEMPLATES.get(key, ["{}"]), ensure_ascii=False),
                 i * 10, now))
            n += 1
        return n

    def ensure_category(self, key: str, label: str | None = None) -> str:
        """确保某个类型存在（老库/自定义 key 自动补一条）。"""
        key = (key or "other").strip() or "other"
        if self.one("SELECT 1 FROM categories WHERE key=?", (key,)):
            return key
        from .config import DEFAULT_CATEGORIES, DEFAULT_CATEGORY_TEMPLATES
        now = time.time()
        self.execute("INSERT INTO categories(key,label,templates,sort,builtin,created_at) VALUES(?,?,?,?,0,?)",
                     (key, label or DEFAULT_CATEGORIES.get(key, key),
                      json.dumps(DEFAULT_CATEGORY_TEMPLATES.get(key, ["{}", "a photo of {}"]), ensure_ascii=False),
                      500, now))
        return key

    def add_category(self, key: str, label: str, templates: Sequence[str] | None = None,
                     sort: int | None = None) -> None:
        key = (key or "").strip().lower()
        if not key:
            raise ValueError("类型 key 不能为空")
        if sort is None:
            row = self.one("SELECT MAX(sort) m FROM categories")
            sort = int((row["m"] or 0) + 10)
        self.execute("INSERT OR REPLACE INTO categories(key,label,templates,sort,builtin,created_at) "
                     "VALUES(?,?,?,?,0,?)",
                     (key, label or key, json.dumps(list(templates or ["{}"]), ensure_ascii=False), sort, time.time()))

    def update_category(self, key: str, label: str | None = None, templates: Sequence[str] | None = None,
                        sort: int | None = None, color: str | None = None) -> None:
        sets, args = [], []
        if label is not None:
            sets.append("label=?")
            args.append(label)
        if templates is not None:
            sets.append("templates=?")
            args.append(json.dumps(list(templates), ensure_ascii=False))
        if sort is not None:
            sets.append("sort=?")
            args.append(int(sort))
        if color is not None:
            sets.append("color=?")
            args.append(color)
        if not sets:
            return
        args.append(key)
        self.execute(f"UPDATE categories SET {','.join(sets)} WHERE key=?", args)

    def reassign_category(self, key: str, new_key: str = "other") -> int:
        cur = self.execute("UPDATE tags SET category=? WHERE category=?", (new_key, key))
        return cur.rowcount

    def delete_category(self, key: str) -> None:
        self.execute("DELETE FROM categories WHERE key=?", (key,))

    def sync_categories_from_tags(self) -> int:
        """把库里出现过、但类型表里没有的类型补上（老库升级用）。"""
        now = time.time()
        cur = self.execute(
            "INSERT OR IGNORE INTO categories(key,label,templates,sort,builtin,created_at) "
            "SELECT DISTINCT t.category, t.category, ?, 500, 0, ? FROM tags t "
            "WHERE t.category IS NOT NULL AND t.category<>'' AND t.category NOT IN (SELECT key FROM categories)",
            (json.dumps(["{}", "a photo of {}"], ensure_ascii=False), now))
        return cur.rowcount

    def tag_id(self, name: str) -> int | None:
        row = self.one("SELECT id FROM tags WHERE name=?", (name,))
        return int(row["id"]) if row else None

    # ---------- 标签状态：pending(待审核) / confirmed(已生效) / rejected(已拒绝) ----------
    ALL_STATUS = ("confirmed", "pending", "rejected")

    def tags_for_file(self, file_id: int, statuses: Sequence[str] | None = ("confirmed",)) -> list[sqlite3.Row]:
        sql = ("SELECT t.id,t.name,t.zh,t.category,ft.source,ft.score,ft.status,ft.updated_at "
               "FROM file_tags ft JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id=?")
        args: list[Any] = [file_id]
        if statuses:
            sql += " AND ft.status IN (%s)" % ",".join("?" * len(statuses))
            args.extend(statuses)
        sql += " ORDER BY (ft.status='pending') DESC, (ft.source='manual') DESC, ft.score DESC"
        return self.query(sql, args)

    def tags_for_files(self, ids: Sequence[int], statuses: Sequence[str] | None = ("confirmed",)) -> dict[int, list[sqlite3.Row]]:
        ids = list(ids)
        out: dict[int, list[sqlite3.Row]] = {i: [] for i in ids}
        if not ids:
            return out
        ph = ",".join("?" * len(ids))
        sql = (f"SELECT ft.file_id,t.id,t.name,t.zh,t.category,ft.source,ft.score,ft.status FROM file_tags ft "
               f"JOIN tags t ON t.id=ft.tag_id WHERE ft.file_id IN ({ph})")
        args = list(ids)
        if statuses:
            sql += " AND ft.status IN (%s)" % ",".join("?" * len(statuses))
            args.extend(statuses)
        sql += " ORDER BY (ft.status='pending') DESC, (ft.source='manual') DESC, ft.score DESC"
        rows = self.query(sql, args)
        for r in rows:
            out[int(r["file_id"])].append(r)
        return out

    def set_file_tags(self, file_id: int, tags: Iterable[tuple[str, str, float]], category: str = "other") -> None:
        """tags: (名称, 来源, 分数) —— 覆盖该文件的全部标签。"""
        self.execute("DELETE FROM file_tags WHERE file_id=?", (file_id,))
        self.add_file_tags(file_id, tags, category)

    def add_file_tags(self, file_id: int, tags: Iterable[tuple[str, str, float]], category: str = "other",
                      status: str | None = None, override: bool = False) -> int:
        """写标签。override=False 时不会覆盖已存在的行（保护人工审核结果）。

        status 为 None 时按来源自动判定：manual/series/face 直接生效，其余（AI）先进待审核。
        """
        now = time.time()
        c = self.conn()
        manual = 0
        n = 0
        for name, source, score in tags:
            name = (name or "").strip()
            if not name:
                continue
            tid = self.ensure_tag(name, category)
            # 手动/系列/人脸/从文件名读回(含推断出的父标签) 直接生效；AI 产生的才进待审核
            st = status or ("confirmed" if source in ("manual", "series", "face", "filename",
                                                      "filename_parent") else "pending")
            verb = "INSERT OR REPLACE" if override else "INSERT OR IGNORE"
            cur = c.execute(f"{verb} INTO file_tags(file_id,tag_id,source,score,status,updated_at) "
                            f"VALUES(?,?,?,?,?,?)", (file_id, tid, source, float(score), st, now))
            n += cur.rowcount if cur.rowcount > 0 else 0
            if source == "manual":
                manual = 1
        c.commit()
        if manual:
            self.execute("UPDATE files SET manual=1 WHERE id=?", (file_id,))
        return n

    def set_tag_status(self, file_id: int, names: Sequence[str], status: str,
                       reviewed: bool = True) -> int:
        names = [n for n in names if n]
        if not names:
            return 0
        ph = ",".join("?" * len(names))
        cur = self.execute(
            f"UPDATE file_tags SET status=?, updated_at=? WHERE file_id=? AND tag_id IN "
            f"(SELECT id FROM tags WHERE name IN ({ph}))", [status, time.time(), file_id, *names])
        if reviewed:
            self.execute("UPDATE files SET reviewed=1 WHERE id=?", (file_id,))
        return cur.rowcount

    def set_all_pending_status(self, file_id: int, status: str) -> int:
        cur = self.execute("UPDATE file_tags SET status=?, updated_at=? WHERE file_id=? AND status='pending'",
                           (status, time.time(), file_id))
        self.execute("UPDATE files SET reviewed=1 WHERE id=?", (file_id,))
        return cur.rowcount

    def pending_files(self, limit: int = 500) -> list[sqlite3.Row]:
        """待审核图片：有 pending 标签的，**或者**还没定级的（未定级也要人工看，避免漏掉）。"""
        return self.query(
            "SELECT f.*, "
            "  (SELECT COUNT(*) FROM file_tags ft WHERE ft.file_id=f.id AND ft.status='pending') AS n_pending, "
            "  CASE WHEN f.rating IS NULL OR f.rating='' THEN 1 ELSE 0 END AS no_rating "
            "FROM files f WHERE f.missing=0 AND ("
            "  EXISTS(SELECT 1 FROM file_tags ft WHERE ft.file_id=f.id AND ft.status='pending')"
            "  OR f.rating IS NULL OR f.rating='') "
            "ORDER BY no_rating DESC, n_pending DESC, f.rel LIMIT ?", (limit,))

    def pending_summary(self) -> dict:
        row = self.one("SELECT COUNT(*) c, COUNT(DISTINCT file_id) f FROM file_tags WHERE status='pending'")
        return {"pending_tags": int(row["c"]) if row else 0, "pending_files": int(row["f"]) if row else 0}

    def status_counts(self, tag_id: int) -> dict[str, int]:
        rows = self.query("SELECT status, COUNT(*) c FROM file_tags WHERE tag_id=? GROUP BY status", (tag_id,))
        return {r["status"]: int(r["c"]) for r in rows}

    def remove_file_tags(self, file_id: int, names: Iterable[str]) -> None:
        names = list(names)
        if not names:
            return
        ph = ",".join("?" * len(names))
        self.execute(f"DELETE FROM file_tags WHERE file_id=? AND tag_id IN (SELECT id FROM tags WHERE name IN ({ph}))",
                     [file_id, *names])
        self.refresh_manual_flag([file_id])

    def remove_tags_by_source(self, file_ids: Sequence[int], sources: Sequence[str]) -> int:
        if not file_ids or not sources:
            return 0
        ph1 = ",".join("?" * len(file_ids))
        ph2 = ",".join("?" * len(sources))
        # 只清掉上一轮“待审核”的自动结果，绝不覆盖人工审核过的结论
        cur = self.execute(f"DELETE FROM file_tags WHERE file_id IN ({ph1}) AND source IN ({ph2}) "
                           f"AND status='pending'", [*file_ids, *sources])
        return cur.rowcount

    def refresh_manual_flag(self, file_ids: Sequence[int]) -> None:
        if not file_ids:
            return
        ph = ",".join("?" * len(file_ids))
        self.execute(
            f"UPDATE files SET manual=(SELECT COUNT(*) FROM file_tags ft WHERE ft.file_id=files.id AND ft.source='manual')>0 "
            f"WHERE id IN ({ph})", list(file_ids))

    def list_tags(self, search: str = "", only_used: bool = False,
                  category: str | None = None) -> list[sqlite3.Row]:
        """标签表。category 传类型 key 时只返回该类型下的标签（所有界面的分类过滤都走这里）。"""
        # 直接读维护好的 tags.count（refresh_counts 负责更新）：
        # 统计"某标签有多少张已生效的图"，不在这里做 join —— 那要回表读十几万行 CLIP BLOB，慢到 1 秒
        sql = "SELECT t.*, COALESCE(t.count, 0) AS count FROM tags t"
        args: list[Any] = []
        where = []
        if search:
            where.append("(t.name LIKE ? OR IFNULL(t.zh,'') LIKE ?)")   # 中文名也能搜
            args.extend([f"%{search}%", f"%{search}%"])
        if category:
            where.append("COALESCE(NULLIF(t.category,''),'other') = ?")
            args.append(category)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t.category, count DESC, t.name"
        rows = self.query(sql, args)
        return [r for r in rows if (not only_used or r["count"] > 0)]

    def search_tags(self, search: str = "", category: str | None = None) -> list[sqlite3.Row]:
        """标签搜索（中文/英文都能搜）：除了库里的 name/zh，还会拿内置词典的译名匹配。

        库里很多标签没存 zh（中文名来自内置词典），只查 SQL 会漏掉，比如搜「斯卡蒂」
        明明有 skadi_(arknights) 却搜不到。
        """
        q = (search or "").strip()
        rows = self.list_tags(q, category=category)
        if not q:
            return rows
        import re as _re
        if not _re.search(r"[\u4e00-\u9fff]", q):
            return rows
        from . import tag_i18n
        seen = {int(r["id"]) for r in rows}
        extra = []
        low = q.lower()
        for t in self.list_tags(category=category):
            if int(t["id"]) in seen:
                continue
            if low in tag_i18n.display(t["name"], t["zh"] or "").lower():
                extra.append(t)
        return rows + extra

    def save_tag(self, name: str, category: str = "other", prompt: str = "",
                 auto: int = 1, requires: str = "") -> int:
        """建标签（已存在则更新类型/提示词/前置条件）——所有「新建标签」界面共用的唯一入口。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("标签名不能为空")
        tid = self.ensure_tag(name, category, prompt or None, auto)
        self.update_tag(tid, category=category, prompt=prompt or "", auto=auto, requires=requires or "")
        return tid

    def mark_reviewed(self, file_ids) -> int:
        """把若干张图片标记成已审核（各界面审完共用）。"""
        ids = [int(i) for i in (file_ids or [])]
        if not ids:
            return 0
        self.executemany("UPDATE files SET reviewed=1 WHERE id=?", [(i,) for i in ids])
        return len(ids)

    def update_tag(self, tag_id: int, **fields) -> None:
        allowed = ("name", "category", "prompt", "note", "enabled", "auto", "requires", "zh")
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        args.extend([time.time(), tag_id])
        self.execute(f"UPDATE tags SET {','.join(sets)} WHERE id=?", args)

    def rename_tag(self, tag_id: int, new_name: str) -> None:
        new_name = new_name.strip()
        if not new_name:
            return
        exist = self.tag_id(new_name)
        if exist and exist != tag_id:
            self.merge_tags(tag_id, exist)
            return
        self.execute("UPDATE tags SET name=?,updated_at=? WHERE id=?", (new_name, time.time(), tag_id))

    def merge_tags(self, src_id: int, dst_id: int) -> None:
        """把 src 合并进 dst：图片、图谱连线、区域框、自训练探针、人物关联都一起搬过去。"""
        if src_id == dst_id:
            return
        now = time.time()
        c = self.conn()
        rows = c.execute("SELECT file_id,source,score FROM file_tags WHERE tag_id=?", (src_id,)).fetchall()
        for r in rows:
            c.execute("INSERT OR REPLACE INTO file_tags(file_id,tag_id,source,score,updated_at) VALUES(?,?,?,?,?)",
                      (r["file_id"], dst_id, r["source"], r["score"], now))
        c.execute("DELETE FROM file_tags WHERE tag_id=?", (src_id,))
        # 图谱连线（标签作为子节点 / 作为父节点）改指到目标标签，避免合并后连线凭空消失
        c.execute("UPDATE OR IGNORE taxonomy_edges SET child_id=? "
                  "WHERE child_kind='tag' AND child_id=?", (dst_id, src_id))
        c.execute("DELETE FROM taxonomy_edges WHERE child_kind='tag' AND child_id=?", (src_id,))
        c.execute("UPDATE OR IGNORE taxonomy_edges SET parent_id=? "
                  "WHERE parent_kind='tag' AND parent_id=?", (dst_id, src_id))
        c.execute("DELETE FROM taxonomy_edges WHERE parent_kind='tag' AND parent_id=?", (src_id,))
        # 区域框 / 自训练探针 / 人物关联
        c.execute("UPDATE regions SET tag_id=? WHERE tag_id=?", (dst_id, src_id))
        c.execute("UPDATE OR IGNORE tag_probe SET tag_id=? WHERE tag_id=?", (dst_id, src_id))
        c.execute("DELETE FROM tag_probe WHERE tag_id=?", (src_id,))
        c.execute("UPDATE persons SET tag_id=? WHERE tag_id=?", (dst_id, src_id))
        # 源标签的中文名/备注如果目标没有，就继承过来（合并后显示仍是中文）
        src = c.execute("SELECT name, zh, note FROM tags WHERE id=?", (src_id,)).fetchone()
        dst = c.execute("SELECT name, zh, note FROM tags WHERE id=?", (dst_id,)).fetchone()
        if src and dst:
            # 目标标签没有中文名时，继承源标签的（那是你自己填的）。
            # 注意不要在这里写词典的译名 —— 存进库里会"冻结"，以后修词典就不生效了；
            # 库里没有中文名时显示会自动走内置词典。
            zh = ((dst["zh"] or "").strip()
                  or (src["zh"] or "").strip()
                  or (src["name"] if re.search(r"[\u4e00-\u9fff]", str(src["name"])) else "")
                  or (src["note"] or "").strip())
            note = (dst["note"] or "").strip() or (src["note"] or "").strip()
            c.execute("UPDATE tags SET zh=?, note=?, updated_at=? WHERE id=?", (zh, note, now, dst_id))
        c.execute("DELETE FROM tags WHERE id=?", (src_id,))
        c.commit()

    def delete_tag(self, tag_id: int, drop_links: bool = True) -> None:
        if drop_links:
            self.execute("DELETE FROM file_tags WHERE tag_id=?", (tag_id,))
            # 连带删掉图谱里指向它的连线，避免悬空边（悬空边会让图谱页面报错）
            self.execute("DELETE FROM taxonomy_edges WHERE child_kind='tag' AND child_id=?", (tag_id,))
        self.execute("DELETE FROM tags WHERE id=?", (tag_id,))

    def prune_dangling_edges(self) -> int:
        """清理悬空连线：指向已删除的标签/分类节点的边全部删掉。"""
        cur = self.execute(
            "DELETE FROM taxonomy_edges WHERE "
            "(child_kind='tag'  AND child_id NOT IN (SELECT id FROM tags)) OR "
            "(child_kind='node' AND child_id NOT IN (SELECT id FROM nodes)) OR "
            "(parent_kind='node' AND parent_id NOT IN (SELECT id FROM nodes))")
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # ---------------- 标签之间的从属关系（白裙子 → 裙子） ----------------
    def link_tag_sub(self, parent_tag_id: int, child_tag_id: int) -> None:
        if int(parent_tag_id) == int(child_tag_id):
            return
        self.execute(
            "INSERT OR IGNORE INTO taxonomy_edges(parent_kind,parent_id,child_kind,child_id,relation,created_at) "
            "VALUES('tag',?, 'tag',?, 'sub_of', ?)", (int(parent_tag_id), int(child_tag_id), time.time()))

    def link_tag_parallel(self, tag_a: int, tag_b: int, weight: float = 1.0) -> None:
        """平行关联：同一个角色的不同形态（能天使 ↔ 新约能天使、斯卡蒂 ↔ 浊心斯卡蒂）。

        无向关系，存一条边（小 id 当父）；weight 是关联度，搜索时大的优先显示。
        """
        a, b = sorted((int(tag_a), int(tag_b)))
        if a == b:
            return
        self.execute(
            "INSERT OR REPLACE INTO taxonomy_edges(parent_kind,parent_id,child_kind,child_id,relation,"
            "created_at,weight) VALUES('tag',?, 'tag',?, 'parallel', ?, ?)",
            (a, b, time.time(), float(weight)))

    def tag_parallels(self, tag_id: int) -> list[sqlite3.Row]:
        """某个标签的平行关联（两个方向都算），按关联度从高到低。"""
        return self.query(
            "SELECT t.id, t.name, t.zh, e.weight FROM taxonomy_edges e JOIN tags t ON t.id = "
            "CASE WHEN e.parent_id=? THEN e.child_id ELSE e.parent_id END "
            "WHERE e.parent_kind='tag' AND e.child_kind='tag' AND e.relation='parallel' "
            "AND (e.parent_id=? OR e.child_id=?) ORDER BY e.weight DESC", (int(tag_id), int(tag_id), int(tag_id)))

    def unlink_tag_sub(self, parent_tag_id: int, child_tag_id: int) -> None:
        self.execute("DELETE FROM taxonomy_edges WHERE parent_kind='tag' AND parent_id=? AND child_kind='tag' "
                     "AND child_id=?", (int(parent_tag_id), int(child_tag_id)))

    def tag_children(self, parent_tag_id: int) -> list[sqlite3.Row]:
        """**从属**子标签（不含平行关联）。"""
        return self.query(
            "SELECT t.id, t.name FROM taxonomy_edges e JOIN tags t ON t.id=e.child_id "
            "WHERE e.parent_kind='tag' AND e.parent_id=? AND e.child_kind='tag' "
            "AND e.relation='sub_of' ORDER BY t.name",
            (int(parent_tag_id),))

    def tag_parents(self, child_tag_id: int) -> list[sqlite3.Row]:
        """**从属**父标签（不含平行关联）。"""
        return self.query(
            "SELECT t.id, t.name FROM taxonomy_edges e JOIN tags t ON t.id=e.parent_id "
            "WHERE e.parent_kind='tag' AND e.child_id=? AND e.child_kind='tag' "
            "AND e.relation='sub_of' ORDER BY t.name",
            (int(child_tag_id),))

    def tag_child_map(self) -> dict[str, set[str]]:
        """父标签名 -> 其直接子标签名集合（用于"只显示最具体的标签"）。"""
        out: dict[str, set[str]] = {}
        for r in self.query(
                "SELECT p.name AS pname, c.name AS cname FROM taxonomy_edges e "
                "JOIN tags p ON p.id=e.parent_id JOIN tags c ON c.id=e.child_id "
                "WHERE e.parent_kind='tag' AND e.child_kind='tag' AND e.relation='sub_of'"):
            out.setdefault(r["pname"], set()).add(r["cname"])
        return out

    def tag_parent_map(self) -> dict[str, set[str]]:
        """子标签名 -> 其直接父标签名集合（文件名只写子标签时，用它补回父标签）。"""
        out: dict[str, set[str]] = {}
        for r in self.query(
                "SELECT p.name AS pname, c.name AS cname FROM taxonomy_edges e "
                "JOIN tags p ON p.id=e.parent_id JOIN tags c ON c.id=e.child_id "
                "WHERE e.parent_kind='tag' AND e.child_kind='tag' AND e.relation='sub_of'"):
            out.setdefault(r["cname"], set()).add(r["pname"])
        return out

    def expand_tag_names(self, names: Sequence[str]) -> list[str]:
        """把父标签展开成"自己 + 所有子孙标签"（筛裙子能带出白裙子/黑裙子），带访问集防环。"""
        out: dict[str, None] = {}
        for n in names:
            row = self.one("SELECT id FROM tags WHERE name=?", (n,))
            if not row:
                out[n] = None
                continue
            stack, seen = [int(row["id"])], set()
            while stack:
                tid = stack.pop()
                if tid in seen:
                    continue
                seen.add(tid)
                t = self.one("SELECT name FROM tags WHERE id=?", (tid,))
                if t:
                    out.setdefault(t["name"], None)
                for ch in self.tag_children(tid):
                    stack.append(int(ch["id"]))
        return list(out.keys())

    def refresh_counts(self) -> None:
        """重算每个标签的图片数（只写变化了的行）。

        拆成"全部已生效"减去"文件已丢失"两步：都不需要回表读 files 的 CLIP BLOB，
        1800+ 标签的库从 ~400ms 降到几十毫秒。
        """
        cur_counts = {int(r["id"]): int(r["count"] or 0)
                      for r in self.query("SELECT id, count FROM tags")}
        # 注意：这里数的是"所有状态的标签"（含待审），和界面上「(N)」的口径一致；
        # 只数 confirmed 会让"只挂待审标签"的标签在筛选树里凭空消失
        allc = {int(r["tid"]): int(r["n"]) for r in self.query(
            "SELECT tag_id AS tid, COUNT(*) AS n FROM file_tags GROUP BY tag_id")}
        miss = {int(r["tid"]): int(r["n"]) for r in self.query(
            "SELECT ft.tag_id AS tid, COUNT(*) AS n FROM file_tags ft "
            "WHERE ft.file_id IN (SELECT id FROM files WHERE missing=1) GROUP BY ft.tag_id")}
        fresh = {tid: max(0, n - miss.get(tid, 0)) for tid, n in allc.items()}
        updates = [(fresh.get(tid, 0), tid) for tid, old in cur_counts.items() if old != fresh.get(tid, 0)]
        if updates:
            self.executemany("UPDATE tags SET count=? WHERE id=?", updates)

    # ---------------- 检索 ----------------
    def search_files(self, required: Sequence[str] = (), any_of: Sequence[str] = (),
                     excluded: Sequence[str] = (), text: str = "", kind: str = "",
                     only_unlabeled: bool = False, root_ids: Sequence[int] = (),
                     rel_prefix: str = "", limit: int = 20000, order: str = "name",
                     include_pending: bool = False, only_pending: bool = False,
                     rating: str = "", category: str = "") -> list[sqlite3.Row]:
        base = ("SELECT f.*, s.dir AS series_dir, s.name AS series_name, s.page_count AS series_pages "
                "FROM files f LEFT JOIN series s ON s.id=f.series_id WHERE f.missing=0")
        conds: list[str] = []
        args: list[Any] = []
        status_clause = "" if include_pending else " AND ft.status='confirmed'"
        ft_status = "ft.status IN ('confirmed','pending')" if include_pending else "ft.status='confirmed'"
        for names, mode in ((required, "AND"), (any_of, "OR")):
            names = [n for n in names if n]
            if not names:
                continue
            ph = ",".join("?" * len(names))
            sub = (f"SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id=ft.tag_id "
                   f"WHERE t.name IN ({ph}) AND {ft_status} GROUP BY ft.file_id "
                   f"HAVING COUNT(DISTINCT t.id)={len(names)}")
            conds.append(f"f.id IN ({sub})")
            args.extend(names)
        if excluded:
            ph = ",".join("?" * len(excluded))
            conds.append(f"f.id NOT IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id=ft.tag_id "
                         f"WHERE t.name IN ({ph}){status_clause})")
            args.extend(excluded)
        if text:
            conds.append("(f.name LIKE ? OR f.rel LIKE ?)")
            args.extend([f"%{text}%", f"%{text}%"])
        if kind:
            conds.append("f.kind=?")
            args.append(kind)
        if only_unlabeled:
            conds.append("NOT EXISTS (SELECT 1 FROM file_tags ft WHERE ft.file_id=f.id AND ft.status='confirmed')")
        if only_pending:
            conds.append("EXISTS (SELECT 1 FROM file_tags ft WHERE ft.file_id=f.id AND ft.status='pending')")
        if root_ids:
            ph = ",".join("?" * len(root_ids))
            conds.append(f"f.root_id IN ({ph})")
            args.extend(root_ids)
        if rel_prefix:
            conds.append("f.rel LIKE ?")
            args.append(rel_prefix.replace("\\", "/").rstrip("/") + "/%")
        if rating:
            conds.append("f.rating=?")
            args.append(rating)
        if category:      # 按"类型/大类"筛选：命中该类型下任一标签即算（标签继承）
            conds.append("f.id IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id=ft.tag_id "
                         "WHERE t.category=? AND " + ft_status + ")")
            args.append(category)
        order_sql = "f.rel" if order == "name" else "f.id DESC"
        sql = base
        if conds:
            sql += " AND " + " AND ".join(conds)
        sql += f" ORDER BY {order_sql} LIMIT {int(limit)}"
        return self.query(sql, args)

    def stats(self) -> dict[str, int]:
        out = {}
        out["files"] = int(self.one("SELECT COUNT(*) c FROM files WHERE missing=0")["c"])
        out["series"] = int(self.one("SELECT COUNT(*) c FROM series")["c"])
        out["tags"] = int(self.one("SELECT COUNT(*) c FROM tags")["c"])
        out["labeled"] = int(self.one("SELECT COUNT(*) c FROM files WHERE missing=0 AND manual=1")["c"])
        row = self.one("SELECT COUNT(*) c, COUNT(DISTINCT file_id) f FROM file_tags WHERE status='pending'")
        out["pending"] = int(row["c"])
        out["pending_files"] = int(row["f"])
        out["faces"] = int(self.one("SELECT COUNT(*) c FROM faces")["c"])
        out["persons"] = int(self.one("SELECT COUNT(*) c FROM persons")["c"])
        return out

    # ---------------- 任务持久化（断电续跑） ----------------
    def create_job(self, kind: str, ids: Sequence[int], params: dict | None = None, note: str = "") -> int:
        now = time.time()
        cur = self.execute(
            "INSERT INTO jobs(kind,params,ids,total,done,status,note,created_at,updated_at) "
            "VALUES(?,?,?,?,0,'running',?,?,?)",
            (kind, json.dumps(params or {}, ensure_ascii=False), json.dumps(list(ids)), len(ids), note, now, now))
        return int(cur.lastrowid)

    def job_tick(self, job_id: int, done: int) -> None:
        self.execute("UPDATE jobs SET done=?, updated_at=? WHERE id=?", (int(done), time.time(), job_id))

    def finish_job(self, job_id: int, status: str = "done", note: str | None = None) -> None:
        if note is None:
            self.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (status, time.time(), job_id))
        else:
            self.execute("UPDATE jobs SET status=?, note=?, updated_at=? WHERE id=?",
                         (status, note, time.time(), job_id))

    def job(self, job_id: int):
        return self.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def unfinished_jobs(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM jobs WHERE status='running' ORDER BY updated_at DESC")

    def job_ids(self, job_id: int) -> list[int]:
        row = self.job(job_id)
        if not row:
            return []
        try:
            return [int(x) for x in json.loads(row["ids"])]
        except Exception:
            return []

    # ---------------- 感知哈希（查重） ----------------
    def set_hash(self, file_id: int, phash: str, ahash: str, width: int | None, height: int | None,
                 phash2: str = "") -> None:
        self.execute("UPDATE files SET phash=?, ahash=?, width=COALESCE(?,width), height=COALESCE(?,height), "
                     "phash2=COALESCE(NULLIF(?,''),phash2), dup_checked=1 WHERE id=?",
                     (phash, ahash, width, height, phash2, file_id))

    def files_without_hash(self, ids: Sequence[int] | None = None) -> list[sqlite3.Row]:
        sql = "SELECT id,path,mtime FROM files WHERE missing=0 AND (phash IS NULL OR dup_checked=0)"
        args: list[Any] = []
        if ids:
            sql += " AND id IN (%s)" % ",".join("?" * len(ids))
            args.extend(ids)
        return self.query(sql, args)

    def hashed_files(self, only_roots: Sequence[int] = ()) -> list[sqlite3.Row]:
        sql = ("SELECT id,path,name,phash,ahash,phash2,width,height,mtime,size,root_id FROM files "
               "WHERE missing=0 AND phash IS NOT NULL")
        args: list[Any] = []
        if only_roots:
            sql += " AND root_id IN (%s)" % ",".join("?" * len(only_roots))
            args.extend(only_roots)
        return self.query(sql, args)

    # ---------------- 重复图反馈 ----------------
    def add_dup_feedback(self, pair_key: str, action: str, note: str = "") -> None:
        self.execute("INSERT OR REPLACE INTO dup_feedback(pair_key,action,note,created_at) VALUES(?,?,?,?)",
                     (pair_key, action, note, time.time()))

    def banned_dup_groups(self) -> set[str]:
        return {r["pair_key"] for r in self.query(
            "SELECT pair_key FROM dup_feedback WHERE action IN ('not_dup_group','merged_series')")}

    def add_dup_group_feedback(self, ids: Sequence[int], note: str = "") -> None:
        key = "g:" + ",".join(str(x) for x in sorted(int(i) for i in ids))
        # 明细里也把每一对记成“不是重复”，这样即使以后少了一张也不会再提示
        ids = sorted(int(i) for i in ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                self.add_dup_feedback(f"f{ids[i]}|f{ids[j]}", "not_dup", note)
        self.add_dup_feedback(key, "not_dup_group", note)

    def add_series_group_feedback(self, ids: Sequence[int], note: str = "") -> None:
        """整组被判定为系列：以后不再当作重复提示。"""
        ids = sorted(int(i) for i in ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                self.add_dup_feedback(f"f{ids[i]}|f{ids[j]}", "not_dup", note)
        self.add_dup_feedback("g:" + ",".join(str(x) for x in ids), "merged_series", note)

    def dup_feedback_map(self) -> dict[str, str]:
        return {r["pair_key"]: r["action"] for r in self.query("SELECT pair_key,action FROM dup_feedback")}

    # ---------------- 系列 ----------------
    def upsert_series(self, root_id: int, dir_rel: str, name: str, tags: Sequence[str]) -> int:
        now = time.time()
        row = self.one("SELECT id FROM series WHERE root_id=? AND dir=?", (root_id, dir_rel))
        tag_str = " ".join(tags)
        if row:
            sid = int(row["id"])
            self.execute("UPDATE series SET name=?,tags=?,updated_at=? WHERE id=?", (name, tag_str, now, sid))
            return sid
        cur = self.execute("INSERT INTO series(root_id,dir,name,tags,page_count,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
                           (root_id, dir_rel, name, tag_str, now, now))
        return int(cur.lastrowid)

    def update_series_stats(self, series_id: int) -> None:
        row = self.one("SELECT COUNT(*) c, MIN(id) f FROM files WHERE series_id=?", (series_id,))
        if not row:
            return
        first = self.one("SELECT id FROM files WHERE series_id=? ORDER BY page_no, id LIMIT 1", (series_id,))
        self.execute("UPDATE series SET page_count=?, first_file_id=? WHERE id=?",
                     (int(row["c"]), int(first["id"]) if first else None, series_id))

    def series_list(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM series ORDER BY updated_at DESC")

    def series_files(self, series_id: int) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM files WHERE series_id=? ORDER BY page_no, id", (series_id,))

    def set_auto_json(self, file_id: int, data: dict) -> None:
        self.execute("UPDATE files SET auto_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), file_id))

    def get_auto_json(self, file_id: int) -> dict:
        row = self.one("SELECT auto_json FROM files WHERE id=?", (file_id,))
        if row and row["auto_json"]:
            try:
                return json.loads(row["auto_json"])
            except Exception:
                return {}
        return {}

    # ---------------- 人脸 ----------------
    def add_face(self, file_id: int, bbox: Sequence[float], det_score: float, emb: bytes, person_id: int | None = None) -> int:
        cur = self.execute("INSERT INTO faces(file_id,person_id,bbox,det_score,emb,created_at) VALUES(?,?,?,?,?,?)",
                           (file_id, person_id, json.dumps([round(float(x), 1) for x in bbox]), float(det_score), emb, time.time()))
        return int(cur.lastrowid)

    def clear_faces(self, file_id: int | None = None) -> None:
        if file_id is None:
            self.execute("DELETE FROM faces")
            self.execute("UPDATE files SET face_done=0")
        else:
            self.execute("DELETE FROM faces WHERE file_id=?", (file_id,))
            self.execute("UPDATE files SET face_done=0 WHERE id=?", (file_id,))

    def all_faces(self, limit: int = 500000) -> list[sqlite3.Row]:
        return self.query("SELECT id,file_id,person_id,bbox,emb FROM faces LIMIT ?", (limit,))

    def persons(self) -> list[sqlite3.Row]:
        return self.query("SELECT p.*, (SELECT COUNT(*) FROM faces fa WHERE fa.person_id=p.id) AS n FROM persons p ORDER BY n DESC")

    def ensure_person(self, name: str) -> int:
        row = self.one("SELECT id FROM persons WHERE name=?", (name,))
        if row:
            return int(row["id"])
        cur = self.execute("INSERT INTO persons(name,updated_at) VALUES(?,?)", (name, time.time()))
        return int(cur.lastrowid)

    def set_face_person(self, face_ids: Sequence[int], person_id: int | None) -> None:
        if not face_ids:
            return
        ph = ",".join("?" * len(face_ids))
        self.execute(f"UPDATE faces SET person_id=? WHERE id IN ({ph})", [person_id, *face_ids])

    # ---------------- 手动框选区域 ----------------
    def add_region(self, file_id: int, tag_name: str, box: Sequence[float],
                   note: str = "", category: str = "other") -> int:
        """box 为归一化的 (x, y, w, h)，取值 0~1。"""
        x, y, w, h = (float(v) for v in box[:4])
        tid = self.ensure_tag(tag_name, category) if tag_name else None
        cur = self.execute(
            "INSERT INTO regions(file_id,tag_id,tag_name,x,y,w,h,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (file_id, tid, tag_name, x, y, w, h, note, time.time()))
        return int(cur.lastrowid)

    def regions_for_file(self, file_id: int) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM regions WHERE file_id=? ORDER BY id", (file_id,))

    def delete_region(self, region_id: int) -> None:
        self.execute("DELETE FROM regions WHERE id=?", (region_id,))

    def update_region(self, region_id: int, **fields) -> None:
        allowed = ("tag_name", "tag_id", "x", "y", "w", "h", "note")
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return
        args.append(region_id)
        self.execute(f"UPDATE regions SET {','.join(sets)} WHERE id=?", args)

    def region_counts(self, file_ids: Sequence[int] | None = None) -> dict[int, int]:
        if file_ids is not None:
            ids = list(file_ids)
            if not ids:
                return {}
            ph = ",".join("?" * len(ids))
            rows = self.query(f"SELECT file_id, COUNT(*) c FROM regions WHERE file_id IN ({ph}) GROUP BY file_id", ids)
        else:
            rows = self.query("SELECT file_id, COUNT(*) c FROM regions GROUP BY file_id")
        return {int(r["file_id"]): int(r["c"]) for r in rows}

    def export_regions(self) -> dict:
        out: dict[str, list[dict]] = {}
        for r in self.query("SELECT rg.*, f.path, f.width, f.height FROM regions rg JOIN files f ON f.id=rg.file_id"):
            out.setdefault(r["path"], []).append({
                "tag": r["tag_name"], "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
                "note": r["note"] or "",
            })
        return out

    def regions_without_vec(self, file_ids: Sequence[int] | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT rg.*, f.path FROM regions rg JOIN files f ON f.id=rg.file_id "
               "WHERE (rg.clip_vec IS NULL OR rg.clip_model<>?)")
        args: list = [""]
        if file_ids:
            sql += " AND rg.file_id IN (%s)" % ",".join("?" * len(file_ids))
            args.extend(file_ids)
        return self.query(sql, args)

    def set_region_vec(self, region_id: int, vec: bytes, model: str) -> None:
        self.execute("UPDATE regions SET clip_vec=?,clip_model=? WHERE id=?", (vec, model, region_id))

    def set_region_vec_ctx(self, region_id: int, vec: bytes) -> None:
        self.execute("UPDATE regions SET clip_vec_ctx=? WHERE id=?", (vec, region_id))

    def region_vectors(self, model: str = "") -> list[sqlite3.Row]:
        if model:
            return self.query("SELECT * FROM regions WHERE clip_vec IS NOT NULL AND clip_model=?", (model,))
        return self.query("SELECT * FROM regions WHERE clip_vec IS NOT NULL")

    # ================== 标签体系（图：分类节点 + 标签节点，多对多） ==================
    def ensure_node(self, name: str, kind: str = "group", note: str = "", color: str = "") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("分类名不能为空")
        row = self.one("SELECT id FROM nodes WHERE name=?", (name,))
        if row:
            return int(row["id"])
        cur = self.execute(
            "INSERT INTO nodes(name,kind,note,color,sort,created_at) VALUES(?,?,?,?,0,?)",
            (name, kind, note, color, time.time()))
        return int(cur.lastrowid)

    def list_nodes(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM nodes ORDER BY sort, name")

    def node(self, node_id: int):
        return self.one("SELECT * FROM nodes WHERE id=?", (node_id,))

    def parents_of_node(self, node_id: int) -> list[sqlite3.Row]:
        """某个分类节点的直接父节点（用于搜索定位时一层层展开）。"""
        return self.query(
            "SELECT n.* FROM taxonomy_edges e JOIN nodes n ON n.id=e.parent_id "
            "WHERE e.child_kind='node' AND e.child_id=? ORDER BY n.name", (int(node_id),))

    def rename_node(self, node_id: int, name: str) -> None:
        name = (name or "").strip()
        if name:
            self.execute("UPDATE nodes SET name=? WHERE id=?", (name, node_id))

    def update_node(self, node_id: int, **fields) -> None:
        allowed = ("name", "kind", "note", "color", "x", "y", "sort", "collapsed")
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if sets:
            args.append(node_id)
            self.execute(f"UPDATE nodes SET {','.join(sets)} WHERE id=?", args)

    def delete_node(self, node_id: int, reparent: bool = False) -> None:
        """删除分类节点：默认把它的子节点接到它的父节点上（避免丢结构）。"""
        parents = self.query("SELECT * FROM taxonomy_edges WHERE child_kind='node' AND child_id=?", (node_id,))
        children = self.query("SELECT * FROM taxonomy_edges WHERE parent_kind='node' AND parent_id=?", (node_id,))
        if reparent:
            for p in parents:
                for c in children:
                    self.link(int(p["parent_id"]), c["child_kind"], int(c["child_id"]), c["relation"])
        self.execute("DELETE FROM taxonomy_edges WHERE (parent_kind='node' AND parent_id=?) OR (child_kind='node' AND child_id=?)",
                     (node_id, node_id))
        self.execute("DELETE FROM nodes WHERE id=?", (node_id,))

    def link(self, parent_id: int, child_kind: str, child_id: int, relation: str = "is_a") -> int:
        cur = self.execute(
            "INSERT OR IGNORE INTO taxonomy_edges(parent_kind,parent_id,child_kind,child_id,relation,created_at) "
            "VALUES('node',?,?,?,?,?)", (parent_id, child_kind, child_id, relation, time.time()))
        row = self.one("SELECT id FROM taxonomy_edges WHERE parent_kind='node' AND parent_id=? AND child_kind=? "
                       "AND child_id=? AND relation=?", (parent_id, child_kind, child_id, relation))
        return int(row["id"]) if row else int(cur.lastrowid)

    def unlink(self, parent_id: int, child_kind: str, child_id: int) -> None:
        self.execute("DELETE FROM taxonomy_edges WHERE parent_kind='node' AND parent_id=? AND child_kind=? AND child_id=?",
                     (parent_id, child_kind, child_id))

    def unlink_edge(self, edge_id: int) -> None:
        self.execute("DELETE FROM taxonomy_edges WHERE id=?", (edge_id,))

    def edges(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM taxonomy_edges")

    def parents_of_tag(self, tag_id: int) -> list[sqlite3.Row]:
        return self.query(
            "SELECT n.* FROM taxonomy_edges e JOIN nodes n ON n.id=e.parent_id "
            "WHERE e.child_kind='tag' AND e.child_id=? ORDER BY n.name", (tag_id,))

    def children_of_node(self, node_id: int) -> list[sqlite3.Row]:
        """返回该分类节点下的子节点（分类 + 标签），标签带图片数。"""
        rows = self.query(
            "SELECT e.child_kind AS kind, e.child_id AS cid, e.id AS edge_id, e.relation, "
            "  CASE WHEN e.child_kind='node' THEN n.name ELSE t.name END AS name, "
            "  CASE WHEN e.child_kind='tag' THEN t.category ELSE 'group' END AS category, "
            "  CASE WHEN e.child_kind='tag' THEN t.count ELSE 0 END AS count "
            "FROM taxonomy_edges e "
            "LEFT JOIN nodes n ON e.child_kind='node' AND n.id=e.child_id "
            "LEFT JOIN tags t ON e.child_kind='tag' AND t.id=e.child_id "
            "WHERE e.parent_kind='node' AND e.parent_id=? ORDER BY e.child_kind DESC, name", (node_id,))
        return rows

    def node_roots(self) -> list[sqlite3.Row]:
        """没有任何父节点的分类节点（顶层大类）。"""
        return self.query(
            "SELECT * FROM nodes WHERE id NOT IN "
            "(SELECT child_id FROM taxonomy_edges WHERE child_kind='node') ORDER BY sort, name")

    def sync_taxonomy_from_categories(self, category_labels: dict[str, str]) -> int:
        """把 tags.category 同步成分类节点（首次使用时快速建出一套层级）。"""
        made = 0
        for key, label in category_labels.items():
            nid = self.ensure_node(label)
            made += 1
            rows = self.query("SELECT id FROM tags WHERE category=?", (key,))
            for r in rows:
                self.link(nid, "tag", int(r["id"]))
        top = self.ensure_node("全部标签", note="根节点")
        for n in self.query("SELECT id FROM nodes WHERE id<>?", (top,)):
            if int(n["id"]) != top:
                self.link(top, "node", int(n["id"]))
        return made

    def tag_group_paths(self) -> dict[int, list[str]]:
        """tag_id -> 所属分类路径列表（多对多，可属于多个分类）。"""
        nodes = {int(n["id"]): n for n in self.list_nodes()}
        node_edges = self.query("SELECT parent_id, child_id FROM taxonomy_edges WHERE child_kind='node'")
        parents: dict[int, list[int]] = {}
        for e in node_edges:
            parents.setdefault(int(e["child_id"]), []).append(int(e["parent_id"]))

        def path_of(nid: int, depth: int = 0) -> list[str]:
            name = nodes[nid]["name"] if nid in nodes else ""
            ps = parents.get(nid) or []
            if not ps or depth > 6:
                return [name]
            return [f"{path_of(p, depth + 1)[0]} / {name}" for p in ps]

        out: dict[int, list[str]] = {}
        for e in self.query("SELECT parent_id, child_id FROM taxonomy_edges WHERE child_kind='tag'"):
            tid = int(e["child_id"])
            out.setdefault(tid, []).extend(path_of(int(e["parent_id"])))
        return out

    def tag_groups(self) -> dict[int, list[str]]:
        """tag_id -> 直接所属分类名（用于左侧筛选树）。"""
        out: dict[int, list[str]] = {}
        for r in self.query(
                "SELECT e.child_id, n.name FROM taxonomy_edges e JOIN nodes n ON n.id=e.parent_id "
                "WHERE e.child_kind='tag'"):
            out.setdefault(int(r["child_id"]), []).append(r["name"])
        return out

    def tag_name_groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in self.query(
                "SELECT t.name AS tag_name, n.name AS node_name FROM taxonomy_edges e "
                "JOIN nodes n ON n.id=e.parent_id JOIN tags t ON t.id=e.child_id "
                "WHERE e.child_kind='tag'"):
            out.setdefault(r["tag_name"], []).append(r["node_name"])
        return out

    def category_key_by_label(self, text: str) -> str | None:
        """把分类节点的名字（或 key）对应回标签类型的 key，供「连线即改分类」使用。"""
        t = (text or "").strip()
        if not t:
            return None
        for c in self.categories():
            if t == c["key"] or t == c["label"]:
                return str(c["key"])
        for c in self.categories():          # 宽松匹配：名字里包含类型名也算
            if c["label"] and c["label"] in t:
                return str(c["key"])
        return None

    # ================== 自训练探针 ==================
    def set_probe(self, tag_id: int, kind: str, data: bytes, n_pos: int, n_neg: int) -> None:
        self.execute("INSERT OR REPLACE INTO tag_probe(tag_id,kind,data,n_pos,n_neg,updated_at) VALUES(?,?,?,?,?,?)",
                     (tag_id, kind, data, n_pos, n_neg, time.time()))

    def get_probe(self, tag_id: int, kind: str):
        return self.one("SELECT * FROM tag_probe WHERE tag_id=? AND kind=?", (tag_id, kind))

    def probes(self, kind: str = "centroid") -> list[sqlite3.Row]:
        return self.query("SELECT * FROM tag_probe WHERE kind=?", (kind,))

    def set_pos(self, kind: str, ref_id: int, x: float, y: float) -> None:
        self.execute("INSERT OR REPLACE INTO layout(kind,ref_id,x,y) VALUES(?,?,?,?)", (kind, ref_id, x, y))

    def positions(self) -> dict[tuple[str, int], tuple[float, float]]:
        out = {}
        for r in self.query("SELECT kind,ref_id,x,y FROM layout"):
            if r["x"] is not None and r["y"] is not None:
                out[(r["kind"], int(r["ref_id"]))] = (float(r["x"]), float(r["y"]))
        return out
