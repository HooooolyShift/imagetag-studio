"""CLIP 零样本打标：让"随时新增的标签"立刻可用（无需训练）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .. import models
from . import torch_device

DEFAULT_NEGATIVES = [
    "something else", "an empty picture", "a blank image", "a random object",
    "a landscape", "a text document", "a screenshot", "a single color image",
]


class ClipZeroShot:
    """ViT-B-32 (laion2b) 零样本标注器。

    - 图片编码一次后缓存，标签变化时只需重算文本向量与打分，增删标签几乎瞬时生效。
    - 每个标签可自定义英文 prompt（命中率更高），默认用标签名。
    """

    def __init__(self, models_dir: Path | None = None, model_name: str = "ViT-B-32",
                 pretrained: str = "laion2b_s34b_b79k", device: str = "auto"):
        self.models_dir = models_dir
        self.model_name = model_name
        self.pretrained = pretrained
        self.device = torch_device(device)
        self.model = None
        self.preprocess = None
        self.tokenizer = None
        self.dim = 512
        self._neg = None
        self._text_cache: dict[tuple, np.ndarray] = {}

    def load(self, progress=None) -> None:
        if self.model is not None:
            return
        import open_clip
        import torch
        try:
            from .. import perf
            perf.apply_torch()          # torch 已加载，这时应用线程数/半精度等挡位参数
        except Exception:
            pass
        md = models.setup_env(self.models_dir)
        if self.model_name == "ViT-B-32" and self.pretrained == "laion2b_s34b_b79k":
            models.ensure("clip", md, progress)
        if progress:
            progress(f"加载 CLIP {self.model_name} …", -1.0)
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained, cache_dir=str(md))
        model.eval()
        if self.device == "cuda":
            model = model.cuda()
        try:
            from .. import perf
            model = model.half() if (self.device == "cuda" and perf.use_half()) else model
        except Exception:
            pass
        self.model = model
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(self.model_name)
        self._torch = torch

    # ---------- 编码 ----------
    def encode_images(self, images: list[Image.Image], batch_size: int = 16, progress=None) -> np.ndarray:
        if self.model is None:
            self.load()
        from .. import perf
        if batch_size is None or batch_size <= 0:
            batch_size = perf.batches()[1]
        torch = self._torch
        feats = []
        idle = perf.sleep_between_batches()
        with torch.no_grad():
            for i in range(0, len(images), batch_size):
                chunk = images[i:i + batch_size]
                x = torch.stack([self.preprocess(im.convert("RGB")) for im in chunk])
                if self.device == "cuda":
                    x = x.cuda().half()
                f = self.model.encode_image(x)
                f = f / f.norm(dim=-1, keepdim=True)
                feats.append(f.float().cpu().numpy())
                if progress:
                    progress("CLIP 图片编码", (i + len(chunk)) / max(1, len(images)))
                if idle:
                    import time as _t
                    _t.sleep(idle)
        return np.concatenate(feats, axis=0) if feats else np.zeros((0, self.dim), dtype=np.float32)

    def encode_texts(self, texts: list[str], templates: list[str] | None = None) -> np.ndarray:
        if self.model is None:
            self.load()
        torch = self._torch
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        templates = templates or ["{}", "a photo of {}", "a picture of {}"]
        key_t = tuple(templates)
        out: list[np.ndarray | None] = []
        missing: list[int] = []
        for i, t in enumerate(texts):
            cached = self._text_cache.get((key_t, t))
            out.append(cached)
            if cached is None:
                missing.append(i)
        with torch.no_grad():
            for idx in missing:
                t = texts[idx]
                prompts = [tp.format(t) if "{}" in tp else f"{tp} {t}" for tp in templates]
                tokens = self.tokenizer(prompts)
                if self.device == "cuda":
                    tokens = tokens.cuda()
                f = self.model.encode_text(tokens)
                f = f / f.norm(dim=-1, keepdim=True)
                vec = f.float().mean(dim=0).cpu().numpy()
                vec = vec / max(1e-8, float(np.linalg.norm(vec)))
                self._text_cache[(key_t, t)] = vec
                out[idx] = vec
        arr = np.stack([e for e in out if e is not None], axis=0)
        arr /= np.clip(np.linalg.norm(arr, axis=1, keepdims=True), 1e-8, None)
        return arr

    def negatives(self, extra: list[str] | None = None) -> np.ndarray:
        if self._neg is None:
            self._neg = self.encode_texts(DEFAULT_NEGATIVES + list(extra or []), templates=["{}", "a photo of {}"])
        return self._neg

    # ---------- 打分 ----------
    def score(self, img_embs: np.ndarray, text_embs: np.ndarray, neg_embs: np.ndarray | None = None,
              scale: float = 100.0) -> np.ndarray:
        """返回 (N, T) 二值概率：该图"是否含此标签"。"""
        if img_embs.size == 0 or text_embs.size == 0:
            return np.zeros((img_embs.shape[0], text_embs.shape[0]), dtype=np.float32)
        sims = img_embs @ text_embs.T
        neg = neg_embs if neg_embs is not None else self.negatives()
        neg_sim = (img_embs @ neg.T).max(axis=1, keepdims=True) if neg.size else np.zeros((img_embs.shape[0], 1))
        z = (sims - neg_sim) * scale
        return 1.0 / (1.0 + np.exp(-z))
