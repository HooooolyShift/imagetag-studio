"""生成「更新包」：只含程序与脚本（不含 wheels/models），可直接覆盖任意旧版本。

更新包结构（拷到安装目录里双击 更新.cmd 即可）：
    ImageTagStudio_更新包\
      更新.cmd              把 app/tools/assets/说明书 覆盖到安装目录
      app\ tools\ assets\   最新代码
      说明书_图片标签工坊.pdf
      图片标签工坊_安装程序.exe（可选，方便修复安装）
    update_manifest.json    版本号 + 文件清单 + 生成时间

设计原则：更新包**只覆盖程序文件**，不碰 models/wheels/python/用户数据，
所以无论对方装的是哪个旧版本，都不会因为缺文件而失败；若缺 Python/模型，
更新包里的安装程序可以直接"修复安装"补上。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("K:\\ImageTagStudio_更新包")
PKG = Path("K:\\ImageTagStudio_安装包")
VERSION = "1.1"

UPDATE_CMD = r"""@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo   图片标签工坊 更新程序
echo ============================================
set /p TARGET=请输入安装目录（例如 K:\ImageTagStudio，直接回车用上次的）:
if "%TARGET%"=="" set /p TARGET=安装目录不能为空，请重新输入:
if not exist "%TARGET%\app\main.py" (
  echo [错误] 这个目录看起来不是安装目录（找不到 app\main.py）
  echo        请确认路径，或先用「图片标签工坊_安装程序.exe」安装一份。
  pause & exit /b 1
)
echo.
echo 正在更新程序文件（不会动 models / python / 你的数据）...
robocopy "app" "%TARGET%\app" /MIR /NFL /NDL /NJH /NJS /NP >nul
robocopy "tools" "%TARGET%\tools" /MIR /NFL /NDL /NJH /NJS /NP >nul
robocopy "assets" "%TARGET%\assets" /MIR /NFL /NDL /NJH /NJS /NP >nul
if exist "说明书_图片标签工坊.pdf" copy /y "说明书_图片标签工坊.pdf" "%TARGET%\" >nul
if exist "图片标签工坊_安装程序.exe" copy /y "图片标签工坊_安装程序.exe" "%TARGET%\" >nul
echo.
echo [完成] 已更新到最新版。双击桌面快捷方式即可使用。
echo        如果启动报错，请运行同目录的「图片标签工坊_安装程序.exe」选择修复安装。
pause
"""


def main() -> int:
    if DEST.exists():
        shutil.rmtree(DEST, ignore_errors=True)
    DEST.mkdir(parents=True, exist_ok=True)
    for name in ("app", "tools", "assets"):
        shutil.copytree(HERE / name, DEST / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    pdf = HERE / "说明书_图片标签工坊.pdf"
    if pdf.exists():
        shutil.copy2(pdf, DEST / pdf.name)
    exe = PKG / "图片标签工坊_安装程序.exe"
    if exe.exists():
        shutil.copy2(exe, DEST / exe.name)
    (DEST / "更新.cmd").write_text(UPDATE_CMD, encoding="utf-8")
    files = sorted(str(p.relative_to(DEST)) for p in DEST.rglob("*") if p.is_file())
    (DEST / "update_manifest.json").write_text(json.dumps(
        {"version": VERSION, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "files": files, "note": "只覆盖程序文件，不含 models/wheels/python；可直接用于任意旧版本"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"更新包已生成：{DEST}（{size / 1024 ** 2:.1f} MB，{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
