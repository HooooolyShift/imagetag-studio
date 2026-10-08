"""命令行：中文描述 → 提示词（不在 GUI 里也能用）。

    python -m prompt_helper "初音未来海边微笑，半身" 
    python -m prompt_helper "红色连衣裙，蕾丝边" --mode outfit
    python -m prompt_helper "..." --json          # 输出完整 JSON
"""
from __future__ import annotations

import argparse
import json
import sys

from .generator import generate, load_dictionary
from .ollama import DEFAULT_MODEL, is_up


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--mode", default="prompt", choices=["prompt", "negative", "outfit"])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-dict", action="store_true", help="跳过词表校验")
    args = ap.parse_args()
    if not is_up():
        print("本机 Ollama 没在运行：先跑 D:\\LocalAI\\start-ollama.cmd", file=sys.stderr)
        return 3
    d = None if args.no_dict else load_dictionary()
    r = generate(args.text, args.mode, dictionary=d, model=args.model)
    if args.json:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=1))
    else:
        print(r.tags)
        if r.invented:
            print("\n【词表外（已保留，可自行替换）】" + ", ".join(r.invented))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
