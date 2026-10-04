"""无界面测试安装流程：直接调用安装程序里的 InstallWorker 做一次真实安装。

用法： python tools\\test_install.py [目标目录，默认 K:\\_install_test]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PKG = Path("K:\\ImageTagStudio_安装包")
sys.path.insert(0, str(HERE / "installer"))      # installer_gui.py
sys.path.insert(0, str(PKG / "payload"))         # app 包
TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("K:\\_install_test")


def main() -> int:
    import installer_gui as ig
    print("安装包目录:", PKG)
    ig.ROOT = PKG
    ig.PAYLOAD = PKG / "payload"
    if TARGET.exists():
        shutil.rmtree(TARGET, ignore_errors=True)
    w = ig.InstallWorker(TARGET, "auto", make_shortcut=False, keep_models_in_dir=True)
    w.log.connect(lambda s: print("   ", s[:160]))
    w.progress.connect(lambda v, s: print(f"   [{v:3d}%] {s}"))
    w._run()                       # 同步执行（不起线程）
    print("安装结束，检查产物：")
    checks = {
        "python": TARGET / "python" / "python.exe",
        "pythonw": TARGET / "python" / "pythonw.exe",
        "程序": TARGET / "app" / "main.py",
        "模型": TARGET / "models" / "wd-swinv2-tagger-v3" / "model.onnx",
        "CLIP": TARGET / "models" / "clip" / "CLIP-ViT-B-32-laion2B-s34B-b79K.bin",
        "人脸": TARGET / "models" / "insightface" / "w600k_r50.onnx",
        "启动器": TARGET / "图片标签工坊.exe",
        "launcher.ini": TARGET / "launcher.ini",
        "perf_profile": TARGET / "perf_profile.json",
        "卸载器": TARGET / "卸载.cmd",
    }
    ok = True
    for name, p in checks.items():
        hit = p.exists()
        ok &= hit
        print(f"   {'✓' if hit else '✗'} {name}: {p.name}")
    # 用装好的解释器验证关键依赖
    import subprocess
    py = TARGET / "python" / "python.exe"
    code = ("import sys;sys.path.insert(0,r'{t}');"
            "import torch,onnxruntime,PySide6,PIL,numpy,cv2,sklearn,open_clip;"
            "print('torch',torch.__version__,'cuda',torch.cuda.is_available());"
            "print('ort',onnxruntime.get_available_providers())").format(t=TARGET)
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(TARGET))
    print("   依赖自检:", (r.stdout or r.stderr).strip()[:400])
    print("结果:", "通过" if ok and "cuda True" in (r.stdout or "") else "有问题，请检查上面的日志")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
