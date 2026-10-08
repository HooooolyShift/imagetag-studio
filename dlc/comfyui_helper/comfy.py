"""ComfyUI 客户端 —— **由宿主提供**（`app/comfy_client.py`），这里只做转发，别再各写一份。

保留这个文件是为了让 DLC 内部继续写 `from .comfy import ComfyClient, ComfyError`
（2026-10-08 起实现统一到宿主，局域网生图接口 /api/gen/* 与 DLC 界面共用同一份）。
"""
from __future__ import annotations

from app.comfy_client import ComfyClient, ComfyError

__all__ = ["ComfyClient", "ComfyError"]
