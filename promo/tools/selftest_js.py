"""自检：不连 Premiere，只检查 build_premiere 生成的 ExtendScript 花括号是否平衡。

（JS 是拼出来的，括号不平衡是最容易犯又最难查的错；这里离线跑一遍先筛掉。）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_premiere as bp  # noqa: E402
from promo_plan import resolve  # noqa: E402

P = r"E:\文档\ChatGPT\图片标签分类"


def main() -> int:
    segs = resolve(200.0)
    index = {
        "card/title": P + r"\promo\cards\title.png",
        "card/github": P + r"\promo\cards\github.png",
        "card/summary": P + r"\promo\cards\summary.png",
        "assets/icon": P + r"\assets\icon.png",
        "shots/01_main_window": P + r"\promo\shots\01_main_window.png",
        "shots/04_review": P + r"\promo\shots\04_review.png",
        "manual/01_主界面": P + r"\manual_shots\01_主界面.png",
    }
    bad = 0
    for n, seg in enumerate(segs, 1):
        index[f"sub:{n}"] = (P + rf"\promo\overlays\sub_{n:03d}.png") if seg.get("sub") else ""
        index[f"title:{n}"] = (P + rf"\promo\overlays\title_{n:03d}.png") if seg.get("title") else ""
    for n, seg in enumerate(segs, 1):
        if seg["src"].startswith("motion/"):
            continue          # 动画段需要真实帧文件，跳过
        try:
            js = bp._segment_code(n, seg, index)
        except KeyError as exc:
            print(f"  段{n:02d} 缺素材索引 {exc}（等抓图脚本跑完再验）")
            continue
        bal = js.count("{") - js.count("}")
        flag = "OK " if bal == 0 else "!! "
        if bal:
            bad += 1
        print(f"  {flag}段{n:02d} {seg['src']:<26} 长度 {len(js):5d} 括号差 {bal}")
    print("结论：" + ("全部平衡 ✓" if bad == 0 else f"{bad} 段有问题 ✗"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
