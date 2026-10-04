"""性能实测：各挡位下 WD14 / CLIP 的吞吐（用本机真实模型）。

用法： python tools\\bench_perf.py [图片数]
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["IMGTAG_DATA"] = str(HERE / ".bench_perf_home")
os.environ["IMGTAG_MODELS"] = str(HERE / "models")
shutil.rmtree(HERE / ".bench_perf_home", ignore_errors=True)

from app import perf                              # noqa: E402
from app.config import Settings                   # noqa: E402
from app.engines.clip_zs import ClipZeroShot      # noqa: E402
from app.engines.wd14 import WD14Tagger           # noqa: E402
from app import imaging                           # noqa: E402


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    models = HERE / "models"
    tmp = Path(tempfile.mkdtemp(prefix="benchperf_"))
    srcs = [HERE / "testdata" / "Wikipe-tan_full_length.png",
            HERE / "testdata" / "Anime_Girl.png",
            HERE / "testdata" / "Barack_Obama.jpg",
            HERE / "testdata" / "Angela_Merkel_2019_cropped.jpg"]
    files = []
    for i in range(n):
        p = tmp / f"b{i:04d}{srcs[i % len(srcs)].suffix.lower()}"
        shutil.copy(srcs[i % len(srcs)], p)
        files.append(p)
    imgs = [imaging.load_rgb(p, 1024) for p in files]
    print(f"素材：{len(imgs)} 张（动漫/真人混合），临时目录 {tmp}\n")

    st = Settings.load()
    st.models_dir = str(models)
    print(f"{'挡位':<22}{'WD14':>18}{'CLIP':>18}{'合计':>12}")
    print("-" * 72)
    for key in ["max", "balanced", "eco", "cpu"]:
        perf._current = key          # 直接指定挡位
        perf.apply_torch(key)
        p = perf.PRESETS[key]
        line = f"{p['label']:<20}"
        # --- WD14 ---
        try:
            tagger = WD14Tagger(models, "cpu" if p["force_cpu"] else "auto")
            tagger.load()
            tagger.predict(imgs[:2])                       # 预热
            t0 = time.time()
            for i in range(0, len(imgs), int(p["wd14_batch"])):
                tagger.predict(imgs[i:i + int(p["wd14_batch"])])
            wd = time.time() - t0
            line += f"{len(imgs) / wd:>9.1f} 张/秒 ({wd:>4.1f}s)"
        except Exception as e:  # noqa: BLE001
            line += f"{'失败 ' + str(e)[:20]:>18}"
            wd = None
        # --- CLIP ---
        try:
            clip = ClipZeroShot(models, "ViT-B-32", "laion2b_s34b_b79k",
                                "cpu" if p["force_cpu"] else "auto")
            clip.load()
            clip.encode_images(imgs[:2])                   # 预热
            t0 = time.time()
            clip.encode_images(imgs, batch_size=int(p["clip_batch"]))
            cl = time.time() - t0
            line += f"{len(imgs) / cl:>9.1f} 张/秒 ({cl:>4.1f}s)"
        except Exception as e:  # noqa: BLE001
            line += f"{'失败 ' + str(e)[:20]:>18}"
            cl = None
        if wd and cl:
            line += f"{len(imgs) / (wd + cl):>9.1f}/s"
        print(line)
        del tagger, clip
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(HERE / ".bench_perf_home", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
