"""打包桌面入口 exe + 桌面快捷方式。

思路：exe 本身只做“启动器”（体积几 MB、启动快），真正的程序在 conda 环境里跑，
这样模型、依赖升级都不用重新打包。生成的 launcher.ini 可以手改路径。

用法： python tools\\build_exe.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY_ENV = Path(sys.executable)
PYTHONW = PY_ENV.with_name("pythonw.exe")
EXE_NAME = "图片标签工坊"


def write_launcher() -> Path:
    src = (HERE / "tools" / "launcher.py").read_text(encoding="utf-8")
    src = src.replace("@PYTHON@", str(PYTHONW)).replace("@PROJECT@", str(HERE))
    out = HERE / "build" / "launcher_gen.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding="utf-8")
    return out


def main() -> int:
    print("1) 生成启动器源码")
    gen = write_launcher()

    print("2) 检查 PyInstaller")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call([str(PY_ENV), "-m", "pip", "install", "-i",
                               "https://pypi.tuna.tsinghua.edu.cn/simple", "pyinstaller"])

    print("3) 打包 exe")
    dist = HERE / "build" / "dist"
    icon = HERE / "assets" / "icon.ico"
    cmd = [
        str(PY_ENV), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", EXE_NAME, "--distpath", str(dist), "--workpath", str(HERE / "build" / "work"),
        "--specpath", str(HERE / "build")]
    if icon.exists():
        cmd += ["--icon", str(icon)]
    cmd.append(str(gen))
    subprocess.check_call(cmd)

    exe = dist / f"{EXE_NAME}.exe"
    target = HERE / f"{EXE_NAME}.exe"
    shutil.copyfile(exe, target)
    ini = HERE / "launcher.ini"
    ini.write_text(f"[app]\npython = {PYTHONW}\nproject = {HERE}\n", encoding="utf-8")
    print("   已生成:", target)

    print("4) 创建桌面快捷方式")
    desktop = Path(os.path.join(os.environ.get("USERPROFILE", str(Path.home())), "Desktop"))
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "桌面"
    lnk = desktop / f"{EXE_NAME}.lnk"
    ps = (
        "$W = New-Object -ComObject WScript.Shell; "
        f"$S = $W.CreateShortcut('{lnk}'); "
        f"$S.TargetPath = '{target}'; "
        f"$S.WorkingDirectory = '{HERE}'; "
        f"$S.IconLocation = '{target},0'; "
        f"$S.Description = '图片标签工坊 - 本地图片打标与检索'; "
        "$S.Save()"
    )
    try:
        subprocess.check_call(["powershell", "-NoProfile", "-Command", ps])
        print("   桌面快捷方式:", lnk)
    except Exception as exc:  # noqa: BLE001
        print("   创建快捷方式失败（可手动把 exe 拖到桌面）:", exc)
    print("\n完成。双击桌面「图片标签工坊」即可启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
