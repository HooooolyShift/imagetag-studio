"""构建 K 盘离线安装包：payload（Python+wheels+程序+模型）+ 安装程序 exe。

用法： python tools\\build_installer.py [目标目录，默认 K:\\ImageTagStudio_安装包]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = Path(sys.executable)
DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("K:\\ImageTagStudio_安装包")
PAYLOAD = DEST / "payload"
CACHE = HERE / ".cache_dl"

PY_EMBED_URL = "https://mirrors.tuna.tsinghua.edu.cn/python/3.10.11/python-3.10.11-embed-amd64.zip"
TARO_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_DIR = HERE / ".wheels"

# 运行时依赖（不含打包工具）；torch/torchvision 用本地已有的 wheel
DEPS = [
    "PySide6==6.11.2", "PySide6_Addons==6.11.2", "PySide6_Essentials==6.11.2", "shiboken6==6.11.2",
    "onnxruntime-gpu==1.23.2", "numpy==2.2.6", "pillow==12.3.0", "opencv-python==5.0.0.93",
    "scikit-learn==1.7.2", "scipy==1.15.3", "joblib==1.6.0", "threadpoolctl==3.7.0",
    "open_clip_torch==3.3.0", "timm==1.0.30", "regex==2026.9.29", "ftfy==6.3.1", "wcwidth==0.9.1",
    "safetensors==0.8.0", "huggingface_hub==2.1.1", "hf-xet==1.6.0", "filelock==4.0.10",
    "fsspec==2026.9.0", "typing_extensions==4.16.0", "sympy==1.13.1", "networkx==3.4.2",
    "Jinja2==3.1.6", "MarkupSafe==3.0.4", "mpmath==1.3.0", "tqdm==4.70.1", "PyYAML==6.0.3",
    "protobuf==7.36.2", "flatbuffers==25.12.19", "coloredlogs==15.0.1", "humanfriendly==10.0",
    "pyreadline3==3.5.6", "tomli==2.4.1", "colorama==0.4.6", "click==8.5.0", "certifi",
    "anyio==4.15.1", "idna==3.20", "h11==0.16.0", "exceptiongroup==1.3.1", "truststore==0.10.4",
    "packaging", "cloudpickle==3.1.2", "httpx2==2.13.1", "httpcore2==2.13.1",
    "pip", "setuptools", "wheel",
]


def run(cmd: list[str]) -> int:
    print("  $", " ".join(str(c) for c in cmd[:8]) + (" …" if len(cmd) > 8 else ""))
    return subprocess.call(cmd)


def fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    print(f"  下载 {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 512)
    return dest


def build_python() -> None:
    z = fetch(PY_EMBED_URL, CACHE / "python-3.10.11-embed-amd64.zip")
    out = PAYLOAD / "python"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(out)
    # 让嵌入式解释器能用 site-packages
    for pth in out.glob("python*._pth"):
        txt = pth.read_text(encoding="utf-8")
        txt = txt.replace("#import site", "import site")
        if "import site" not in txt:
            txt += "\nimport site\n"
        pth.write_text(txt, encoding="utf-8")
    print("  嵌入式 Python 就绪:", out)


def build_wheels() -> None:
    w = PAYLOAD / "wheels"
    w.mkdir(parents=True, exist_ok=True)
    # 本地已有的 torch / torchvision（cu121）
    for f in TORCH_DIR.glob("torch-*.whl"):
        shutil.copy2(f, w / f.name)
    for f in TORCH_DIR.glob("torchvision-*.whl"):
        shutil.copy2(f, w / f.name)
    have = {p.name.split("-")[0].lower() for p in w.glob("*.whl")}
    todo = [d for d in DEPS if d.split("==")[0].split("[")[0].lower() not in have]
    for d in todo:
        run([str(PY), "-m", "pip", "download", d, "--no-deps", "--dest", str(w),
             "-i", TARO_MIRROR])
    n = len(list(w.glob("*")))
    size = sum(f.stat().st_size for f in w.glob("*") if f.is_file())
    print(f"  wheels: {n} 个文件，{size / 1024 ** 3:.2f} GB")


def copy_app() -> None:
    for name in ("app", "tools", "assets"):
        src, dst = HERE / name, PAYLOAD / name
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("README.md", "run.cmd", "图片标签工坊.exe", "说明书_图片标签工坊.pdf"):
        if (HERE / name).exists():
            shutil.copy2(HERE / name, PAYLOAD / name)
    # 运行时依赖清单（供离线 pip 使用）
    reqs = [d for d in DEPS if not d.startswith(("pip", "setuptools", "wheel"))]
    reqs += ["torch==2.5.1+cu121", "torchvision==0.20.1+cu121"]
    (PAYLOAD / "requirements.txt").write_text("\n".join(reqs), encoding="utf-8")
    print("  程序文件 + requirements.txt 就绪")


def copy_models() -> None:
    src, dst = HERE / "models", PAYLOAD / "models"
    dst.mkdir(parents=True, exist_ok=True)
    for sub in ("wd-swinv2-tagger-v3", "insightface", "clip"):
        s = src / sub
        if s.exists():
            shutil.copytree(s, dst / sub, dirs_exist_ok=True)
    # CLIP 权重还可能缓存在 HF 目录结构里，一并带上
    for extra in ("models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K",):
        s = src / extra
        if s.exists():
            shutil.copytree(s, dst / extra, dirs_exist_ok=True)
    size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    print(f"  模型: {size / 1024 ** 3:.2f} GB")


def build_installer_exe() -> None:
    gen = HERE / "build" / "installer_gen.py"
    gen.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "installer" / "installer_gui.py", gen)
    dist = HERE / "build" / "installer_dist"
    icon = HERE / "assets" / "icon.ico"
    cmd = [str(PY), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
           "--name", "图片标签工坊_安装程序", "--distpath", str(dist),
           "--workpath", str(HERE / "build" / "installer_work"),
           "--specpath", str(HERE / "build"),
           "--paths", str(HERE), "--hidden-import", "app.hardware", "--hidden-import", "app.perf"]
    # 安装器自身只需要 PySide6 的核心组件；把推理/科学计算栈和重型 Qt 模块排除掉，
    # 否则会被 PyInstaller 打进去，安装器会膨胀到 2.5 GB。
    for mod in ("torch", "torchvision", "onnxruntime", "cv2", "sklearn", "scipy", "open_clip",
                "timm", "huggingface_hub", "transformers", "matplotlib", "pandas", "numpy",
                "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
                "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
                "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
                "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtPdf",
                "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest",
                "PySide6.QtBluetooth", "PySide6.QtSensors", "PySide6.QtPositioning",
                "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtSpatialAudio",
                "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtNfc",
                "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"):
        cmd += ["--exclude-module", mod]
    if icon.exists():
        cmd += ["--icon", str(icon)]
    cmd.append(str(gen))
    run(cmd)
    src = dist / "图片标签工坊_安装程序.exe"
    if src.exists():
        shutil.copy2(src, DEST / src.name)
        print("  安装程序 exe:", DEST / src.name)


def main() -> int:
    print(f"目标目录：{DEST}")
    DEST.mkdir(parents=True, exist_ok=True)
    exe_only = "--exe-only" in sys.argv
    if not exe_only:
        print("1) 嵌入式 Python"); build_python()
        print("2) 依赖 wheels（离线）"); build_wheels()
        print("3) 程序文件"); copy_app()
        print("4) 本地模型"); copy_models()
    print("5) 安装程序 exe"); build_installer_exe()
    total = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    (DEST / "安装说明.txt").write_text(
        "图片标签工坊 · 离线安装说明\r\n"
        "============================\r\n\r\n"
        "1. 双击「图片标签工坊_安装程序.exe」\r\n"
        "2. 程序会自动检测本机 CPU/内存/显卡，给出推荐性能挡位（可手动改）\r\n"
        "3. 选择安装位置（默认装在剩余空间最大的盘）\r\n"
        "4. 点「开始安装」：复制程序与模型 → 解压内置 Python → 离线安装依赖\r\n"
        f"   全程不联网，安装包自带全部运行库与模型（约 {total / 1024 ** 3:.1f} GB）\r\n"
        "5. 安装完成后桌面会有快捷方式；卸载执行安装目录里的「卸载.cmd」\r\n\r\n"
        "注意：安装过程约需 5~15 分钟（取决于磁盘速度），期间请勿关闭窗口。\r\n",
        encoding="utf-8")
    print(f"\n完成：{DEST}（合计 {total / 1024 ** 3:.2f} GB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
