"""性能自检：大量图片时的界面响应（导入列表 / 主网格刷新）。

用法： python tools\\bench_import.py [数量]
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
os.environ["IMGTAG_DATA"] = str(HERE / ".bench_home")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
shutil.rmtree(HERE / ".bench_home", ignore_errors=True)

from PySide6.QtWidgets import QApplication          # noqa: E402

from app.config import Settings                     # noqa: E402
from app.library import EngineHub, Library          # noqa: E402
from app.store import Store                         # noqa: E402
from app.ui.import_dialog import ImportDialog       # noqa: E402
from app.ui.main_window import MainWindow           # noqa: E402
from app import imaging                             # noqa: E402


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 800
    app = QApplication([])
    src = Path(tempfile.mkdtemp(prefix="bench_"))
    base = HERE / "testdata" / "Barack_Obama.jpg"
    blob = base.read_bytes()
    t0 = time.time()
    for i in range(n):
        (src / f"img{i:05d}.jpg").write_bytes(blob)
    print(f"造 {n} 张测试图：{time.time() - t0:.1f}s  目录 {src}")

    st = Settings.load()
    st.models_dir = str(HERE / "models")
    store = Store()
    lib = Library(store, st)
    t0 = time.time()
    rid = store.add_root(src)
    res = lib.scan_root(rid, src)
    print(f"扫描入库：{time.time() - t0:.1f}s  {res}")

    # 主界面刷新（批量取标签后）
    win = MainWindow(store, st)
    win.root_filter = rid
    t0 = time.time()
    win.refresh_files()
    print(f"主网格刷新 {win.model.rowCount()} 项：{time.time() - t0:.2f}s")

    # 导入对话框：分块填列表 + 后台缩略图
    dlg = ImportDialog(lib, win)
    dlg.sources = [str(src)]
    cands = [dict(r) for r in store.search_files(root_ids=[rid], limit=200000)]
    t0 = time.time()
    dlg.list.blockSignals(True)
    for c in cands:
        dlg._append_item(c)
    dlg.list.blockSignals(False)
    print(f"导入列表填充 {len(cands)} 项（不生成缩略图）：{time.time() - t0:.2f}s")
    t0 = time.time()
    dlg.update_summary()
    print(f"统计勾选状态：{time.time() - t0:.2f}s")
    t0 = time.time()
    dlg.check_all(True)
    print(f"全选 {len(cands)} 项：{time.time() - t0:.2f}s")

    # 对照：以前是同步生成缩略图，实测单张成本
    t0 = time.time()
    imaging.make_thumb(str(base), 999999, time.time(), 320)
    one = time.time() - t0
    print(f"单张缩略图（冷、无缓存）：{one * 1000:.0f} ms  →  以前一次性同步生成 {n} 张约 {one * n:.0f}s（这就是卡死原因）")

    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(HERE / ".bench_home", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
