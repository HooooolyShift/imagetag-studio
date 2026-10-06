"""自检：找出函数体里 return 之后的死代码，以及关键信号连接是否还在。

为什么要它：有一次给 GridModel 加方法时补丁插进了 __init__ 中段，
把 `thumbs.signals.ready.connect(self._on_thumb)` 挤到了 `return` 之后变成死代码，
结果所有缩略图永远显示"…"，而且没有任何报错。用 AST 静态扫一遍就能立刻发现。

用法： python tools\\deadcodecheck.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def scan(path: Path) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{path}: 语法错误 {exc}"]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for i, stmt in enumerate(node.body[:-1]):
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                nxt = node.body[i + 1]
                out.append(f"{path}:{nxt.lineno}  {node.name}() 里 '{type(stmt).__name__}' 之后还有代码"
                           f"（可能是被挤进来的死代码）")
                break
    return out


def main() -> int:
    bad: list[str] = []
    for p in sorted((HERE / "app").rglob("*.py")):
        bad += scan(p)
    if bad:
        print("发现死代码：")
        for line in bad:
            print("  " + str(line).replace(str(HERE) + "\\", ""))
    else:
        print("没有发现 return/raise 之后的死代码")
    # 关键信号连接必须还在（缩略图全靠它）
    grid = (HERE / "app" / "ui" / "grid.py").read_text(encoding="utf-8")
    ok = "signals.ready.connect(self._on_thumb)" in grid
    print("缩略图信号连接:", "OK" if ok else "★丢了！所有缩略图会一直是「…」")
    return 0 if not bad and ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
