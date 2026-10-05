"""审核台"父子标签联动"自检（临时库，不动正式数据）。

验证：
  1) 同一张图上父标签和子标签都在待审时，列表里**只显示子标签**；
  2) 通过子标签 → 父标签也一并通过（白裙子成立 ⇒ 裙子、服装成立）；
  3) 否决子标签 → 父标签重新出现在待审列表里，可以单独判断；
  4) 「全部通过」把藏起来的父标签也一起通过。

用法： python tools\\reviewtagcheck.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main() -> int:
    tmp_data = Path(tempfile.mkdtemp(prefix="imtag_rvt_"))
    pics = Path(tempfile.mkdtemp(prefix="imtag_rvtpics_"))
    os.environ["IMGTAG_DATA"] = str(tmp_data)
    shutil.copy(HERE / "testdata" / "Anime_Girl.png", pics / "a.png")

    from PySide6.QtWidgets import QApplication

    from app.config import Settings
    from app.library import EngineHub, Library
    from app.store import Store
    from app.ui.review import ReviewDialog

    app = QApplication(sys.argv)
    settings = Settings.load()
    settings.library_dir_name = "ImageTags_rvttest"
    store = Store()
    lib = Library(store, settings)
    rid = store.add_root(pics)
    lib.scan_root(rid, pics)
    fid = int(store.search_files(root_ids=[rid])[0]["id"])

    dress = store.save_tag("dress", "clothing")
    skirt = store.save_tag("skirt", "clothing")
    white = store.save_tag("white_skirt", "clothing")
    store.update_tag(dress, zh="服装")
    store.update_tag(skirt, zh="裙子")
    store.update_tag(white, zh="白裙子")
    store.link_tag_sub(dress, skirt)
    store.link_tag_sub(skirt, white)
    store.add_file_tags(fid, [("dress", "wd14", 0.9), ("skirt", "wd14", 0.9),
                              ("white_skirt", "wd14", 0.9), ("solo", "wd14", 0.9)], status="pending")
    store.refresh_counts()

    dlg = ReviewDialog(lib, EngineHub(settings))
    bad = 0
    visible = list(dlg.pending_names)
    ok1 = visible == ["white_skirt", "solo"] or (set(visible) == {"white_skirt", "solo"})
    print(f"[1] 待审列表只显示子标签：{visible} {'OK' if ok1 else '不一致'}")
    bad += 0 if ok1 else 1

    dlg.decide("white_skirt", "confirmed")
    got = dict(dlg.decisions)
    ok2 = got.get("white_skirt") == "confirmed" and got.get("skirt") == "confirmed" \
        and got.get("dress") == "confirmed"
    print(f"[2] 通过子标签 → 判定表：{got} {'OK' if ok2 else '不一致'}")
    bad += 0 if ok2 else 1

    dlg.decisions.clear()
    dlg.reload_table()
    dlg.decide("white_skirt", "rejected")
    visible2 = list(dlg.pending_names)
    ok3 = "skirt" in visible2 and "dress" not in visible2     # 否决子 → 只放出直接父级
    print(f"[3] 否决子标签后放回父标签：{visible2} {'OK' if ok3 else '不一致'}")
    bad += 0 if ok3 else 1
    dlg.decide("skirt", "rejected")                           # 再否决裙子 → 服装也该出来
    ok3b = "dress" in dlg.pending_names
    print(f"    再否决裙子 → 服装出现在列表：{list(dlg.pending_names)} {'OK' if ok3b else '不一致'}")
    bad += 0 if ok3b else 1

    dlg.decisions.clear()
    dlg.reload_table()
    dlg.accept_all()
    ok4 = all(dlg.decisions.get(n) == "confirmed"
              for n in ("dress", "skirt", "white_skirt", "solo"))
    print(f"[4] 全部通过（含藏起来的父标签）：{dict(dlg.decisions)} {'OK' if ok4 else '不一致'}")
    bad += 0 if ok4 else 1

    dlg.close()
    shutil.rmtree(tmp_data, ignore_errors=True)
    shutil.rmtree(pics, ignore_errors=True)
    print("结论:", "全部通过" if bad == 0 else f"{bad} 处不一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
