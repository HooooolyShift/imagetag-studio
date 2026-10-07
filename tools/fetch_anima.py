"""下载 Anima（CircleStone Labs × Comfy Org 的二次元专用模型）所需的 3 个文件。

官方说明：Anima 2B，ComfyUI 原生支持。
  · anima-aesthetic-v1.1.safetensors → models/diffusion_models
  · qwen_3_06b_base.safetensors      → models/text_encoders
  · qwen_image_vae.safetensors       → models/vae
用法：python tools\\fetch_anima.py [变体= aesthetic-v1.1|turbo-v1.1|base-v1.0]
（走 hf-mirror + 断点续传，断电后重跑即可接着下。）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_model import download  # noqa: E402

COMFY = Path(r"E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5\models")
BASE = "https://hf-mirror.com/circlestone-labs/Anima/resolve/main/split_files"


def main() -> int:
    variant = sys.argv[1] if len(sys.argv) > 1 else "aesthetic-v1.1"
    jobs = [
        (f"{BASE}/diffusion_models/anima-{variant}.safetensors",
         COMFY / "diffusion_models" / f"anima-{variant}.safetensors"),
        (f"{BASE}/text_encoders/qwen_3_06b_base.safetensors",
         COMFY / "text_encoders" / "qwen_3_06b_base.safetensors"),
        (f"{BASE}/vae/qwen_image_vae.safetensors",
         COMFY / "vae" / "qwen_image_vae.safetensors"),
    ]
    for url, dst in jobs:
        print(f"\n=== 下载 {dst.name} ===", flush=True)
        rc = download(url, dst)
        if rc != 0:
            print(f"失败：{dst}", flush=True)
            return rc
    print("\nAnima 三个文件都下好了。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
