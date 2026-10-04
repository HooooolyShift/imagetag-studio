"""把 K 盘里属于本项目的文件集中到 K:\\ImageTagStudio\\ 一个文件夹里。

不触碰：K:\\pictures（你的图库）、文件索引复刻包、CodexAppInstaller、DeepSeekHarness 等无关目录。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(r"K:\ImageTagStudio")
SRC_PROJECT = Path(__file__).resolve().parent.parent


def move(src: str, dst_rel: str) -> None:
    s, d = Path(src), ROOT / dst_rel
    if not s.exists():
        print("  （不存在，跳过）", src)
        return
    if d.exists():
        print("  （目标已存在，跳过）", d)
        return
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    print("  ✓", s.name, "->", d)


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    print("集中到:", ROOT)
    move(r"K:\ImageTagStudio_安装包", "安装包")
    move(r"K:\ImageTagStudio_安装包.7z", "安装包.7z")
    move(r"K:\ImageTagStudio_更新包", "更新包")
    move(r"K:\ImageTagStudio_更新包.7z", "更新包.7z")
    move(r"K:\release_parts", "分卷发布（可删，可由安装包重新生成）")
    # 我的临时目录：把里面的更新包 zip 并进"更新包"，然后删掉
    scratch = Path(r"K:\release_assets")
    if scratch.exists():
        for p in scratch.glob("*.zip"):
            dst = ROOT / "更新包" / p.name
            if not dst.exists():
                shutil.copy2(p, dst)
                print("  ✓ 复制", p.name, "-> 更新包/")
        shutil.rmtree(scratch, ignore_errors=True)
        print("  ✓ 已删除临时目录 release_assets")
    # 源码快照（含 .git，便于以后直接 git pull/push）
    dst_src = ROOT / "源码"
    if not dst_src.exists():
        ignore = shutil.ignore_patterns("models", ".wheels", "build", "__pycache__", "*.pyc",
                                        "manual_shots", ".selftest_home", "testdata_work",
                                        "perf_profile.json", "launcher.ini", "*.log")
        shutil.copytree(SRC_PROJECT, dst_src, ignore=ignore)
        print("  ✓ 源码快照 -> 源码/")
    print("\n最终结构：")
    for p in sorted(ROOT.iterdir()):
        if p.is_dir():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            n = sum(1 for f in p.rglob("*") if f.is_file())
            print("  %-42s %8.2f GB  %4d 文件" % (p.name, size / 1024 ** 3, n))
        else:
            print("  %-42s %8.2f GB" % (p.name, p.stat().st_size / 1024 ** 3))
    total = sum(f.stat().st_size for f in ROOT.rglob("*") if f.is_file())
    print("  合计: %.2f GB" % (total / 1024 ** 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
