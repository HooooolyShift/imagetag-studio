"""直连本机 Ollama（不依赖 SwarmUI，也不走任何云端）。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-8b:latest"

# 本机有多个模型时，按这个优先级挑（翻译/改写这类任务 8B 够用；有更大的就优先更大的）
PREFERRED_HINTS = ("qwen3-8b", "qwen2.5-7b", "qwen3-14b", "qwen2.5-14b", "qwen2.5-3b", "qwen3-4b", "gemma", "llama")


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


def pick_best_model(models: list[str] | None = None, host: str = DEFAULT_HOST) -> str:
    """**优先用本机已有的模型**：在已安装的模型里挑最合适的一个；一个都没有才返回默认名（让用户去下）。
    规则：先看 PREFERRED_HINTS 里的名字，再按参数量（7b/8b/14b…）取大的。"""
    try:
        names = models if models is not None else list_models(host)
    except Exception:                                        # noqa: BLE001
        names = []
    names = [n for n in names if n]
    if not names:
        return DEFAULT_MODEL

    def score(name: str) -> float:
        low = name.lower()
        for idx, hint in enumerate(PREFERRED_HINTS):
            if hint in low:
                return 1000 - idx
        m = re.search(r"(\d+(?:\.\d+)?)b", low)
        return float(m.group(1)) if m else 1.0

    return max(names, key=score)


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
