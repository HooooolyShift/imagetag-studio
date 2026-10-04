"""生成说明书 PDF（真实界面截图 + 详细操作步骤）。

做法：离屏启动真实界面（QT_QPA_PLATFORM=offscreen），用 widget.grab() 抓真实渲染的界面，
再用 reportlab 排版成中文 PDF。所有截图都来自真实程序，不是示意图。

用法： python tools\\make_manual.py [输出.pdf]
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
SHOT_DIR = HERE / "manual_shots"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (HERE / "说明书_图片标签工坊.pdf")
DEMO_HOME = HERE / ".manual_home"

os.environ["IMGTAG_HOME"] = str(HERE)
os.environ["IMGTAG_DATA"] = str(DEMO_HOME)
os.environ["IMGTAG_MODELS"] = str(HERE / "models")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt, QTimer                     # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

from app import hardware, perf                            # noqa: E402
from app.config import Settings                           # noqa: E402
from app.library import EngineHub, Library                # noqa: E402
from app.store import Store                               # noqa: E402
from app.ui.main_window import MainWindow                 # noqa: E402


def shot(widget, name: str, w: int = 1500, h: int = 950) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    widget.resize(w, h)
    QApplication.processEvents()
    pm = widget.grab()
    p = SHOT_DIR / f"{name}.png"
    pm.save(str(p))
    print(f"  截图 {p.name}  {pm.width()}x{pm.height()}")
    return p


def shot_dialog(dlg, name: str, w: int = 1500, h: int = 950, settle_ms: int = 600) -> Path:
    """对话框要先 show 再抓，后台任务的多等一会儿，抓完关掉避免线程残留。"""
    try:
        dlg.show()
        settle(settle_ms)
        p = shot(dlg, name, w, h)
    finally:
        try:
            dlg.close()
        except Exception:
            pass
    return p


def settle(ms: int = 1200) -> None:
    """让后台任务/缩略图线程跑一会儿（离屏也要给事件循环时间）。"""
    end = QTimer()
    loop = QApplication.instance()

    def stop():
        loop.quit()

    end.singleShot(ms, stop)
    loop.exec()


def prepare_demo() -> tuple[Store, Settings, Library, EngineHub]:
    """造一份演示数据：导入几张图 → 打标 → 留一部分待审核，方便截图有内容。"""
    if DEMO_HOME.exists():
        shutil.rmtree(DEMO_HOME, ignore_errors=True)
    demo_src = HERE / ".manual_demo_src"
    shutil.rmtree(demo_src, ignore_errors=True)
    demo_src.mkdir(parents=True)
    for f in (HERE / "testdata").glob("*"):
        if f.suffix.lower() in (".jpg", ".png", ".jpeg", ".webp"):
            shutil.copy(f, demo_src / f.name)
    # 多复制几份，让“系列/查重”截图有内容
    base = HERE / "testdata" / "Wikipe-tan_full_length.png"
    for i in range(3):
        shutil.copy(base, demo_src / f"漫画页_{i + 1:02d}.png")

    settings = Settings.load()
    settings.models_dir = str(HERE / "models")
    settings.library_dir_name = "ImageTags_manual"
    store = Store()
    lib = Library(store, settings)
    hub = EngineHub(settings)

    rid = store.add_root(demo_src)
    lib.scan_root(rid, demo_src)
    lib.ensure_builtin_tags()
    for name, cat, prompt in (("初音未来", "character", "hatsune_miku"), ("围裙", "clothing", "apron"),
                              ("泳装", "clothing", "swimsuit"), ("西装", "clothing", "business suit"),
                              ("真人照片", "style", "photograph of a real person"),
                              ("二次元插画", "style", "anime illustration")):
        tid = store.ensure_tag(name, cat, prompt)
        store.update_tag(tid, category=cat, prompt=prompt)
    store.update_tag(store.tag_id("西装"), requires="真人照片")
    ids = [int(r["id"]) for r in store.search_files(limit=500)]
    print("  演示数据打标中…")
    lib.run_wd14(ids, hub)
    lib.ensure_clip_embeddings(ids, hub)
    lib.auto_tags_from_clip(hub, ids)
    lib.run_rating(ids, hub)
    # 审核一部分，留一部分待审（截图里才会同时出现“已生效”和“待审核”）
    for i, r in enumerate(store.search_files(limit=500)):
        fid = int(r["id"])
        pend = [t["name"] for t in store.tags_for_file(fid, statuses=("pending",))]
        if not pend:
            continue
        if i % 2 == 0:
            lib.review_file(fid, {n: "confirmed" for n in pend})
    print("  演示数据完成")
    return store, settings, lib, hub


def main() -> int:
    app = QApplication(sys.argv)
    # 离屏平台默认字体库可能是空的，导致界面文字变成方框：
    # 手动注册系统中文字体，并把样式表里的 font-family 换成实际可用的族。
    from PySide6.QtGui import QFont, QFontDatabase
    for f in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "segoeui.ttf", "arial.ttf"):
        p = Path("C:/Windows/Fonts") / f
        if p.exists():
            QFontDatabase.addApplicationFont(str(p))
    fams = set(QFontDatabase.families())
    pick = next((c for c in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Segoe UI", "Arial")
                 if c in fams), "")
    print("  可用字体:", pick or "（未找到中文字体！）", "共", len(fams), "族")
    if pick:
        app.setFont(QFont(pick, 9))
    from app.ui.common import STYLE
    style = STYLE
    if pick:
        style = re.sub(r'font-family:[^;]+;', f'font-family: "{pick}";', style)
    app.setStyleSheet(style)
    store, settings, lib, hub = prepare_demo()
    win = MainWindow(store, settings)
    win.show()
    settle(2500)

    shots: dict[str, Path] = {}
    shots["main"] = shot(win, "01_主界面")

    # 选中一张图 → 右侧标签面板
    if win.model.rowCount():
        win.grid.selectAll()
        QApplication.processEvents()
        settle(400)
        shots["tagpanel"] = shot(win, "02_标签面板与批量打标")
    # 左侧筛选
    if win.filter_tree.topLevelItemCount():
        top = win.filter_tree.topLevelItem(0)
        if top.childCount():
            top.child(0).setCheckState(0, Qt.Checked)
            win.refresh_files()
            settle(600)
            shots["filter"] = shot(win, "03_按标签筛选")

    # 系列视图
    series = store.series_list()
    if series:
        win.enter_series(int(series[0]["id"]))
        settle(800)
        shots["series"] = shot(win, "04_系列内拖动排序")
        win.exit_special()

    # 各个对话框
    from app.ui.dialogs import (CategoryManagerDialog, ModelsDialog, PersonDialog, PreviewDialog,
                                SettingsDialog, TagManagerDialog)
    from app.ui.dupes import DuplicateDialog
    from app.ui.import_dialog import ImportDialog
    from app.ui.review import ReviewDialog
    from app.ui.taxonomy import TaxonomyDialog
    shots["review"] = shot_dialog(ReviewDialog(lib, hub, win), "05_审核台", 1500, 950, 900)
    shots["tagmgr"] = shot_dialog(TagManagerDialog(store, win), "06_标签管理", 1180, 720, 500)
    shots["catmgr"] = shot_dialog(CategoryManagerDialog(store, win), "07_类型管理", 1000, 640, 500)
    shots["taxonomy"] = shot_dialog(TaxonomyDialog(lib, win), "08_标签体系图谱", 1500, 950, 900)
    shots["dupes"] = shot_dialog(DuplicateDialog(lib, win), "09_查重与保留选择", 1500, 950, 6000)
    imp_dlg = ImportDialog(lib, win)
    imp_dlg.candidates = [dict(r) for r in store.search_files(limit=60)]
    imp_dlg.fill_list_chunked()
    settle(1200)
    shots["import"] = shot_dialog(imp_dlg, "10_导入图片", 1500, 950, 600)
    shots["settings"] = shot_dialog(SettingsDialog(settings, win), "11_设置", 760, 800, 500)
    shots["person"] = shot_dialog(PersonDialog(lib, win), "12_人物管理", 1200, 700, 800)
    shots["models"] = shot_dialog(ModelsDialog(settings, win), "13_模型管理", 860, 520, 500)
    # 大图预览（含框选标注）
    first = store.search_files(limit=1)
    if first:
        shots["preview"] = shot_dialog(PreviewDialog(store, first[0]["path"], win), "14_大图预览与框选",
                                       1400, 900, 900)

    # 硬件信息（用于说明书里的“本机实测环境”一节）
    hw = hardware.detect(HERE)
    rec = hardware.recommend(hw)
    from make_pdf import build_pdf
    build_pdf(OUT, shots, hw, rec)
    print("说明书已生成:", OUT)
    shutil.rmtree(HERE / ".manual_demo_src", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
