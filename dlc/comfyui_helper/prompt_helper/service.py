"""本地 HTTP 服务：把"中文 → 提示词"暴露成 HTTP 接口，供原生 PySide6 界面或任何前端调用。

启动： python -m prompt_helper.service [--port 8199] [--host 127.0.0.1]
接口：
    GET  /health                 -> {"ok": true, "ollama": true/false, "tags": 233796}
    POST /prompt  {text, mode?, model?}  -> 见 __init__ 文档
    POST /validate {tags}        -> 只做词表校验（不调模型），返回 result/invented
只用标准库，无需额外依赖。
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .generator import generate, load_dictionary
from .ollama import DEFAULT_MODEL, OllamaError, is_up

_dict = None
_dict_lock = threading.Lock()


def _get_dict():
    global _dict
    with _dict_lock:
        if _dict is None:
            _dict = load_dictionary()
    return _dict


class Handler(BaseHTTPRequestHandler):
    server_version = "PromptHelper/0.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:                       # noqa: N802
        self._send(200, {"ok": True})

    def do_GET(self) -> None:                           # noqa: N802
        if self.path.startswith("/health"):
            d = _get_dict()
            self._send(200, {"ok": True, "ollama": is_up(),
                             "tags": len(d.by_name), "aliases": len(d.by_alias),
                             "groups": {k: len(v) for k, v in d.groups.items()}})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:                          # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:                        # noqa: BLE001
            self._send(400, {"error": f"bad json: {exc}"})
            return
        if self.path.startswith("/validate"):
            tags = data.get("tags", "")
            clean, invented = _get_dict().validate(tags)
            self._send(200, {"result": clean, "invented": ", ".join(invented)})
            return
        if self.path.startswith("/prompt"):
            text = (data.get("text") or "").strip()
            if not text:
                self._send(400, {"error": "缺少 text"})
                return
            try:
                r = generate(text, data.get("mode", "prompt"), dictionary=_get_dict(),
                             model=data.get("model") or DEFAULT_MODEL)
            except OllamaError as exc:
                self._send(503, {"error": str(exc)})
                return
            except Exception as exc:                    # noqa: BLE001
                self._send(500, {"error": str(exc)})
                return
            self._send(200, r.to_dict())
            return
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):                  # noqa: A003
        print(f"[prompt-helper] {self.address_string()} {fmt % args}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8199)
    args = ap.parse_args()
    d = _get_dict()
    print(f"[prompt-helper] 词表已载入：{len(d.by_name)} tag / {len(d.by_alias)} 别名")
    print(f"[prompt-helper] Ollama: {'在线' if is_up() else '未运行（/prompt 会返回 503）'}")
    print(f"[prompt-helper] 监听 http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
