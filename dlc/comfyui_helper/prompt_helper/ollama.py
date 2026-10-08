"""直连本机 Ollama（不依赖 SwarmUI，也不走任何云端）。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-8b:latest"


class OllamaError(RuntimeError):
    pass


def is_up(host: str = DEFAULT_HOST, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout):
            return True
    except Exception:                                    # noqa: BLE001
        return False


def list_models(host: str = DEFAULT_HOST, timeout: float = 5.0) -> list[str]:
    with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as resp:
        data = json.load(resp)
    return [m.get("name", "") for m in data.get("models", [])]


def chat(system: str, user: str, host: str = DEFAULT_HOST, model: str = DEFAULT_MODEL,
         temperature: float = 0.2, repeat_penalty: float = 1.15,
         num_predict: int = 220, timeout: float = 300.0) -> str:
    """一次问答，返回纯文本回复。"""
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "repeat_penalty": repeat_penalty,
            "num_predict": num_predict,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(f"{host}/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.URLError as exc:
        raise OllamaError(f"连不上本机 Ollama（{host}）：{exc}") from exc
    return (data.get("message", {}) or {}).get("content", "") or ""
