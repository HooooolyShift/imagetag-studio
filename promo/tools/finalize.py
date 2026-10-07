"""把 Premiere 导出的成片和混音合成最终文件（视频流直接复制，音频转 AAC）。

用法：
    python finalize.py [输入mp4] [输出mp4]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

WORK = Path(r"E:\文档\ChatGPT\图片标签分类\promo\premiere")
AUDIO = Path(r"E:\文档\ChatGPT\图片标签分类\promo\audio\mix.wav")
DEFAULT_IN = WORK / "ImageTagStudio_promo_v1.mp4"
DEFAULT_OUT = WORK / "图片标签工坊1.2_宣传片_1080p60.mp4"
AUDIO_BITRATE = "192k"


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    if not src.exists():
        print("找不到输入:", src)
        return 1
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-i", str(src), "-i", str(AUDIO),
           "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy",
           "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-movflags", "+faststart", "-shortest", str(out)]
    print("运行 ffmpeg（视频流复制，不重新编码）…")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("失败:", r.stderr.decode("utf-8", "replace")[-800:])
        return 1
    mb = out.stat().st_size / 1024 / 1024
    print(f"完成 {out.name}  {mb:.1f} MB  ≈ {mb*8/200:.2f} Mbps（含音频）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
