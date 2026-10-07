"""从 __pycache__ 里的 promo_plan.pyc 把分镜脚本捞回来（源码被误删了）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PYC = Path(r"E:\文档\ChatGPT\图片标签分类\promo\tools\__pycache__\promo_plan.cpython-312.pyc")
OUT = Path(r"E:\文档\ChatGPT\图片标签分类\promo\tools\promo_plan_recovered.json")


def main() -> int:
    if not PYC.exists():
        print("没有 pyc：", PYC)
        return 1
    spec = importlib.util.spec_from_file_location("promo_plan", PYC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["promo_plan"] = mod
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    segs = getattr(mod, "SEGMENTS", None)
    if segs is None:
        print("pyc 里没有 SEGMENTS")
        return 1
    total = sum(float(s.get("dur") or 0) for s in segs)
    print(f"捞到 {len(segs)} 段，总时长 {total:.1f} 秒")
    print(f"函数: {[n for n in dir(mod) if not n.startswith('_') and callable(getattr(mod, n))]}")
    OUT.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
