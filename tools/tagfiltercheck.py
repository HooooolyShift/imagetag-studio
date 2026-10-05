"""分类 ↔ 标签列表联动自检（用真实数据库，只读，不写任何数据）。

逐个检查所有"能建标签"的地方：默认应为「全部（不限分类）」= 全量，
切到某个分类后只列该分类下的标签，切回「全部」恢复全量。

用法： python tools\\tagfiltercheck.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app import categories as cats_mod
    from app.config import Settings
    from app.library import EngineHub, Library
    from app.store import Store
    from app.ui import common as common_mod
    from app.ui.import_dialog import ImportDialog
    from app.ui.main_window import TagPanel
    from app.ui.review import ReviewDialog
    from app.ui.taxonomy import TaxonomyDialog

    app = QApplication(sys.argv)
    settings = Settings.load()
    store = Store()
    lib = Library(store, settings)
    total = len(store.list_tags(""))
    print("库内标签总数:", total)

    def words_of(widget):
        if hasattr(widget, "itemText"):            # QComboBox
            return [widget.itemText(i) for i in range(widget.count())]
        comp = widget.completer()                   # QLineEdit + QCompleter
        m = comp.model() if comp else None
        if m is None:
            return []
        return [m.index(i, 0).data() for i in range(m.rowCount())]

    pick = [c["key"] for c in cats_mod.ordered(store) if c["count"] > 0][:2]
    if not pick:
        pick = [cats_mod.ordered(store)[0]["key"]]

    bad = 0

    def check(tag: str, cat_combo, tag_widget, combo_mode: bool):
        nonlocal bad
        n_all = len(common_mod.tag_words(store, None))
        if combo_mode:
            n_default = tag_widget.count()
            want_default = total
        else:
            n_default = len(words_of(tag_widget))
            want_default = n_all
        ok = cat_combo.currentData() == "" and n_default == want_default
        print(f"[{tag}] 分类默认={cat_combo.currentText()} 标签列表={n_default} 项（全量 {want_default}）"
              f"{'OK' if ok else '不一致'}")
        bad += 0 if ok else 1
        for key in pick:
            cat_combo.setCurrentIndex(cat_combo.findData(key))
            want = (len([t for t in store.list_tags("") if (t["category"] or "other") == key])
                    if combo_mode else len(common_mod.tag_words(store, key)))
            got = tag_widget.count() if combo_mode else len(words_of(tag_widget))
            ok2 = got == want and got < want_default
            print(f"    切到 {key}: {got} 项（期望 {want}）{'OK' if ok2 else '不一致'}")
            bad += 0 if ok2 else 1
        cat_combo.setCurrentIndex(0)
        got_back = tag_widget.count() if combo_mode else len(words_of(tag_widget))
        ok3 = got_back == want_default
        print(f"    切回全部: {got_back} 项 {'OK' if ok3 else '不一致'}")
        bad += 0 if ok3 else 1

    # 1) 图库管理右栏（输入框 + 联想）
    panel = TagPanel(store, lib)
    check("图库管理-新增标签", panel.new_cat, panel.new_tag, False)
    panel.deleteLater()

    # 2) 审核台（补标签下拉框）
    rdlg = ReviewDialog(lib, EngineHub(settings))
    check("审核台-补标签", rdlg.new_cat, rdlg.new_tag, True)
    rdlg.close()

    # 3) 导入对话框
    idlg = ImportDialog(lib)
    check("导入-标签框", idlg.tag_cat, idlg.tag_edit, False)
    idlg.close()

    # 4) 图谱页左侧「标签管理」标签页（分类过滤 + 标签表）
    tax = TaxonomyDialog(lib)
    tab = tax.tag_cat_filter
    n_default = tax.tag_search.count()
    ok = tab.currentData() == "" and n_default == total
    print(f"[图谱-标签管理] 分类默认={tab.currentText()} 标签下拉={n_default} 项 表格={tax.tag_table.rowCount()} 行"
          f"{'OK' if ok else '不一致'}")
    bad += 0 if ok else 1
    for key in pick:
        tab.setCurrentIndex(tab.findData(key))
        want = len([t for t in store.list_tags("") if (t["category"] or "other") == key])
        got = tax.tag_search.count()
        ok2 = got == want
        print(f"    切到 {key}: 下拉={got} 项 表格={tax.tag_table.rowCount()} 行（期望 {want}）"
              f"{'OK' if ok2 else '不一致'}")
        bad += 0 if ok2 else 1
    tab.setCurrentIndex(0)
    ok3 = tax.tag_search.count() == total
    print(f"    切回全部: 下拉={tax.tag_search.count()} 项 {'OK' if ok3 else '不一致'}")
    bad += 0 if ok3 else 1
    tax.close()

    print("结论:", "全部通过" if bad == 0 else f"{bad} 处不一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
