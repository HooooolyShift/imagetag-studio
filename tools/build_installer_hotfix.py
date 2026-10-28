"""生成小型安装器热修包。

用法：
    python tools\\build_installer_hotfix.py <installer.exe> <输出目录> [版本] [分卷列表]

分卷列表用逗号分隔；默认适配 v1.5 Release 的 payload.zip.001/002/003。
热修包不包含 4.5GB 的 Python、wheel 和模型，只包含新的引导安装器及合并脚本。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from split_release import merge_bat_text  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_immediate_fix(path: Path, installer_name: str) -> None:
    path.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if not exist \"merged\\payload\" (\r\n"
        "  echo [提示] 还没有合并旧安装包。\r\n"
        "  echo 请将本压缩包解压到 payload.zip.001 所在目录，再运行 merge_and_install.bat。\r\n"
        "  pause & exit /b 1\r\n"
        ")\r\n"
        f"if not exist \"{installer_name}\" (\r\n"
        "  echo [错误] 热修包不完整：缺少新版安装器。\r\n"
        "  pause & exit /b 1\r\n"
        ")\r\n"
        f"copy /y \"{installer_name}\" \"merged\\{installer_name}\" >nul\r\n"
        "if errorlevel 1 (\r\n"
        "  echo [错误] 替换安装器失败，请关闭正在运行的旧安装器后重试。\r\n"
        "  pause & exit /b 1\r\n"
        ")\r\n"
        "echo 已替换为无 Qt 依赖的新版安装器，正在启动...\r\n"
        f"start \"\" \"merged\\{installer_name}\"\r\n",
        encoding="utf-8",
    )


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    installer = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    version = sys.argv[3] if len(sys.argv) > 3 else "v1.5.1-installer"
    parts = (
        [x.strip() for x in sys.argv[4].split(",") if x.strip()]
        if len(sys.argv) > 4
        else ["payload.zip.001", "payload.zip.002", "payload.zip.003"]
    )
    if not installer.is_file():
        raise FileNotFoundError(f"找不到安装器：{installer}")
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="imtag_installer_fix_", dir=out) as tmp:
        stage = Path(tmp)
        dst_exe = stage / installer.name
        shutil.copy2(installer, dst_exe)
        (stage / "merge_and_install.bat").write_text(
            merge_bat_text(parts), encoding="utf-8"
        )
        (stage / "合并并安装.bat").write_text(
            merge_bat_text(parts), encoding="utf-8"
        )
        write_immediate_fix(stage / "立即修复并安装.bat", installer.name)
        (stage / "修复说明.txt").write_text(
            "图片标签工坊 · 安装器热修复\r\n"
            "================================\r\n\r\n"
            "这个包只替换安装器，不包含 Python、模型和 wheels，因此无需重新下载约 4.7GB。\r\n\r\n"
            "情况 A：已经运行过旧版 merge_and_install.bat，并生成了 merged 文件夹\r\n"
            "1. 关闭“Unhandled exception in script”错误窗口；\r\n"
            "2. 把本压缩包解压到旧版 merge_and_install.bat 和 payload.zip.001 所在目录；\r\n"
            "3. 双击“立即修复并安装.bat”。\r\n\r\n"
            "情况 B：还没下载或合并安装包\r\n"
            "1. 到 v1.5 Release 下载 payload.zip.001、.002、.003；\r\n"
            "2. 把本压缩包解压到这三个分卷旁边；\r\n"
            "3. 双击“merge_and_install.bat”。脚本会自动合并、解压并优先使用新版安装器。\r\n\r\n"
            "说明：新版“安装器”自身已移除 Qt/PySide6 依赖，VC++ 运行库问题不会再阻止安装入口启动。\r\n"
            "应用安装完成后仍会在安装目录内使用自带的 PySide6 运行环境。\r\n",
            encoding="utf-8-sig",
        )

        zip_path = out / f"installer_fix_{version}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for p in sorted(stage.iterdir(), key=lambda x: x.name):
                if p.is_file():
                    zf.write(p, p.name)

    lines = [
        f"{sha256(installer)}  {installer.name}",
        f"{sha256(zip_path)}  {zip_path.name}",
    ]
    (out / "SHA256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"热修包：{zip_path}（{zip_path.stat().st_size / 1024 ** 2:.1f} MB）")
    print(f"安装器 SHA256：{lines[0].split()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
