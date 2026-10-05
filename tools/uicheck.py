"""界面冒烟测试：离屏启动主窗口，跑几秒看有没有异常（不会真的弹窗）。

用法： python tools\\uicheck.py
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("IMGTAG_HOME", str(HERE))
os.environ.setdefault("IMGTAG_DATA", str(HERE / ".selftest_home"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.config import Settings
    from app.library import Library
    from app.store import Store
    from app.ui.common import STYLE
    from app.ui.main_window import MainWindow
    from app.ui.taxonomy import TaxonomyDialog

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    settings = Settings.load()
    settings.models_dir = str(HERE / "models")
    store = Store()
    win = MainWindow(store, settings)
    win.show()
    print("主窗口 OK")
    print("  图片项:", win.model.rowCount(), " 标签分组:", win.filter_tree.topLevelItemCount())
    if win.model.rowCount():
        win.model.items[0].tags and win.tag_panel.set_selection([win.model.items[0].file_id])
        print("  标签面板 OK:", win.tag_panel.header.text(), "| 树节点:", win.tag_panel.tree.topLevelItemCount())
    # 打开几个对话框（不进入事件循环，只构造看是否报错）
    from app.config import CATEGORY_ORDER
    from app.ui.dialogs import CategoryManagerDialog, ModelsDialog, PersonDialog, TagManagerDialog
    from app.ui.review import ReviewDialog
    from app.ui.dupes import DuplicateDialog
    from app.ui.import_dialog import ImportDialog
    from app.library import EngineHub
    # 造一个待审标签，用来验证“审核界面里补标签”这条路径
    first_rows = store.search_files(limit=1)
    if first_rows:
        store.add_file_tags(int(first_rows[0]["id"]), [("待审测试", "other", 0.9)], status="pending")
    for name, factory in (("标签管理", lambda: TagManagerDialog(store, win)),
                          ("人物管理", lambda: PersonDialog(Library(store, settings), win)),
                          ("模型管理", lambda: ModelsDialog(settings, win)),
                          ("类型管理", lambda: CategoryManagerDialog(store, win)),
                          ("标签体系", lambda: TaxonomyDialog(Library(store, settings), win)),
                          ("审核界面", lambda: ReviewDialog(Library(store, settings), EngineHub(settings), win)),
                          ("查重界面", lambda: DuplicateDialog(Library(store, settings), win))):
        try:
            dlg = factory()
            print(f"  {name} OK")
            dlg.close()
        except Exception:
            print(f"  {name} 失败:\n{traceback.format_exc()}")
            return 1
    # 审核界面里补标签的实测
    try:
        rlib = Library(store, settings)
        rdlg = ReviewDialog(rlib, EngineHub(settings), win)
        if rdlg.queue:
            # 分类 ↔ 标签框联动：默认「全部」应看到全量；切分类后收窄
            n_all = rdlg.new_tag.count()
            assert rdlg.new_cat.currentData() == "", "审核台分类默认应为「全部（不限分类）」"
            idx_cloth = rdlg.new_cat.findData("clothing")
            rdlg.new_cat.setCurrentIndex(idx_cloth)
            n_cloth = rdlg.new_tag.count()
            print(f"  审核台标签框：全部={n_all} 项 → 切到服装={n_cloth} 项")
            if not (n_all >= n_cloth > 0):
                print("  分类联动自检失败：切分类后标签数量未收窄")
                return 1
            rdlg.new_cat.setCurrentIndex(0)
            if rdlg.new_tag.count() != n_all:
                print("  分类联动自检失败：切回「全部」未恢复全量")
                return 1
            rdlg.new_tag.setEditText("审核时补的标签")
            rdlg.new_cat.setCurrentIndex(rdlg.new_cat.findData("clothing"))
            rdlg.add_manual_tag()
            fid = int(rdlg.queue[0]["id"])
            tags = [t["name"] for t in store.tags_for_file(fid, statuses=("confirmed",))]
            print("  审核界面补标签:", "审核时补的标签" in tags, "| 该图已生效标签数:", len(tags))
            rdlg.load_suggestions()
            print("  推荐列表条目:", rdlg.suggest_list.count())
        else:
            print("  审核队列为空，跳过补标签实测")
        rdlg.close()
    except Exception:
        print("  审核界面补标签实测失败:\n" + traceback.format_exc())
        return 1
    # 类型下拉框：列出全部类型 + 就地新建类型
    try:
        from app.ui.dialogs import CategoryCombo
        combo = CategoryCombo(store)
        n_items = combo.count()
        existed = combo.ensure_current()
        # 模拟“输入一个新类型名 → 就地新建”（用一个自动确认的回调，避免弹窗阻塞测试）
        combo.setEditText("测试类型X")
        created = combo.ensure_current(ask=lambda name: (name, ["a {} object", "{}"]))
        ok_new = store.category(created) is not None
        print(f"  类型下拉框：{n_items} 项（含「＋ 新建类型…」）｜当前={existed}｜就地新建={created}，库里已存在={ok_new}")
        if not ok_new or n_items < 13:
            print("  类型下拉框自检失败")
            return 1
    except Exception:
        print("  类型下拉框自检失败:\n" + traceback.format_exc())
        return 1
    # 导入对话框核心流程实测（导入 + 顺手打标签）
    try:
        import shutil as _sh
        import tempfile
        settings.library_dir_name = "ImageTags_uitest"
        tmp_src = Path(tempfile.mkdtemp(prefix="imptest_"))
        _sh.copy(HERE / "testdata" / "Anime_Girl.png", tmp_src / "imp1.png")
        _sh.copy(HERE / "testdata" / "Wikipe-tan_full_length.png", tmp_src / "imp2.png")
        lib2 = Library(store, settings)
        rid = store.add_root(tmp_src)
        lib2.scan_root(rid, tmp_src)
        idlg = ImportDialog(lib2, win)
        idlg.candidates = [dict(r) for r in store.search_files(root_ids=[rid])]
        idlg.fill_list()
        idlg.tag_edit.setText("导入批次A 测试")
        res = idlg.do_import()
        print("  导入对话框实测:", res.get("ok"), "移动", res.get("moved"),
              "｜标签", res.get("tags"), "｜目标", res.get("dirs"), "｜错误", res.get("errors"))
        if res.get("dirs"):
            _sh.rmtree(res["dirs"][0], ignore_errors=True)
        idlg.close()
        _sh.rmtree(tmp_src, ignore_errors=True)
    except Exception:
        print("  导入对话框实测失败:\n" + traceback.format_exc())
        return 1
    QTimer.singleShot(1500, app.quit)
    app.exec()
    print("事件循环结束，无异常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
