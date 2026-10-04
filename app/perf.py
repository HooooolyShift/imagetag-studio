"""性能挡位：榨干硬件 / 均衡 / 节能 / 只用 CPU。

一个挡位会同时影响：推理批大小、ONNX 线程数、torch 线程与半精度、缩略图线程数、
进程优先级（Windows）、以及是否强制走 CPU。切换挡位后重新加载引擎即可生效。
"""
from __future__ import annotations

import os

_CPU = os.cpu_count() or 8
_HALF = max(2, _CPU // 2)
_ECO = max(2, _CPU // 3)

PRESETS: dict[str, dict] = {
    "max": {
        "label": "榨干硬件",
        "desc": "线程用满 + 进程高优先级 + cudnn 穷举调优 + 缩略图线程最多（批大小按实测最优，"
                "不再往上堆：这台机器批 8 就已经把 GPU 跑满了，堆大反而变慢）。适合你离开电脑前让它全速跑完。",
        "wd14_batch": 8, "clip_batch": 16, "thumb_workers": 6,
        "ort_threads": 0, "torch_threads": _CPU, "priority": "high",
        "half": True, "cudnn_benchmark": True, "cudnn_exhaustive": True,
        "sleep": 0.0, "force_cpu": False,
    },
    "balanced": {
        "label": "均衡（默认）",
        "desc": "GPU 正常跑，线程数按物理核，CPU 保持正常优先级，日常使用推荐。",
        "wd14_batch": 8, "clip_batch": 16, "thumb_workers": 4,
        "ort_threads": _HALF, "torch_threads": _HALF, "priority": "normal",
        "half": True, "cudnn_benchmark": True, "cudnn_exhaustive": False,
        "sleep": 0.0, "force_cpu": False,
    },
    "eco": {
        "label": "节能",
        "desc": "小批次 + 线程数减半 + 进程降优先级 + 批间短暂停顿。速度慢一些，"
                "但基本不会拖慢你正在用的其它程序，笔记本也更省电、更凉。",
        "wd14_batch": 4, "clip_batch": 8, "thumb_workers": 2,
        "ort_threads": _ECO, "torch_threads": _ECO, "priority": "below",
        "half": True, "cudnn_benchmark": False, "cudnn_exhaustive": False,
        "sleep": 0.05, "force_cpu": False,
    },
    "cpu": {
        "label": "只用 CPU（不占显卡）",
        "desc": "完全不使用独立显卡，适合你同时在打游戏/渲染/跑其它 AI 的时候。"
                "速度约为显卡的 1/5~1/10，但显存零占用。",
        "wd14_batch": 4, "clip_batch": 8, "thumb_workers": 3,
        "ort_threads": _HALF, "torch_threads": _HALF, "priority": "below",
        "half": False, "cudnn_benchmark": False, "cudnn_exhaustive": False,
        "sleep": 0.0, "force_cpu": True,
    },
}

ORDER = ["max", "balanced", "eco", "cpu"]
_current: str = "balanced"
_applied_priority: str | None = None
_profile: dict | None = None


def load_profile(path: str | Path | None = None) -> dict | None:
    """读取安装时生成的 perf_profile.json（含硬件检测结果与参数覆盖）。"""
    global _profile
    from pathlib import Path as _P
    cands = [path] if path else []
    cands += [os.environ.get("IMGTAG_PROFILE") or "",
              _P(__file__).resolve().parent.parent / "perf_profile.json",
              _P(os.environ.get("LOCALAPPDATA", str(_P.home()))) / "ImageTagStudio" / "perf_profile.json"]
    try:
        from .hardware import read_profile
        _profile = read_profile(*[c for c in cands if c])
    except Exception:
        _profile = None
    return _profile


def profile() -> dict:
    return _profile or {}


def configure(settings) -> dict:
    """按设置选定挡位，并立即应用进程优先级 / torch 线程。返回当前预设。"""
    global _current
    if _profile is None:
        load_profile()
    mode = getattr(settings, "perf_mode", "balanced") or "balanced"
    prof_mode = (profile().get("default_mode") or "").strip()
    if prof_mode and getattr(settings, "perf_mode", "") in ("", "auto"):
        mode = prof_mode
    if mode not in PRESETS:
        mode = prof_mode if prof_mode in PRESETS else "balanced"
    _current = mode
    apply_process_priority(mode)
    apply_torch(mode)
    return PRESETS[mode]


def current() -> dict:
    base = dict(PRESETS.get(_current, PRESETS["balanced"]))
    base.update((profile().get("overrides") or {}).get(_current, {}) or {})
    return base


def mode() -> str:
    return _current


def label(mode: str | None = None) -> str:
    p = PRESETS.get(mode or _current, PRESETS["balanced"])
    return p["label"]


def apply_process_priority(mode: str | None = None) -> None:
    """Windows 下调整进程优先级（节能模式降到 Below Normal，避免抢你正在用的程序）。"""
    global _applied_priority
    want = (PRESETS.get(mode or _current, PRESETS["balanced"])["priority"])
    if _applied_priority == want:
        return
    _applied_priority = want
    if os.name != "nt":
        return
    try:
        import ctypes
        cls = {"high": 0x00000080, "normal": 0x00000020, "below": 0x00004000}.get(want, 0x00000020)
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), cls)
    except Exception:
        pass


def apply_torch(mode: str | None = None) -> None:
    # torch 很重（约 400MB 常驻），没用到时不要为了设线程数把它加载起来
    import sys
    if "torch" not in sys.modules:
        return
    try:
        import torch
        p = PRESETS.get(mode or _current, PRESETS["balanced"])
        torch.set_num_threads(int(p["torch_threads"]))
        if torch.cuda.is_available() and not p["force_cpu"]:
            torch.backends.cudnn.benchmark = bool(p["cudnn_benchmark"])
            try:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
            except Exception:
                pass
    except Exception:
        pass


def batches() -> tuple[int, int]:
    p = current()
    return int(p["wd14_batch"]), int(p["clip_batch"])


def thumb_workers() -> int:
    return int(current()["thumb_workers"])


def sleep_between_batches() -> float:
    return float(current().get("sleep", 0.0))


def force_cpu() -> bool:
    return bool(current().get("force_cpu"))


def use_half() -> bool:
    return bool(current().get("half", True))


def ort_session_options(enable_mem_pattern: bool = True):
    """统一的 onnxruntime SessionOptions（线程数 / 图优化 / cudnn 调优策略）。"""
    import onnxruntime as ort
    p = current()
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.enable_mem_pattern = enable_mem_pattern
    so.enable_cpu_mem_arena = True
    threads = int(p["ort_threads"])
    if threads > 0:
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = max(1, threads // 2)
    if p.get("cudnn_exhaustive"):
        try:
            so.add_session_config_entry("cudnn_conv_algo_search", "EXHAUSTIVE")
        except Exception:
            pass
    else:
        try:
            so.add_session_config_entry("cudnn_conv_algo_search", "HEURISTIC")
        except Exception:
            pass
    return so


def describe() -> str:
    p = current()
    bits = [f"挡位：{p['label']}"]
    if p["force_cpu"]:
        bits.append("仅 CPU")
    else:
        bits.append("GPU" + ("+fp16" if p["half"] else ""))
    bits.append(f"WD14批{p['wd14_batch']}")
    bits.append(f"CLIP批{p['clip_batch']}")
    bits.append(f"缩略图线程{p['thumb_workers']}")
    return " · ".join(bits)
