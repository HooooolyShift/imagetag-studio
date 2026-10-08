"""ComfyUI 自动接入：找安装目录、探活端口、把它拉起来。

只用标准库。给原生界面上的「自动检测 / 测试连接 / 启动 ComfyUI」三个按钮用，
也给第一次安装的用户省事（他们只要点一下"自动检测"）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_PORTS = (8188, 8189, 8000)


def probe_api(url: str, timeout: float = 3.0) -> bool:
    """这个地址是不是活着的 ComfyUI API。"""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/system_stats", timeout=timeout) as r:
            data = json.load(r)
        return "system_stats" not in data and ("devices" in data or "system" in data)
    except Exception:                                        # noqa: BLE001
        return False


def find_live_api(ports=DEFAULT_PORTS) -> str | None:
    """在本机常见端口上找已经在跑的 ComfyUI，返回它的 API 地址。"""
    for port in ports:
        url = f"http://127.0.0.1:{port}"
        if probe_api(url):
            return url
    return None


def _is_comfy_dir(path: Path) -> bool:
    """判断一个目录像不像 ComfyUI 安装目录。"""
    if not path.is_dir():
        return False
    if not (path / "main.py").exists():
        return False
    return (path / "comfy").is_dir() or (path / "nodes.py").exists() or (path / "custom_nodes").is_dir()


def _candidate_roots() -> list[Path]:
    """常见位置 + 环境变量；不做整盘扫描（太慢）。"""
    out: list[Path] = []
    env = os.environ.get("COMFYUI_PATH") or os.environ.get("COMFYUI_DIR")
    if env:
        out.append(Path(env))
    home = Path.home()
    for drive in ("C:", "D:", "E:", "F:", "G:"):
        out += [
            Path(f"{drive}/ComfyUI"),
            Path(f"{drive}/ComfyUI_windows_portable/ComfyUI"),
            Path(f"{drive}/AI/ComfyUI"),
            Path(f"{drive}/Programs/ComfyUI"),
        ]
    out += [home / "ComfyUI", home / "Documents" / "ComfyUI", home / "Desktop" / "ComfyUI"]
    # 秋叶整合包常见结构：<某处>/ComfyUI-aki-*/ComfyUI-aki-*（里面才是 main.py）
    for drive in ("C:", "D:", "E:", "F:", "G:"):
        base = Path(f"{drive}/ComfyUI")
        if base.is_dir():
            try:
                for sub in base.iterdir():
                    if sub.is_dir() and "aki" in sub.name.lower():
                        out.append(sub)
                        for sub2 in sub.iterdir():
                            if sub2.is_dir():
                                out.append(sub2)
            except OSError:
                pass
    return out


def find_running_comfy() -> tuple[str | None, Path | None]:
    """从进程命令行里找正在跑的 ComfyUI：返回 (API 地址, 安装目录)。"""
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*main.py*' } | "
            "Select-Object -ExpandProperty CommandLine")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20)
        for line in (out.stdout or "").splitlines():
            cmd = line.strip()
            if "main.py" not in cmd:
                continue
            # 命令行形如： "...\python.exe" main.py --listen ...（工作目录可能是别的）
            port = 8188
            if "--port" in cmd:
                try:
                    port = int(cmd.split("--port", 1)[1].split()[0].strip())
                except Exception:                            # noqa: BLE001
                    pass
            url = f"http://127.0.0.1:{port}"
            if probe_api(url):
                return url, None
    except Exception:                                        # noqa: BLE001
        pass
    return None, None


def detect(deep_dirs: bool = True) -> dict:
    """一次性做完整检测：返回 {api, comfy_dir, python, candidates, running}。"""
    report = {"api": None, "comfy_dir": None, "python": None, "candidates": [], "running": False}
    api = find_live_api()
    if api:
        report["api"] = api
        report["running"] = True
    api2, _dir = find_running_comfy()
    if api2 and not report["api"]:
        report["api"] = api2
        report["running"] = True

    cands: list[Path] = []
    # 已配置过的路径优先
    if _dir:
        cands.append(Path(_dir))
    for root in _candidate_roots():
        for p in (root, *([] if not deep_dirs else []) ):
            if _is_comfy_dir(p) and p not in cands:
                cands.append(p)
    report["candidates"] = [str(p) for p in cands]
    if cands:
        report["comfy_dir"] = str(cands[0])
        py = cands[0] / "python" / "python.exe"
        embedded = cands[0] / "python_embeded" / "python.exe"
        report["python"] = str(py if py.exists() else (embedded if embedded.exists() else Path(sys.executable)))
    return report


def start_comfy(comfy_dir: str, port: int = 8188, clean: bool = False,
                extra_args: list[str] | None = None) -> tuple[bool, str]:
    """把 ComfyUI 拉起来（后台、无窗口）。返回 (是否成功, 说明)。"""
    root = Path(comfy_dir)
    if not (root / "main.py").exists():
        return False, f"这个目录里没有 main.py：{root}"
    py = root / "python" / "python.exe"
    if not py.exists():
        py = root / "python_embeded" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    args = [str(py), "main.py", "--listen", "127.0.0.1", "--port", str(port)]
    if clean:
        args.append("--disable-all-custom-nodes")
    if extra_args:
        args += extra_args
    try:
        creation = 0
        if sys.platform == "win32":
            creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(args, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=creation)
    except Exception as exc:                                 # noqa: BLE001
        return False, f"启动失败：{exc}"
    for _ in range(40):                                      # 最多等 2 分钟
        time.sleep(3)
        if probe_api(f"http://127.0.0.1:{port}"):
            return True, f"已启动：http://127.0.0.1:{port}"
    return False, "已尝试启动，但 2 分钟内没连上（看 ComfyUI 控制台的报错）"


if __name__ == "__main__":                                    # 方便命令行自检
    import pprint
    pprint.pprint(detect())
