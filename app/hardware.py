"""硬件检测与「自动调参」：安装时和首次启动时都会跑一次。

检测 CPU/内存/显卡/显存/剩余空间，给出推荐挡位与具体参数（批大小、线程数等），
结果写入 perf_profile.json，程序启动时读取并覆盖内置预设。
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: int = 12) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def detect_gpus() -> list[dict]:
    gpus: list[dict] = []
    txt = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
                "--format=csv,noheader,nounits"])
    for line in txt.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and parts[0]:
            try:
                gpus.append({"vendor": "NVIDIA", "name": parts[0], "vram_mb": int(float(parts[1])),
                             "vram_free_mb": int(float(parts[2])), "driver": parts[3], "compute": parts[4]})
            except Exception:
                continue
    if not gpus:
        txt = _run(["wmic", "path", "win32_VideoController", "get", "name,AdapterRAM", "/format:csv"])
        for line in txt.strip().splitlines():
            if not line or line.count(",") < 2 or line.startswith("Node"):
                continue
            name = line.split(",")[-1].strip()
            if name:
                gpus.append({"vendor": "other", "name": name, "vram_mb": 0, "vram_free_mb": 0,
                             "driver": "", "compute": ""})
    return gpus


def detect(install_dir: str | Path | None = None) -> dict:
    import shutil
    cpu_name = platform.processor() or ""
    cores = os.cpu_count() or 4
    physical = cores
    # 优先用 PowerShell CIM（新系统上 wmic 可能已被移除）
    try:
        txt = _run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,"
                    "NumberOfLogicalProcessors | ConvertTo-Json -Compress"], timeout=20)
        data = json.loads(txt.strip().splitlines()[-1]) if txt.strip() else {}
        cpu_name = (data.get("Name") or cpu_name).strip()
        physical = int(data.get("NumberOfCores") or physical)
        cores = int(data.get("NumberOfLogicalProcessors") or cores)
    except Exception:
        # 退路：wmic 单列查询
        for key, attr in (("Name", "name"), ("NumberOfCores", "physical"),
                          ("NumberOfLogicalProcessors", "logical")):
            txt = _run(["wmic", "cpu", "get", key])
            vals = [x.strip() for x in txt.splitlines()[1:] if x.strip()]
            if not vals:
                continue
            if attr == "name":
                cpu_name = vals[0]
            else:
                nums = re.findall(r"\d{1,3}", vals[0])
                if nums:
                    if attr == "physical":
                        physical = int(nums[0])
                    else:
                        cores = int(nums[0])
    # 内存：直接用 Windows API，最可靠
    ram_gb = 0.0
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            ram_gb = round(st.ullTotalPhys / 1024 ** 3, 1)
    except Exception:
        try:
            txt = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"])
            m = re.findall(r"\d{6,}", txt)
            if m:
                ram_gb = round(int(m[0]) / 1024 ** 3, 1)
        except Exception:
            pass
    target = Path(install_dir) if install_dir else Path("E:\\")
    try:
        free_gb = round(shutil.disk_usage(str(target.anchor or target)).free / 1024 ** 3, 1)
    except Exception:
        free_gb = 0.0
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": cpu_name.strip() or "Unknown CPU",
        "cores_physical": int(physical),
        "cores_logical": int(cores),
        "ram_gb": ram_gb,
        "gpus": detect_gpus(),
        "install_dir": str(install_dir or ""),
        "free_gb": free_gb,
        "torch_cuda": None,     # 由 pip 装完后回填
    }


def recommend(hw: dict) -> dict:
    """按硬件给出挡位与参数覆盖值。返回 {'mode':..., 'overrides':{...}, 'reasons':[...]}"""
    cores = max(2, int(hw.get("cores_logical") or 4))
    physical = max(1, int(hw.get("cores_physical") or cores // 2))
    ram = float(hw.get("ram_gb") or 0)
    nv = next((g for g in hw.get("gpus", []) if g.get("vendor") == "NVIDIA"), None)
    vram = int(nv.get("vram_mb") or 0) if nv else 0
    reasons: list[str] = []
    overrides: dict = {}

    if not nv or vram < 2048:
        mode = "cpu"
        reasons.append("没有可用的 NVIDIA 显卡（或显存 <2GB）→ 建议「只用 CPU」挡，避免显存不足报错")
    elif vram < 4096:
        mode = "eco"
        overrides.update({"wd14_batch": 3, "clip_batch": 6, "thumb_workers": min(3, max(2, physical // 4))})
        reasons.append(f"显存 {vram} MB 偏小 → 用小批次（WD14 批3 / CLIP 批6），防止显存溢出")
    elif vram < 10240:
        mode = "balanced"
        overrides.update({"wd14_batch": 8, "clip_batch": 16,
                          "thumb_workers": min(4, max(2, physical // 4))})
        reasons.append(f"显存 {vram} MB → 批 8/16 已能把这张卡跑满（实测再堆大批反而更慢）")
    else:
        mode = "max"
        overrides.update({"wd14_batch": 12, "clip_batch": 24,
                          "thumb_workers": min(8, max(4, physical // 3))})
        reasons.append(f"显存 {vram} MB 充足 → 允许更大批次 + 高优先级全速跑")

    if physical >= 16:
        overrides.update({"ort_threads": physical, "torch_threads": min(cores, physical + 4)})
        reasons.append(f"{physical} 物理核 → ONNX/torch 线程放宽到 {physical}")
    elif physical <= 4:
        overrides.update({"ort_threads": physical, "torch_threads": physical,
                          "thumb_workers": min(2, overrides.get("thumb_workers", 2))})
        reasons.append(f"只有 {physical} 核 → 限制线程数，避免和系统抢 CPU")

    if ram and ram < 8:
        overrides.update({"wd14_batch": min(overrides.get("wd14_batch", 8), 4),
                          "clip_batch": min(overrides.get("clip_batch", 16), 8),
                          "thumb_workers": 2})
        reasons.append(f"内存 {ram} GB 偏小 → 降低批次与缩略图线程，防止换页卡顿")
    elif ram and ram < 16:
        reasons.append(f"内存 {ram} GB：可正常使用，跑全库时少开其它大程序更稳")
    else:
        reasons.append(f"内存 {ram} GB：充足")

    free = float(hw.get("free_gb") or 0)
    if free and free < 20:
        reasons.append(f"目标盘剩余 {free} GB：装程序+模型约需 8~10 GB，建议换盘或先清理")

    return {"mode": mode, "overrides": overrides, "reasons": reasons}


def build_profile(hw: dict, rec: dict) -> dict:
    return {"generated_by": "ImageTagStudio installer", "hardware": hw,
            "default_mode": rec["mode"], "overrides": {rec["mode"]: rec["overrides"]},
            "reasons": rec["reasons"]}


def write_profile(path: str | Path, profile: dict) -> None:
    Path(path).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def read_profile(*candidates: str | Path) -> dict | None:
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None
