"""把大安装包做成 GitHub Release 可上传的分卷，并生成一键合并脚本。

做法：先把整个安装包打成一个 zip，再对这个 zip 做**二进制切割**成 <2GB 的分卷
（必须切二进制：torch 的单个 whl 就有 2.34GB，超过 Release 单文件 2GB 上限，
按文件分卷切不开）。下载后运行「合并并安装.bat」自动拼回并解压。

用法： python tools\\split_release.py "K:\\ImageTagStudio_安装包" "K:\\release_parts" [分卷MB]
产物：payload.zip.001 / .002 … + 合并并安装.bat + SHA256.txt
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

PART_MB = 1900          # 每卷上限（GitHub 单文件 2GB 上限，留余量）


def merge_bat_text(part_names: list[str]) -> str:
    """生成与实际分卷数量一致的一键合并脚本。"""
    if not part_names:
        raise ValueError("至少需要一个分卷")
    copy_list = "+".join(part_names)
    return (
        "@echo off\r\nchcp 65001 >nul\r\ncd /d \"%~dp0\"\r\n"
        "echo ============================================\r\n"
        "echo   图片标签工坊：合并分卷并启动安装\r\n"
        "echo ============================================\r\n"
        "if not exist \"merged\" mkdir merged\r\n"
        "echo 正在合并分卷（约需几分钟）...\r\n"
        "rem 用 copy /b 把分卷拼回一个 zip（二进制切割必须这样拼）\r\n"
        f"copy /b {copy_list} payload_full.zip >nul\r\n"
        "if errorlevel 1 (\r\n"
        "  echo [错误] 分卷合并失败，请重新下载 SHA256.txt 中列出的全部分卷。\r\n"
        "  pause & exit /b 1\r\n"
        ")\r\n"
        "echo 正在解压...\r\n"
        "powershell -NoProfile -Command \"Expand-Archive -Path 'payload_full.zip' "
        "-DestinationPath 'merged' -Force\"\r\n"
        "if errorlevel 1 (\r\n"
        "  echo [错误] 解压失败。请确认磁盘空间充足，并检查安全软件是否拦截。\r\n"
        "  pause & exit /b 1\r\n"
        ")\r\n"
        "rem 热修复包可把新版安装器放在分卷旁边，优先使用它覆盖旧安装器。\r\n"
        "if exist \"图片标签工坊_安装程序.exe\" (\r\n"
        "  copy /y \"图片标签工坊_安装程序.exe\" \"merged\\图片标签工坊_安装程序.exe\" >nul\r\n"
        ")\r\n"
        "if not exist \"merged\\图片标签工坊_安装程序.exe\" (\r\n"
        "  echo [错误] 合并失败或缺少分卷，请确认所有 payload.zip.001/.002/... 都在同一个文件夹。\r\n"
        "  pause & exit /b 1\r\n)\r\n"
        "echo 合并完成，正在启动安装程序...\r\n"
        "start \"\" \"merged\\图片标签工坊_安装程序.exe\"\r\n"
    )


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("K:\\ImageTagStudio_安装包")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("K:\\release_parts")
    part_bytes = (int(sys.argv[3]) if len(sys.argv) > 3 else PART_MB) * 1024 * 1024
    out.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in src.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    print(f"源目录 {src}：{len(files)} 个文件 / {total / 1024 ** 3:.2f} GB")
    print(f"切成每卷 ≤{part_bytes / 1024 ** 2:.0f} MB，输出到 {out}")

    # 1) 打包（存储不压缩：whl/onnx 本身已压缩，省时间）
    whole = out / "payload.zip"
    with zipfile.ZipFile(whole, "w", zipfile.ZIP_STORED) as zf:
        for p in files:
            zf.write(p, p.relative_to(src).as_posix())
    print(f"  打包完成 {whole.name}：{whole.stat().st_size / 1024 ** 3:.2f} GB")

    # 2) 二进制切割
    written: list[Path] = []
    with open(whole, "rb") as f:
        idx = 1
        while True:
            chunk = f.read(part_bytes)
            if not chunk:
                break
            part = out / f"payload.zip.{idx:03d}"
            part.write_bytes(chunk)
            written.append(part)
            print(f"  {part.name}  {len(chunk) / 1024 ** 3:.2f} GB")
            idx += 1
    whole.unlink()

    # SHA256
    lines = []
    for p in written:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {p.name}")
        print(f"  {p.name}  {p.stat().st_size / 1024 ** 3:.2f} GB")
    (out / "SHA256.txt").write_text("\n".join(lines), encoding="utf-8")

    bat_text = merge_bat_text([p.name for p in written])
    bat = out / "merge_and_install.bat"
    bat.write_text(bat_text, encoding="utf-8")
    (out / "合并并安装.bat").write_text(bat_text, encoding="utf-8")
    print("已生成:", bat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
