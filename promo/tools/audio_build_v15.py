"""v1.5 音频：塞壬唱片官网《离解复合》裁到片长 + 节拍检测，输出 mix_v15.wav。

复用 audio_build.py 的解码/节拍/淡入淡出函数，只是换了 BGM 与分镜脚本。
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_build as ab  # noqa: E402
import promo_plan_v15 as plan  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "audio"
BGM = OUT / "BGM_离解复合.wav"


def main() -> int:
    if not BGM.exists():
        print("缺 BGM：", BGM)
        return 1
    bgm = ab.decode(BGM)
    bgm_len = len(bgm) / ab.SR
    flux, times = ab.onset_envelope(bgm)
    bpm = ab.estimate_tempo(flux)
    beats = ab.beat_grid(flux, times, bpm)
    segs = plan.resolve(bgm_len, beats)
    total = sum(float(s["dur"]) for s in segs)
    print(f"BGM《离解复合》{bgm_len:.1f}s ／ 片子 {total:.1f}s")
    print(f"BPM {bpm:.1f}  拍点 {len(beats)}  前 10 拍 {[round(b, 2) for b in beats[:10]]}")

    (OUT / "beats_v15.json").write_text(json.dumps(
        {"bpm": bpm, "duration": bgm_len, "beats": beats,
         "plan": [{"src": s["src"], "start": s["start"], "dur": s["dur"]} for s in segs]},
        ensure_ascii=False), encoding="utf-8")

    need = int(total * ab.SR)
    bed = bgm[:need] if len(bgm) >= need else np.resize(bgm, (need, 2))
    mix = ab.fade(ab.normalize(bed, ab.BGM_RMS_DB), ab.SR, 1.5, 4.0)
    peak = float(np.abs(mix).max())
    if peak > 0.95:
        mix *= 0.95 / peak
    out = OUT / "mix_v15.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(ab.SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())
    rms_db = 20 * float(np.log10(np.sqrt((mix ** 2).mean()) + 1e-9))
    print(f"混音完成 {out}  {len(mix)/ab.SR:.1f}s  峰值 {float(np.abs(mix).max()):.2f}  RMS {rms_db:.1f} dBFS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
