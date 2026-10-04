"""推理引擎：WD14（二次元打标）、CLIP（自定义标签零样本）、人脸。"""
from __future__ import annotations

import os
from pathlib import Path

_ort_ready = False


def prepare_ort() -> None:
    """让 onnxruntime-gpu 用上 torch 自带的 CUDA/cuDNN 运行时（免装 CUDA Toolkit）。"""
    global _ort_ready
    if _ort_ready:
        return
    _ort_ready = True
    try:
        import torch  # noqa: F401
        lib = Path(torch.__file__).resolve().parent / "lib"
        if lib.exists():
            os.add_dll_directory(str(lib))
    except Exception:
        pass


def available_providers() -> list[str]:
    prepare_ort()
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:
        return []


def torch_device(prefer: str = "auto"):
    """返回 torch 设备字符串。"""
    import torch
    from .. import perf
    if perf.force_cpu():
        return "cpu"
    if prefer == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def ort_providers(prefer: str = "auto") -> list[str]:
    """onnxruntime 执行提供器（GPU 不可用自动回落 CPU）。"""
    prepare_ort()
    from .. import perf
    if perf.force_cpu():
        return ["CPUExecutionProvider"]
    av = available_providers()
    if prefer != "cpu" and "CUDAExecutionProvider" in av:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if prefer != "cpu" and "DmlExecutionProvider" in av:
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def describe_runtime() -> str:
    bits = []
    av = available_providers()
    if av:
        bits.append("onnx:" + ("GPU" if any("CUDA" in a or "Dml" in a for a in av) else "CPU"))
    try:
        import torch
        if torch.cuda.is_available():
            bits.append(f"torch:GPU({torch.cuda.get_device_name(0).split(' ')[-1]})")
        else:
            bits.append("torch:CPU")
    except Exception:
        bits.append("torch:未安装")
    return " / ".join(bits)
