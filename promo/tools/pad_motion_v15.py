"""把 v15_motion 渲染的 30fps 帧序列补成「按 60fps 播放正好等于分镜时长」。

原因：Premiere 没有 setFootageFrameRate，图片序列按序列帧率（60）解释，
30fps 的素材直接导入会变成 2 倍速。这里按需要的总帧数均匀取帧、重复补齐。
输出 promo/motion_seq_v15/<片段>/<片段>_00001.jpg
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promo_plan_v15 import resolve  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
SRC = PROJECT / "promo" / "motion_v15"
DST = PROJECT / "promo" / "motion_seq_v15"
FPS = 60


def main() -> int:
    segs = resolve()
    wanted: dict[str, float] = {}
    for s in segs:
        if str(s["src"]).startswith("motion_v15/"):
            wanted[str(s["src"]).split("/", 1)[1]] = float(s["dur"])
    total = 0
    for name, dur in wanted.items():
        frames = sorted((SRC / name).glob("*.jpg"))
        if not frames:
            print(f"{name}: 没有帧（先跑 v15_motion.py），跳过")
            continue
        need = int(round(dur * FPS))
        out_dir = DST / name
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.jpg"):
            old.unlink()
        for i in range(need):
            src = frames[min(len(frames) - 1, int(i * len(frames) / need))]
            Image.open(src).save(out_dir / f"{name}_{i + 1:05d}.jpg", quality=90)
        total += need
        print(f"  {name:14} {len(frames)} -> {need} 帧（{dur}s @60fps）")
    print(f"共 {len(wanted)} 段 / {total} 帧 -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
