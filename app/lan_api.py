"""PC 端局域网 API（给平板 / 手机端用）。

只用标准库（http.server + threading），不引第三方依赖。所有接口除 /api/ping 外都要配对码，
配对码就是「已连接设备」窗口里显示的那 4 位数（也可用请求头 X-Imtag-Code 传）。

接口一览：
  GET  /api/ping                        → 本机信息（不需要配对码，用于发现后握手）
  GET  /api/roots                       → 库/来源根列表
  GET  /api/list?root=&dir=&offset=&limit=  → 目录里的图片（带标签、分级、系列）
  GET  /api/thumb?id=&size=340          → 缩略图 JPEG（缺失就现生成，带缓存）
  GET  /api/image?id=                   → 原图（支持 HTTP Range，方便大图/断点）
  GET  /api/tags                        → 标签词典（name / zh / category）
  GET  /api/graph                       → 图谱数据（节点/边/热度/折叠状态，移动端照它画）
  GET  /api/events                      → SSE 事件流（tag/库变更实时推送）
  POST /api/file_tags                   → 远程改标签 {file_id, add:[...], remove:[...]}

写入接口只做"加/删标签"这一件事（审核遥控的基础），落库仍然走同一个 Store，
避免移动端绕过 PC 的规则去改文件名。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .imaging import make_thumb, thumb_path

API_PORT = 47824
THUMB_RATING_ORDER = ("全年龄", "R15", "R18", "R18G")


class LanApi:
    """局域网 HTTP 服务：随设备发现一起启动。"""

    def __init__(self, store, settings, code: str, name: str, version: str, port: int = API_PORT,
                 is_trusted=None, require_pair: bool = True):
        self.store = store
        self.settings = settings
        self.code = code
        self.name = name
        self.version = version
        self.port = port
        self.is_trusted = is_trusted or (lambda device_id: True)
        self.require_pair = require_pair
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._clients: list = []          # SSE 客户端队列
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        if self._httpd is not None:
            return True
        api = self

        class Handler(_ApiHandler):
            server_api = api

        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except Exception:
            self._httpd = None
            return False
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True,
                                        name="imtag-lan-api")
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    # ---------- 事件推送 ----------
    def subscribe(self):
        q: list = []
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def bump(self, kind: str, **data) -> None:
        """数据有变化时喊一声，所有连着的移动端立刻知道。"""
        msg = {"kind": kind, "at": time.time(), **data}
        with self._lock:
            for q in list(self._clients):
                q.append(msg)


class _ApiHandler(BaseHTTPRequestHandler):
    server_api: "LanApi" = None       # 由 LanApi.start 注入
    server_version = "ImageTagStudio-LAN/1.0"

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):          # 别把访问日志刷到控制台
        pass

    @property
    def api(self) -> "LanApi":
        return type(self).server_api

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _authed(self, q: dict) -> bool:
        code = self.headers.get("X-Imtag-Code") or q.get("code") or ""
        if str(code) != str(self.api.code):
            return False
        if not self.api.require_pair:
            return True
        # 对码只是一半：还要求这台设备**在本机点过"同意"**（双向确认），
        # 设备号由移动端生成并在请求头 X-Imtag-Device 里带上。
        dev = self.headers.get("X-Imtag-Device") or q.get("device") or ""
        return bool(dev) and bool(self.api.is_trusted(str(dev)))

    def _deny(self, why: str = "pair_code_required") -> None:
        self._json({"ok": False, "error": why,
                    "hint": "先在 PC 端「已连接设备」里同意这台设备的连接请求"}, 401)

    def _json(self, obj, status: int = 200) -> None:      # noqa: F811
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 路由 ----------
    def do_GET(self) -> None:                   # noqa: N802
        q = self._query()
        path = urlparse(self.path).path
        if path == "/api/ping":
            self._json({"ok": True, "app": "imagetag", "role": "pc", "name": self.api.name,
                        "version": self.api.version, "api_port": self.api.port,
                        "need_code": True})
            return
        if not self._authed(q):
            self._deny()
            return
        if path == "/api/roots":
            self._roots()
        elif path == "/api/list":
            self._list(q)
        elif path == "/api/thumb":
            self._thumb(q)
        elif path == "/api/image":
            self._image(q)
        elif path == "/api/tags":
            self._tags()
        elif path == "/api/graph":
            self._graph()
        elif path == "/api/events":
            self._events()
        else:
            self._json({"ok": False, "error": "not_found"}, 404)

    def do_POST(self) -> None:                  # noqa: N802
        q = self._query()
        if not self._authed(q):
            self._deny()
            return
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            self._json({"ok": False, "error": "bad_json"}, 400)
            return
        if path == "/api/file_tags":
            self._file_tags(body)
        else:
            self._json({"ok": False, "error": "not_found"}, 404)

    # ---------- 各接口 ----------
    def _roots(self) -> None:
        rows = self.api.store.query(
            "SELECT r.id, r.path, r.label, r.is_library, r.drive, "
            "(SELECT COUNT(*) FROM files f WHERE f.root_id=r.id) AS files "
            "FROM roots r ORDER BY r.is_library DESC, r.id")
        self._json({"ok": True, "roots": [dict(r) for r in rows]})

    def _list(self, q: dict) -> None:
        root = q.get("root") or ""
        rel = (q.get("dir") or "").strip("\\/")
        offset = max(0, int(q.get("offset") or 0))
        limit = min(500, max(1, int(q.get("limit") or 120)))
        where = ["f.missing=0"]
        args: list = []
        if root.isdigit():
            where.append("f.root_id=?")
            args.append(int(root))
        elif root:
            where.append("f.root_id=(SELECT id FROM roots WHERE path=?)")
            args.append(str(Path(root)))
        if rel:
            where.append("(f.rel LIKE ? OR f.rel LIKE ?)")
            args += [rel + "\\%", rel + "/%"]
        sql = ("SELECT f.id, f.path, f.rel, f.name, f.mtime, f.root_id, f.series_id, f.rating "
               "FROM files f WHERE " + " AND ".join(where) + " ORDER BY f.rel LIMIT ? OFFSET ?")
        rows = self.api.store.query(sql, (*args, limit, offset))
        out = []
        for r in rows:
            d = {k: r[k] for k in ("id", "path", "rel", "name", "mtime", "root_id", "series_id")}
            d["name"] = r["name"] or Path(r["path"]).name
            d["tags"] = [{"name": t["name"], "source": t["source"],
                          "score": float(t["score"] or 0)} for t in self.api.store.tags_for_file(r["id"])]
            d["rating"] = (r["rating"] or next(
                (t["name"] for t in d["tags"] if t["name"] in THUMB_RATING_ORDER), ""))
            d["thumb_sizes"] = self._present_thumbs(r["id"], r["mtime"])
            out.append(d)
        self._json({"ok": True, "count": len(out), "offset": offset, "limit": limit, "files": out})

    def _present_thumbs(self, file_id: int, mtime: float) -> list[int]:
        """这个文件已经生成过哪些边长的缩略图（移动端据此挑，别猜）。"""
        have = []
        for size in (340, 320, 200, 160):
            try:
                p = thumb_path(file_id, mtime or 0, size)
                if p.exists() and p.stat().st_size > 0:
                    have.append(size)
            except Exception:
                pass
        return have

    def _thumb(self, q: dict) -> None:
        try:
            fid = int(q.get("id") or 0)
            size = int(q.get("size") or 340)
        except ValueError:
            self._json({"ok": False, "error": "bad_id"}, 400)
            return
        row = self.api.store.one("SELECT path, mtime FROM files WHERE id=?", (fid,))
        if row is None:
            self._json({"ok": False, "error": "no_such_file"}, 404)
            return
        p = thumb_path(fid, row["mtime"] or 0, size)
        if not p.exists() or p.stat().st_size == 0:
            p = make_thumb(row["path"], fid, row["mtime"] or 0, size=size) or p
        if not p.exists():
            self._json({"ok": False, "error": "thumb_failed"}, 500)
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _image(self, q: dict) -> None:
        """原图，支持 Range（移动端放大看图用；只读，不改文件）。"""
        try:
            fid = int(q.get("id") or 0)
        except ValueError:
            self._json({"ok": False, "error": "bad_id"}, 400)
            return
        row = self.api.store.one("SELECT path FROM files WHERE id=?", (fid,))
        if row is None:
            self._json({"ok": False, "error": "no_such_file"}, 404)
            return
        p = Path(row["path"])
        if not p.exists():
            self._json({"ok": False, "error": "file_missing"}, 404)
            return
        total = p.stat().st_size
        ctype = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
                 ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}.get(
            p.suffix.lower(), "image/jpeg")
        rng = self.headers.get("Range") or ""
        start, end = 0, total - 1
        status = 200
        if rng.startswith("bytes="):
            try:
                a, b = rng[6:].split("-", 1)
                start = int(a) if a else 0
                end = int(b) if b else total - 1
                start = max(0, min(start, total - 1))
                end = max(start, min(end, total - 1))
                status = 206
            except Exception:
                start, end = 0, total - 1
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        with open(p, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(262144, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _tags(self) -> None:
        rows = self.api.store.query("SELECT name, zh, category FROM tags ORDER BY name")
        self._json({"ok": True, "count": len(rows),
                    "tags": [{"name": r["name"], "zh": r["zh"], "category": r["category"]}
                             for r in rows]})

    def _graph(self) -> None:
        """图谱数据：分类节点 + 标签节点（带图片数/坐标/折叠）+ 父子边。

        移动端拿到就能画：分类=大圆、标签=小圆；颜色按 category 色系 + 层级深浅；
        热度视图直接用 count。坐标 x/y 为 None 表示还没自动布局过（移动端自己跑一次径向布局）。

        数据结构（PC 库里的事实源）：
          · 分类节点在 `nodes` 表（kind='group'），19~20 条；
          · 标签节点在 `tags` 表，边在 `taxonomy_edges`
            （`parent_kind/parent_id` → `child_kind/child_id`，child_kind='tag' 时 id 是 tags.id）；
          · 节点 id 统一加前缀：分类 `g<id>`、标签 `t<id>`，移动端照着拼就行。
        """
        nodes: list[dict] = []
        counts: dict[str, int] = {}
        try:
            for r in self.api.store.query(
                    "SELECT tag_id AS tid, COUNT(*) c FROM file_tags "
                    "WHERE status='confirmed' GROUP BY tag_id"):
                counts[str(r["tid"])] = int(r["c"])
        except Exception:
            pass
        for r in self.api.store.query(
                "SELECT id, name, note, color, x, y, collapsed FROM nodes WHERE kind='group' ORDER BY id"):
            nodes.append({"id": f"g{r['id']}", "kind": "group", "raw_id": int(r["id"]),
                          "name": r["name"], "zh": "", "note": r["note"] or "",
                          "category": r["name"], "count": 0,
                          "x": r["x"], "y": r["y"], "collapsed": int(r["collapsed"] or 0)})
        for r in self.api.store.query(
                "SELECT id, name, zh, category FROM tags ORDER BY id"):
            cnt = counts.get(str(r["id"]), 0)
            nodes.append({"id": f"t{r['id']}", "kind": "tag", "raw_id": int(r["id"]),
                          "name": r["name"], "zh": r["zh"] or "", "note": "",
                          "category": r["category"] or "other", "count": cnt,
                          "x": None, "y": None, "collapsed": 0})
        edges = []
        try:
            for r in self.api.store.query(
                    "SELECT parent_kind, parent_id, child_kind, child_id, relation FROM taxonomy_edges"):
                pre = "g" if str(r["parent_kind"]) in ("node", "group") else "t"
                cre = "g" if str(r["child_kind"]) in ("node", "group") else "t"
                edges.append({"from": f"{pre}{int(r['parent_id'])}",
                              "to": f"{cre}{int(r['child_id'])}",
                              "relation": r["relation"] or "is_a"})
        except Exception:
            pass
        self._json({"ok": True, "nodes": nodes, "edges": edges,
                    "heat_scale": [0, 1, 5, 20, 100, 400, 1000]})

    def _file_tags(self, body: dict) -> None:
        try:
            fid = int(body.get("file_id") or 0)
        except Exception:
            self._json({"ok": False, "error": "bad_file_id"}, 400)
            return
        add = [str(t) for t in (body.get("add") or []) if str(t).strip()]
        rm = [str(t) for t in (body.get("remove") or []) if str(t).strip()]
        if add:
            self.api.store.add_file_tags(fid, [(t, "manual", 1.0) for t in add])
        if rm:
            self.api.store.remove_file_tags(fid, rm)
        self.api.bump("tags_changed", file_id=fid, add=add, remove=rm)
        self._json({"ok": True, "file_id": fid, "added": add, "removed": rm})

    def _events(self) -> None:
        """SSE：移动端连上后，tag/库有变化就会收到一行 JSON。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = self.api.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            last = time.time()
            while True:
                if q:
                    msg = q.pop(0)
                    self.wfile.write(("data: " + json.dumps(msg, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    last = time.time()
                elif time.time() - last > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last = time.time()
                else:
                    time.sleep(0.25)
        except Exception:
            pass
        finally:
            self.api.unsubscribe(q)
