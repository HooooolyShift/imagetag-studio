"""用软件自己的 PySide6 界面离屏渲染高清截图（宣传片素材）。

⚠️ 现在**不要跑**：用户要求等做新版时再重录（1.5 会改界面，现在录的很快过时）。

要点：
  * IMGTAG_DATA 指向 promo\\素材\\沙盒数据（**绝不碰真实库**）
  * QT_SCALE_FACTOR=2 → 1920x1080 窗口抓出来 3840x2160，字更清
  * 网格类截图统一把「分级」筛到全年龄 + 打开打码开关，镜头里不出现 R15/R18

用法：python shots_farm.py [main review taxonomy …]   # 不传就全跑
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "shots"
SANDBOX = PROJECT / "promo" / "素材" / "沙盒数据"

os.environ.setdefault("IMGTAG_HOME", str(PROJECT))
os.environ.setdefault("IMGTAG_DATA", str(SANDBOX))
os.environ.setdefault("IMGTAG_MODELS", str(PROJECT / "models"))
os.environ["QT_QPA_PLATFORM"] = os.environ.get("SHOTS_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = os.environ.get("SHOTS_SCALE", "2")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Settings  # noqa: E402
from app.library import EngineHub, Library  # noqa: E402
from app.store import Store  # noqa: E402
from app.ui.common import STYLE  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
ctx: dict = {}


def pump(seconds: float = 1.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def shoot(widget, name: str, width: int = 1920, height: int = 1080, wait: float = 1.2) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    widget.resize(width, height)
    widget.show()
    pump(wait)
    path = OUT / f"{name}.png"
    pix = widget.grab()
    pix.save(str(path))
    print(f"  [shot] {name}  {pix.width()}x{pix.height()}")
    return path


def only_all_ages(win) -> None:
    """把分级筛选切到全年龄（镜头里不出现 R15/R18 画面）。"""
    combo = getattr(win, "rating_filter", None)
    if combo is None:
        return
    for i in range(combo.count()):
        if str(combo.itemData(i)) == "all_ages":
            combo.setCurrentIndex(i)
            pump(1.5)
            print(f"  [filter] 分级 -> {combo.currentText()}（{win.model.rowCount()} 张）")
            return


def run(name: str, fn) -> None:
    print(f"\n=== {name}")
    try:
        fn()
        RESULTS.append((name, True, ""))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        RESULTS.append((name, False, str(exc)))


def build() -> dict:
    app = QApplication.instance() or QApplication(sys.argv)
    for family in ("Microsoft YaHei", "微软雅黑", "SimHei", "Segoe UI"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family, 10))
            break
    app.setStyleSheet(STYLE)
    settings = Settings.load()
    settings.models_dir = str(PROJECT / "models")
    settings.rating_blur = True                 # 成片里一律打码
    store = Store()
    return {"app": app, "settings": settings, "store": store,
            "library": Library(store, settings), "hub": EngineHub(settings)}


def main_window() -> None:
    from app.ui.main_window import MainWindow

    win = MainWindow(ctx["store"], ctx["settings"])
    win.resize(1920, 1080)
    win.show()
    pump(2.5)
    only_all_ages(win)
    rows = ctx["store"].query("SELECT id FROM files WHERE missing=0 ORDER BY id LIMIT 40")
    win.tag_panel.set_selection([])
    shoot(win, "01_main_window", 1920, 1080, 1.0)
    if len(rows) > 8:
        win.tag_panel.set_selection([int(r["id"]) for r in rows[:8]])
        shoot(win, "02_main_selection", 1920, 1080, 1.0)
    # 左侧按标签筛选（挑一个筛得出结果的）
    tree, best = win.filter_tree, None
    win.tag_panel.set_selection([])
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        for j in range(top.childCount()):
            child = top.child(j)
            if child.childCount():
                continue
            child.setCheckState(0, Qt.Checked)
            pump(0.6)
            n = win.model.rowCount()
            child.setCheckState(0, Qt.Unchecked)
            pump(0.25)
            if n and (best is None or abs(n - 14) < abs(best[2] - 14)):
                best = (top, child, n)
    if best:
        best[1].setCheckState(0, Qt.Checked)
        pump(2.0)
        print(f"  [filter] {best[1].text(0)} -> {best[2]} 张")
    shoot(win, "03_filter_tags", 1920, 1080, 1.0)
    # 手动打标：单图 + 新标签框里填真实角色名
    if best:
        best[1].setCheckState(0, Qt.Unchecked)
        pump(1.0)
    miku = ctx["store"].query(
        "SELECT f.id FROM files f JOIN file_tags ft ON ft.file_id=f.id "
        "JOIN tags t ON t.id=ft.tag_id WHERE t.name='hatsune_miku' LIMIT 1")
    if miku:
        win.tag_panel.set_selection([int(miku[0]["id"])])
        pump(1.5)
        win.tag_panel.new_tag.setText("初音未来")
        win.tag_panel.new_tag.setFocus()
        shoot(win, "04_manual_tag", 1920, 1080, 0.8)
    ctx["main_window"] = win


def review() -> None:
    from app.ui.review import ReviewDialog
    dlg = ReviewDialog(ctx["library"], ctx["hub"])
    shoot(dlg, "04_review", 1920, 1080, 2.5)


def taxonomy() -> None:
    from app.ui.taxonomy import TaxonomyDialog
    shoot(TaxonomyDialog(ctx["library"]), "05_taxonomy_graph", 1920, 1080, 2.5)


def dupes() -> None:
    from app.ui.dupes import DuplicateDialog
    shoot(DuplicateDialog(ctx["library"]), "06_duplicates", 1920, 1080, 2.5)


def import_dlg() -> None:
    from app.ui.import_dialog import ImportDialog
    dlg = ImportDialog(ctx["library"])
    dlg.resize(1600, 900)
    dlg.show()
    pump(1.0)
    dlg.sources = [str(PROJECT / "测试图")]
    dlg.scan_sources()
    for _ in range(60):
        pump(0.5)
        if dlg.candidates:
            break
    dlg.check_all(True)
    pump(3.0)
    shoot(dlg, "07_import", 1600, 900, 1.0)


def tag_manager() -> None:
    from app.ui.dialogs import TagManagerDialog
    shoot(TagManagerDialog(ctx["store"]), "08_tag_manager", 1500, 900, 2.0)


def categories() -> None:
    from app.ui.dialogs import CategoryManagerDialog
    shoot(CategoryManagerDialog(ctx["store"]), "09_categories", 1280, 800, 1.8)


def persons() -> None:
    from app.ui.dialogs import PersonDialog
    dlg = PersonDialog(ctx["library"])
    dlg.show()
    pump(1.5)
    if dlg.people.count():
        dlg.people.setCurrentRow(0)
    pump(6.0)
    shoot(dlg, "10_persons", 1500, 900, 1.0)


def models_dlg() -> None:
    from app.ui.dialogs import ModelsDialog
    shoot(ModelsDialog(ctx["settings"]), "11_models", 1100, 700, 1.5)


def settings_dlg() -> None:
    from app.ui.dialogs import SettingsDialog
    shoot(SettingsDialog(ctx["settings"]), "12_settings", 1000, 950, 1.5)


def preview() -> None:
    from app.ui.dialogs import PreviewDialog
    row = ctx["store"].one("SELECT path FROM files WHERE missing=0 ORDER BY RANDOM() LIMIT 1")
    if not row:
        raise RuntimeError("库里没有图片")
    shoot(PreviewDialog(ctx["store"], str(row["path"]),
                        models_dir=str(PROJECT / "models")), "13_preview", 1700, 1000, 2.5)


def series() -> None:
    from app.ui.dialogs import SeriesDialog
    rows = [dict(r) for r in ctx["store"].query(
        "SELECT * FROM files WHERE missing=0 ORDER BY id LIMIT 12")]
    shoot(SeriesDialog(rows, default_name="初音未来 精选"), "14_series", 1200, 800, 1.5)


SCREENS = {
    "main": main_window, "review": review, "taxonomy": taxonomy, "dupes": dupes,
    "import": import_dlg, "tag_manager": tag_manager, "categories": categories,
    "persons": persons, "models": models_dlg, "settings": settings_dlg,
    "preview": preview, "series": series,
}


def main() -> int:
    names = sys.argv[1:] or list(SCREENS)
    ctx.update(build())
    print("沙盒数据目录:", os.environ.get("IMGTAG_DATA"))
    print("库里图片数:", ctx["store"].stats())
    if ctx["store"].stats().get("files", 0) == 0:
        print("沙盒是空的：先跑 build_demo_library.py 把 测试图\\ 入库并打标")
    for name in names:
        fn = SCREENS.get(name)
        if fn:
            run(name, fn)
        else:
            print("未知界面:", name)
    print("\n========= 汇总 =========")
    for name, ok, err in RESULTS:
        print(("  [OK]  " if ok else "  [FAIL]") + f" {name} {err[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
