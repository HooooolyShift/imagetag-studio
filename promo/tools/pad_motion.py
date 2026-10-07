"""把动画帧补齐到「按成片帧率播放正好等于分镜时长」。

原因：Premiere 2024 没有 ProjectItem.setFootageFrameRate，图片序列按序列帧率解释；
抓帧通常是 12~15fps，直接导入会变成 4 倍速。这里按需要的总帧数重复帧，
导入后时长天然对上。输出 JPEG（体积小得多）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promo_plan import SEGMENTS  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
SRC = PROJECT / "promo" / "motion"
DST = PROJECT / "promo" / "motion_seq"
FPS = 60


def main() -> int:
    wanted: dict[str, float] = {}
    for seg in SEGMENTS:
        if seg["src"].startswith("motion/"):
            wanted[seg["src"].split("/", 1)[1]] = float(seg["dur"])
    if not wanted:
        print("分镜里没有动图片段")
        return 0
    for name, dur in wanted.items():
        frames = sorted((SRC / name).glob("*.png"))
        if not frames:
            print(f"{name}: 没有帧（先跑 motion_farm.py），跳过")
            continue
        need = int(round(dur * FPS))
        out_dir = DST / name
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.jpg"):
            old.unlink()
        for i in range(need):
            src_idx = min(len(frames) - 1, int(i * len(frames) / need))
            Image.open(frames[src_idx]).convert("RGB").save(
                out_dir / f"{name}_{i + 1:05d}.jpg", quality=92)
        print(f"{name}: {len(frames)} 帧 -> {need} 帧（{dur}s @ {FPS}fps）")
    print("输出目录:", DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
