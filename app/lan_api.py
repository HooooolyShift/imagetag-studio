"""PC 端局域网 API（给平板 / 手机端用）。

只用标准库（http.server + threading），不引第三方依赖。所有接口除 /api/ping 外都要配对码，
配对码就是「已连接设备」窗口里显示的那 4 位数（也可用请求头 X-Imtag-Code 传）。

接口一览：
  GET  /api/ping                        → 本机信息（不需要配对码，用于发现后握手）
  GET  /api/roots                       → 库/来源根列表
  GET  /api/list?root=&dir=&offset=&limit=  → 目录里的图片（带标签、分级、系列）
  GET  /api/thumb?id=&size=340          → 缩略图 JPEG（缺失就现生成，带缓存）
  GET  /api/thumbs?ids=1,2,3&size=340   → 打包下载多张缩略图（zip，省往返、吃满带宽）
  GET  /api/image?id=                   → 原图（支持 HTTP Range，方便大图/断点）
  GET  /api/tags                        → 标签词典（name / zh / category）
  GET  /api/graph                       → 图谱数据（节点/边/热度/折叠状态，移动端照它画）
  GET  /api/library?root=&dir=          → **和图库界面一样**的条目（系列算一条 + 散图），系列带封面/页数
  GET  /api/series                      → 系列列表（名字/标签/页数/封面）
  GET  /api/series/detail?id=           → 某个系列的页（按页码排好）
  POST /api/series/create               → 把若干张合并成系列 {ids, name?, mode?, digits?}
  POST /api/series/reorder              → 重排页序并重命名 {series_id, order:[ids]}
  POST /api/series/rename               → 改系列名（同时刷文件夹名）{series_id, name}
  POST /api/series/dissolve             → 拆开系列 {series_id}
  POST /api/tag/run                     → 远程打标 {ids|all, kind:"autotag"|"rating"|"rescore"|"face"}
  POST /api/writeback                   → 把标签写回文件名 {ids|all}
  GET  /api/dupes                       → 最近一次查重结果（重复图分组）
  POST /api/dupes/scan                  → 开始查重（后台跑，进度走 SSE）
  POST /api/dupes/resolve               → 保留一张、其余隔离/删除
  POST /api/dupes/not_dup               → 误判反馈（这一组不再提示）
  POST /api/dupes/as_series             → 判为系列（合并成一个系列文件夹）
  GET  /api/events                      → SSE 事件流（tag/库变更实时推送）
  POST /api/file_tags                   → 远程改标签 {file_id, add:[...], remove:[...]}

写入接口只做"加/删标签"这一件事（审核遥控的基础），落库仍然走同一个 Store，
避免移动端绕过 PC 的规则去改文件名。
"""
from __future__ import annotations

import json
import hashlib
import io
import socket
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .imaging import make_thumb, thumb_path

API_PORT = 47824
THUMB_RATING_ORDER = ("全年龄", "R15", "R18", "R18G")


class _LanHttpServer(ThreadingHTTPServer):
    """**必须关掉 allow_reuse_address**：Windows 上它允许第二个进程绑同一个端口（端口劫持），
    那样「正式版 + 测试版」两份程序会抢 47824，移动端连哪一个全凭运气。
    关掉之后第二个实例 bind 会失败，start() 就会顺延到 47825、47826…"""

    allow_reuse_address = False
    daemon_threads = True


def review_ver(tags, rating: str = "") -> str:
    """审核版本号：标签(名+状态) + 分级 的哈希。用来做乐观并发控制。

    ⚠ 分级一定要先归一成字符串：队列端拿到的是 `None`（未定级），提交端是 `""`，
    直接 f-string 会分别得到 "None" 和 ""，哈希不一样 → 未定级的图首次提交必 409。
    （2026-10-07 平板端报的"审核卡死"就是这条；**两端都必须走 `rating or ""`**。）
    """
    rating = str(rating or "")
    h = hashlib.md5()
    for t in sorted(tags, key=lambda x: str(x.get("name"))):
        h.update(f"{t.get('name')}|{t.get('status')}\n".encode("utf-8", "ignore"))
    h.update(f"rating={rating}".encode("utf-8", "ignore"))
    return h.hexdigest()[:16]


