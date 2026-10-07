"""宣传片音频：BGM（+ 可选转场音效）混成 48kHz 立体声 WAV，并输出节拍点。

    python audio_build.py analyze    只看 BGM 时长/节拍
    python audio_build.py build      生成 mix.wav + beats.json（+ 可选 cues.json）

来源：promo/audio/BGM_Endospore.wav（塞壬唱片官网官方 WAV，见 fetch_bgm.py）
音效默认关闭（用户 2026-10-07 反馈"突兀"），需要时把 ADD_SFX 打开。
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promo_plan import resolve  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "audio"
BGM = OUT / "BGM_Endospore.wav"

SR = 48000
BGM_RMS_DB = -19.0       # BGM 铺底电平
SFX_RMS_DB = -17.5       # 音效电平（比 BGM 只高一点，别抢戏）
ADD_SFX = False          # 转场音效：默认关（用户要求去掉）

SFX = {
    "whoosh": Path(r"E:\Music\转场音效\whooosh-呼呼声\Whoosh呼呼声1.wav"),
    "whoosh2": Path(r"E:\Music\转场音效\whooosh-呼呼声\Whoosh呼呼声4.wav"),
    "riser": Path(r"E:\Music\上升音效\上升音效3.mp3"),
    "impact": Path(r"E:\Music\重音\重音3.mp3"),
}


def decode(path: Path, sr: int = SR) -> np.ndarray:
    """PyAV 解码任意格式 → float32 立体声 (n,2)。"""
    container = av.open(str(path))
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sr)
    chunks: list[np.ndarray] = []
    for frame in container.decode(stream):
        for rf in resampler.resample(frame):
            chunks.append(rf.to_ndarray().T.astype(np.float32))
    for rf in resampler.resample(None):
        chunks.append(rf.to_ndarray().T.astype(np.float32))
    container.close()
    if not chunks:
        raise RuntimeError(f"解不出音频：{path}")
    x = np.concatenate(chunks, axis=0)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return np.clip(x, -1.0, 1.0)


def onset_envelope(x: np.ndarray, sr: int = SR, n_fft: int = 2048, hop: int = 512):
    mono = x.mean(axis=1)
    win = np.hanning(n_fft).astype(np.float32)
    n = 1 + max(0, (len(mono) - n_fft) // hop)
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop][:n]
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    flux = np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)
    flux = np.maximum(flux - np.convolve(flux, np.ones(31) / 31, mode="same"), 0)
    return flux, np.arange(len(flux)) * hop / sr


def estimate_tempo(flux: np.ndarray, hop: int = 512, sr: int = SR,
                   lo: float = 60, hi: float = 180) -> float:
    if flux.std() < 1e-9:
        return 120.0
    f = flux - flux.mean()
    ac = np.correlate(f, f, mode="full")[len(f) - 1:]
    fps = sr / hop
    best, best_score = 120.0, -1e9
    for bpm in np.arange(lo, hi, 0.5):
        lag = int(round(fps * 60.0 / bpm))
        if 1 <= lag < len(ac):
            score = (ac[lag] / (ac[0] + 1e-9)) * (1.0 - 0.15 * abs(bpm - 110) / 70.0)
            if score > best_score:
                best, best_score = float(bpm), float(score)
    return best


def beat_grid(flux: np.ndarray, times: np.ndarray, bpm: float) -> list[float]:
    """按 BPM 铺网格，再把每个拍点吸附到最近的起音峰。"""
    if not len(times):
        return []
    period = 60.0 / bpm
    beats, t = [], 0.0
    while t < times[-1]:
        i = int(np.searchsorted(times, t))
        lo, hi = max(0, i - 3), min(len(flux), i + 4)
        beats.append(float(times[lo + int(np.argmax(flux[lo:hi]))]) if hi > lo else float(t))
        t += period
    return beats


def normalize(x: np.ndarray, target_rms_db: float) -> np.ndarray:
    rms = float(np.sqrt((x ** 2).mean())) or 1e-6
    y = x * ((10 ** (target_rms_db / 20.0)) / rms)
    peak = float(np.abs(y).max())
    return y * (0.98 / peak) if peak > 0.98 else y


def fade(x: np.ndarray, sr: int, fin: float = 1.5, fout: float = 5.0) -> np.ndarray:
    y = x.copy()
    n_in, n_out = int(fin * sr), int(fout * sr)
    if n_in:
        y[:n_in] *= np.linspace(0, 1, n_in)[:, None]
    if n_out:
        y[-n_out:] *= np.linspace(1, 0, n_out)[:, None]
    return y


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    if not BGM.exists():
        print("缺 BGM：", BGM, "（先跑 fetch_bgm.py）")
        return 1

    bgm = decode(BGM)
    bgm_len = len(bgm) / SR
    flux, times = onset_envelope(bgm)
    bpm = estimate_tempo(flux)
    beats = beat_grid(flux, times, bpm)
    segs = resolve(bgm_len, beats)
    total = sum(float(s["dur"]) for s in segs)
    print(f"BGM {bgm_len:.1f}s ／ 片子 {total:.1f}s")
    print(f"BPM {bpm:.1f}  拍点 {len(beats)}  前 10 拍 {[round(b, 2) for b in beats[:10]]}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "beats.json").write_text(json.dumps(
        {"bpm": bpm, "duration": bgm_len, "beats": beats}, ensure_ascii=False),
        encoding="utf-8")
    if mode == "analyze":
        return 0

    need = int(total * SR)
    bed = bgm[:need] if len(bgm) >= need else np.resize(bgm, (need, 2))
    mix = fade(normalize(bed, BGM_RMS_DB), SR, 1.5, 5.0)

    cues: list[dict] = []
    if ADD_SFX:
        for i, s in enumerate(segs, 1):
            title, src = s.get("title", ""), s["src"]
            if not (title or src.startswith("card/")):
                continue
            kind = "impact" if src.startswith("card/github") else (
                "riser" if i == 1 else ("whoosh" if i % 2 else "whoosh2"))
            path = SFX.get(kind)
            if not path or not path.exists():
                continue
            clip = decode(path)[: int((2.2 if kind == "riser" else 1.6) * SR)]
            clip = fade(normalize(clip, SFX_RMS_DB), SR, 0.01, min(0.3, len(clip) / SR / 2))
            at = float(s["start"]) - (0.35 if kind == "riser" else 0.05)
            off = int(max(0.0, at) * SR)
            end = min(len(mix), off + len(clip))
            mix[off:end] += clip[: end - off]
            cues.append({"at": round(float(s["start"]), 2), "kind": kind, "seg": i})

    peak = float(np.abs(mix).max())
    if peak > 0.95:
        mix *= 0.95 / peak
    out = OUT / "mix.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())
    rms_db = 20 * float(np.log10(np.sqrt((mix ** 2).mean()) + 1e-9))
    print(f"混音完成 {out}  {len(mix)/SR:.1f}s  峰值 {float(np.abs(mix).max()):.2f}  RMS {rms_db:.1f} dBFS")
    if cues:
        print("音效点：", ", ".join(f"{c['at']}s {c['kind']}" for c in cues[:8]), "…")
    (OUT / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
