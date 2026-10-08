"""中文 → danbooru 提示词助手（本机 Ollama 离线，词表校验）。

两种用法：
    1) Python 直接 import（原生界面/脚本用）：
           from prompt_helper import generate
           r = generate("初音未来海边微笑", mode="prompt")
           print(r.tags, r.invented)
    2) 本地 HTTP 服务（任何前端都能调）：
           python -m prompt_helper.service            # 默认 127.0.0.1:8199
           POST /prompt  {"text": "...", "mode": "prompt|negative|outfit", "model": "qwen3-8b:latest"}
         返回 {"result": "...", "invented": "...", "raw": "...", "model": "...", "mode": "..."}
"""
from .dictionary import BooruDict, get as get_dictionary
from .generator import Result, generate, generate_both, load_dictionary

__all__ = ["BooruDict", "get_dictionary", "Result", "generate", "generate_both", "load_dictionary"]
__version__ = "0.1.0"
