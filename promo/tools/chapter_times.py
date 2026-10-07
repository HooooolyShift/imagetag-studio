"""按当前分镜脚本 + 节拍吸附结果，输出章节时间轴（给 B 站简介用）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promo_plan import resolve  # noqa: E402

PROMO = Path(r"E:\文档\ChatGPT\图片标签分类\promo")


def main() -> int:
    info = json.loads((PROMO / "audio" / "beats.json").read_text(encoding="utf-8"))
    segs = resolve(info["duration"], info["beats"])
    print("00:00 开场")
    for s in segs:
        t = int(round(float(s["start"])))
        if s.get("title"):
            print(f"{t // 60:02d}:{t % 60:02d} {s['title']}")
        elif "summary" in s["src"]:
            print(f"{t // 60:02d}:{t % 60:02d} 功能一览")
        elif "github" in s["src"]:
            print(f"{t // 60:02d}:{t % 60:02d} 结尾 / GitHub")
    print(f"总长 {sum(float(x['dur']) for x in segs):.1f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
