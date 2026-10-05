"""全量扫描词典里的可疑条目（机械性错误，能自动修的自动修，其余列出来人工过）。

用法：
    python tools\\audit_zh_suspicious.py            # 只报告
    python tools\\audit_zh_suspicious.py --apply    # 自动修"夹英文括号/占位符"这两类
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
DICT = HERE / "app" / "tag_zh_dict.json"
CJK = re.compile(r"[\u4e00-\u9fff]")
TRAIL_ASCII = re.compile(r"^(?P<zh>[^A-Za-z()]+)[\s]*\((?P<en>[A-Za-z0-9 ._/:'-]{2,})\)$")
PLACEHOLDER = {"未知", "unknown", "?", "??", "N/A", "n/a", "无", "无。", "空"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    raw = json.loads(DICT.read_text(encoding="utf-8"))
    strip_paren: list[tuple[str, str, str]] = []
    placeholders: list[str] = []
    ascii_only: list[str] = []
    long_zh: list[tuple[str, str]] = []
    for k, v in list(raw.items()):
        v = str(v)
        m = TRAIL_ASCII.match(v)
        if m and CJK.search(m.group("zh")):
            strip_paren.append((k, v, m.group("zh").strip()))
        if v.strip().lower() in {x.lower() for x in PLACEHOLDER}:
            placeholders.append(k)
        if not CJK.search(v) and re.search(r"[A-Za-z]", v):
            ascii_only.append(k)
        if len(v) > 12:
            long_zh.append((k, v))
    print(f"词典 {len(raw)} 条，扫描结果：")
    print(f"  1) 中文后面拖着英文括号（如 阿尔法·潘德拉贡 (Fate)）：{len(strip_paren)} 条")
    for k, v, fix in strip_paren[:8]:
        print(f"       {k:<32} {v!r} → {fix!r}")
    print(f"  2) 占位符翻译（未知/unknown/?）：{len(placeholders)} 条  {placeholders[:10]}")
    print(f"  3) 译文里没有中文（纯英文/数字）：{len(ascii_only)} 条")
    print(f"  4) 译文过长（>12 字，可能是句子的碎片）：{len(long_zh)} 条")
    for k, v in long_zh[:6]:
        print(f"       {k:<32} {v!r}")
    if args.apply:
        for k, _v, fix in strip_paren:
            raw[k] = fix
        for k in placeholders:          # 占位符去掉，让界面直接显示英文原名
            raw.pop(k, None)
        DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已自动修正 {len(strip_paren)} 条夹英文括号 + 删除 {len(placeholders)} 条占位符")
    else:
        print("（只报告；加 --apply 自动修前两类）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