class LanApi:
    """局域网 HTTP 服务：随设备发现一起启动。"""

    def __init__(self, store, settings, code: str, name: str, version: str, port: int = API_PORT,
                 is_trusted=None, require_pair: bool = True, library=None, hub=None):
        self.store = store
        self.library = library          # 审核转发要用 Library.finish_review
        self.hub = hub                  # 远程打标要用的模型引擎（EngineHub）
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
        self._client_devices: dict[int, str] = {}   # SSE 客户端 → 设备号
        self._lock = threading.Lock()
        self._dupes: list = []            # 最近一次查重结果（内存缓存，供移动端取）
        self._dupes_scanning = False
        self._tag_running = ""            # 正在跑的远程打标类型（空=没跑）

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        if self._httpd is not None:
            return True
        api = self

        class Handler(_ApiHandler):
            server_api = api

        # 端口顺延：同一台机器上可能同时跑「正式版」和「测试版」两份程序，
        # 谁先起来谁占 47824，后来者自动往后找（实际端口会写进 /api/ping 与设备发现广播，
        # 移动端按发现里的 api 端口连，就不会连错人家）。
        last_err = None
        for port in range(int(self.port), int(self.port) + 20):
            try:
                self._httpd = _LanHttpServer(("0.0.0.0", port), Handler)
                self.port = port
                break
            except Exception as exc:
                last_err = exc
                self._httpd = None
        if self._httpd is None:
            return False
        # 长连接保活：移动端挂机/息屏也尽量别被中间设备掐断
        try:
            self._httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception:
            pass
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
    def subscribe(self, device_id: str = ""):
        q: list = []
        with self._lock:
            self._clients.append(q)
            self._client_devices[id(q)] = str(device_id or "")
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)
            self._client_devices.pop(id(q), None)

    def connected_devices(self) -> set[str]:
        """当前挂着 SSE 长连接的设备号（界面上显示"已连接"）。"""
        with self._lock:
            return {d for d in self._client_devices.values() if d}

    def bump(self, event: str, **data) -> None:
        """数据有变化时喊一声，所有连着的移动端立刻知道。"""
        # 注意：参数名不能叫 kind —— 调用方经常要传 `kind=`（比如打标类型），
        # 撞名会直接 TypeError（2026-10-07 就是这么把 /api/tag/run 打成 500 的）。
        msg = {"kind": event, "at": time.time(), **data}
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
        try:
            self._route_get()
        except Exception as exc:                # 别让异常把连接直接掐断：移动端会只看到
            import traceback                 # "Remote end closed connection without response"
            traceback.print_exc()
            try:
                self._json({"ok": False, "error": "internal", "detail": str(exc)}, 500)
            except Exception:
                pass

    def _route_get(self) -> None:
        q = self._query()
        path = urlparse(self.path).path
        if path == "/api/ping":
            import datetime as _dt
            self._json({"ok": True, "app": "imagetag", "role": "pc", "name": self.api.name,
                        "version": self.api.version, "api_port": self.api.port,
                        "need_code": True,
                        # 服务端时间：移动端可以拿它校准自己的时钟/时区
                        "server_time": time.time(),
                        "server_iso": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "server_tz": "UTC+08:00"})
            return
        if not self._authed(q):
            self._deny()
            return
        if path == "/api/roots":
            self._roots()
        elif path == "/api/review/queue":
            self._review_queue(q)
        elif path == "/api/review/undo_state":
            fid = q.get("file_id")
            n = (self.api.library.review_undo_count(int(fid) if str(fid or "").isdigit() else None)
                 if self.api.library is not None else 0)
            self._json({"ok": True, "count": n})
        elif path == "/api/list":
            self._list(q)
        elif path == "/api/thumb":
            self._thumb(q)
        elif path == "/api/thumbs":
            self._thumbs_zip(q)
        elif path == "/api/image":
            self._image(q)
        elif path == "/api/tags":
            self._tags()
        elif path == "/api/graph":
            self._graph()
        elif path == "/api/dupes":
            self._dupes_list()
        elif path == "/api/library":
            self._library(q)
        elif path == "/api/series":
            self._series_list()
        elif path == "/api/series/detail":
            self._series_detail(q)
        elif path == "/api/events":
            self._events()
        else:
            self._json({"ok": False, "error": "not_found"}, 404)

    def do_POST(self) -> None:                  # noqa: N802
        try:
            self._route_post()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            try:
                self._json({"ok": False, "error": "internal", "detail": str(exc)}, 500)
            except Exception:
                pass

    def _route_post(self) -> None:
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
        elif path == "/api/review/apply":
            self._review_apply(body)
        elif path == "/api/review/undo":
            self._review_undo(body)
        elif path == "/api/dupes/scan":
            self._dupes_scan(body)
        elif path == "/api/dupes/resolve":
            self._dupes_resolve(body)
        elif path == "/api/dupes/not_dup":
            self._dupes_not_dup(body)
        elif path == "/api/dupes/as_series":
            self._dupes_as_series(body)
        elif path == "/api/series/create":
            self._series_create(body)
        elif path == "/api/series/reorder":
            self._series_reorder(body)
        elif path == "/api/series/rename":
            self._series_rename(body)
        elif path == "/api/series/dissolve":
            self._series_dissolve(body)
        elif path == "/api/tag/run":
            self._tag_run(body)
        elif path == "/api/writeback":
            self._writeback(body)
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

    @staticmethod
    def _review_ver(tags, rating: str = "") -> str:
        return review_ver(tags, rating)

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

    def _thumbs_zip(self, q: dict) -> None:
        """一次请求打包多张缩略图（zip）。移动端拉一屏时用它，别一张张请求。"""
        raw = str(q.get("ids") or "")
        ids = [int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()][:400]
        if not ids:
            self._json({"ok": False, "error": "no_ids"}, 400)
            return
        try:
            size = int(q.get("size") or 340)
        except ValueError:
            size = 340
        buf = io.BytesIO()
        n = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for fid in ids:
                row = self.api.store.one("SELECT path, mtime FROM files WHERE id=?", (fid,))
                if row is None:
                    continue
                p = thumb_path(fid, row["mtime"] or 0, size)
                if not p.exists() or p.stat().st_size == 0:
                    p = make_thumb(row["path"], fid, row["mtime"] or 0, size=size) or p
                if p.exists() and p.stat().st_size:
                    zf.write(p, arcname=f"{fid}.jpg")
                    n += 1
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Thumb-Count", str(n))
        self.send_header("Cache-Control", "public, max-age=3600")
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
        # 每个节点的父节点列表：移动端据此把标签摆到所属分类周围，不用自己算层级
        parents: dict[str, list[str]] = {}
        for e in edges:
            parents.setdefault(e["to"], []).append(e["from"])
        for n in nodes:
            n["parents"] = parents.get(n["id"], [])
        # 坐标下放：PC 端算好（分类用库里的真实坐标，标签按父分类做环形分布），
        # 移动端直接用，不用自己跑布局，也就不会和 PC 摆得不一样。
        try:
            from .graph_layout import place_graph
            groups = [{"id": n["id"], "x": n["x"], "y": n["y"], "name": n["name"]}
                      for n in nodes if n["kind"] == "group"]
            tags_ = [{"id": n["id"], "count": n["count"], "parents": n["parents"]}
                     for n in nodes if n["kind"] == "tag"]
            pos = place_graph(groups, tags_)
            for n in nodes:
                p = pos.get(n["id"])
                if p:
                    n["x"], n["y"] = round(float(p[0]), 1), round(float(p[1]), 1)
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

    def _review_queue(self, q: dict) -> None:
        """待审队列（平板遥控审核用）：和 PC 端审核台同一份数据。"""
        limit = min(500, max(1, int(q.get("limit") or 200)))
        rows = self.api.store.pending_files(limit)
        out = []
        for r in rows:
            fid = int(r["id"])
            tags = []
            for t in self.api.store.tags_for_file(fid, statuses=("pending", "confirmed")):
                tags.append({"name": t["name"], "source": t["source"],
                             "score": float(t["score"] or 0), "status": t["status"]})
            out.append({"id": fid, "name": r["name"] if "name" in r.keys() else "",
                        "path": r["path"], "n_pending": int(r["n_pending"] or 0),
                        "no_rating": bool(r["no_rating"]) if "no_rating" in r.keys() else False,
                        "rating": (r["rating"] if "rating" in r.keys() else "") or "",
                        "ver": self._review_ver(tags, (r["rating"] if "rating" in r.keys() else "") or ""),
                        "tags": tags, "thumb_sizes": self._present_thumbs(fid, r["mtime"] or 0)})
        summary = self.api.store.pending_summary()
        self._json({"ok": True, "count": len(out), "summary": summary, "files": out})

    def _review_apply(self, body: dict) -> None:
        """提交一张图的审核结果：confirmed / rejected / drop（丢弃不参与模型反馈）+ 可选 rating。"""
        if self.api.library is None:
            self._json({"ok": False, "error": "review_not_available"}, 503)
            return
        try:
            fid = int(body.get("file_id") or 0)
        except Exception:
            self._json({"ok": False, "error": "bad_file_id"}, 400)
            return
        conf = [str(t) for t in (body.get("confirmed") or []) if str(t).strip()]
        rej = [str(t) for t in (body.get("rejected") or []) if str(t).strip()]
        drop = [str(t) for t in (body.get("drop") or []) if str(t).strip()]
        rating = body.get("rating") or None
        # 乐观并发：客户端提交它读到的版本号；不一致说明这张图在别处（PC 审核台或其
        # 它设备）已经被改过，回 409 让它重新拉一次，避免两边的判断互相覆盖。
        want_ver = str(body.get("ver") or "")
        if want_ver:
            tags_now = [{"name": t["name"], "status": t["status"]}
                        for t in self.api.store.tags_for_file(fid, statuses=("pending", "confirmed"))]
            row = self.api.store.one("SELECT rating FROM files WHERE id=?", (fid,))
            now_ver = self._review_ver(tags_now, (row["rating"] if row else "") or "")
            if now_ver != want_ver:
                self._json({"ok": False, "error": "stale", "ver": now_ver,
                            "hint": "这张图已被别处改过，请重新拉取队列再提交"}, 409)
                return
        try:
            res = self.api.library.finish_review(fid, conf, rej, drop, rating)
        except Exception as exc:
            self._json({"ok": False, "error": "apply_failed", "detail": str(exc)}, 500)
            return
        self.api.bump("review_applied", file_id=fid, confirmed=conf, rejected=rej, dropped=drop)
        self._json({"ok": True, "result": res})

    def _review_undo(self, body: dict) -> None:
        """撤销最近一次审核（平板端的「撤销」按钮）：还原标签行/分级/已审标记并重算探针。"""
        if self.api.library is None:
            self._json({"ok": False, "error": "review_not_available"}, 503)
            return
        fid = body.get("file_id")
        try:
            fid = int(fid) if fid not in (None, "", 0) else None
        except Exception:
            fid = None
        res = self.api.library.undo_last_review(fid)
        if res.get("ok"):
            self.api.bump("review_undone", file_id=res.get("file"))
            res["remaining"] = self.api.library.review_undo_count(fid)
        self._json(res, 200 if res.get("ok") else 404)

    # ---------- 查重（重复 / 近似重复）----------

    # ---------- 图库条目（系列算一条）+ 系列管理 ----------
    def _file_brief(self, r) -> dict:
        fid = int(r["id"])
        return {"id": fid, "name": r["name"] if "name" in r.keys() else Path(r["path"]).name,
                "path": r["path"], "rel": r["rel"] if "rel" in r.keys() else "",
                "mtime": r["mtime"], "rating": (r["rating"] if "rating" in r.keys() else "") or "",
                "series_id": (r["series_id"] if "series_id" in r.keys() else None),
                "page_no": (r["page_no"] if "page_no" in r.keys() else None),
                "tags": [t["name"] for t in self.api.store.tags_for_file(fid, statuses=("confirmed",))][:12],
                "thumb_sizes": self._present_thumbs(fid, r["mtime"] or 0)}

    def _series_brief(self, s) -> dict:
        """系列条目：和图库界面一样——**封面用第一页**，显示页数，文件夹名就是系列名。"""
        first = self.api.store.one("SELECT * FROM files WHERE id=?", (int(s["first_file_id"] or 0),))
        cover = self._file_brief(first) if first is not None else None
        return {"type": "series", "series_id": int(s["id"]), "name": s["name"],
                "dir": s["dir"], "tags": [t for t in str(s["tags"] or "").split() if t],
                "page_count": int(s["page_count"] or 0), "root_id": int(s["root_id"] or 0),
                "cover": cover}

    def _library(self, q: dict) -> None:
        """**移动端要和 PC 图库表现一致**：系列作为一条（封面=第一页、带页数），
        同一目录下的散图照常一条一条列；点开系列再调 /api/series/detail 拿内页。"""
        root = q.get("root") or ""
        rel = (q.get("dir") or "").strip("\\/")
        limit = min(1000, max(1, int(q.get("limit") or 300)))
        entries: list[dict] = []
        # 1) 本层的系列（series.dir 的父目录正好是当前目录）
        series_rows = []
        for s in self.api.store.series_list():
            d = str(s["dir"] or "")
            parent = d.rsplit("/", 1)[0] if "/" in d else ""
            if root and str(root).isdigit() and int(s["root_id"] or 0) != int(root):
                continue
            if (parent or "") != rel:
                continue
            series_rows.append(s)
        entries.extend(self._series_brief(s) for s in series_rows)
        # 2) 本层的散图（属于系列的图不在这里重复出现）
        where = ["f.missing=0", "f.series_id IS NULL"]
        args: list = []
        if root and str(root).isdigit():
            where.append("f.root_id=?")
            args.append(int(root))
        if rel:
            where.append("(f.rel LIKE ? OR f.rel LIKE ?)")
            args += [rel + "\\%", rel + "/%"]
        rows = self.api.store.query(
            "SELECT f.* FROM files f WHERE " + " AND ".join(where) + " ORDER BY f.rel LIMIT ?",
            (*args, limit))
        entries.extend({"type": "file", **self._file_brief(r)} for r in rows)
        self._json({"ok": True, "dir": rel, "count": len(entries), "entries": entries})

    def _series_list(self) -> None:
        self._json({"ok": True, "count": len(self.api.store.series_list()),
                    "series": [self._series_brief(s) for s in self.api.store.series_list()]})

    def _series_detail(self, q: dict) -> None:
        try:
            sid = int(q.get("id") or 0)
        except ValueError:
            sid = 0
        s = self.api.store.one("SELECT * FROM series WHERE id=?", (sid,))
        if s is None:
            self._json({"ok": False, "error": "no_such_series"}, 404)
            return
        pages = [self._file_brief(r) for r in self.api.store.series_files(sid)]
        self._json({"ok": True, "series": self._series_brief(s), "pages": pages})

    def _series_create(self, body: dict) -> None:
        ids = [int(x) for x in (body.get("ids") or [])]
        if len(ids) < 2:
            self._json({"ok": False, "error": "need_at_least_two"}, 400)
            return
        res = self.api.library.merge_into_series(
            ids, name=str(body.get("name") or ""),
            mode=str(body.get("mode") or getattr(self.api.settings, "series_move_mode", "copy")),
            digits=body.get("digits") or None)
        self.api.bump("series_changed", ids=ids, result=res)
        self._json({"ok": bool(res.get("ok", True)), "result": res})

    def _series_reorder(self, body: dict) -> None:
        try:
            sid = int(body.get("series_id") or 0)
        except Exception:
            sid = 0
        order = [int(x) for x in (body.get("order") or [])]
        if not sid or not order:
            self._json({"ok": False, "error": "bad_args"}, 400)
            return
        res = self.api.library.reorder_series(sid, order, digits=body.get("digits") or None)
        self.api.bump("series_changed", series_id=sid, result=res)
        self._json({"ok": bool(res.get("ok", True)), "result": res})

    def _series_rename(self, body: dict) -> None:
        try:
            sid = int(body.get("series_id") or 0)
        except Exception:
            sid = 0
        name = str(body.get("name") or "").strip()
        if not sid or not name:
            self._json({"ok": False, "error": "bad_args"}, 400)
            return
        self.api.library.update_series(sid, name=name)
        renamed = self.api.library.rename_series_dir(sid)     # 文件夹名一起改（和 PC 一样）
        self.api.bump("series_changed", series_id=sid, name=name)
        self._json({"ok": True, "renamed_dir": bool(renamed)})

    def _series_dissolve(self, body: dict) -> None:
        try:
            sid = int(body.get("series_id") or 0)
        except Exception:
            sid = 0
        if not sid:
            self._json({"ok": False, "error": "bad_args"}, 400)
            return
        res = self.api.library.dissolve_series(sid, keep_in_place=bool(body.get("keep_in_place", True)))
        self.api.bump("series_changed", series_id=sid)
        self._json({"ok": bool(res.get("ok", True)), "result": res})

    # ---------- 远程打标 / 写回文件名 ----------
    def _tag_run(self, body: dict) -> None:
        """在 PC 上跑打标（算力在 PC）：WD14 + CLIP + 分级，结果照旧进**待审核队列**。

        body: {ids?: [..], all?: true, kind?: "autotag"|"rating"|"rescore"|"face"}
        进度走 SSE：`tag_progress{text,frac}` → `tag_done{kind,ids,pending}` / `tag_failed{error}`
        """
        api = self.api
        if api.library is None or api.hub is None:
            self._json({"ok": False, "error": "tag_not_available",
                        "hint": "PC 端没把模型引擎传进来（EngineHub）"}, 503)
            return
        if api._tag_running:
            self._json({"ok": True, "started": False, "reason": "already_running",
                        "kind": api._tag_running})
            return
        kind = str(body.get("kind") or "autotag")
        if kind not in ("autotag", "rating", "rescore", "face"):
            self._json({"ok": False, "error": "bad_kind"}, 400)
            return
        ids = [int(x) for x in (body.get("ids") or [])]
        if not ids and body.get("all"):
            ids = [int(r["id"]) for r in api.store.query(
                "SELECT id FROM files WHERE missing=0 ORDER BY id")]
        if not ids:
            self._json({"ok": False, "error": "no_files"}, 400)
            return
        s = api.settings
        if kind in ("autotag", "rescore") and not (s.wd14_enabled or s.clip_enabled):
            self._json({"ok": False, "error": "tagger_disabled",
                        "hint": "WD14 与 CLIP 都没开"}, 400)
            return
        if kind == "rating" and not s.rating_enabled:
            self._json({"ok": False, "error": "rating_disabled"}, 400)
            return
        api._tag_running = kind
        api.bump("tag_started", mode=kind, count=len(ids))

        def progress(text: str, frac: float = 0.0) -> None:
            api.bump("tag_progress", mode=kind, text=text, frac=frac)

        def job() -> None:
            job_id = None
            try:
                job_id = api.library.start_job(f"lan-{kind}", ids, {}, note="平板遥控")
                if kind in ("autotag",) and s.wd14_enabled:
                    api.library.run_wd14(ids, api.hub, progress, lambda: False)
                if kind in ("autotag", "rescore") and s.clip_enabled:
                    api.library.ensure_clip_embeddings(ids, api.hub, progress, lambda: False)
                    api.library.auto_tags_from_clip(api.hub, ids, progress, lambda: False)
                if kind == "face":
                    api.library.run_face(ids, api.hub, progress, lambda: False)
                    api.library.cluster_faces(None, progress)
                if kind in ("rating", "autotag") and s.rating_enabled:
                    api.library.run_rating(ids, api.hub, progress, lambda: False)
                try:
                    api.library.finish_job(job_id, "done")
                except Exception:
                    pass
                pend = api.store.pending_summary()
                api.bump("tag_done", mode=kind, ids=ids,
                         pending=pend.get("pending_tags"), pending_files=pend.get("pending_files"))
            except Exception as exc:
                try:
                    if job_id:
                        api.library.finish_job(job_id, "failed")
                except Exception:
                    pass
                api.bump("tag_failed", mode=kind, error=str(exc))
            finally:
                api._tag_running = ""

        threading.Thread(target=job, daemon=True, name="imtag-lan-tag").start()
        self._json({"ok": True, "started": True, "kind": kind, "count": len(ids)})

    def _writeback(self, body: dict) -> None:
        """把标签写回文件名（和 PC 的「写回文件名」同一套规则：按设置里的中文名/分级/保留原名）。"""
        api = self.api
        if api.library is None:
            self._json({"ok": False, "error": "not_available"}, 503)
            return
        ids = [int(x) for x in (body.get("ids") or [])]
        if not ids and body.get("all"):
            ids = [int(r["id"]) for r in api.store.query(
                "SELECT id FROM files WHERE missing=0 ORDER BY id")]
        if not ids:
            self._json({"ok": False, "error": "no_files"}, 400)
            return
        res = api.library.apply_disk_names(ids)
        api.bump("tags_changed", file_ids=ids, reason="writeback")
        self._json({"ok": True, "result": res})
    def _dupes_payload(self, groups) -> list:
        """把 library 的查重结果整理成移动端好用的结构（含缩略图边长与标签）。"""
        out = []
        for g in groups:
            files = []
            for f in g.get("files", []):
                fid = int(f["id"])
                tags = [t["name"] for t in self.api.store.tags_for_file(fid, statuses=("confirmed",))][:8]
                files.append({"id": fid, "name": f["name"] if "name" in f.keys() else "",
                              "path": f["path"], "mtime": f["mtime"], "size": f["size"],
                              "tags": tags,
                              "thumb_sizes": self._present_thumbs(fid, f["mtime"] or 0)})
            out.append({"key": g.get("key"), "size": g.get("size"), "max_dist": g.get("max_dist"),
                        "mixed_series": g.get("mixed_series"), "series_ids": g.get("series_ids") or [],
                        "files": files})
        return out

    def _dupes_list(self) -> None:
        api = self.api
        self._json({"ok": True, "scanning": bool(api._dupes_scanning),
                    "threshold": int(getattr(api.settings, "dup_threshold", 6)),
                    "use_clip": bool(getattr(api.settings, "dup_use_clip", True)),
                    "count": len(api._dupes), "groups": self._dupes_payload(api._dupes)})

    def _dupes_scan(self, body: dict) -> None:
        """开始查重：后台线程跑（感知哈希 + 可选 CLIP 兜底），进度/结果通过 SSE 推。"""
        api = self.api
        if api.library is None:
            self._json({"ok": False, "error": "dupes_not_available"}, 503)
            return
        if api._dupes_scanning:
            self._json({"ok": True, "started": False, "reason": "already_scanning"})
            return
        try:
            threshold = int(body.get("threshold") or getattr(api.settings, "dup_threshold", 6))
        except Exception:
            threshold = 6
        use_clip = bool(body.get("use_clip", getattr(api.settings, "dup_use_clip", True)))
        roots = [int(r["id"]) for r in api.store.library_roots()]
        api._dupes_scanning = True
        api.bump("dupes_started", threshold=threshold)

        def job() -> None:
            try:
                api.library.ensure_hashes(None,
                                          progress=lambda t, f=0.0: api.bump("dupes_progress", text=t, frac=f),
                                          cancel=lambda: False)
                groups = api.library.find_duplicate_groups(
                    threshold=threshold, only_roots=roots, use_clip=use_clip,
                    progress=lambda t, f=0.0: api.bump("dupes_progress", text=t, frac=f),
                    cancel=lambda: False)
                api._dupes = list(groups)
                api.bump("dupes_done", count=len(groups))
            except Exception as exc:
                api.bump("dupes_failed", error=str(exc))
            finally:
                api._dupes_scanning = False

        threading.Thread(target=job, daemon=True, name="imtag-dupes").start()
        self._json({"ok": True, "started": True})

    def _dupes_resolve(self, body: dict) -> None:
        """保留一张、其余隔离（默认）或永久删除；顺带把这一组从缓存里去掉。"""
        api = self.api
        if api.library is None:
            self._json({"ok": False, "error": "dupes_not_available"}, 503)
            return
        try:
            keep = int(body.get("keep_id") or 0)
            others = [int(x) for x in (body.get("remove_ids") or []) if int(x) != keep]
        except Exception:
            self._json({"ok": False, "error": "bad_ids"}, 400)
            return
        if not keep or not others:
            self._json({"ok": False, "error": "need_keep_and_remove"}, 400)
            return
        action = str(body.get("action") or "quarantine")     # quarantine（进 .removed 可恢复） / delete
        res = api.library.resolve_duplicate(keep, others, action=action,
                                            merge_tags=bool(body.get("merge_tags", True)))
        ids = {keep, *others}
        api._dupes = [g for g in api._dupes
                      if not (ids & {int(f["id"]) for f in g.get("files", [])})]
        api.bump("dupes_resolved", keep=keep, removed=others, action=action)
        self._json({"ok": bool(res.get("ok", True)), "result": res})

    def _dupes_not_dup(self, body: dict) -> None:
        """误判反馈：这一组以后不再作为重复提示（PC 与移动端共用同一份反馈表）。"""
        api = self.api
        ids = [int(x) for x in (body.get("ids") or [])]
        if len(ids) < 2:
            self._json({"ok": False, "error": "need_at_least_two"}, 400)
            return
        api.library.mark_group_not_duplicate(ids, note="mobile_feedback")
        api._dupes = [g for g in api._dupes
                      if not (set(ids) & {int(f["id"]) for f in g.get("files", [])})]
        api.bump("dupes_not_dup", ids=ids)
        self._json({"ok": True, "ids": ids})

    def _dupes_as_series(self, body: dict) -> None:
        """判为系列：把这一组合并成一个系列（默认移动；可传 name 指定系列名）。"""
        api = self.api
        ids = [int(x) for x in (body.get("ids") or [])]
        if len(ids) < 2:
            self._json({"ok": False, "error": "need_at_least_two"}, 400)
            return
        name = str(body.get("name") or "")
        mode = str(body.get("mode") or getattr(api.settings, "series_move_mode", "copy"))
        res = api.library.merge_into_series(ids, name=name, mode=mode)
        api.library.mark_group_as_series(ids)
        api._dupes = [g for g in api._dupes
                      if not (set(ids) & {int(f["id"]) for f in g.get("files", [])})]
        api.bump("dupes_as_series", ids=ids, result=res)
        self._json({"ok": bool(res.get("ok", True)), "result": res})

    def _events(self) -> None:
        """SSE：移动端连上后，tag/库有变化就会收到一行 JSON。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        dev = self.headers.get("X-Imtag-Device") or self._query().get("device") or ""
        q = self.api.subscribe(dev)
        try:
            try:                      # SSE 连接也开 keepalive，避免中间设备掐长连接
                sock = self.connection
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
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
