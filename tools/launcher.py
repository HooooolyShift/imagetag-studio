"""桌面 exe 入口：找到环境与项目目录，然后拉起图形界面。

打包成 exe 后，读取同目录的 launcher.ini（可手改）：
    [app]
    python   = C:\\Users\\xxx\\.conda\\envs\\imtag\\pythonw.exe
    project  = E:\\文档\\ChatGPT\\图片标签分类
没有 ini 时使用打包时写死的默认值。
"""
from __future__ import annotations

import configparser
import os
import subprocess
import sys
from pathlib import Path

BUILD_PYTHON = r"@PYTHON@"
BUILD_PROJECT = r"@PROJECT@"


def read_config() -> tuple[str, str]:
    python, project = BUILD_PYTHON, BUILD_PROJECT
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    ini = here / "launcher.ini"
    if ini.exists():
        cp = configparser.ConfigParser()
        try:
            cp.read(ini, encoding="utf-8")
            python = cp.get("app", "python", fallback=python)
            project = cp.get("app", "project", fallback=project)
        except Exception:
            pass
    return python.strip('"'), project.strip('"')


def main() -> int:
    python, project = read_config()
    py = Path(python)
    if not py.exists():
        # 常见兜底：同目录下的 pythonw.exe / python.exe
        for cand in (py.with_name("pythonw.exe"), py.with_name("python.exe")):
            if cand.exists():
                py = cand
                break
    proj = Path(project)
    if not (py.exists() and proj.exists()):
        msg = (f"找不到运行环境。\n\npython: {python}\nproject: {project}\n\n"
               f"请修改与本程序同目录的 launcher.ini。")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "图片标签工坊", 0x10)
        except Exception:
            print(msg)
        return 1
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.setdefault("IMGTAG_HOME", str(proj))
    env["PYTHONPATH"] = str(proj) + os.pathsep + env.get("PYTHONPATH", "")
    exe = py if py.name.lower().startswith("pythonw") else py.with_name("pythonw.exe")
    if not exe.exists():
        exe = py
    creation = 0x08000000 if str(exe).lower().endswith("pythonw.exe") else 0
    try:
        subprocess.Popen([str(exe), "-m", "app.main"], cwd=str(proj), env=env,
                         creationflags=creation, close_fds=True)
    except Exception as exc:  # noqa: BLE001
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"启动失败：{exc}", "图片标签工坊", 0x10)
        except Exception:
            print("启动失败：", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
