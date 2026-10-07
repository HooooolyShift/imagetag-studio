"""抓「动态素材」：用软件真实跑一遍操作，逐帧截图成序列。

⚠️ 现在**不要跑**：等做新版时再重录（1.5 会改界面）。

产出 promo\\motion\\<片段名>\\f00001.png …（Premiere 里按图片序列导入，
再用 pad_motion.py 补齐帧数；本脚本按 1x 渲染，正好是成片分辨率）。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "motion"
SANDBOX = PROJECT / "promo" / "素材" / "沙盒数据"

os.environ.setdefault("IMGTAG_HOME", str(PROJECT))
os.environ.setdefault("IMGTAG_DATA", str(SANDBOX))
os.environ.setdefault("IMGTAG_MODELS", str(PROJECT / "models"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Settings  # noqa: E402
from app.library import EngineHub, Library  # noqa: E402
from app.store import Store  # noqa: E402
from app.ui.common import STYLE  # noqa: E402

W, H = 1920, 1080


def pump(seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class Recorder:
    def __init__(self, clip: str, widget) -> None:
        self.dir = OUT / clip
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.widget = widget
        self.n = 0

    def frame(self) -> None:
        self.n += 1
        self.widget.grab().save(str(self.dir / f"f{self.n:05d}.png"))

    def hold(self, n: int) -> None:
        for _ in range(n):
            pump(0.02)
            self.frame()

    def done(self) -> None:
        print(f"  [{self.dir.name}] {self.n} 帧 -> {self.dir}")


def build_app():
    app = QApplication.instance() or QApplication(sys.argv)
    for family in ("Microsoft YaHei", "微软雅黑", "SimHei"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 10))
            break
    app.setStyleSheet(STYLE)
    settings = Settings.load()
    settings.models_dir = str(PROJECT / "models")
    settings.rating_blur = True
    store = Store()
    return app, settings, store, Library(store, settings), EngineHub(settings)


def clip_tagging(store, library, hub, settings) -> None:
    from app.ui.main_window import MainWindow
    win = MainWindow(store, settings)
    win.resize(W, H)
    win.show()
    pump(2.0)
    ids = [int(r["id"]) for r in store.search_files(limit=60)]
    rec = Recorder("01_tagging", win)
    rec.hold(6)
    win.progress.setVisible(True)
    win.progress.setRange(0, 0)
    win.status_label.setText("自动打标（WD14 + CLIP + 分级）…")

    def progress(msg: str, frac: float) -> None:
        win.on_progress(msg, frac)
        rec.frame()

    library.run_wd14(ids, hub, progress)
    rec.hold(8)
    win.progress.setRange(0, 100)
    win.progress.setValue(100)
    win.status_label.setText("自动打标完成")
    win.refresh_tags()
    rec.hold(10)
    rec.done()
    win.close()


def clip_review(store, library, hub) -> None:
    from app.ui.review import ReviewDialog
    dlg = ReviewDialog(library, hub)
    dlg.resize(W, H)
    dlg.show()
    pump(2.0)
    rec = Recorder("02_review", dlg)
    rec.hold(8)
    row = store.pending_files(limit=1)
    if not row:
        print("  [review] 没有待审图片")
        rec.done()
        return
    pend = store.tags_for_file(int(row[0]["id"]), statuses=("pending",))
    plan = ["ok", "ok", "no"] + ["ok"] * max(0, len(pend) - 3)
    for tag, action in zip(pend, plan):
        dlg.decide(str(tag["name"]), action)
        rec.hold(3)
    rec.hold(10)
    dlg.finish_current()
    rec.hold(12)
    rec.done()
    dlg.close()


def clip_filter(store, library, settings) -> None:
    from app.ui.main_window import MainWindow
    win = MainWindow(store, settings)
    win.resize(W, H)
    win.show()
    pump(2.5)
    rec = Recorder("03_filter", win)
    rec.hold(10)
    tree = win.filter_tree
    targets = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            ch = top.child(j)
            if ch.childCount() == 0:
                targets.append(ch)
    seq = [c for c in targets if any(k in c.text(0) for k in
                                     ("初音未来", "阿米娅", "千早爱音", "全年龄", "R15"))] or targets[:3]
    for ch in seq[:3]:
        ch.setCheckState(0, Qt.Checked)
        for _ in range(8):
            pump(0.06)
            rec.frame()
        rec.hold(4)
        ch.setCheckState(0, Qt.Unchecked)
        for _ in range(4):
            pump(0.06)
            rec.frame()
    rec.hold(10)
    rec.done()
    win.close()


def clip_scroll(store, library, settings) -> None:
    from app.ui.main_window import MainWindow
    win = MainWindow(store, settings)
    win.resize(W, H)
    win.show()
    pump(3.0)
    rec = Recorder("04_scroll", win)
    bar = win.grid.verticalScrollBar()
    rec.hold(6)
    steps = 90
    for i in range(steps):
        bar.setValue(int(bar.minimum() + (bar.maximum() - bar.minimum()) * i / (steps - 1)))
        pump(0.03)
        rec.frame()
    rec.hold(6)
    rec.done()
    win.close()


CLIPS = {"tagging": clip_tagging, "review": clip_review,
         "filter": clip_filter, "scroll": clip_scroll}


def main() -> int:
    names = sys.argv[1:] or list(CLIPS)
    app, settings, store, library, hub = build_app()
    print("库里图片:", store.stats())
    for name in names:
        print(f"\n=== {name}")
        fn = CLIPS.get(name)
        if fn is None:
            print("  未知片段")
        elif name == "review":
            fn(store, library, hub)
        elif name == "tagging":
            fn(store, library, hub, settings)
        else:
            fn(store, library, settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
