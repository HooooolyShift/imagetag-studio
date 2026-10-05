"""审核队列缩略图自检：队列很长时，滚到后半段必须会为"当前可见的那批"请求缩略图。

复现的老 bug：可见区间用 indexAt(视口右下角) 算，图标网格下这个角常落在空隙里，
判定失败后回退成"取前 60~80 个"，于是往下拉时真正可见的那批永远不请求 → 一片空白。

用法： python tools\\reviewthumbcheck.py
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
    from PySide6.QtWidgets import QApplication

    tmp_data = Path(tempfile.mkdtemp(prefix="imtag_rv_"))
    pics = Path(tempfile.mkdtemp(prefix="imtag_rvpics_"))
    os.environ["IMGTAG_DATA"] = str(tmp_data)
    src = HERE / "testdata" / "Anime_Girl.png"
    for i in range(120):                       # 造一个长队列
        shutil.copy(src, pics / f"p{i:03d}.png")

    from app.config import Settings
    from app.library import EngineHub, Library
    from app.store import Store
    from app.ui.review import ReviewDialog

    app = QApplication(sys.argv)
    settings = Settings.load()
    settings.library_dir_name = "ImageTags_rvtest"
    store = Store()
    lib = Library(store, settings)
    rid = store.add_root(pics)
    lib.scan_root(rid, pics)
    ids = [int(r["id"]) for r in store.search_files(root_ids=[rid])]
    for fid in ids:                            # 每张都塞一个待审标签 + 未定级 → 全部进审核队列
        store.add_file_tags(fid, [("待审测试", "wd14", 0.5)], status="pending")
    store.refresh_counts()

    dlg = ReviewDialog(lib, EngineHub(settings))
    dlg.resize(1200, 900)
    dlg.show()
    app.processEvents()
    n = dlg.queue_list.count()
    print("审核队列长度:", n)

    requested: list[int] = []
    dlg.thumbs.request = lambda fid, path, mtime: requested.append(int(fid))   # 拦截请求，不发真线程
    bar = dlg.queue_list.verticalScrollBar()
    print("滚动条范围:", bar.minimum(), "~", bar.maximum())

    bad = 0
    # 1) 首屏
    requested.clear()
    dlg._queue_thumbs()
    first_batch = list(requested)
    ok1 = len(first_batch) > 0
    print(f"[1] 首屏请求 {len(first_batch)} 张，例：{sorted(first_batch)[:5]} {'OK' if ok1 else '没请求到'}")
    bad += 0 if ok1 else 1

    # 2) 滚到中间
    requested.clear()
    bar.setValue(bar.maximum() // 2)
    dlg._queue_thumbs()
    mid_batch = list(requested)
    mid_ok = any(i > n * 0.3 for i in range(n) if dlg.queue[i]["id"] in mid_batch)
    print(f"[2] 滚到中间请求 {len(mid_batch)} 张，含后半段条目={mid_ok}")
    bad += 0 if (mid_batch and mid_ok) else 1

    # 3) 滚到底部：这是老 bug 的重灾区
    requested.clear()
    bar.setValue(bar.maximum())
    dlg._queue_thumbs()
    tail_batch = list(requested)
    tail_ok = any(int(dlg.queue[i]["id"]) in tail_batch for i in range(int(n * 0.7), n))
    print(f"[3] 滚到底部请求 {len(tail_batch)} 张，含最后 30% 的条目={tail_ok} "
          f"{'OK' if tail_ok else '不一致（老 bug：还在请求开头那批）'}")
    bad += 0 if tail_ok else 1

    # 4) 对照：老算法（indexAt 视口右下角）在同一位置会算出哪个区间
    rect = dlg.queue_list.viewport().rect()
    top = dlg.queue_list.indexAt(rect.topLeft())
    bottom = dlg.queue_list.indexAt(rect.bottomRight())
    old_first = max(0, (top.row() if top.isValid() else 0) - 20)
    old_last = min(n - 1, (bottom.row() if bottom.isValid() else min(n - 1, 60)) + 20)
    print(f"[4] 对照老算法：视口右下角命中={bottom.isValid()} → 它只会请求第 {old_first}~{old_last} 张"
          f"（这就是'拉后面看不到缩略图'的原因）")

    dlg.close()
    shutil.rmtree(tmp_data, ignore_errors=True)
    shutil.rmtree(pics, ignore_errors=True)
    print("结论:", "全部通过" if bad == 0 else f"{bad} 处不一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
