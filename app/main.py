"""程序入口：启动 Qt 界面。"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _setup_paths() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("PYTHONUTF8", "1")


def _excepthook(exc_type, exc, tb) -> None:
    from .config import data_dir
    msg = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        (data_dir() / "error.log").write_text(msg, encoding="utf-8")
    except Exception:
        pass
    sys.stderr.write(msg)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(None, "出错了", msg[-2000:])
    except Exception:
        pass


def main() -> int:
    _setup_paths()
    sys.excepthook = _excepthook
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox

    from . import models as model_lib
    from . import perf
    from .config import APP_NAME, Settings, VERSION, data_dir, project_root
    from .store import Store
    from .ui.common import STYLE
    from .ui.dialogs import ModelsDialog
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(f"{APP_NAME} {VERSION}")
    app.setStyleSheet(STYLE)
    for ico in (project_root() / "assets" / "icon.ico", project_root() / "assets" / "icon.png"):
        if ico.exists():
            app.setWindowIcon(QIcon(str(ico)))
            break

    settings = Settings.load()
    model_lib.setup_env(settings.models_path())
    perf.configure(settings)
    from . import singleton
    if not singleton.acquire(data_dir()):
        QMessageBox.warning(None, f"{APP_NAME} 已在运行",
                            "检测到已经有一个「图片标签工坊」在运行。\n\n"
                            "同时开两个会同时写数据库，可能把索引写坏——请用已经打开的那个窗口。")
        return 1
    store = Store()
    try:
        other = store.one("SELECT COUNT(*) c FROM tags WHERE category IN ('','other')")["c"]
        if other:
            res = Library(store, settings).auto_organize_tags()
            print(f"标签整理：分类 {res['categorized']} 个，补中文 {res['zh']} 个")
    except Exception:
        pass
    store_stats = store.stats()
    win = MainWindow(store, settings)
    win.statusBar().showMessage(f"性能挡位：{perf.describe()}", 8000)
    win.show()

    def first_run() -> None:
        # 「开始使用」提醒：库里真的一个图片目录都没有时才提示，且可以勾选「下次不再显示」
        # （以前判断的是 settings.roots，它永远是空的 → 每次启动都弹，很烦）
        try:
            n_roots = int(store.one("SELECT COUNT(*) AS c FROM roots")["c"])
        except Exception:
            n_roots = len(settings.roots)
        if n_roots == 0 and getattr(settings, "show_startup_tip", True):
            box = QMessageBox(win)
            box.setWindowTitle("开始使用")
            box.setIcon(QMessageBox.Information)
            box.setText(
                "第一次使用：\n\n1) 点左上角「添加文件夹」选择你的图片目录（建议先用小目录试）\n"
                "2) 点「自动打标」给图片打标签\n"
                "3) 在左侧按标签筛选，在右侧勾选/新增标签\n\n"
                "标签会写进文件名（[tag1 tag2]），手机上用文件管理器就能搜到。")
            cb = QCheckBox("下次不再显示这条提醒")
            box.setCheckBox(cb)
            box.exec()
            if cb.isChecked():
                settings.show_startup_tip = False
                try:
                    settings.save()
                except Exception:
                    pass
        missing = model_lib.missing_keys(["wd14_onnx", "wd14_tags"])
        if missing and getattr(settings, "show_model_tip", True):
            box2 = QMessageBox(win)
            box2.setWindowTitle("模型未下载")
            box2.setIcon(QMessageBox.Question)
            box2.setText("本地模型还没下载（约 450MB / 用国内镜像）。现在下载吗？")
            box2.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box2.button(QMessageBox.Yes).setText("现在下载")
            box2.button(QMessageBox.No).setText("以后再说")
            cb2 = QCheckBox("下次不再提示（可在「模型管理」里手动下载）")
            box2.setCheckBox(cb2)
            box2.exec()
            if cb2.isChecked():
                settings.show_model_tip = False
                try:
                    settings.save()
                except Exception:
                    pass
            if box2.standardButton(box2.clickedButton()) == QMessageBox.Yes:
                dlg = ModelsDialog(settings, win)
                dlg.download_missing()
                dlg.exec()

    QTimer.singleShot(400, first_run)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
