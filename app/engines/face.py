"""人脸检测(SCRFD det_10g) + 特征(ArcFace w600k_r50)：真人照片按人物聚类/命名。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .. import models
from . import ort_providers, prepare_ort

ARCFACE_DST = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


class FaceEngine:
    def __init__(self, models_dir: Path | None = None, device: str = "auto",
                 det_threshold: float = 0.5, min_size: int = 32):
        self.models_dir = models_dir
        self.device = device
        self.det_threshold = det_threshold
        self.min_size = min_size
        self.det = None
        self.rec = None
        self._det_input = None
        self._rec_input = None
        self.input_size = 640

    def load(self, progress=None) -> None:
        if self.det is not None:
            return
        prepare_ort()
        import onnxruntime as ort
        from .. import perf
        det_path = models.ensure("face_det", self.models_dir, progress)
        rec_path = models.ensure("face_rec", self.models_dir, progress)
        prov = ort_providers(self.device)
        so = perf.ort_session_options()
        for path, attr in ((det_path, "det"), (rec_path, "rec")):
            try:
                sess = ort.InferenceSession(str(path), sess_options=so, providers=prov)
            except Exception:
                sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
            if attr == "det":
                self.det = sess
                self._det_input = sess.get_inputs()[0].name
                shp = sess.get_inputs()[0].shape
                self.input_size = int(shp[2]) if isinstance(shp[2], int) else 640
            else:
                self.rec = sess
                self._rec_input = sess.get_inputs()[0].name

    # ---------- 检测 ----------
    def _letterbox(self, img: np.ndarray):
        h, w = img.shape[:2]
        s = self.input_size / max(h, w)
        nh, nw = int(round(h * s)), int(round(w * s))
        import cv2
        resized = cv2.resize(img, (nw, nh))
        canvas = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
        canvas[:nh, :nw] = resized
        return canvas, s

    def detect(self, img: Image.Image) -> list[dict]:
        if self.det is None:
            self.load()
        import cv2
        rgb = np.asarray(img.convert("RGB"))
        canvas, scale = self._letterbox(rgb)
        blob = canvas[:, :, ::-1].astype(np.float32)          # BGR
        blob = (blob - 127.5) / 128.0
        blob = np.transpose(blob, (2, 0, 1))[None]
        outs = self.det.run(None, {self._det_input: blob})

        scores_l, bbox_l, kps_l = [], [], []
        for o in outs:
            a = np.asarray(o)
            if a.ndim == 3:
                a = a[0]                       # 这个模型没有 batch 维，兼容两种排布
            if a.ndim != 2:
                continue
            n, last = a.shape
            if last == 1:
                scores_l.append((n, a.reshape(-1)))
            elif last == 4:
                bbox_l.append((n, a.reshape(-1, 4)))
            elif last == 10:
                kps_l.append((n, a.reshape(-1, 5, 2)))
        if not scores_l:
            return []
        fmc = self.input_size // 8
        order = sorted(range(len(scores_l)), key=lambda i: -scores_l[i][0])

        boxes, scores, kpss = [], [], []
        for idx in order:
            num, sc = scores_l[idx]
            stride = int(round(self.input_size / (np.sqrt(num / 2))))
            w = h = self.input_size // stride
            cx = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            cx = (cx * stride).reshape(-1, 2)
            cx = np.repeat(cx, 2, axis=0)
            bbox = [b for n, b in bbox_l if n == num]
            kps = [k for n, k in kps_l if n == num]
            if not bbox:
                continue
            d = bbox[0]
            keep = np.where(sc > self.det_threshold)[0]
            if keep.size == 0:
                continue
            c = cx[keep]
            x1 = c[:, 0] - d[keep, 0] * stride
            y1 = c[:, 1] - d[keep, 1] * stride
            x2 = c[:, 0] + d[keep, 2] * stride
            y2 = c[:, 1] + d[keep, 3] * stride
            boxes.append(np.stack([x1, y1, x2, y2], axis=1))
            scores.append(sc[keep])
            if kps:
                k = kps[0][keep]
                pts = np.stack([c[:, 0:1] + k[:, :, 0] * stride, c[:, 1:2] + k[:, :, 1] * stride], axis=2)
                kpss.append(pts)
            else:
                kpss.append(np.zeros((keep.size, 5, 2), dtype=np.float32))
        if not boxes:
            return []
        boxes = np.concatenate(boxes) / scale
        scores = np.concatenate(scores)
        kpss = np.concatenate(kpss) / scale
        keep_idx = cv2.dnn.NMSBoxes(
            [list(map(float, b)) for b in boxes], [float(s) for s in scores], self.det_threshold, 0.4)
        out = []
        for i in np.asarray(keep_idx).reshape(-1):
            b = boxes[int(i)]
            if min(b[2] - b[0], b[3] - b[1]) < self.min_size:
                continue
            out.append({"bbox": b, "kps": kpss[int(i)], "score": float(scores[int(i)])})
        return out

    # ---------- 特征 ----------
    def embed(self, img: Image.Image, det: dict) -> np.ndarray | None:
        if self.rec is None:
            self.load()
        import cv2
        rgb = np.asarray(img.convert("RGB"))
        kps = det.get("kps")
        if kps is None or not np.any(kps):
            x1, y1, x2, y2 = det["bbox"]
            crop = rgb[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
            if crop.size == 0:
                return None
            face = cv2.resize(crop, (112, 112))
        else:
            M, _ = cv2.estimateAffinePartial2D(np.asarray(kps, dtype=np.float32), ARCFACE_DST, method=cv2.LMEDS)
            if M is None:
                return None
            face = cv2.warpAffine(rgb, M, (112, 112), borderValue=0.0)
        blob = face[:, :, ::-1].astype(np.float32)
        blob = (blob - 127.5) / 127.5
        blob = np.transpose(blob, (2, 0, 1))[None]
        emb = self.rec.run(None, {self._rec_input: blob})[0].reshape(-1).astype(np.float32)
        n = np.linalg.norm(emb)
        return emb / n if n > 1e-8 else None

    def analyze(self, img: Image.Image, max_faces: int = 8) -> list[np.ndarray]:
        dets = self.detect(img)[:max_faces]
        out = []
        for d in dets:
            e = self.embed(img, d)
            if e is not None:
                out.append(e)
        return out
