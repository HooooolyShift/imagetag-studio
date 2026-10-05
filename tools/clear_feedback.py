"""清空模型反馈（探针 + 否决记录 + 已审标记），回到干净状态重新学。

用法：
    python tools\\clear_feedback.py            # 先看会清掉多少（干跑）
    python tools\\clear_feedback.py --apply    # 真清（自动备份数据库）

背景：标签中文名错译时，审核里的"否决"是按错名字点的，这些否决会当负样本训练，
把模型带偏。与其逐条判断哪些是错的，不如整批清掉重来 —— 已确认的标签本身不动。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from app.config import Settings
    from app.library import Library
    from app.store import Store

    store = Store()
    lib = Library(store, Settings.load())
    probes = store.one("SELECT COUNT(*) c FROM tag_probe")["c"]
    rejected = store.one("SELECT COUNT(*) c FROM file_tags WHERE status='rejected'")["c"]
    reviewed = store.one("SELECT COUNT(*) c FROM files WHERE reviewed=1")["c"]
    confirmed = store.one("SELECT COUNT(*) c FROM file_tags WHERE status='confirmed'")["c"]
    print(f"现在：探针 {probes} 条 ｜ 否决记录 {rejected} 条 ｜ 已审标记 {reviewed} 张 ｜ "
          f"（已确认标签 {confirmed} 条 —— 这些不动）")
    if not args.apply:
        print("（干跑，加 --apply 才会清）")
        return 0
    db = store.db_path
    backup = db.with_name(f"library_before_clearfb_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db, backup)
    print("已备份：", backup.name)
    res = lib.clear_model_feedback(progress=lambda s, p: None)
    print("清空完成：", res["before"], "→", res["after"])
    print("接下来：审核时会重新积累反馈；错译已修好，这次学到的就是对的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
