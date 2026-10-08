"""把 ProRes 中间文件用显卡编码器（NVENC）压成 6 Mbps 的 1080p60 成片。

用法：python encode_nvenc.py <中间文件.mov> [输出.mp4] [目标Mbps]
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg

WORK = Path(r"E:\文档\ChatGPT\图片标签分类\promo\premiere")
AUDIO = Path(r"E:\文档\ChatGPT\图片标签分类\promo\audio\mix.wav")


def pick_audio(explicit: str | None = None) -> Path:
    """优先用命令行指定的混音；否则 v15 的 mix_v15.wav；再退回旧版 mix.wav。"""
    if explicit:
        return Path(explicit)
    v15 = AUDIO.with_name("mix_v15.wav")
    return v15 if v15.exists() else AUDIO


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "promo_master_prores.mov"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else WORK / "图片标签工坊1.2_宣传片_1080p60.mp4"
    mbps = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0
    audio = pick_audio(sys.argv[4] if len(sys.argv) > 4 else None)
    if not src.exists():
        print("找不到中间文件:", src)
        return 1
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-i", str(src)]
    if audio.exists():
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-map", "0:v:0", "-an"]
    cmd += [
        "-c:v", "h264_nvenc",
        "-preset", "p5",          # 质量/速度平衡
        # CBR：静态画面多的片子用 VBR 会被自适配压得很低（实测只有 1.7 Mbps），
        # 这里按目标码率恒定输出，保证码率就是你要的数。
        "-rc", "cbr",
        "-b:v", f"{mbps}M",
        "-minrate", f"{mbps}M",
        "-maxrate", f"{mbps}M",
        "-bufsize", f"{mbps * 2:.0f}M",
        "-profile:v", "high", "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-shortest", str(out),
    ]
    print("NVENC 编码中 …")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True)
    dt = time.time() - t0
    if r.returncode != 0:
        print("失败:", r.stderr.decode("utf-8", "replace")[-900:])
        return 1
    size = out.stat().st_size / 1024 / 1024
    # 实际时长按素材算，别写死
    dur = probe_duration(src) or 200.0
    print(f"完成 {out.name}  {size:.1f} MB  ≈ {size*8/dur:.2f} Mbps  用时 {dt:.0f}s")
    return 0


def probe_duration(path: Path) -> float | None:
    try:
        import av
        with av.open(str(path)) as c:
            v = next(s for s in c.streams if s.type == "video")
            return float(v.duration * v.time_base)
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    raise SystemExit(main())
