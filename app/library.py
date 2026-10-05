"""服务层：扫描库、打标编排、系列合并、把标签写回磁盘。"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import imaging, models, naming
from .config import IMAGE_EXTS, Settings
from .engines.clip_zs import ClipZeroShot
from .engines.face import FaceEngine
from .engines.wd14 import WD14Tagger
from .store import Store

# 内置的“画风/真人”判断标签（用 CLIP 零样本，默认开启）
BUILTIN_TAGS = [
    ("anime_style", "style", "an anime illustration, 2d drawing"),
    ("real_photo", "style", "a photograph of a real person, realistic photo"),
    ("3d_render", "style", "a 3d cg render"),
    ("manga_page", "style", "a scanned manga or comic book page"),
    ("screenshot", "style", "a screenshot of a software interface"),
]

# 写回文件名 / 系列文件夹名时忽略的噪声标签（仍然存在数据库里，只是不塞进文件名）
DISK_TAG_STOPLIST = {"general", "sensitive", "questionable", "explicit", "realistic", "absurdres",
                     "highres", "lowres", "bad_quality", "worst_quality", "jpeg_artifacts", "signature",
                     "watermark", "text", "logo", "artist_name", "resolution", "_d", "commentary"}
SOURCE_PRIORITY = {"manual": 0, "series": 1, "face": 2, "clip_region": 3, "probe": 4, "clip": 5, "wd14": 6}
# 系列文件夹名里更希望出现的标签类型（越靠前越优先）
CATEGORY_PRIORITY = {"character": 0, "real_person": 1, "series": 2, "clothing": 3, "count": 4,
                     "pose": 5, "action": 6, "scene": 7, "body": 8, "style": 9, "other": 10, "rating": 11}

# 按标签类型选择 CLIP 提示词模板：同一句“{}”对服装/人物名效果差很多
PROMPT_TEMPLATES: dict[str, list[str]] = {
    "character": ["{}", "the anime character {}", "a picture of {}", "{} (anime)"],
    "real_person": ["{}", "a photo of {}", "a portrait of {}", "the person named {}"],
    "clothing": ["{}, clothing", "wearing {}", "clothed in {}", "a person wearing {}"],
    "count": ["{}", "{} people", "a picture with {}"],
    "pose": ["{}, pose", "{} pose", "a person in {} pose", "{}"],
    "body": ["{}", "{} of the body", "visible {}"],
    "scene": ["{}", "in a {}", "{} background", "a scene of {}"],
    "style": ["{}", "{} art style", "an image in {} style"],
    "action": ["{}", "a person {}", "{} action"],
    "series": ["{}", "from the series {}"],
    "rating": ["{}"],
    "other": ["{}", "a photo of {}", "{} object"],
}


def prompt_templates(category: str, store=None) -> list[str]:
    """类型的 CLIP 提示词模板：优先用库里用户自定义的，其次内置默认。"""
    from . import categories as cats
    return cats.templates_for(store, category or "other")


def probe_score(embs, centroid, n_pos: int, max_w: float = 0.6):
    """用自训练的标签特征中心给图片打分（0~1）。"""
    import numpy as np
    if centroid is None or len(embs) == 0:
        return None
    sim = embs @ centroid
    p = np.clip((sim + 1.0) / 2.0, 0.0, 1.0)
    w = min(max_w, n_pos / 40.0)
    return p, w


# ---------------- 分级识别（对齐 pixiv：全年齢 / R-15 / R-18 / R-18G）----------------
RATING_CLIP_PROMPTS: dict[str, list[str]] = {
    "all_ages": ["a safe for work image", "a fully clothed person, no nudity",
                 "an ordinary non-sexual illustration"],
    "r15": ["a suggestive image, swimsuit or underwear", "a mildly ecchi pin-up, not explicit",
            "a revealing outfit but no nudity", "clothing showing breasts or buttocks, see-through clothes"],
    "r18": ["explicit sexual content", "nudity, sex, pornographic image",
            "an image with visible genitals or intercourse"],
    "r18g": ["guro, blood, mutilation, dismemberment", "grotesque horror with gore",
             "extreme violence and injury"],
}


def decide_rating(wd14: dict | None, clip_p: dict | None,
                  questionable_as_r18: bool = False) -> tuple[str | None, dict]:
    """融合 WD14 的 4 类分级与 CLIP 零样本分级，输出最终等级 + 全部分数。

    WD14 的 questionable（半露/性暗示）默认并入 R15；想更严格可把 questionable_as_r18 打开。
    """
    def g(d, k):
        try:
            return float((d or {}).get(k, 0.0))
        except Exception:
            return 0.0
    # WD14 原始类别 -> 最终等级
    wd_mapped = {
        "all_ages": g(wd14, "general"),
        "r15": max(g(wd14, "sensitive"), g(wd14, "questionable") if not questionable_as_r18 else 0.0),
        "r18": max(g(wd14, "explicit"), g(wd14, "questionable") if questionable_as_r18 else 0.0),
    }
    scores = {lv: (0.65 * wd_mapped.get(lv, 0.0) + 0.35 * g(clip_p, lv)) if (wd14 and clip_p)
              else (wd_mapped.get(lv, 0.0) if wd14 else g(clip_p, lv))
              for lv in ("all_ages", "r15", "r18")}
    scores["r18g"] = g(clip_p, "r18g")
    # 猎奇单独判断（和性向分级正交）
    if g(clip_p, "r18g") >= 0.55 or max(scores, key=scores.get) == "r18g":
        if scores["r18g"] >= 0.4:
            return "r18g", scores
    if wd14 and max(wd14.values() or [0]) > 0:
        top = max(wd14, key=wd14.get)
        lvl = {"general": "all_ages", "sensitive": "r15",
               "questionable": "r18" if questionable_as_r18 else "r15", "explicit": "r18"}.get(top, "all_ages")
        return lvl, scores
    if clip_p and max(clip_p.values() or [0]) > 0:
        return max(clip_p, key=clip_p.get), scores
    # 两个数据源都没有 → 不做判断（以前这里兜底成全年龄，是"全部全年龄"的元凶）
    return None, scores


@dataclass
class EngineHub:
    """按需加载的推理引擎集合。"""

    settings: Settings
    wd14: WD14Tagger | None = None
    clip: ClipZeroShot | None = None
    face: FaceEngine | None = None
    _loaded: set[str] = field(default_factory=set)

    def _md(self):
        return self.settings.models_path()

    def get_wd14(self, progress=None) -> WD14Tagger:
        if self.wd14 is None:
            mk, tk = models.TAGGER_CHOICES.get(self.settings.wd14_model,
                                              ("wd14_onnx", "wd14_tags"))
            self.wd14 = WD14Tagger(self._md(), self.settings.device, model_key=mk, tags_key=tk)
        if "wd14" not in self._loaded:
            self.wd14.load(progress)
            self._loaded.add("wd14")
        return self.wd14

    def get_clip(self, progress=None) -> ClipZeroShot:
        if self.clip is None:
            self.clip = ClipZeroShot(self._md(), self.settings.clip_model,
                                     self.settings.clip_pretrained, self.settings.device)
        if "clip" not in self._loaded:
            self.clip.load(progress)
            self._loaded.add("clip")
        return self.clip

    def get_face(self, progress=None) -> FaceEngine:
        if self.face is None:
            self.face = FaceEngine(self._md(), self.settings.device,
                                   self.settings.face_det_threshold, self.settings.face_min_size)
        if "face" not in self._loaded:
            self.face.load(progress)
            self._loaded.add("face")
        return self.face

    def unload(self, which: str | None = None) -> None:
        for name in ([which] if which else ["wd14", "clip", "face"]):
            setattr(self, name, None)
            self._loaded.discard(name)


class Library:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings

    # ------------------------------------------------------------------ 扫描
    def iter_images(self, root: Path):
        ignore = {d.lower() for d in self.settings.ignore_dirs}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in ignore and not d.startswith(".")]
            for fn in filenames:
                if Path(fn).suffix.lower() in IMAGE_EXTS:
                    yield Path(dirpath) / fn

    def scan_root(self, root_id: int, root_path: str | Path,
                  progress: Callable[[str, float], None] | None = None,
                  cancel: Callable[[], bool] | None = None) -> dict:
        root = Path(root_path)
        seen: set[str] = set()
        added = updated = 0
        files = list(self.iter_images(root))
        total = max(1, len(files))
        for i, p in enumerate(files):
            if cancel and cancel():
                break
            try:
                st = p.stat()
            except OSError:
                continue
            abs_p = str(p.resolve())
            seen.add(abs_p)
            base, name_tags = naming.split_name(p.stem)
            row = self.store.file_by_path(abs_p)
            rel = str(p.relative_to(root)).replace("\\", "/")
            if row is None:
                fid = self.store.upsert_file(root_id=root_id, path=abs_p, rel=rel, name=p.name,
                                             ext=p.suffix.lower(), size=st.st_size, mtime=st.st_mtime)
                added += 1
            else:
                fid = int(row["id"])
                if row["size"] != st.st_size or abs(row["mtime"] or 0) != abs(st.st_mtime):
                    self.store.upsert_file(path=abs_p, size=st.st_size, mtime=st.st_mtime,
                                           name=p.name, rel=rel)
                updated += 1
            if name_tags:
                self.store.add_file_tags(fid, [(t, "filename", 1.0) for t in name_tags])
            if i % 25 == 0:
                self.store.conn().commit()
                if progress:
                    progress(f"扫描 {root.name}", (i + 1) / total)
        gone = self.store.mark_missing(root_id, seen)
        self._link_series_from_dirs(root_id, root)
        self.store.refresh_counts()
        if progress:
            progress("扫描完成", 1.0)
        return {"images": len(files), "added": added, "seen": updated, "missing": gone}

    def _link_series_from_dirs(self, root_id: int, root: Path) -> None:
        """文件夹名带 [标签] 的目录视为系列，内部图片按文件名当页码。"""
        rows = self.store.query("SELECT id,path,rel FROM files WHERE root_id=? AND missing=0", (root_id,))
        groups: dict[str, list] = {}
        for r in rows:
            rel = Path(r["rel"])
            if rel.parent == Path("."):
                continue
            groups.setdefault(str(rel.parent).replace("\\", "/"), []).append(r)
        for rel_dir, items in groups.items():
            dirname = Path(rel_dir).name
            base, tags = naming.split_name(dirname)
            if not tags:
                continue
            sid = self.store.upsert_series(root_id, rel_dir, base, tags)
            for r in items:
                self.store.execute("UPDATE files SET series_id=?,kind='series_page' WHERE id=?",
                                   (sid, int(r["id"])))
            self.store.update_series_stats(sid)

    # -------------------------------------------------------------- 标签操作
    def add_tags_to_files(self, file_ids: Sequence[int], tags: Sequence[str], source: str = "manual",
                          score: float = 1.0) -> None:
        for fid in file_ids:
            self.store.add_file_tags(fid, [(t, source, score) for t in tags], override=(source == "manual"))
        if source == "manual":
            self.update_probes_for_tags(tags)
        self.store.refresh_counts()

    def remove_tags_from_files(self, file_ids: Sequence[int], tags: Sequence[str], purge: bool = False) -> None:
        """去掉标签：默认记为「已拒绝」（保留为负样本，让模型知道“这张图不是这个标签”）。"""
        for fid in file_ids:
            if purge:
                self.store.remove_file_tags(fid, tags)
            else:
                self.store.set_tag_status(fid, tags, "rejected")
        self.update_probes_for_tags(tags)
        self.store.refresh_counts()

    # -------------------------------------------------------------- 审核
    def pending_files(self, limit: int = 500):
        return self.store.pending_files(limit)

    def pending_summary(self) -> dict:
        return self.store.pending_summary()

    def review_file(self, file_id: int, decisions: dict) -> dict:
        """提交一张图的审核结果。decisions: {标签名: 'confirmed' | 'rejected' | 'delete'}"""
        for name, action in decisions.items():
            if action == "delete":
                self.store.remove_file_tags(file_id, [name])
            else:
                self.store.set_tag_status(file_id, [name], action)
        self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (file_id,))
        # 审核结论立刻回馈模型：确认=正样本，拒绝=负样本
        if decisions:
            self.update_probes_for_tags(list(decisions.keys()))
        self.store.refresh_counts()
        return {"file": file_id, "changed": len(decisions)}

    def finish_review(self, file_id: int, confirmed: Sequence[str], rejected: Sequence[str],
                      drop: Sequence[str], rating: str | None = None) -> dict:
        """「审核完毕」：通过/否决按你的判断写库，其余未决的标签直接丢弃。

        丢弃 = 删掉该行，既不生效也不参与模型训练（只有明确通过/否决才算反馈）。
        """
        for n in confirmed:
            self.store.set_tag_status(file_id, [n], "confirmed")
        for n in rejected:
            self.store.set_tag_status(file_id, [n], "rejected")
        if drop:
            self.store.remove_file_tags(file_id, list(drop))
        if rating:
            from .config import RATING_TAG
            self.store.execute("DELETE FROM file_tags WHERE file_id=? AND source='rating'", (file_id,))
            self.store.ensure_tag(RATING_TAG[rating], "rating", auto=0)
            self.store.add_file_tags(file_id, [(RATING_TAG[rating], "rating", 1.0)], status="confirmed")
            self.store.execute("UPDATE files SET rating=? WHERE id=?", (rating, file_id))
        self.store.execute("UPDATE files SET reviewed=1 WHERE id=?", (file_id,))
        feedback = list(confirmed) + list(rejected)
        if feedback:
            self.update_probes_for_tags(feedback)
        self.store.refresh_counts()
        return {"confirmed": len(confirmed), "rejected": len(rejected), "dropped": len(drop)}

    def tag_zh(self, name: str) -> str:
        row = self.store.one("SELECT zh FROM tags WHERE name=?", (name,))
        return (row["zh"] or "") if row else ""

    def set_tag_zh(self, name: str, zh: str) -> None:
        tid = self.store.tag_id(name)
        if tid:
            self.store.update_tag(tid, zh=zh.strip())

    def confirm_all_pending(self, file_id: int) -> int:
        n = self.store.set_all_pending_status(file_id, "confirmed")
        rows = self.store.tags_for_file(file_id, statuses=("confirmed",))
        self.update_probes_for_tags([r["name"] for r in rows])
        self.store.refresh_counts()
        return n

    def reject_all_pending(self, file_id: int) -> int:
        n = self.store.set_all_pending_status(file_id, "rejected")
        self.store.refresh_counts()
        return n

    # -------------------------------------------------- 自训练（越用越准）
    def update_probes_for_tags(self, tags: Sequence[str], logit_min_pos: int = 20, max_pos: int = 4000) -> int:
        """用已确认的标注更新每个标签的特征中心（1 个样本也能学，做 few-shot）。"""
        import numpy as np
        n_done = 0
        for name in tags:
            tid = self.store.tag_id(name)
            if not tid:
                continue
            rows = self.store.query(
                "SELECT f.clip_vec FROM files f JOIN file_tags ft ON ft.file_id=f.id "
                "WHERE ft.tag_id=? AND ft.status='confirmed' AND f.clip_vec IS NOT NULL LIMIT ?",
                (tid, max_pos))
            if len(rows) < 1:
                continue
            X = np.stack([np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32) for r in rows])
            X /= np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-8, None)
            c = X.mean(axis=0)
            c /= max(1e-8, float(np.linalg.norm(c)))
            self.store.set_probe(int(tid), "centroid", c.astype(np.float16).tobytes(), len(rows), 0)
            n_done += 1
            if len(rows) >= logit_min_pos:
                # 负样本 = 审核时明确拒绝的 + 已审核但不含该标签的
                neg = self.store.query(
                    "SELECT clip_vec FROM files WHERE clip_vec IS NOT NULL AND id IN ("
                    "  SELECT file_id FROM file_tags WHERE tag_id=? AND status='rejected' "
                    "  UNION "
                    "  SELECT id FROM files WHERE reviewed=1 AND id NOT IN "
                    "     (SELECT file_id FROM file_tags WHERE tag_id=? AND status IN ('confirmed','rejected'))"
                    ") LIMIT 400", (tid, tid))
                if len(neg) >= 20:
                    Y = np.stack([np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32) for r in neg])
                    Y /= np.clip(np.linalg.norm(Y, axis=1, keepdims=True), 1e-8, None)
                    try:
                        from sklearn.linear_model import LogisticRegression
                        clf = LogisticRegression(C=1.0, max_iter=400)
                        clf.fit(np.concatenate([X, Y]), np.concatenate([np.ones(len(X)), np.zeros(len(Y))]))
                        blob = clf.coef_.reshape(-1).astype(np.float32).tobytes() + np.float32(clf.intercept_[0]).tobytes()
                        self.store.set_probe(int(tid), "logit", blob, len(X), len(Y))
                    except Exception:
                        pass
        return n_done

    # -------------------------------------------------- 框选区域：作为该 tag 的训练样本
    @staticmethod
    def crop_box(img, x: float, y: float, w: float, h: float, pad: float = 0.12):
        """按归一化坐标裁剪区域（带一点外扩），太小的框自动放大到至少 48px。"""
        W, H = img.size
        if W < 2 or H < 2:
            return None
        px, py = w * pad, h * pad
        x0 = max(0.0, x - px) * W
        y0 = max(0.0, y - py) * H
        x1 = min(1.0, x + w + px) * W
        y1 = min(1.0, y + h + py) * H
        if x1 - x0 < 48:
            cx = (x0 + x1) / 2
            x0, x1 = max(0, cx - 24), min(W, cx + 24)
        if y1 - y0 < 48:
            cy = (y0 + y1) / 2
            y0, y1 = max(0, cy - 24), min(H, cy + 24)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        return img.crop((int(x0), int(y0), int(x1), int(y1)))

    def ensure_region_embeddings(self, file_ids: Sequence[int] | None = None, hub: EngineHub | None = None,
                                 progress=None, cancel=None) -> int:
        """给每个框内的区域算 CLIP 特征：这一步让「标签对应画面哪一块」真正进入模型。"""
        rows = self.store.regions_without_vec(file_ids)
        if not rows:
            return 0
        clip = hub.get_clip(progress)
        import numpy as np
        by_file: dict[str, list] = {}
        for r in rows:
            by_file.setdefault(r["path"], []).append(r)
        done = 0
        total = max(1, len(by_file))
        for i, (path, regions) in enumerate(by_file.items()):
            if cancel and cancel():
                break
            try:
                img = imaging.load_rgb(path, 1024)
            except Exception:
                continue
            crops, ids, cats = [], [], []
            for r in regions:
                c = self.crop_box(img, r["x"], r["y"], r["w"], r["h"])
                if c is None:
                    continue
                crops.append(c)
                ids.append(int(r["id"]))
                cats.append(r["tag_name"] or "")
                ctx = self.crop_box(img, r["x"], r["y"], r["w"], r["h"], pad=0.6)
                if ctx is not None:
                    crops.append(ctx)
            if not crops:
                continue
            embs = clip.encode_images(crops)
            # 每两个一组：奇数下标是「带上下文的外扩裁剪」
            for i, rid in enumerate(ids):
                tight = embs[i * 2]
                self.store.set_region_vec(rid, np.asarray(tight, dtype=np.float16).tobytes(), self.settings.clip_model)
                if i * 2 + 1 < len(embs):
                    self.store.set_region_vec_ctx(rid, np.asarray(embs[i * 2 + 1], dtype=np.float16).tobytes())
            done += len(ids)
            if progress:
                progress(f"框选区域特征 {i + 1}/{len(by_file)}", (i + 1) / total)
        # 更新“区域特征中心”
        self.update_region_probes()
        return done

    def update_region_probes(self, tags: Sequence[str] | None = None) -> int:
        """每个 tag 学三样东西：区域外观中心、区域上下文中心、以及“应该出现在哪”的位置先验。"""
        import numpy as np
        rows = self.store.query(
            "SELECT rg.*, t.id AS tag_id, t.name AS name FROM regions rg "
            "JOIN tags t ON t.id=rg.tag_id WHERE rg.clip_vec IS NOT NULL")
        groups: dict[tuple[int, str], list] = {}
        for r in rows:
            if tags and r["name"] not in tags:
                continue
            groups.setdefault((int(r["tag_id"]), r["name"]), []).append(r)
        n = 0
        for (tid, name), items in groups.items():
            if len(items) < 1:
                continue
            X = np.stack([np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32) for r in items])
            X /= np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-8, None)
            c = X.mean(axis=0)
            c /= max(1e-8, float(np.linalg.norm(c)))
            self.store.set_probe(tid, "region", c.astype(np.float16).tobytes(), len(items), 0)
            ctx_items = [r for r in items if r["clip_vec_ctx"]]
            if len(ctx_items) >= 1:
                C = np.stack([np.frombuffer(r["clip_vec_ctx"], dtype=np.float16).astype(np.float32) for r in ctx_items])
                C /= np.clip(np.linalg.norm(C, axis=1, keepdims=True), 1e-8, None)
                cc = C.mean(axis=0)
                cc /= max(1e-8, float(np.linalg.norm(cc)))
                self.store.set_probe(tid, "region_ctx", cc.astype(np.float16).tobytes(), len(ctx_items), 0)
            prior = self._region_prior(items)
            if prior:
                self.store.set_probe(tid, "region_prior", json.dumps(prior).encode("utf-8"), len(items), 0)
            n += 1
        return n

    def _region_prior(self, items) -> dict:
        """统计框中心分布：绝对位置 + （有脸时）相对人脸的位置。"""
        import numpy as np
        abs_c = [(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2) for r in items]
        A = np.array(abs_c, dtype=np.float32)
        prior: dict = {"abs": {"cx": float(A[:, 0].mean()), "cy": float(A[:, 1].mean()),
                               "sx": max(0.12, float(A[:, 0].std() or 0.12)),
                               "sy": max(0.12, float(A[:, 1].std() or 0.12)), "n": len(A)}}
        rel = []
        size_cache: dict[str, tuple[int, int] | None] = {}
        for r in items:
            file_id = int(r["file_id"])
            row = self.store.one("SELECT path FROM files WHERE id=?", (file_id,))
            if not row:
                continue
            path = row["path"]
            if path not in size_cache:
                size_cache[path] = imaging.image_size(path)
            size = size_cache[path]
            if not size or not size[0]:
                continue
            faces = self.store.query("SELECT bbox FROM faces WHERE file_id=?", (file_id,))
            if not faces:
                continue
            try:
                boxes = [json.loads(f["bbox"]) for f in faces]
            except Exception:
                continue
            if not boxes:
                continue
            b = max(boxes, key=lambda x: (x[2] - x[0]) * (x[3] - x[1]))
            W, H = size
            fx = (b[0] + b[2]) / 2 / W
            fy = (b[1] + b[3]) / 2 / H
            fw = max(1e-3, (b[2] - b[0]) / W)
            fh = max(1e-3, (b[3] - b[1]) / H)
            rel.append(((r["x"] + r["w"] / 2 - fx) / fw, (r["y"] + r["h"] / 2 - fy) / fh))
        if len(rel) >= 2:
            R = np.array(rel, dtype=np.float32)
            prior["rel"] = {"u": float(R[:, 0].mean()), "v": float(R[:, 1].mean()),
                            "su": max(0.8, float(R[:, 0].std() or 0.8)),
                            "sv": max(0.8, float(R[:, 1].std() or 0.8)), "n": len(R)}
        return prior

    def _region_probes(self) -> dict[int, tuple[str, "object", int]]:
        import numpy as np
        out = {}
        rows = self.store.query(
            "SELECT p.tag_id, p.data, p.n_pos, t.name FROM tag_probe p JOIN tags t ON t.id=p.tag_id "
            "WHERE p.kind='region' AND p.n_pos>=1")
        for r in rows:
            tid = int(r["tag_id"])
            ctx = self.store.get_probe(tid, "region_ctx")
            prior = self.store.get_probe(tid, "region_prior")
            tag = self.store.one("SELECT requires FROM tags WHERE id=?", (tid,))
            out[tid] = {
                "name": r["name"],
                "tight": np.frombuffer(r["data"], dtype=np.float16).astype(np.float32),
                "ctx": np.frombuffer(ctx["data"], dtype=np.float16).astype(np.float32) if ctx else None,
                "prior": json.loads(prior["data"].decode("utf-8")) if prior else None,
                "requires": [x for x in ((tag["requires"] if tag else "") or "").split() if x],
                "n": int(r["n_pos"]),
            }
        return out

    @staticmethod
    def candidate_windows(img):
        """候选窗口：整图/中间区域/四象限，每个窗口再配一份“带上下文”的外扩裁剪。"""
        W, H = img.size
        boxes = [(0.0, 0.0, 1.0, 1.0)]
        for s in (0.62, 0.42):
            boxes.append(((1 - s) / 2, (1 - s) / 2, s, s))
        for ox in (0.0, 0.45):
            for oy in (0.0, 0.45):
                boxes.append((ox, oy, 0.55, 0.55))
        out = []
        for (x, y, w, h) in boxes:
            cx, cy = x + w / 2, y + h / 2
            ew, eh = min(1.0, w * 1.9), min(1.0, h * 1.9)
            ex, ey = max(0.0, cx - ew / 2), max(0.0, cy - eh / 2)
            ew, eh = min(ew, 1.0 - ex), min(eh, 1.0 - ey)
            out.append({
                "box": (x, y, w, h), "cx": cx, "cy": cy,
                "crop": img.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))),
                "ctx": img.crop((int(ex * W), int(ey * H), int((ex + ew) * W), int((ey + eh) * H))),
            })
        return out

    def _face_anchor(self, file_id: int, size) -> tuple[float, float, float, float] | None:
        """最大人脸框的归一化位置，用作「相对人脸」的位置基准。"""
        if not size or not size[0]:
            return None
        rows = self.store.query("SELECT bbox FROM faces WHERE file_id=?", (file_id,))
        boxes = []
        for r in rows:
            try:
                boxes.append(json.loads(r["bbox"]))
            except Exception:
                continue
        if not boxes:
            return None
        b = max(boxes, key=lambda x: (x[2] - x[0]) * (x[3] - x[1]))
        W, H = size
        return ((b[0] + b[2]) / 2 / W, (b[1] + b[3]) / 2 / H,
                max(1e-3, (b[2] - b[0]) / W), max(1e-3, (b[3] - b[1]) / H))

    @staticmethod
    def prior_score(prior: dict | None, cx: float, cy: float, anchor) -> float:
        """位置先验：偏离该标签的典型位置就降权；有脸时用「相对人脸」的坐标更稳。"""
        import math
        if not prior:
            return 1.0
        scores = []
        rel = prior.get("rel")
        if rel and anchor:
            u = (cx - anchor[0]) / anchor[2]
            v = (cy - anchor[1]) / anchor[3]
            d = ((u - rel["u"]) / max(0.5, rel["su"])) ** 2 + ((v - rel["v"]) / max(0.5, rel["sv"])) ** 2
            scores.append(math.exp(-0.5 * d))
        ab = prior.get("abs")
        if ab:
            d = ((cx - ab["cx"]) / max(0.12, ab["sx"])) ** 2 + ((cy - ab["cy"]) / max(0.12, ab["sy"])) ** 2
            scores.append(math.exp(-0.5 * d))
        if not scores:
            return 1.0
        return float(max(0.08, max(scores)))

    def region_refine(self, file_ids: Sequence[int], hub: EngineHub, progress=None, cancel=None,
                      threshold: float | None = None, job_id: int | None = None) -> dict:
        """区域精修打标：外观相似度 × 上下文相似度 × 位置先验，再套「前置条件」过滤。"""
        import numpy as np
        probes = self._region_probes()
        if not probes:
            return {"tags": 0, "files": 0, "hits": 0}
        clip = hub.get_clip(progress)
        threshold = threshold if threshold is not None else self.settings.clip_threshold
        rows = self.store.files_by_ids(file_ids)
        hits_total = 0
        vetoed = 0
        for i, r in enumerate(rows):
            if cancel and cancel():
                break
            fid = int(r["id"])
            try:
                img = imaging.load_rgb(r["path"], 1024)
            except Exception:
                continue
            wins = self.candidate_windows(img)
            for rg in self.store.regions_for_file(fid):
                x, y, w, h = rg["x"], rg["y"], rg["w"], rg["h"]
                crop = self.crop_box(img, x, y, w, h, pad=0.0)
                if crop is None:
                    continue
                wins.append({"box": (x, y, w, h), "cx": x + w / 2, "cy": y + h / 2,
                             "crop": crop, "ctx": self.crop_box(img, x, y, w, h, pad=0.6) or crop})
            embs = clip.encode_images([w["crop"] for w in wins])
            ctxs = clip.encode_images([w["ctx"] for w in wins])
            anchor = self._face_anchor(fid, imaging.image_size(r["path"]))
            existing = {t["name"] for t in self.store.tags_for_file(fid, statuses=("confirmed", "pending"))
                        if t["source"] != "clip_region"}
            # 宽松判定：前置标签自己差一点没过阈值时也算满足，避免误伤（如“西装”需要“真人照片”）
            auto = self.store.get_auto_json(fid)
            satisfied = set(existing)
            satisfied |= {k for k, v in (auto.get("clip") or {}).items()
                          if v >= self.settings.clip_threshold * 0.8}
            satisfied |= {k for k, v in (auto.get("wd14") or {}).items()
                          if v >= self.settings.wd14_threshold * 0.8}
            hits = []
            for p in probes.values():
                app = np.clip((embs @ p["tight"] + 1.0) / 2.0, 0.0, 1.0)
                if p["ctx"] is not None:
                    ctxs_s = np.clip((ctxs @ p["ctx"] + 1.0) / 2.0, 0.0, 1.0)
                    comb = 0.6 * app + 0.4 * ctxs_s
                else:
                    comb = app
                best = -1.0
                for k, w in enumerate(wins):
                    s = float(comb[k]) * self.prior_score(p["prior"], w["cx"], w["cy"], anchor)
                    if s > best:
                        best = s
                if p["requires"] and not (set(p["requires"]) & satisfied):
                    best *= 0.25          # 前置条件不满足（例如“领带”没有“人物”）→ 基本否决
                    vetoed += 1
                need = threshold + (0.0 if p["n"] >= 4 else (0.10 if p["n"] >= 2 else 0.15))
                if best >= need:
                    hits.append((p["name"], "clip_region", best))
            self.store.remove_tags_by_source([fid], ["clip_region"])
            if hits:
                self.store.add_file_tags(fid, hits)
                hits_total += len(hits)
            self.store.execute("UPDATE files SET region_done=1 WHERE id=?", (fid,))
            self.job_tick(job_id, i + 1)
            if progress and i % 2 == 0:
                progress(f"区域精修 {i + 1}/{len(rows)}", (i + 1) / max(1, len(rows)))
        self.store.refresh_counts()
        return {"tags": len(probes), "files": len(rows), "hits": hits_total, "vetoed": vetoed}

    def replace_region(self, file_id: int, tag_name: str, box, hub: EngineHub | None = None) -> bool:
        """审核时修正偏差：用新框替换该标签在这张图上的旧框，并立即重算区域特征。"""
        try:
            x, y, w, h = (float(v) for v in box[:4])
        except Exception:
            return False
        if w < 0.01 or h < 0.01:
            return False
        for r in self.store.regions_for_file(file_id):
            if (r["tag_name"] or "") == tag_name:
                self.store.delete_region(int(r["id"]))
        tag = self.store.one("SELECT category FROM tags WHERE name=?", (tag_name,))
        self.store.add_region(file_id, tag_name, (x, y, w, h), category=(tag["category"] if tag else "other"))
        self.store.execute("UPDATE files SET region_done=0 WHERE id=?", (file_id,))
        if hub is not None:
            try:
                self.ensure_region_embeddings([file_id], hub)
                self.update_region_probes([tag_name])
            except Exception:
                pass
        return True

    def delete_region_of_tag(self, file_id: int, tag_name: str) -> int:
        n = 0
        for r in self.store.regions_for_file(file_id):
            if (r["tag_name"] or "") == tag_name:
                self.store.delete_region(int(r["id"]))
                n += 1
        if n:
            self.update_region_probes()
        return n

    # ================================================== 批量删除标签 / 学习包
    def delete_tags_bulk(self, names: Sequence[str], file_ids: Sequence[int] | None = None,
                         also_filename: bool = False, progress=None) -> dict:
        """批量删除标签（默认只删索引）。also_filename=True 时顺带把文件名里的标签也去掉。"""
        names = [n for n in names if n]
        if not names:
            return {"tags": 0, "files": 0, "renamed": 0}
        if file_ids:
            ids = [int(i) for i in file_ids]
        else:
            ph = ",".join("?" * len(names))
            ids = [int(r["file_id"]) for r in self.store.query(
                f"SELECT DISTINCT file_id FROM file_tags WHERE tag_id IN (SELECT id FROM tags WHERE name IN ({ph}))",
                list(names))]
        for fid in ids:
            self.store.remove_file_tags(fid, names)
        for n in names:
            tid = self.store.tag_id(n)
            if tid:
                self.store.delete_tag(tid)      # 连图谱连线一起清理
        self.store.refresh_counts()
        renamed = 0
        if also_filename and self.settings.tag_storage == "filename" and ids:
            res = self.apply_disk_names(ids, progress)
            renamed = res.get("renamed", 0)
        return {"tags": len(names), "files": len(ids), "renamed": renamed}

    # -------------------------------------------------- 学习包（跨库/跨实例合并反馈）
    def export_learning_pack(self, path: Path | str, with_probes: bool = True,
                             progress=None) -> dict:
        """导出「学习包」：汉化词典 + 标签类型 + 体系图（分类/从属连线） + 每个标签的自训练探针。

        重点是探针（特征中心/逻辑回归）：这样 A 库审核训练出来的判断，B 库导入后直接可用。
        """
        import base64
        out: dict = {"version": 1, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "clip_model": self.settings.clip_model,
                     "categories": [dict(c) for c in self.store.categories()],
                     "nodes": [dict(n) for n in self.store.list_nodes()],
                     "edges": [dict(e) for e in self.store.edges()],
                     "tags": [{"name": t["name"], "category": t["category"], "zh": t["zh"],
                               "prompt": t["prompt"], "requires": t["requires"], "auto": t["auto"]}
                              for t in self.store.list_tags()],
                     "probes": []}
        if with_probes:
            for p in self.store.query("SELECT tag_id, kind, data, n_pos, n_neg FROM tag_probe"):
                row = self.store.one("SELECT name FROM tags WHERE id=?", (int(p["tag_id"]),))
                if not row:
                    continue
                out["probes"].append({"tag": row["name"], "kind": p["kind"],
                                      "data": base64.b64encode(p["data"] or b"").decode("ascii"),
                                      "n_pos": int(p["n_pos"] or 0), "n_neg": int(p["n_neg"] or 0)})
        Path(path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return {"file": str(path), "tags": len(out["tags"]), "probes": len(out["probes"]),
                "edges": len(out["edges"])}

    def import_learning_pack(self, path: Path | str, progress=None) -> dict:
        """导入学习包并**合并**（不覆盖本地已有数据）：补标签中文名/类型、补体系连线、补探针。"""
        import base64
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        n_tag = n_edge = n_probe = 0
        for c in data.get("categories", []):
            if not self.store.one("SELECT 1 FROM categories WHERE key=?", (c["key"],)):
                self.store.add_category(c["key"], c["label"], c.get("templates") or ["{}"])
        for t in data.get("tags", []):
            tid = self.store.ensure_tag(t["name"], t.get("category") or "other",
                                       t.get("prompt") or None, int(t.get("auto", 1)))
            cur = self.store.one("SELECT zh,category FROM tags WHERE id=?", (tid,))
            if t.get("zh") and not (cur and cur["zh"]):
                self.store.update_tag(tid, zh=t["zh"])
                n_tag += 1
            if t.get("requires"):
                self.store.update_tag(tid, requires=t["requires"])
        name2id = {t["name"]: int(t["id"]) for t in self.store.list_tags()}
        for e in data.get("edges", []):
            try:
                if e.get("parent_kind") == "tag" and e.get("child_kind") == "tag":
                    pid, cid = e.get("parent_id"), e.get("child_id")
                    pn = next((nm for nm, i in name2id.items() if i == pid), None)
                    cn = next((nm for nm, i in name2id.items() if i == cid), None)
                    if pn and cn:
                        self.store.link_tag_sub(name2id[pn], name2id[cn])
                        n_edge += 1
            except Exception:
                continue
        for p in data.get("probes", []):
            tid = name2id.get(p["tag"])
            if not tid:
                tid = self.store.ensure_tag(p["tag"], "other")
                name2id[p["tag"]] = tid
            old = self.store.get_probe(tid, p["kind"])
            if old and int(old["n_pos"] or 0) >= int(p.get("n_pos") or 0):
                continue                     # 本地样本更多，保留本地的
            try:
                blob = base64.b64decode(p["data"])
            except Exception:
                continue
            self.store.set_probe(tid, p["kind"], blob, int(p.get("n_pos") or 0), int(p.get("n_neg") or 0))
            n_probe += 1
        self.store.refresh_counts()
        return {"tags_zh": n_tag, "sub_edges": n_edge, "probes": n_probe}

    def export_regions_yolo(self, out_dir: Path | str) -> dict:
        """把框选标注导出成 YOLO 数据集（images/ labels/ classes.txt），可直接拿去训练检测器。"""
        out = Path(out_dir)
        (out / "labels").mkdir(parents=True, exist_ok=True)
        rows = self.store.query("SELECT rg.*, f.path, f.name FROM regions rg JOIN files f ON f.id=rg.file_id")
        classes = sorted({(r["tag_name"] or "unknown") for r in rows})
        index = {c: i for i, c in enumerate(classes)}
        per_file: dict[str, list[str]] = {}
        for r in rows:
            cid = index[r["tag_name"] or "unknown"]
            cx = r["x"] + r["w"] / 2
            cy = r["y"] + r["h"] / 2
            per_file.setdefault(r["path"], []).append(
                f"{cid} {cx:.6f} {cy:.6f} {r['w']:.6f} {r['h']:.6f}")
        for path, lines in per_file.items():
            (out / "labels" / (Path(path).stem + ".txt")).write_text("\n".join(lines), encoding="utf-8")
        (out / "classes.txt").write_text("\n".join(classes), encoding="utf-8")
        (out / "images.txt").write_text("\n".join(per_file.keys()), encoding="utf-8")
        return {"files": len(per_file), "boxes": len(rows), "classes": len(classes), "dir": str(out)}

    def _probe_models(self, tag_rows) -> dict[int, tuple]:
        """给一批标签取出可用的自训练探针。返回 {列号: ('centroid'|'logit', 参数, 样本数)}。"""
        import numpy as np
        out = {}
        for i, (tid, _name) in enumerate(tag_rows):
            row = self.store.get_probe(tid, "logit")
            if row and row["n_pos"] >= 20:
                blob = np.frombuffer(row["data"], dtype=np.float32)
                if blob.size == 513:
                    out[i] = ("logit", (blob[:512], float(blob[512])), int(row["n_pos"]))
                    continue
            row = self.store.get_probe(tid, "centroid")
            if row and row["n_pos"] >= 1:
                vec = np.frombuffer(row["data"], dtype=np.float16).astype(np.float32)
                out[i] = ("centroid", vec, int(row["n_pos"]))
        return out

    def probe_confidence(self, tid: int) -> dict:
        """该标签目前的学习状态：样本数、推荐阈值提升量。"""
        logit = self.store.get_probe(tid, "logit")
        cent = self.store.get_probe(tid, "centroid")
        region = self.store.get_probe(tid, "region")
        n = max([int(r["n_pos"]) for r in (logit, cent, region) if r] or [0])
        return {"n": n, "logit": bool(logit), "centroid": bool(cent), "region": bool(region),
                "extra_threshold": 0.0 if n >= 5 else (0.12 if n >= 2 else 0.18)}

    # -------------------------------------------------- 以图找图（冷启动最快的方式）
    def find_similar(self, file_id: int, kind: str = "image", region_id: int | None = None,
                     limit: int = 120, progress=None, cancel=None) -> list[tuple[int, float]]:
        """给一张图或一个框，按 CLIP 特征在库里找相似图片。"""
        import numpy as np
        if kind == "region" and region_id:
            row = self.store.one("SELECT clip_vec FROM regions WHERE id=?", (region_id,))
        else:
            row = self.store.one("SELECT clip_vec FROM files WHERE id=?", (file_id,))
        if not row or not row["clip_vec"]:
            return []
        q = np.frombuffer(row["clip_vec"], dtype=np.float16).astype(np.float32)
        q /= max(1e-8, float(np.linalg.norm(q)))
        out: list[tuple[int, float]] = []
        offset, chunk = 0, 4000
        while True:
            if cancel and cancel():
                break
            rows = self.store.query(
                "SELECT id, clip_vec FROM files WHERE clip_vec IS NOT NULL AND missing=0 LIMIT ? OFFSET ?",
                (chunk, offset))
            if not rows:
                break
            X = np.stack([np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32) for r in rows])
            X /= np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-8, None)
            sims = X @ q
            for r, s in zip(rows, sims):
                if int(r["id"]) != int(file_id):
                    out.append((int(r["id"]), float(s)))
            offset += chunk
            if progress:
                progress(f"相似检索 {offset} 张…", -1.0)
        out.sort(key=lambda x: -x[1])
        return out[:limit]

    def suggest_tags(self, file_ids: Sequence[int]) -> list[str]:
        """从已标注的相似图片里推荐标签（基于 CLIP 特征最近邻）。"""
        if not file_ids:
            return []
        rows = self.store.query(
            "SELECT f.id, f.clip_vec, f.clip_model FROM files f WHERE f.id IN (%s) AND f.clip_vec IS NOT NULL"
            % ",".join("?" * len(file_ids)), list(file_ids))
        if not rows:
            return []
        import numpy as np
        targets = {}
        for r in rows:
            v = np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32)
            n = np.linalg.norm(v)
            if n > 0:
                targets[int(r["id"])] = v / n
        if not targets:
            return []
        others = self.store.query(
            "SELECT f.id, f.clip_vec FROM files f JOIN file_tags ft ON ft.file_id=f.id "
            "WHERE f.clip_vec IS NOT NULL AND f.manual=1")
        counter: dict[str, float] = {}
        for r in others:
            if int(r["id"]) in targets:
                continue
            v = np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32)
            n = np.linalg.norm(v)
            if n <= 0:
                continue
            v /= n
            sim = max(float(v @ t) for t in targets.values())
            if sim < 0.85:
                continue
            for t in self.store.tags_for_file(int(r["id"])):
                if t["source"] == "manual":
                    counter[t["name"]] = max(counter.get(t["name"], 0.0), sim)
        return [k for k, _ in sorted(counter.items(), key=lambda kv: -kv[1])[:20]]

    # ---------------------------------------------------------- 标签写回磁盘
    def _series_tag_union(self, series_id: int) -> list[str]:
        best: dict[str, tuple[int, int]] = {}
        for r in self.store.series_files(series_id):
            for t in self.store.tags_for_file(int(r["id"])):
                name = t["name"]
                if name.lower() in DISK_TAG_STOPLIST or t["category"] == "rating":
                    continue
                pri = SOURCE_PRIORITY.get(t["source"], 9)
                cat = CATEGORY_PRIORITY.get(t["category"], 9)
                old = best.get(name)
                if old is None or (pri, cat) < old:
                    best[name] = (pri, cat)
        ordered = sorted(best.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0].lower()))
        return [name for name, _ in ordered]

    def disk_tags_for_file(self, file_id: int, max_tags: int | None = None) -> list[str]:
        """挑出真正要写进文件名的标签：手动的优先，过滤噪声与分级标签，限制数量。"""
        max_tags = max_tags or self.settings.disk_max_tags
        rows = self.store.tags_for_file(file_id)
        scored: list[tuple[int, int, str]] = []
        for t in rows:
            if t["name"].lower() in DISK_TAG_STOPLIST or t["category"] == "rating":
                continue
            if t["name"].startswith("_"):
                continue
            pri = SOURCE_PRIORITY.get(t["source"], 9)
            scored.append((pri, CATEGORY_PRIORITY.get(t["category"], 9), t["name"]))
        scored.sort(key=lambda x: (x[0], x[1], x[2].lower()))
        return [name for _, _, name in scored[:max_tags]]

    def apply_disk_names(self, file_ids: Sequence[int],
                         progress: Callable[[str, float], None] | None = None) -> dict:
        """按当前标签重命名文件/系列文件夹（tag_storage=filename 时有效）。"""
        if self.settings.tag_storage != "filename":
            return {"renamed": 0, "skipped": len(file_ids), "errors": ["当前标签存储方式不是文件名"]}
        renamed, errors, touched_series = 0, [], set()
        files = self.store.files_by_ids(file_ids) if file_ids else []
        sid_map = {}
        for r in files:
            if r["series_id"]:
                sid_map[int(r["series_id"])] = True
        total = max(1, len(files) + len(sid_map))
        done = 0
        for r in files:
            if r["series_id"]:
                continue
            p = Path(r["path"])
            tags = self.disk_tags_for_file(int(r["id"]))
            base, _ = naming.split_name(p.stem)
            stem = naming.build_name(base, tags, keep_base=self.settings.rename_keep_original)
            new_path = p.with_name(stem + p.suffix.lower())
            k = 2
            while new_path.exists() and new_path.resolve() != p.resolve():
                new_path = p.with_name(f"{stem}_{k}{p.suffix.lower()}")
                k += 1
            if new_path != p:
                try:
                    p.rename(new_path)
                    self._update_path(int(r["id"]), new_path)
                    renamed += 1
                except OSError as e:
                    errors.append(f"{p.name}: {e}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{p.name}: {e}")
            done += 1
            if progress:
                progress("写回文件名", done / total)
        # 系列：标签合并到文件夹名
        for sid in sid_map:
            try:
                if self.rename_series_dir(sid):
                    renamed += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"系列 {sid}: {e}")
            done += 1
            if progress:
                progress("写回系列文件夹名", done / total)
        self.store.refresh_counts()
        return {"renamed": renamed, "skipped": 0, "errors": errors}

    def _update_path(self, file_id: int, new_path: Path) -> None:
        root = self.store.one("SELECT r.path FROM roots r JOIN files f ON f.root_id=r.id WHERE f.id=?", (file_id,))
        rel = str(new_path.relative_to(Path(root["path"]))).replace("\\", "/") if root else new_path.name
        try:
            self.store.execute("UPDATE files SET path=?,rel=?,name=?,ext=? WHERE id=?",
                               (str(new_path.resolve()), rel, new_path.name, new_path.suffix.lower(), file_id))
        except sqlite3.IntegrityError:
            # 目标路径已被别的行占用：给文件名加后缀，磁盘上一并改名，避免撞唯一约束
            other = self.store.one("SELECT id,path FROM files WHERE path=? AND id<>?",
                                   (str(new_path.resolve()), file_id))
            if not other:
                raise
            # 占用者已经从磁盘上消失（陈旧记录）→ 直接清掉，不让它挡住正常文件
            stale = self.store.one("SELECT missing FROM files WHERE id=?", (int(other["id"]),))
            if stale and int(stale["missing"] or 0) == 1:
                self.store.execute("DELETE FROM files WHERE id=?", (int(other["id"]),))
                self.store.execute("UPDATE files SET path=?,rel=?,name=?,ext=? WHERE id=?",
                                   (str(new_path.resolve()), rel, new_path.name, new_path.suffix.lower(), file_id))
                return
            i = 2
            cand = new_path
            while self.store.one("SELECT id FROM files WHERE path=? AND id<>?", (str(cand.resolve()), file_id)):
                cand = new_path.with_name(f"{new_path.stem}_{i}{new_path.suffix}")
                i += 1
            try:
                if new_path.exists() and not cand.exists():
                    new_path.rename(cand)
            except Exception:
                cand = new_path
            rel2 = str(cand.relative_to(Path(root["path"]))).replace("\\", "/") if root else cand.name
            self.store.execute("UPDATE files SET path=?,rel=?,name=?,ext=? WHERE id=?",
                               (str(cand.resolve()), rel2, cand.name, cand.suffix.lower(), file_id))

    def rename_series_dir(self, series_id: int) -> bool:
        s = self.store.one("SELECT * FROM series WHERE id=?", (series_id,))
        if not s:
            return False
        root = self.store.one("SELECT path FROM roots WHERE id=?", (int(s["root_id"]),))
        if not root:
            return False
        root_path = Path(root["path"])
        old_dir = root_path / Path(s["dir"])
        if not old_dir.exists():
            return False
        tags = self._series_tag_union(series_id)
        new_name = naming.build_series_dirname(s["name"] or old_dir.name, tags,
                                               max_tags=self.settings.series_folder_max_tags)
        if old_dir.name == new_name:
            self.store.execute("UPDATE series SET tags=? WHERE id=?", (" ".join(tags), series_id))
            return False
        new_dir = old_dir.with_name(new_name)
        i = 2
        def taken(p: Path, _i=i) -> bool:
            rel = str(p.relative_to(root_path)).replace("\\", "/")
            other = self.store.one("SELECT id FROM series WHERE root_id=? AND dir=? AND id<>?",
                                   (int(s["root_id"]), rel, series_id))
            return p.exists() or other is not None

        while taken(new_dir):
            new_dir = old_dir.with_name(f"{new_name} ({i})")
            i += 1
        old_dir.rename(new_dir)
        rel = str(new_dir.relative_to(root_path)).replace("\\", "/")
        self.store.execute("UPDATE series SET dir=?,name=?,tags=? WHERE id=?", (rel, naming.split_name(new_name)[0], " ".join(tags), series_id))
        for r in self.store.series_files(series_id):
            self._update_path(int(r["id"]), new_dir / Path(r["name"]))
        return True

    # ------------------------------------------------------------------ 系列
    def create_series(self, file_ids: Sequence[int], name: str, tags: Sequence[str],
                      order: Sequence[int] | None = None, mode: str | None = None,
                      digits: int | None = None, start: int = 1,
                      page_names: dict[int, str] | None = None,
                      progress: Callable[[str, float], None] | None = None) -> dict:
        files = self.store.files_by_ids(file_ids)
        if not files:
            return {"ok": False, "msg": "没有选中文件"}
        if order:
            pos = {int(fid): i for i, fid in enumerate(order)}
            files = sorted(files, key=lambda r: pos.get(int(r["id"]), 10 ** 9))
        else:
            files = sorted(files, key=lambda r: r["rel"].lower())
        roots = {int(r["root_id"]) for r in files}
        if len(roots) != 1:
            return {"ok": False, "msg": "所选图片必须来自同一个库（根目录）"}
        root = self.store.one("SELECT path FROM roots WHERE id=?", (roots.pop(),))
        root_path = Path(root["path"])
        parents = {str(Path(r["path"]).parent) for r in files}
        if len(parents) != 1:
            return {"ok": False, "msg": "所选图片必须位于同一个目录下"}
        parent = Path(parents.pop())
        digits = digits or self.settings.page_digits
        mode = mode or self.settings.series_move_mode

        dirname = naming.build_series_dirname(name, tags)
        target = parent / dirname
        i = 2
        while target.exists() and not target.is_dir():
            target = parent / f"{dirname} ({i})"
            i += 1
        target.mkdir(parents=True, exist_ok=True)

        moved: list[tuple[int, Path]] = []
        total = max(1, len(files))
        for idx, r in enumerate(files):
            if progress and idx % 5 == 0:
                progress(f"合并系列 {name}", idx / total)
            src = Path(r["path"])
            ext = src.suffix.lower()
            label = (page_names or {}).get(int(r["id"])) or naming.page_filename(start + idx, digits, "")
            dst = target / f"{label if label.startswith(str(Path(label).stem)) else label}{ext}"
            dst = target / f"{Path(label).stem}{ext}"
            k = 2
            while dst.exists() and src.resolve() != dst.resolve():
                dst = target / f"{Path(label).stem}_{k}{ext}"
                k += 1
            try:
                if mode == "move":
                    if src.resolve() != dst.resolve():
                        shutil.move(str(src), str(dst))
                else:
                    shutil.copy2(str(src), str(dst))
                    if self.settings.tag_storage == "filename" and src.exists() and not src.is_dir():
                        pass  # 保留原文件
                moved.append((int(r["id"]), dst))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "msg": f"复制/移动失败：{src.name} -> {e}"}

        rel_dir = str(target.relative_to(root_path)).replace("\\", "/")
        sid = self.store.upsert_series(int(files[0]["root_id"]), rel_dir, naming.split_name(target.name)[0], tags)
        for idx, (fid, dst) in enumerate(moved):
            self._update_path(fid, dst)
            self.store.execute("UPDATE files SET series_id=?,page_no=?,kind='series_page' WHERE id=?",
                               (sid, start + idx, fid))
        self.store.update_series_stats(sid)
        # 系列标签合并到文件夹名
        self.rename_series_dir(sid)
        self.store.refresh_counts()
        return {"ok": True, "series_id": sid, "dir": self.series_dir(sid), "count": len(moved)}

    def dissolve_series(self, series_id: int, keep_in_place: bool = True) -> dict:
        s = self.store.one("SELECT * FROM series WHERE id=?", (series_id,))
        if not s:
            return {"ok": False, "msg": "系列不存在"}
        self.store.execute("UPDATE files SET series_id=NULL,page_no=NULL,kind='image' WHERE series_id=?", (series_id,))
        self.store.execute("DELETE FROM series WHERE id=?", (series_id,))
        return {"ok": True}

    def series_page_start(self, series_id: int | None, target: Path | None = None) -> int:
        """下一页页码：优先看已有 series 记录，其次看目录里的纯数字文件名。"""
        n = 0
        if series_id:
            row = self.store.one("SELECT MAX(page_no) m FROM files WHERE series_id=?", (series_id,))
            n = int(row["m"] or 0)
        if target and target.exists():
            for p in target.iterdir():
                if p.is_file() and p.stem.isdigit():
                    n = max(n, int(p.stem))
        return n + 1

    def merge_into_series(self, file_ids: Sequence[int], name: str = "", tags: Sequence[str] | None = None,
                          series_id: int | None = None, mode: str = "move", digits: int | None = None,
                          start: int | None = None, page_names: dict | None = None,
                          order: Sequence[int] | None = None, progress=None, cancel=None) -> dict:
        """把若干图片并进系列（已存在的系列，或新建一个）。

        与 create_series 的区别：允许跨目录合并、允许把单图追加进已有系列，页码自动接着排。
        """
        rows = self.store.files_by_ids(file_ids)
        if not rows:
            return {"ok": False, "msg": "没有选中文件"}
        if order:
            pos = {int(fid): i for i, fid in enumerate(order)}
            rows = sorted(rows, key=lambda r: pos.get(int(r["id"]), 10 ** 9))
        else:
            rows = sorted(rows, key=lambda r: r["rel"].lower())
        digits = digits or self.settings.page_digits
        tags = list(tags or [])
        # ---- 目标目录 ----
        if series_id:
            s = self.store.one("SELECT * FROM series WHERE id=?", (series_id,))
            if not s:
                return {"ok": False, "msg": "系列不存在"}
            root = self.store.one("SELECT path FROM roots WHERE id=?", (int(s["root_id"]),))
            if not root:
                return {"ok": False, "msg": "系列所在图库不存在"}
            target = Path(root["path"]) / Path(s["dir"])
            name = name or s["name"] or target.name
            tags = tags or [t for t in (s["tags"] or "").split() if t]
        else:
            first = Path(rows[0]["path"])
            root = self.store.one("SELECT path FROM roots WHERE id=?", (int(rows[0]["root_id"]),))
            base = Path(root["path"]) if root else first.parent
            target = base / naming.build_series_dirname(name or first.parent.name or "系列", tags,
                                                       max_tags=self.settings.series_folder_max_tags)
        if target.exists() and not target.is_dir():
            return {"ok": False, "msg": f"目标已存在同名文件：{target}"}
        target.mkdir(parents=True, exist_ok=True)
        # 先确定系列记录（后面每张图要写 page_no），再逐张搬
        root_for_series = int(s["root_id"]) if series_id else int(rows[0]["root_id"])
        root_row = self.store.one("SELECT path FROM roots WHERE id=?", (root_for_series,))
        root_path0 = Path(root_row["path"]) if root_row else target.parent
        sid = series_id or self.store.upsert_series(
            root_for_series, str(target.relative_to(root_path0)).replace("\\", "/"),
            naming.split_name(target.name)[0], tags)
        # ---- 逐张移动 + 编号 ----
        page = start if start else self.series_page_start(series_id, target)
        moved, errors = 0, []
        detached: set[int] = set()
        for i, r in enumerate(rows):
            if cancel and cancel():
                break
            src = Path(r["path"])
            if not src.exists():
                errors.append(f"{src.name}: 文件不存在")
                continue
            # 原本属于别的系列：先摘出来，避免一页同时挂在两个系列上
            old_sid = int(r["series_id"]) if r["series_id"] else None
            if old_sid and old_sid != series_id:
                self.store.execute("UPDATE files SET series_id=NULL,page_no=NULL,kind='image' WHERE id=?",
                                   (int(r["id"]),))
                detached.add(old_sid)
            try:
                same_dir = False
                try:
                    same_dir = src.parent.resolve() == target.resolve()
                except Exception:
                    pass
                if same_dir and src.stem.isdigit() and series_id is not None:
                    # 已经在系列目录里：只更新页码，不搬动
                    self.store.execute("UPDATE files SET series_id=?,page_no=?,kind='series_page' WHERE id=?",
                                       (series_id, page, int(r["id"])))
                    page += 1
                    continue
                label = (page_names or {}).get(int(r["id"])) or naming.page_filename(page, digits, "")
                dst = target / f"{Path(label).stem}{src.suffix.lower()}"
                while dst.exists() or self.store.one("SELECT id FROM files WHERE path=? AND id<>?",
                                                     (str(dst.resolve()), int(r["id"]))):
                    page += 1
                    label = (page_names or {}).get(int(r["id"])) or naming.page_filename(page, digits, "")
                    dst = target / f"{Path(label).stem}{src.suffix.lower()}"
                if mode == "move":
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copy2(str(src), str(dst))
                self._update_path_to_root(int(r["id"]), dst, base if not series_id else Path(root["path"]),
                                          int(r["root_id"]) if not series_id else int(s["root_id"]))
                self.store.execute("UPDATE files SET series_id=?,page_no=?,kind='series_page' WHERE id=?",
                                   (sid, page, int(r["id"])))
                page += 1
                moved += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.name}: {e}")
            if progress:
                progress(f"并入系列 {i + 1}/{len(rows)}", (i + 1) / max(1, len(rows)))
        # ---- 系列记录 ----
        for old in detached:
            left = self.store.one("SELECT COUNT(*) c FROM files WHERE series_id=?", (old,))
            if not left or int(left["c"]) == 0:
                self.store.execute("DELETE FROM series WHERE id=?", (old,))
            else:
                self.store.update_series_stats(old)
        root_path = Path(self.store.one("SELECT path FROM roots WHERE id=?", (root_for_series,))["path"])
        rel_dir = str(target.relative_to(root_path)).replace("\\", "/")
        self.store.execute("UPDATE series SET dir=? WHERE id=?", (rel_dir, sid))
        for r in rows:
            row = self.store.one("SELECT series_id FROM files WHERE id=?", (int(r["id"]),))
            if row and not row["series_id"]:
                self.store.execute("UPDATE files SET series_id=?,kind='series_page' WHERE id=?",
                                   (sid, int(r["id"])))
        self.store.update_series_stats(sid)
        self.rename_series_dir(sid)
        self.store.refresh_counts()
        self._cleanup_empty_dirs({Path(r["path"]).parent for r in rows})
        return {"ok": True, "series_id": sid, "dir": self.series_dir(sid), "moved": moved, "errors": errors}

    def series_dir(self, series_id: int) -> str:
        """系列当前的绝对目录（文件夹名会被标签改写，所以要从库里重新取）。"""
        s = self.store.one("SELECT * FROM series WHERE id=?", (series_id,))
        if not s:
            return ""
        root = self.store.one("SELECT path FROM roots WHERE id=?", (int(s["root_id"]),))
        return str(Path(root["path"]) / Path(s["dir"])) if root else ""

    def export_captions(self, file_ids: Sequence[int], out_dir: Path | str | None = None,
                        include_rating: bool = True, mode: str = "kohya") -> dict:
        """导出 Stable Diffusion 训练用的字幕文件（每张图一个同名 .txt）。

        mode='kohya'：逗号分隔的一行（kohya_ss / sd-scripts 直接可用）
        mode='a1111'：同样逗号分隔，写进 <图名>.txt（WebUI 训练也认）
        只导出**已生效**的标签；可选附带分级标签（如 R18）。
        """
        from .config import RATING_TAG
        rows = self.store.files_by_ids(file_ids)
        written, empty = 0, 0
        for r in rows:
            fid = int(r["id"])
            names = [t["name"] for t in self.store.tags_for_file(fid, statuses=("confirmed",))
                     if t["category"] != "rating"]
            if include_rating and r["rating"]:
                names.append(RATING_TAG.get(r["rating"], r["rating"]))
            if not names:
                empty += 1
                continue
            src = Path(r["path"])
            target = (Path(out_dir) / (src.stem + ".txt")) if out_dir else src.with_suffix(".txt")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(", ".join(names), encoding="utf-8")
                written += 1
            except Exception:
                continue
        return {"written": written, "empty": empty, "total": len(rows)}

    def reorder_series(self, series_id: int, ordered_ids: Sequence[int],
                       digits: int | None = None, progress=None) -> dict:
        """按给定顺序重排系列页码，并把文件重命名成 001/002/…（两阶段改名，避免互相覆盖）。"""
        rows = self.store.series_files(series_id)
        if len(rows) < 2:
            return {"ok": False, "msg": "这个系列只有一页，不需要排序"}
        digits = digits or self.settings.page_digits
        by_id = {int(r["id"]): r for r in rows}
        order = [int(i) for i in ordered_ids if int(i) in by_id]
        for r in rows:                       # 没提到的页保持相对顺序放到最后
            if int(r["id"]) not in order:
                order.append(int(r["id"]))
        target_dir = Path(rows[0]["path"]).parent
        errors: list[str] = []
        # 第一阶段：全部改成临时名，避免 001 <-> 002 互换时互相覆盖
        staged: list[tuple[sqlite3.Row, Path, int]] = []
        for i, fid in enumerate(order):
            r = by_id[fid]
            src = Path(r["path"])
            if not src.exists():
                errors.append(f"{src.name}: 文件不存在")
                continue
            tmp = src.with_name(f".__imtag_tmp{i}{src.suffix.lower()}")
            try:
                if src.resolve() != tmp.resolve():
                    src.rename(tmp)
                    self._update_path(int(r["id"]), tmp)      # 数据库同步到临时名，避免“自己挡自己”
                staged.append((r, tmp, i + 1))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.name}: {e}")
        # 第二阶段：改成最终页码名
        for r, tmp, page in staged:
            dst = target_dir / naming.page_filename(page, digits, tmp.suffix.lower())
            k = 2
            staged_ids = [int(x[0]["id"]) for x in staged]
            ph = ",".join("?" * len(staged_ids))
            while dst.exists() or self.store.one(
                    f"SELECT id FROM files WHERE path=? AND id NOT IN ({ph})",
                    [str(dst.resolve()), *staged_ids]):
                dst = target_dir / f"{naming.page_filename(page, digits, '')}_{k}{tmp.suffix.lower()}"
                k += 1
            try:
                tmp.rename(dst)
                self._update_path(int(r["id"]), dst)
                self.store.execute("UPDATE files SET page_no=?,kind='series_page',series_id=? WHERE id=?",
                                   (page, series_id, int(r["id"])))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{tmp.name}: {e}")
            if progress:
                progress(f"重排页码 {page}/{len(staged)}", page / max(1, len(staged)))
        self.store.update_series_stats(series_id)
        self.store.refresh_counts()
        return {"ok": True, "count": len(staged), "errors": errors, "dir": self.series_dir(series_id)}

    def update_series(self, series_id: int, name: str | None = None, tags: Sequence[str] | None = None) -> None:
        s = self.store.one("SELECT * FROM series WHERE id=?", (series_id,))
        if not s:
            return
        new_name = name if name is not None else s["name"]
        if tags is not None:
            for r in self.store.series_files(series_id):
                self.store.add_file_tags(int(r["id"]), [(t, "series", 1.0) for t in tags])
        self.store.execute("UPDATE series SET name=? WHERE id=?", (new_name, series_id))
        self.rename_series_dir(series_id)

    # --------------------------------------------------------------- 自动打标
    def _tag_lookup(self) -> dict[str, tuple[str, str]]:
        """WD14 英文 tag -> (用户标签名, 分类)；用户可用 prompt 指定 WD14 名。"""
        out: dict[str, tuple[str, str]] = {}
        for t in self.store.list_tags():
            name, cat = t["name"], t["category"]
            for key in (t["name"], t["prompt"] or ""):
                k = (key or "").strip().lower().replace(" ", "_")
                if k:
                    out.setdefault(k, (name, cat))
                k2 = (key or "").strip().lower().replace("_", " ")
                if k2:
                    out.setdefault(k2, (name, cat))
        return out

    def _split_tag_rows(self):
        """把可用于 CLIP 的标签按类型分组，方便套用不同提示词模板。"""
        groups: dict[str, list[tuple[int, str, str]]] = {}
        for t in self.store.list_tags():
            if not t["auto"]:
                continue
            # WD14 自动生成的英文词表标签不重复丢给 CLIP（噪声大、也没必要）
            if t["category"] == "rating" or t["name"].lower() in DISK_TAG_STOPLIST:
                continue
            cat = t["category"] or "other"
            prompt = (t["prompt"] or "").strip()
            groups.setdefault(cat, []).append((int(t["id"]), t["name"], prompt))
        return groups

    def _prompt_args(self, name: str, prompt: str, category: str):
        """返回 (送入 CLIP 的文本, 模板列表)。"""
        if prompt and ("," in prompt or len(prompt.split()) > 4):
            return prompt, ["{}"]      # 用户写的是整句 prompt
        text = prompt or name.replace("_", " ")
        return text, prompt_templates(category, self.store)

    def run_wd14(self, file_ids: Sequence[int], hub: EngineHub,
                 progress: Callable[[str, float], None] | None = None,
                 cancel: Callable[[], bool] | None = None,
                 per_file: Callable[[int, dict], None] | None = None,
                 batch: int | None = None, job_id: int | None = None) -> int:
        tagger = hub.get_wd14(progress)
        from . import perf
        if not batch:
            batch = perf.batches()[0]
        idle = perf.sleep_between_batches()
        from .engines.wd14 import guess_category
        lookup = self._tag_lookup()
        rows = self.store.files_by_ids(file_ids)
        total = max(1, len(rows))
        done = 0
        for i in range(0, len(rows), batch):
            if cancel and cancel():
                break
            chunk = rows[i:i + batch]
            imgs, ids = [], []
            for r in chunk:
                try:
                    imgs.append(imaging.load_rgb(r["path"], 1024))
                    ids.append(int(r["id"]))
                except Exception:
                    continue
            if not imgs:
                continue
            res, rating_probs = tagger.predict(imgs, self.settings.wd14_threshold,
                                               self.settings.wd14_char_threshold,
                                               self.settings.wd14_max_tags,
                                               self.settings.wd14_keep_rating, return_rating=True)
            for fid, tags, rp in zip(ids, res, rating_probs):
                mapped: list[tuple[str, str, float]] = []
                for n, s in tags.items():
                    hit = lookup.get(n.lower()) or lookup.get(n.lower().replace("_", " "))
                    if hit:
                        mapped.append((hit[0], "wd14", s))
                    else:
                        mapped.append((n, "wd14", s))
                        # WD14 自建的标签默认不参与 CLIP（避免把整个英文词表都丢给 CLIP 打分）
                        self.store.ensure_tag(n, guess_category(n, 0), auto=0)
                self.store.add_file_tags(fid, mapped)
                self.store.execute("UPDATE files SET wd_done=1 WHERE id=?", (fid,))
                auto = self.store.get_auto_json(fid)
                auto["wd14"] = tags
                auto["rating_wd14"] = rp
                self.store.set_auto_json(fid, auto)
                # 分级不在这里下结论：等 CLIP 也跑完，由 run_rating 一起判定（避免只有半份证据就定级）
                if per_file:
                    per_file(fid, {m[0]: m[2] for m in mapped})
            done += len(chunk)
            self.job_tick(job_id, done)
            if progress:
                progress(f"WD14 识别 {done}/{len(rows)}", done / total)
            if idle:
                time.sleep(idle)
        self.store.refresh_counts()
        return done

    def ensure_clip_embeddings(self, file_ids: Sequence[int], hub: EngineHub,
                               progress: Callable[[str, float], None] | None = None,
                               cancel: Callable[[], bool] | None = None, batch: int | None = None,
                               job_id: int | None = None) -> int:
        from . import perf
        if not batch:
            batch = perf.batches()[1]
        rows = [r for r in self.store.files_by_ids(file_ids)
                if not r["clip_vec"] or r["clip_model"] != self.settings.clip_model]
        if not rows:
            return 0
        clip = hub.get_clip(progress)
        import numpy as np
        total = max(1, len(rows))
        done = 0
        for i in range(0, len(rows), batch):
            if cancel and cancel():
                break
            chunk = rows[i:i + batch]
            imgs, ids = [], []
            for r in chunk:
                try:
                    imgs.append(imaging.load_rgb(r["path"], 512))
                    ids.append(int(r["id"]))
                except Exception:
                    continue
            if not imgs:
                continue
            embs = clip.encode_images(imgs)
            for fid, v in zip(ids, embs):
                self.store.execute("UPDATE files SET clip_vec=?,clip_model=?,clip_done=1 WHERE id=?",
                                   (np.asarray(v, dtype=np.float16).tobytes(), self.settings.clip_model, fid))
            done += len(chunk)
            self.job_tick(job_id, done)
            if progress:
                progress(f"CLIP 特征 {done}/{len(rows)}", done / total)
        return done

    def auto_tags_from_clip(self, hub: EngineHub, only_files: Sequence[int] | None = None,
                            progress=None, cancel=None) -> int:
        """用 CLIP 零样本给（全部/指定）图片打自定义标签。"""
        clip = hub.get_clip(progress)
        import numpy as np
        groups = self._split_tag_rows()
        if not groups:
            return 0
        # 顺序：按类型分组编码文本向量，再拼回与 tag_rows 对应的顺序
        order: list[tuple[int, str]] = []
        cols: list[np.ndarray] = []
        for cat, items in groups.items():
            texts = []
            per_tag_templates = []
            for _tid, name, prompt in items:
                text, tmpl = self._prompt_args(name, prompt, cat)
                texts.append(text)
                per_tag_templates.append(tmpl)
            # 模板不一致时逐个编码（数量小，代价可接受）
            embs = np.stack([clip.encode_texts([t], tmpl)[0] for t, tmpl in zip(texts, per_tag_templates)])
            for (_tid, name, _p), _e in zip(items, embs):
                order.append((_tid, name))
            cols.append(embs)
        text_embs = np.concatenate(cols, axis=0)
        tag_rows = order
        neg = clip.negatives()
        sql = "SELECT id,clip_vec FROM files WHERE clip_vec IS NOT NULL AND clip_model=?"
        args: list = [self.settings.clip_model]
        if only_files:
            sql += " AND id IN (%s)" % ",".join("?" * len(only_files))
            args.extend(only_files)
        rows = self.store.query(sql, args)
        total = max(1, len(rows))
        applied = 0
        chunk = 64
        probe_models = self._probe_models(tag_rows)
        for i in range(0, len(rows), chunk):
            if cancel and cancel():
                break
            part = rows[i:i + chunk]
            embs = np.stack([np.frombuffer(r["clip_vec"], dtype=np.float16).astype(np.float32) for r in part])
            scores = clip.score(embs, text_embs, neg)
            # 自训练探针：把“你确认过的标注”学到的判据并进来（只增强、不削弱；
            # 样本少时用一个更严的阈值兜住精度，所以 1 个框也能用）
            thr = np.full(len(tag_rows), float(self.settings.clip_threshold))
            for k, (kind, params, n_pos) in probe_models.items():
                if kind == "logit":
                    coef, b = params
                    p = 1.0 / (1.0 + np.exp(-(embs @ coef + b)))
                    thr[k] += 0.0
                else:
                    p = np.clip((embs @ params + 1.0) / 2.0, 0.0, 1.0)
                    thr[k] += 0.0 if n_pos >= 5 else (0.12 if n_pos >= 2 else 0.18)
                scores[:, k] = np.maximum(scores[:, k], p)
            for j, r in enumerate(part):
                fid = int(r["id"])
                hits = [(tag_rows[k][1], "clip", float(scores[j, k]))
                        for k in range(len(tag_rows)) if scores[j, k] >= thr[k]]
                self.store.remove_tags_by_source([fid], ["clip"])
                if hits:
                    self.store.add_file_tags(fid, hits)
                    applied += len(hits)
                # 记录全部打分：供“前置条件”宽松判定与后续调阈值使用
                auto = self.store.get_auto_json(fid)
                auto["clip"] = {tag_rows[k][1]: round(float(scores[j, k]), 3) for k in range(len(tag_rows))}
                self.store.set_auto_json(fid, auto)
            if progress:
                progress(f"CLIP 打标 {min(i + chunk, len(rows))}/{len(rows)}", (i + chunk) / total)
        self.store.refresh_counts()
        return applied

    def rescore_all_clip(self, hub: EngineHub, progress=None, cancel=None) -> int:
        """标签变化后，用已缓存的图片特征重新打分（秒级，无需重读图片）。"""
        return self.auto_tags_from_clip(hub, None, progress, cancel)

    # ------------------------------------------------------------------ 人脸
    def run_face(self, file_ids: Sequence[int], hub: EngineHub, progress=None, cancel=None,
                 per_file=None, job_id: int | None = None) -> int:
        engine = hub.get_face(progress)
        import numpy as np
        rows = self.store.files_by_ids(file_ids)
        total = max(1, len(rows))
        for i, r in enumerate(rows):
            if cancel and cancel():
                break
            fid = int(r["id"])
            try:
                img = imaging.load_rgb(r["path"], 1600)
                dets = engine.detect(img)
            except Exception:
                dets = []
            self.store.clear_faces(fid)
            for d in dets:
                emb = engine.embed(img, d)
                if emb is None:
                    continue
                self.store.add_face(fid, [float(x) for x in d["bbox"]], float(d["score"]),
                                    np.asarray(emb, dtype=np.float16).tobytes())
            self.store.execute("UPDATE files SET face_done=1 WHERE id=?", (fid,))
            self.job_tick(job_id, i + 1)
            if per_file:
                per_file(fid, len(dets))
            if progress and i % 5 == 0:
                progress(f"人脸检测 {i + 1}/{len(rows)}", (i + 1) / total)
        return len(rows)

    def cluster_faces(self, eps: float | None = None, progress=None) -> dict:
        """把所有人脸特征聚类成人物；返回聚类数量。"""
        import numpy as np
        eps = eps if eps is not None else self.settings.face_cluster_eps
        rows = self.store.query("SELECT id,emb FROM faces")
        if len(rows) < 2:
            return {"clusters": 0, "faces": len(rows)}
        X = np.stack([np.frombuffer(r["emb"], dtype=np.float16).astype(np.float32) for r in rows])
        X /= np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-8, None)
        try:
            from sklearn.cluster import AgglomerativeClustering
            model = AgglomerativeClustering(n_clusters=None, metric="cosine",
                                            linkage="average", distance_threshold=eps)
            labels = model.fit_predict(X)
        except Exception:
            labels = self._greedy_cluster(X, 1.0 - eps)
        # 保留已命名的人物：把新聚类结果与旧标签对齐
        old = {int(r["id"]): r["person_id"] for r in self.store.query("SELECT id,person_id FROM faces")}
        name_of_person = {int(p["id"]): p["name"] for p in self.store.persons()}
        groups: dict[int, list[int]] = {}
        for label, r in zip(labels, rows):
            groups.setdefault(int(label), []).append(int(r["id"]))
        self.store.execute("UPDATE faces SET person_id=NULL")
        self.store.execute("DELETE FROM persons WHERE name IS NULL")
        n = 0
        for label, fids in groups.items():
            if len(fids) < 2:
                continue
            # 找一个已有名字（若该簇包含旧命名人脸）
            votes: dict[int, int] = {}
            for fid in fids:
                op = old.get(fid)
                if op:
                    votes[op] = votes.get(op, 0) + 1
            pid = None
            if votes:
                pid = max(votes, key=votes.get)
            if pid is None:
                cur = self.store.execute("INSERT INTO persons(name,updated_at) VALUES(NULL,?)", (time.time(),))
                pid = int(cur.lastrowid)
            self.store.set_face_person(fids, pid)
            n += 1
            if progress:
                progress("人脸聚类", min(1.0, n / max(1, len(groups))))
        for p in self.store.persons():
            cnt = self.store.one("SELECT COUNT(DISTINCT file_id) c FROM faces WHERE person_id=?", (int(p["id"]),))
            self.store.execute("UPDATE persons SET count=?,updated_at=? WHERE id=?",
                               (int(cnt["c"]) if cnt else 0, time.time(), int(p["id"])))
        return {"clusters": n, "faces": len(rows)}

    @staticmethod
    def _greedy_cluster(X, max_dist: float):
        import numpy as np
        labels = np.full(len(X), -1, dtype=int)
        centroids: list[np.ndarray] = []
        for i, v in enumerate(X):
            if not centroids:
                centroids.append(v)
                labels[i] = 0
                continue
            sims = np.stack(centroids) @ v
            j = int(np.argmax(sims))
            if 1.0 - float(sims[j]) <= max_dist:
                labels[i] = j
                centroids[j] = (centroids[j] * 0.8 + v * 0.2)
                centroids[j] /= max(1e-8, float(np.linalg.norm(centroids[j])))
            else:
                centroids.append(v)
                labels[i] = len(centroids) - 1
        return labels

    def name_person(self, person_id: int, name: str, category: str = "real_person") -> None:
        name = name.strip()
        self.store.execute("UPDATE persons SET name=?,updated_at=? WHERE id=?", (name, time.time(), person_id))
        tid = self.store.ensure_tag(name, category, auto=1)
        self.store.execute("UPDATE persons SET tag_id=? WHERE id=?", (tid, person_id))
        files = self.store.query("SELECT DISTINCT file_id FROM faces WHERE person_id=?", (person_id,))
        for f in files:
            self.store.add_file_tags(int(f["file_id"]), [(name, "face", 1.0)])
        self.store.refresh_counts()

    def merge_persons(self, src_id: int, dst_id: int) -> None:
        self.store.set_face_person([int(r["id"]) for r in self.store.query("SELECT id FROM faces WHERE person_id=?", (src_id,))], dst_id)
        self.store.execute("DELETE FROM persons WHERE id=?", (src_id,))
        self.store.refresh_counts()

    # ------------------------------------------------------------ 首次建库
    def ensure_builtin_tags(self) -> None:
        for name, cat, prompt in BUILTIN_TAGS:
            self.store.ensure_tag(name, cat, prompt)

    # -------------------------------------------------- 标签整理（自动分类 + 中文备注）
    def auto_organize_tags(self, progress=None) -> dict:
        """把 WD14 打出来的一堆「其它」标签自动分类，并给它们补中文备注。

        - 分类：用关键词启发式（服装/身体/姿势/场景/画风/人数…），只动 category='other' 的；
        - 中文名：写进 tags.zh（用户手填过的不会被覆盖），界面按「备注（标签）」显示。
        """
        from .engines.wd14 import guess_category
        from . import tag_i18n
        n_cat = n_zh = 0
        rows = self.store.query("SELECT id,name,category,zh FROM tags")
        for i, t in enumerate(rows):
            name = t["name"]
            if t["category"] in (None, "", "other"):
                cat = guess_category(name, 0)
                if cat != "other":
                    self.store.update_tag(int(t["id"]), category=cat)
                    n_cat += 1
            if not (t["zh"] or "").strip():
                zh = tag_i18n.translate(name)
                if zh and zh != name:
                    self.store.update_tag(int(t["id"]), zh=zh)
                    n_zh += 1
            if progress and i % 500 == 0:
                progress(f"整理标签 {i + 1}/{len(rows)}", (i + 1) / max(1, len(rows)))
        self.store.refresh_counts()
        return {"categorized": n_cat, "zh": n_zh, "total": len(rows)}

    def cleanup_missing(self, progress=None) -> dict:
        """清理失效路径（修：删掉的文件夹/库不再留在界面里）。

        - 根目录已不复存在 → 删除该库根（连同它的文件与系列记录）；
        - 库还在、但文件/子目录已被删 → 把文件标记 missing（界面与检索立即不再显示）。
        """
        from pathlib import Path as _P
        roots_removed, files_missing = [], 0
        for r in self.store.list_roots():
            if not _P(r["path"]).exists():
                self.store.remove_root(int(r["id"]))
                roots_removed.append(r["path"])
                if progress:
                    progress(f"移除失效库目录 {r['path']}", -1.0)
                continue
            rows = self.store.query("SELECT id,path FROM files WHERE root_id=? AND missing=0",
                                    (int(r["id"]),))
            for row in rows:
                if not _P(row["path"]).exists():
                    self.store.execute("UPDATE files SET missing=1 WHERE id=?", (int(row["id"]),))
                    files_missing += 1
        self.store.refresh_counts()
        return {"roots_removed": roots_removed, "files_missing": files_missing}

    def tags_under_node(self, node_id: int) -> list[str]:
        """分类节点下的所有标签（含子分类，支持多级继承）——用于"按大类筛选"。"""
        seen_nodes, out = set(), []
        stack = [int(node_id)]
        while stack:
            nid = stack.pop()
            if nid in seen_nodes:
                continue
            seen_nodes.add(nid)
            for e in self.store.query(
                    "SELECT child_kind,child_id FROM taxonomy_edges WHERE parent_kind='node' AND parent_id=?",
                    (nid,)):
                if e["child_kind"] == "node":
                    stack.append(int(e["child_id"]))
                else:
                    row = self.store.one("SELECT name FROM tags WHERE id=?", (int(e["child_id"]),))
                    if row and row["name"] not in out:
                        out.append(row["name"])
        if not out:      # 没连线时退回"按类型"：该节点名等于某个类型标签时，取该类型全部标签
            node = self.store.node(node_id)
            if node:
                for c in self.store.categories():
                    if c["label"] == node["name"] or c["key"] == node["name"]:
                        out += [t["name"] for t in self.store.list_tags() if t["category"] == c["key"]]
        return out

    # -------------------------------------------------- 图谱 → 分类（连线即改分类）
    def sync_tag_categories_from_graph(self, tag_id: int | None = None) -> int:
        """按图谱里的连线更新标签的分类。

        一个标签可能连到多个分类节点（多分类），这里取第一个作为主分类写进 tags.category，
        其余分类仍然保留在图谱关系里（标签管理界面会显示全部分类）。
        """
        n = 0
        tag_ids = [tag_id] if tag_id else [int(r["id"]) for r in self.store.query("SELECT id FROM tags")]
        for tid in tag_ids:
            keys: list[str] = []
            for p in self.store.parents_of_tag(int(tid)):
                key = self.store.category_key_by_label(p["name"])
                if key and key not in keys:
                    keys.append(key)
            if keys:
                row = self.store.one("SELECT category FROM tags WHERE id=?", (int(tid),))
                if not row or row["category"] != keys[0]:
                    self.store.update_tag(int(tid), category=keys[0])
                    n += 1
        self.store.refresh_counts()
        return n

    def tag_categories_map(self) -> dict[int, list[str]]:
        """tag_id -> 图谱里连着的全部分类名（供标签管理显示多分类）。"""
        out: dict[int, list[str]] = {}
        for r in self.store.query(
                "SELECT e.child_id AS tid, n.name AS name FROM taxonomy_edges e "
                "JOIN nodes n ON n.id=e.parent_id WHERE e.child_kind='tag' ORDER BY n.sort, n.name"):
            out.setdefault(int(r["tid"]), []).append(r["name"])
        return out

    def relink_all_categories(self, progress=None) -> dict:
        """按 tags.category 重建"分类边"：删掉与该标签当前分类不一致的旧连线，补上正确的连线。

        批量改分类之后必须调用它，否则图谱里的旧连线会把分类改回去（sync_tag_categories_from_graph 是
        给"手动连线"用的反向同步，方向相反，别混用）。
        """
        node_key: dict[int, str] = {}
        key_node: dict[str, int] = {}
        for n in self.store.list_nodes():
            k = self.store.category_key_by_label(n["name"])
            if k:
                node_key[int(n["id"])] = k
                key_node.setdefault(k, int(n["id"]))
        fixed = dropped = 0
        for t in self.store.list_tags():
            tid, cat = int(t["id"]), t["category"]
            for e in self.store.query(
                    "SELECT id,parent_id FROM taxonomy_edges WHERE child_kind='tag' AND child_id=?", (tid,)):
                k = node_key.get(int(e["parent_id"]))
                if k and k != cat:
                    self.store.unlink_edge(int(e["id"]))
                    dropped += 1
            nid = key_node.get(cat)
            if nid and not self.store.one(
                    "SELECT 1 FROM taxonomy_edges WHERE parent_kind='node' AND parent_id=? "
                    "AND child_kind='tag' AND child_id=?", (nid, tid)):
                self.store.link(nid, "tag", tid)
                fixed += 1
        self.store.refresh_counts()
        return {"linked": fixed, "unlinked": dropped}

    # -------------------------------------------------- 标签从属（白裙子 → 裙子）
    BASE_SUFFIXES = ("dress", "skirt", "shirt", "sweater", "jacket", "coat", "hat", "cap", "socks",
                     "thighhighs", "pantyhose", "gloves", "shoes", "boots", "bikini", "swimsuit",
                     "bra", "panties", "ribbon", "bow", "collar", "choker", "glasses", "hair",
                     "eyes", "breasts", "kimono", "yukata", "uniform", "suit", "tie", "apron",
                     "leotard", "armor", "cape", "tail", "wings", "horns")

    def build_tag_hierarchy(self, progress=None) -> dict:
        """按规则生成 tag→tag 从属关系（只在基础标签存在于库里时才连）。

        规则：`<颜色/修饰>_<基础类>` → `<基础类>`，例如 white_dress → dress、black_skirt → skirt、
        long_hair → hair（hair 存在时）。颜色+服装是覆盖最多的一类。
        """
        names = {t["name"] for t in self.store.list_tags()}
        by_name = {t["name"]: int(t["id"]) for t in self.store.list_tags()}
        made = 0
        for name in names:
            for suf in self.BASE_SUFFIXES:
                if suf in names and name != suf and name.endswith("_" + suf):
                    self.store.link_tag_sub(by_name[suf], by_name[name])   # 基础类 ← 具体类
                    made += 1
                    break
        self.store.refresh_counts()
        return {"links": made}

    @staticmethod
    def most_specific_tags(tags: Sequence[str], child_map: dict) -> list[str]:
        """同时存在父标签和子标签时，只保留子标签（显示用）。"""
        tagset = set(tags)
        drop: set[str] = set()
        for t in tags:
            for child in child_map.get(t, ()):     # t 有子标签且子标签也在这张图上 → 丢掉父标签
                if child in tagset:
                    drop.add(t)
                    break
        return [t for t in tags if t not in drop]

    # -------------------------------------------------- 任务持久化（断电续跑）
    def start_job(self, kind: str, ids: Sequence[int], params: dict | None = None, note: str = "") -> int:
        return self.store.create_job(kind, ids, params, note)

    def job_tick(self, job_id: int | None, done: int) -> None:
        if job_id:
            self.store.job_tick(job_id, done)

    def finish_job(self, job_id: int | None, status: str = "done") -> None:
        if job_id:
            self.store.finish_job(job_id, status)

    def job_remaining(self, job_id: int) -> list[int]:
        """上次没跑完的部分：按任务类型跳过已完成的文件。"""
        job = self.store.job(job_id)
        if not job:
            return []
        ids = self.store.job_ids(job_id)
        if not ids:
            return []
        kind = job["kind"]
        cond = {
            "wd14": "wd_done=0",
            "clip": "clip_done=0",
            "rescore": "clip_done=0",
            "face": "face_done=0",
            "region": "region_done=0",
            "autotag": "(wd_done=0 OR clip_done=0)",
        }.get(kind, "1=1")
        ph = ",".join("?" * len(ids))
        rows = self.store.query(f"SELECT id FROM files WHERE id IN ({ph}) AND missing=0 AND {cond}", ids)
        return [int(r["id"]) for r in rows]

    def describe_job(self, job_id: int) -> str:
        job = self.store.job(job_id)
        if not job:
            return ""
        names = {"wd14": "WD14 打标", "clip": "CLIP 打标", "autotag": "自动打标",
                 "face": "人脸检测", "region": "区域精修", "rescore": "CLIP 重打分",
                 "scan": "扫描"}
        left = len(self.job_remaining(job_id))
        return (f"{names.get(job['kind'], job['kind'])}：共 {job['total']} 项，"
                f"已完成 {job['done']}，剩余 {left}")

    # -------------------------------------------------- 标签体系 / 重命名联动
    def sync_taxonomy(self) -> int:
        """按标签的“主类型”初始化分类体系（只做一次，之后随便改都不影响图片）。"""
        from . import categories as cats
        mapping = {c["key"]: c["label"] for c in cats.ordered(self.store)}
        return self.store.sync_taxonomy_from_categories(mapping)

    def rename_tag_global(self, tag_id: int, new_name: str, update_filenames: bool | None = None,
                          progress=None) -> dict:
        """重命名标签：图片只引用标签 id，所以所有图片自动跟着改；可选同步改名文件名。"""
        new_name = (new_name or "").strip()
        if not new_name:
            return {"ok": False, "msg": "标签名不能为空"}
        row = self.store.one("SELECT name FROM tags WHERE id=?", (tag_id,))
        if row is None:
            return {"ok": False, "msg": "标签不存在"}
        old_name = row["name"]
        files = [int(r["file_id"]) for r in
                 self.store.query("SELECT file_id FROM file_tags WHERE tag_id=?", (tag_id,))]
        merged = False
        target = self.store.tag_id(new_name)
        if target and target != tag_id:
            self.store.merge_tags(tag_id, target)
            tag_id = target
            merged = True
        else:
            self.store.rename_tag(tag_id, new_name)
        if update_filenames is None:
            update_filenames = True
        renamed = 0
        if update_filenames and self.settings.tag_storage == "filename" and files:
            res = self.apply_disk_names(files, progress)
            renamed = res.get("renamed", 0)
        self.update_probes_for_tags([new_name])
        self.store.refresh_counts()
        return {"ok": True, "old": old_name, "new": new_name, "files": len(files),
                "renamed": renamed, "merged": merged}

    # ================================================== 专用图库（Steam 式多库）
    def library_roots(self) -> list:
        return self.store.library_roots()

    def ensure_library_dir(self, drive: str, create: bool = True) -> int | None:
        """取/建某个盘上的图库目录（默认 <盘符>:\\ImageTags）。

        没建过就自动创建；创建失败（只读盘/无权限/盘不存在）返回 None，并把原因记在
        self.last_dir_error 里，由调用方转成一条可读的错误，而不是让整批导入崩掉。
        """
        self.last_dir_error = ""
        drive = (drive or "C:").upper()
        rid = self.store.root_for_drive(drive, create=False, dir_name=self.settings.library_dir_name)
        path, ok, msg = self.check_library_dir(drive, create=create)
        if not ok:
            self.last_dir_error = msg
            return None
        if rid is None:
            rid = self.store.add_root(path)
        if rid:
            row = self.store.one("SELECT path FROM roots WHERE id=?", (rid,))
            if row:
                Path(row["path"]).mkdir(parents=True, exist_ok=True)
            self.store.set_root_library(rid, True)
        return rid

    def library_target_path(self, drive: str) -> Path:
        return Path(f"{(drive or 'C:').upper()}\\{self.settings.library_dir_name}")

    def check_library_dir(self, drive: str, create: bool = False) -> tuple[str, bool, str]:
        """检查某盘图库目录能否使用；create=True 时顺手真的创建并试写一次。"""
        import re
        if re.search(r'[<>:"/\\|?*]', self.settings.library_dir_name or ""):
            return str(self.library_target_path(drive)), False, '图库目录名里有非法字符（< > : " / \\ | ? *）'
        p = self.library_target_path(drive)
        try:
            if not p.parent.exists():
                return str(p), False, "盘符不存在或未挂载"
            if create:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / ".imtag_write_test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                return str(p), True, "已就绪（可写）"
            if p.exists():
                return str(p), True, "已存在"
            return str(p), True, "将自动创建"
        except PermissionError:
            return str(p), False, "没有写入权限（系统盘/受保护目录建议改用别的盘）"
        except OSError as e:
            return str(p), False, f"无法写入：{e}"

    def import_to_library(self, file_ids: Sequence[int], move: bool = True,
                          auto_write_names: bool = True, progress=None, cancel=None,
                          keep_folder: bool = True) -> dict:
        """把选中的图片正式收进「图库」：移动到同盘图库目录，源文件随之消失（下次扫描不会重复）。

        同盘移动 = rename，毫秒级；跨盘自动复制后删除。
        """
        rows = self.store.files_by_ids(file_ids)
        moved, errors, roots_used, renamed = 0, [], set(), 0
        names_by_root: dict[int, list[int]] = {}
        total = max(1, len(rows))
        for i, r in enumerate(rows):
            if cancel and cancel():
                break
            src = Path(r["path"])
            if not src.exists():
                errors.append(f"{src.name}: 文件不存在")
                continue
            drive = src.drive or (src.anchor or "C:")
            root_id = self.ensure_library_dir(drive, create=True)
            if root_id is None:
                err = self.last_dir_error or "无法建立图库目录"
                if err not in errors:
                    errors.append(f"目标图库不可用（{err}）")
                continue
            root_path = Path(self.store.one("SELECT path FROM roots WHERE id=?", (root_id,))["path"])
            try:
                if src.is_relative_to(root_path):
                    self._update_path_to_root(int(r["id"]), src, root_path, root_id)
                    roots_used.add(root_id)
                    names_by_root.setdefault(root_id, []).append(int(r["id"]))
                    continue
            except Exception:
                pass
            sub = ""
            if r["series_id"]:
                s = self.store.one("SELECT dir FROM series WHERE id=?", (int(r["series_id"]),))
                if s and s["dir"]:
                    sub = Path(s["dir"]).name
            elif keep_folder:
                sub = src.parent.name
            target_dir = root_path / sub if sub else root_path
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / src.name
            k = 2
            while target.exists():
                target = target_dir / f"{src.stem}_{k}{src.suffix}"
                k += 1
            try:
                if move:
                    self._move_with_retry(src, target)
                else:
                    shutil.copy2(str(src), str(target))
                self._update_path_to_root(int(r["id"]), target, root_path, root_id)
                moved += 1
                roots_used.add(root_id)
                names_by_root.setdefault(root_id, []).append(int(r["id"]))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.name}: {e}")
            if progress and i % 5 == 0:
                progress(f"收录到图库 {i + 1}/{len(rows)}", (i + 1) / total)
        if auto_write_names and self.settings.tag_storage == "filename":
            for _rid, ids in names_by_root.items():
                res = self.apply_disk_names(ids, progress)
                renamed += res.get("renamed", 0)
        self._cleanup_empty_dirs({Path(r["path"]).parent for r in rows})
        self.store.refresh_counts()
        dirs = [self.store.one("SELECT path FROM roots WHERE id=?", (i,))["path"] for i in sorted(roots_used)]
        return {"moved": moved, "renamed": renamed, "roots": sorted(roots_used),
                "dirs": dirs, "errors": errors}

    def _update_path_to_root(self, file_id: int, new_path: Path, root_path: Path, root_id: int | None = None) -> None:
        rel = str(new_path.relative_to(root_path)).replace("\\", "/")
        if root_id is None:
            row = self.store.one("SELECT id FROM roots WHERE path=?", (str(root_path),))
            root_id = int(row["id"]) if row else None
        self.store.execute("UPDATE files SET path=?,rel=?,name=?,ext=?,root_id=? WHERE id=?",
                           (str(new_path.resolve()), rel, new_path.name, new_path.suffix.lower(), root_id, file_id))
        row = self.store.one("SELECT series_id FROM files WHERE id=?", (file_id,))
        if row and row["series_id"]:
            sub = str(Path(rel).parent).replace("\\", "/")
            self.store.execute("UPDATE series SET root_id=?,dir=? WHERE id=?", (root_id, sub, int(row["series_id"])))

    @staticmethod
    def _cleanup_empty_dirs(dirs) -> None:
        """导入后清掉残留的空目录（到第一个非空目录为止）。"""
        for d in sorted({Path(x) for x in dirs if x}, key=lambda p: len(p.parts), reverse=True):
            try:
                while d and d.exists() and d.is_dir() and not any(d.iterdir()):
                    parent = d.parent
                    d.rmdir()
                    d = parent
            except Exception:
                pass

    @staticmethod
    def _move_with_retry(src: Path, dst: Path, tries: int = 4) -> None:
        """移动文件：Windows 上被杀毒/看图/缩略图短暂占用时会报 WinError 32，重试几次即可。"""
        import time as _t
        last: Exception | None = None
        for i in range(tries):
            try:
                shutil.move(str(src), str(dst))
                return
            except (PermissionError, OSError) as e:
                last = e
                _t.sleep(0.4 * (i + 1))
        raise last if last else RuntimeError("move failed")

    # ================================================== 重复图检测
    # ================================================== 分级识别
    def apply_rating(self, file_id: int, wd14: dict | None = None, clip_p: dict | None = None) -> str:
        """写入分级结果：files.rating + 分级标签（全年龄/R15/R18/R18G）。"""
        from .config import RATING_TAG
        level, scores = decide_rating(wd14, clip_p,
                                      questionable_as_r18=bool(self.settings.rating_questionable_as_r18))
        self.store.execute("UPDATE files SET rating=?, rating_scores=? WHERE id=?",
                           (level or "", json.dumps({"wd14": wd14, "clip": clip_p, "fused": scores},
                                                    ensure_ascii=False), file_id))
        self.store.execute("DELETE FROM file_tags WHERE file_id=? AND source='rating'", (file_id,))
        if not level:
            return ""                      # 没有依据就不打分级标签（也不会误标成全年龄）
        tid = self.store.ensure_tag(RATING_TAG[level], "rating", auto=0)
        self.store.add_file_tags(file_id, [(RATING_TAG[level], "rating", float(scores.get(level, 0.0)))],
                                 status="confirmed" if self.settings.rating_auto_confirm else "pending")
        return level

    def clip_rating_probs(self, clip, emb) -> dict | None:
        import numpy as np
        if emb is None:
            return None
        if getattr(self, "_rating_text_embs", None) is None:
            texts, groups = [], []
            for lv, prompts in RATING_CLIP_PROMPTS.items():
                groups.append(lv)
                texts.append(clip.encode_texts(prompts, templates=["{}"]) .mean(axis=0))
            T = np.stack(texts)
            T /= np.clip(np.linalg.norm(T, axis=1, keepdims=True), 1e-8, None)
            self._rating_text_embs = (groups, T)
        groups, T = self._rating_text_embs
        sims = (T @ np.asarray(emb, dtype=np.float32)) * 100.0
        z = sims - sims.max()
        p = np.exp(z) / np.exp(z).sum()
        return {g: float(v) for g, v in zip(groups, p)}

    def run_rating(self, file_ids: Sequence[int], hub: EngineHub, progress=None, cancel=None,
                   job_id: int | None = None, per_file=None) -> dict:
        """给一批图片定级：WD14 分级概率为主（动漫很准），CLIP 提示词兜底（真人/未知）。"""
        rows = self.store.files_by_ids(file_ids)
        if not rows:
            return {"rated": 0}
        clip = hub.get_clip(progress) if self.settings.clip_enabled else None
        if clip is not None:
            self.ensure_clip_embeddings(file_ids, hub, progress, cancel)
        counts: dict[str, int] = {}
        total = max(1, len(rows))
        wd_pending: list = []
        for r in rows:
            auto = self.store.get_auto_json(int(r["id"]))
            if not auto.get("rating_wd14"):
                wd_pending.append(int(r["id"]))
        if wd_pending and self.settings.wd14_enabled:
            self.run_wd14(wd_pending, hub, progress, cancel, batch=8, job_id=None)
        import numpy as np
        for i, r in enumerate(rows):
            if cancel and cancel():
                break
            fid = int(r["id"])
            auto = self.store.get_auto_json(fid)
            wd = auto.get("rating_wd14") or None
            cp = None
            if clip is not None:
                row = self.store.one("SELECT clip_vec FROM files WHERE id=?", (fid,))
                if row and row["clip_vec"]:
                    emb = np.frombuffer(row["clip_vec"], dtype=np.float16).astype(np.float32)
                    n = float(np.linalg.norm(emb))
                    if n > 0:
                        cp = self.clip_rating_probs(clip, emb / n)
            lvl = self.apply_rating(fid, wd, cp)
            counts[lvl or "未知（缺 WD14 与 CLIP 结果）"] = counts.get(lvl or "未知", 0) + 1
            self.job_tick(job_id, i + 1)
            if per_file:
                per_file(fid, lvl)
            if progress:
                progress(f"分级识别 {i + 1}/{len(rows)}", (i + 1) / total)
        self.store.refresh_counts()
        return {"rated": len(rows), "counts": counts}

    def ensure_hashes(self, file_ids: Sequence[int] | None = None, progress=None, cancel=None) -> int:
        rows = self.store.files_without_hash(file_ids)
        total = max(1, len(rows))
        for i, r in enumerate(rows):
            if cancel and cancel():
                break
            p = r["path"]
            h1, h2 = imaging.dhash(p), imaging.ahash(p)
            h3 = imaging.phash(p)
            w, h = imaging.image_size(p)
            self.store.set_hash(int(r["id"]), h1 or "", h2 or "", w, h, h3 or "")
            if progress and i % 10 == 0:
                progress(f"计算图像指纹 {i + 1}/{len(rows)}", (i + 1) / total)
        return len(rows)

    @staticmethod
    def _hamming(a: str, b: str) -> int:
        if not a or not b:
            return 64
        return bin(int(a, 16) ^ int(b, 16)).count("1")

    @staticmethod
    def _pair_key(a: int, b: int) -> str:
        return f"f{min(int(a), int(b))}|f{max(int(a), int(b))}"

    def find_duplicate_groups(self, threshold: int = 6, only_roots: Sequence[int] = (),
                              use_clip: bool = True, clip_threshold: float = 0.97,
                              progress=None, cancel=None) -> list[dict]:
        """找重复/近似重复：先按感知哈希分桶比对，CLIP 特征再兜一层（图不多时）。"""
        rows = self.store.hashed_files(only_roots)
        if len(rows) < 2:
            return []
        feedback = self.store.dup_feedback_map()
        parent: dict[int, int] = {}
        info = {int(r["id"]): r for r in rows}
        vals = {int(r["id"]): (r["phash"] or "") for r in rows}
        vals_p = {int(r["id"]): (r["phash2"] or "") for r in rows}
        # 同一系列内部的相似是正常的（漫画页本来就长得像），这类不报警
        series_of: dict[int, int | None] = {}
        for fid in info:
            row = self.store.one("SELECT series_id FROM files WHERE id=?", (fid,))
            series_of[fid] = int(row["series_id"]) if row and row["series_id"] else None
        for fid in info:
            parent[fid] = fid

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        buckets: dict[tuple[int, int], list[int]] = {}
        for fid, ph in vals.items():
            if not ph:
                continue
            v = int(ph, 16)
            for band in range(4):
                buckets.setdefault((band, (v >> (band * 16)) & 0xFFFF), []).append(fid)
        pairs = set()
        for _key, fids in buckets.items():
            if len(fids) < 2 or len(fids) > 80:
                continue
            for i in range(len(fids)):
                for j in range(i + 1, len(fids)):
                    pairs.add((fids[i], fids[j]))
        if progress:
            progress(f"比对指纹（候选 {len(pairs)} 对）…", 0.4)
        for a, b in pairs:
            if cancel and cancel():
                break
            if feedback.get(self._pair_key(a, b)) == "not_dup":
                continue
            if series_of.get(a) and series_of.get(a) == series_of.get(b):
                continue                       # 同系列的两页，不当重复
            if self._hamming(vals[a], vals[b]) > threshold:
                # dHash 没命中时再用 pHash（更稳，能抓住调色/加边的情况）
                if not vals_p.get(a) or not vals_p.get(b) or \
                        self._hamming(vals_p[a], vals_p[b]) > threshold:
                    continue
            ra, rb = info[a], info[b]
            if ra["width"] and rb["width"] and ra["height"] and rb["height"]:
                r1 = ra["width"] / max(1, ra["height"])
                r2 = rb["width"] / max(1, rb["height"])
                if abs(r1 - r2) > 0.08:
                    continue
            union(a, b)
        if use_clip and len(rows) <= 6000:
            import numpy as np
            sql = "SELECT id, clip_vec FROM files WHERE missing=0 AND clip_vec IS NOT NULL"
            args: list = []
            if only_roots:
                sql += " AND root_id IN (%s)" % ",".join("?" * len(only_roots))
                args = list(only_roots)
            vecs = [v for v in self.store.query(sql, args) if int(v["id"]) in parent]
            if len(vecs) >= 2:
                ids = [int(v["id"]) for v in vecs]
                X = np.stack([np.frombuffer(v["clip_vec"], dtype=np.float16).astype(np.float32) for v in vecs])
                X /= np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-8, None)
                chunk = 512
                for i in range(0, len(ids), chunk):
                    if cancel and cancel():
                        break
                    sims = X[i:i + chunk] @ X.T
                    for a in range(sims.shape[0]):
                        for b in range(i + a + 1, len(ids)):
                            if sims[a, b] < clip_threshold:
                                continue
                            if feedback.get(self._pair_key(ids[i + a], ids[b])) == "not_dup":
                                continue
                            union(ids[i + a], ids[b])
                    if progress:
                        frac = 0.4 + 0.5 * min(1.0, (i + chunk) / len(ids))
                        progress(f"CLIP 兜底比对 {min(i + chunk, len(ids))}/{len(ids)}", frac)
        groups: dict[int, list[int]] = {}
        for fid in parent:
            groups.setdefault(find(fid), []).append(fid)
        banned_groups = self.store.banned_dup_groups()
        out = []
        for root, fids in groups.items():
            if len(fids) < 2:
                continue
            fids.sort(key=lambda x: (info[x]["mtime"] or 0))
            if ("g:" + ",".join(str(x) for x in fids)) in banned_groups:
                continue
            in_series = [x for x in fids if series_of.get(x)]
            mixed = bool(in_series) and len(in_series) < len(fids)
            maxd = 0
            for i in range(len(fids)):
                for j in range(i + 1, len(fids)):
                    maxd = max(maxd, self._hamming(vals[fids[i]], vals[fids[j]]))
            out.append({"key": f"g{root}", "files": [info[x] for x in fids], "max_dist": maxd,
                        "size": len(fids), "mixed_series": mixed,
                        "series_ids": sorted({series_of[x] for x in in_series if series_of.get(x)})})
        out.sort(key=lambda g: (-g["size"], g["max_dist"]))
        return out

    def mark_not_duplicate(self, file_a: int, file_b: int, note: str = "") -> None:
        """误报反馈：这一对以后不再提示。"""
        self.store.add_dup_feedback(self._pair_key(file_a, file_b), "not_dup", note)

    def mark_group_not_duplicate(self, ids: Sequence[int], note: str = "user_feedback") -> None:
        """误报反馈（整组）：这一组以后不再当作重复提示。"""
        self.store.add_dup_group_feedback(list(ids), note)

    def mark_group_as_series(self, ids: Sequence[int], note: str = "merged_series") -> None:
        self.store.add_series_group_feedback(list(ids), note)

    def resolve_duplicate(self, keep_id: int, remove_ids: Sequence[int], action: str = "quarantine",
                          merge_tags: bool = True, progress=None) -> dict:
        """保留一张，其余移入图库下的 .removed 隔离区（可手动恢复）或永久删除。"""
        keep = self.store.one("SELECT * FROM files WHERE id=?", (keep_id,))
        if keep is None:
            return {"ok": False, "msg": "保留的文件不存在"}
        removed, merged, errors = 0, 0, []
        keep_tags = {t["name"] for t in self.store.tags_for_file(keep_id, statuses=("confirmed", "pending"))}
        for fid in remove_ids:
            if int(fid) == int(keep_id):
                continue
            row = self.store.one("SELECT * FROM files WHERE id=?", (int(fid),))
            if row is None:
                continue
            src = Path(row["path"])
            if merge_tags:
                for t in self.store.tags_for_file(int(fid), statuses=("confirmed", "pending")):
                    if t["name"] in keep_tags:
                        continue
                    self.store.add_file_tags(int(keep_id), [(t["name"], t["source"], float(t["score"]))],
                                             status=t["status"])
                    keep_tags.add(t["name"])
                    merged += 1
            for rg in self.store.regions_for_file(int(fid)):
                self.store.add_region(int(keep_id), rg["tag_name"] or "",
                                      (rg["x"], rg["y"], rg["w"], rg["h"]), note=rg["note"] or "")
            try:
                if action == "delete":
                    if src.exists():
                        src.unlink()
                else:
                    root = self.store.one("SELECT path FROM roots WHERE id=?", (int(row["root_id"]),))
                    base = Path(root["path"]) if root else src.parent
                    dst_dir = base / ".removed"
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    dst = dst_dir / src.name
                    k = 2
                    while dst.exists():
                        dst = dst_dir / f"{src.stem}_{k}{src.suffix}"
                        k += 1
                    if src.exists():
                        shutil.move(str(src), str(dst))
                self.store.execute("UPDATE files SET missing=1 WHERE id=?", (int(fid),))
                self.store.add_dup_feedback(self._pair_key(int(keep_id), int(fid)), "resolved")
                removed += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{src.name}: {e}")
        if merge_tags:
            self.update_probes_for_tags(list(keep_tags))
        self.store.refresh_counts()
        return {"ok": True, "removed": removed, "merged": merged, "errors": errors}
