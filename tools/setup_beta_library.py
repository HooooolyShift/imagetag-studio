"""把测试版（E 盘那份）的素材图整理成它的图库并建索引。

做四件事：
  1) 把程序目录下的 `测试图/` 搬到 `E:\ImageTagsBeta\测试图\`（图库目录，别放在代码仓库里）；
  2) 把 `E:\ImageTagsBeta` 登记成图库根（is_library=1）；
  3) 扫描建索引（写 E 盘那份便携库 `data\library.db`）；
  4) 补内置标签。

用法（在测试版程序目录下跑）：
    python tools\setup_beta_library.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 测试版程序目录
sys.path.insert(0, str(ROOT))

LIB = Path(r"E:\ImageTagsBeta")
SRC = ROOT / "测试图"


def main() -> int:
    # 1) 素材图搬进图库目录
    if SRC.is_dir():
        dst = LIB / "测试图"
        dst.mkdir(parents=True, exist_ok=True)
        moved = 0
        for item in SRC.iterdir():
            target = dst / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))
            moved += 1
        print(f"搬入图库：{moved} 项 → {dst}")
        try:
            SRC.rmdir()
        except OSError:
            pass
    else:
        print("程序目录下没有 测试图/，跳过搬运")
    LIB.mkdir(parents=True, exist_ok=True)

    # 2) 登记为图库根 + 扫描
    from app.config import Settings
    from app.library import EngineHub, Library
    from app.store import Store

    settings = Settings.load()
    settings.roots = [str(LIB)]
    settings.library_dir_name = "ImageTagsBeta"
    settings.save()
    print("数据目录：", settings.path().parent)

    store = Store()
    lib = Library(store, settings)
    rid = store.add_root(LIB)
    store.set_root_library(rid, True)
    print(f"图库根 #{rid} = {LIB}")

    res = lib.scan_root(rid, LIB, lambda m, f=0.0: print(" ", m))
    lib.ensure_builtin_tags()
    print("扫描结果：", res)
    st = store.one("SELECT COUNT(*) c FROM files")["c"]
    print("库内图片数：", st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
