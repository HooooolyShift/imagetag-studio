"""下载 IP-Adapter（SDXL）+ 配套 ViT-H 图像编码器。

本机原有的 `ip-adapter_xl.pth` 需要 1280 维的 ViT-H 图像编码器，
而 `models/clip_vision/clip_h.pth` 只有 1024 维（OpenCLIP ViT-L），
加载时会报 "size mismatch for proj.weight ... [8192,1280] vs [8192,1024]"。
所以这里把配套的两个文件补齐（走 hf-mirror，支持断点续传）：
  · sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors → models/ipadapter/
  · models/image_encoder/model.safetensors            → models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors
用法：python tools\\fetch_ipadapter.py [ComfyUI 目录]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_model import download  # noqa: E402

BASE = "https://hf-mirror.com/h94/IP-Adapter/resolve/main"


def main() -> int:
    comfy = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5")
    models = comfy / "models"
    jobs = [
        (f"{BASE}/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors",
         models / "ipadapter" / "ip-adapter-plus_sdxl_vit-h.safetensors"),
        (f"{BASE}/models/image_encoder/model.safetensors",
         models / "clip_vision" / "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"),
    ]
    for url, dst in jobs:
        print(f"\n=== 下载 {dst.name} ===", flush=True)
        rc = download(url, dst)
        if rc != 0:
            print(f"失败：{dst}", flush=True)
            return rc
    print("\nIP-Adapter 两个文件都下好了（ComfyUI 会自动刷新模型列表）。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
