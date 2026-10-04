"""模型管理：统一负责从 hf-mirror 下载/校验本地模型文件。"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import default_models_dir


@dataclass(frozen=True)
class ModelFile:
    key: str
    repo: str
    remote: str
    dest: str
    label: str
    approx_mb: int = 0


MODELS: dict[str, ModelFile] = {
    "wd14_onnx": ModelFile("wd14_onnx", "SmilingWolf/wd-swinv2-tagger-v3", "model.onnx",
                           "wd-swinv2-tagger-v3/model.onnx", "WD14 二次元打标模型", 446),
    "wd14_tags": ModelFile("wd14_tags", "SmilingWolf/wd-swinv2-tagger-v3", "selected_tags.csv",
                           "wd-swinv2-tagger-v3/selected_tags.csv", "WD14 标签表", 1),
    "face_det": ModelFile("face_det", "immich-app/buffalo_l", "detection/model.onnx",
                          "insightface/det_10g.onnx", "人脸检测 SCRFD", 17),
    "face_rec": ModelFile("face_rec", "immich-app/buffalo_l", "recognition/model.onnx",
                          "insightface/w600k_r50.onnx", "人脸特征 ArcFace", 167),
    "clip": ModelFile("clip", "laion/CLIP-ViT-B-32-laion2B-s34B-b79K", "open_clip_pytorch_model.bin",
                      "clip/CLIP-ViT-B-32-laion2B-s34B-b79K.bin", "CLIP ViT-B-32（自定义标签）", 578),
    "wd14_eva02_onnx": ModelFile("wd14_eva02_onnx", "SmilingWolf/wd-eva02-large-tagger-v3", "model.onnx",
                                 "wd-eva02-large-tagger-v3/model.onnx", "WD14 EVA02-large（更准更慢）", 1174),
    "wd14_eva02_tags": ModelFile("wd14_eva02_tags", "SmilingWolf/wd-eva02-large-tagger-v3", "selected_tags.csv",
                                 "wd-eva02-large-tagger-v3/selected_tags.csv", "WD14 EVA02 标签表", 1),
    "wd14_convnext_onnx": ModelFile("wd14_convnext_onnx", "SmilingWolf/wd-v1-4-convnext-tagger-v2", "model.onnx",
                                    "wd-v1-4-convnext-tagger-v2/model.onnx", "WD14 convnext v1.4（更轻）", 408),
    "wd14_convnext_tags": ModelFile("wd14_convnext_tags", "SmilingWolf/wd-v1-4-convnext-tagger-v2", "selected_tags.csv",
                                    "wd-v1-4-convnext-tagger-v2/selected_tags.csv", "WD14 convnext 标签表", 1),
}

# 设置里可选的 tagger 模型 → (模型 key, 标签表 key)
TAGGER_CHOICES = {
    "SmilingWolf/wd-swinv2-tagger-v3": ("wd14_onnx", "wd14_tags"),
    "SmilingWolf/wd-eva02-large-tagger-v3": ("wd14_eva02_onnx", "wd14_eva02_tags"),
    "SmilingWolf/wd-v1-4-convnext-tagger-v2": ("wd14_convnext_onnx", "wd14_convnext_tags"),
}

_download_lock = threading.Lock()


def setup_env(models_dir: Path | None = None) -> Path:
    """设置 HF 相关环境变量（国内镜像、缓存位置）。"""
    md = Path(models_dir) if models_dir else default_models_dir()
    md.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", str(md / "hf_cache"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.pop("HF_HUB_OFFLINE", None)
    return md


def path_of(key: str, models_dir: Path | None = None) -> Path:
    md = Path(models_dir) if models_dir else default_models_dir()
    return md / MODELS[key].dest


def is_present(key: str, models_dir: Path | None = None) -> bool:
    p = path_of(key, models_dir)
    if not p.exists():
        return False
    min_bytes = max(1024, MODELS[key].approx_mb * 1024 * 1024 // 10)
    return p.stat().st_size >= min_bytes


def missing_keys(keys, models_dir: Path | None = None) -> list[str]:
    return [k for k in keys if not is_present(k, models_dir)]


def ensure(key: str, models_dir: Path | None = None,
           progress: Callable[[str, float], None] | None = None) -> Path:
    """确保模型文件存在，缺失时下载；返回本地路径。"""
    md = setup_env(models_dir)
    spec = MODELS[key]
    dest = md / spec.dest
    if is_present(key, md):
        return dest
    from huggingface_hub import hf_hub_download

    dest.parent.mkdir(parents=True, exist_ok=True)
    with _download_lock:
        if is_present(key, md):
            return dest
        if progress:
            progress(f"下载 {spec.label} ({spec.approx_mb} MB)…", -1.0)
        tmp = hf_hub_download(repo_id=spec.repo, filename=spec.remote, cache_dir=str(md / "hf_cache"))
        src = Path(tmp)
        if src.resolve() != dest.resolve():
            import shutil
            shutil.copyfile(src, dest)
    if progress:
        progress(f"{spec.label} 就绪", 1.0)
    return dest


def ensure_all(models_dir: Path | None = None,
               progress: Callable[[str, float], None] | None = None,
               include: tuple[str, ...] = ("wd14_onnx", "wd14_tags", "face_det", "face_rec")) -> None:
    for k in include:
        ensure(k, models_dir, progress)
