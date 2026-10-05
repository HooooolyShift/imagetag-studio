"""把 app/tag_zh_corrections.json 里的修正应用到位：词典 + 历史表 + 库内已存旧译名。

用法：
    python tools\\apply_zh_corrections.py            # 干跑
    python tools\\apply_zh_corrections.py --apply    # 应用
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
DICT = HERE / "app" / "tag_zh_dict.json"
CORR = HERE / "app" / "tag_zh_corrections.json"
HIST = HERE / "app" / "tag_zh_fix_history.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    corr = json.loads(CORR.read_text(encoding="utf-8"))
    raw = json.loads(DICT.read_text(encoding="utf-8"))
    try:
        hist = json.loads(HIST.read_text(encoding="utf-8"))
    except Exception:
        hist = {}
    changed = []
    for k, v in corr.items():
        old = raw.get(k, "")
        if old != v:
            changed.append((k, old, v))
    print(f"修正表 {len(corr)} 条，需要改 {len(changed)} 条：")
    for k, o, v in changed[:20]:
        print(f"   {k:<34} {o!r} → {v!r}")
    if len(changed) > 20:
        print(f"   …其余 {len(changed) - 20} 条")
    if not args.apply:
        print("（干跑，加 --apply 才写入）")
        return 0
    for k, o, v in changed:
        raw[k] = v
        hist[k] = [o, v]
    DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    HIST.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"词典已更新（{len(raw)} 条）")
    # 库里存过的旧译名同步（空值不写，保持"显示时走词典"的机制）
    from app.store import Store
    store = Store()
    n = 0
    for k, o, v in changed:
        row = store.one("SELECT id, zh FROM tags WHERE name=?", (k,))
        if not row:
            continue
        cur = (row["zh"] or "").strip()
        if cur and cur == (o or "").strip():
            store.update_tag(int(row["id"]), zh=v)
            n += 1
    print(f"库内旧译名同步 {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
