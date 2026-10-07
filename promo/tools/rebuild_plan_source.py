"""用捞回来的 JSON 重建 promo_plan.py 源码（源码被误删，以 pyc 恢复内容为准）。"""
from __future__ import annotations

import json
import pprint
from pathlib import Path

TOOLS = Path(r"E:\文档\ChatGPT\图片标签分类\promo\tools")
SRC_JSON = TOOLS / "promo_plan_recovered.json"
OUT = TOOLS / "promo_plan.py"

HEADER = '''"""宣传片分镜脚本（单一事实来源）。

排片原则：
  * 相邻两段不用同一张素材，避免"同一画面停很久"的观感
  * 静态截图一律给慢速推拉（zoom 不为 none），画面始终在动
  * 单段不超过 12 秒，章节切换点会吸附到音乐节拍（见 resolve）

字段：
    src      素材：shots/xxx、manual/xxx（说明书截图）、motion/xxx、card/xxx、assets/xxx
    dur      这段占多少秒
    zoom     in / out / none（none 只给本身在动的动画序列）/ focus
    focus    (cx, cy, 放大倍数)，倍数上限 1.12，落点会往画面中心收
    title    章节标题；sub 字幕；card_sub 卡片文字；fps 图片序列解释帧率

注意：本文件是 2026-10-07 从 __pycache__/promo_plan.cpython-312.pyc 恢复的，
内容与当时剪出 v5 的那版一致。
"""
from __future__ import annotations

'''

FOOTER = '''

def snap(t: float, beats: list[float], tol: float = 0.5) -> float:
    """把时间点吸附到最近的拍点（超出容差就不动）。"""
    if not beats:
        return t
    best = min(beats, key=lambda b: abs(b - t))
    return best if abs(best - t) <= tol else t


def resolve(bgm_seconds: float | None = None,
            beats: list[float] | None = None,
            tol: float = 0.5) -> list[dict]:
    """给出最终时间线：段落带 start、dur 定稿，章节切换吸附到节拍。

    所有时间都量化到 60fps 的帧格——不量化的话 Premiere 会对齐到帧，
    脚本按 ticks 精确查找刚放上去的镜头就会失败。
    """
    fps = 60.0

    def q(t: float) -> float:
        return round(t * fps) / fps

    segs = [dict(s) for s in SEGMENTS]
    t = 0.0
    for i, seg in enumerate(segs):
        if beats and (seg.get("title") or i == 0):
            target = snap(t, beats, tol)
            if target != t and i > 0:
                prev = float(segs[i - 1]["dur"])
                segs[i - 1]["dur"] = q(max(1.0, prev + (target - t)))
                t = q(target)
        seg["start"] = round(t, 4)
        t += q(float(seg["dur"]))
        seg["dur"] = q(float(seg["dur"]))
    return segs


def total_duration(bgm_seconds: float | None = None) -> float:
    return sum(float(s["dur"]) for s in resolve(bgm_seconds))


if __name__ == "__main__":
    segs = resolve(200.0)
    t = 0.0
    for i, s in enumerate(segs, 1):
        mark = ("◆ " + s["title"]) if s.get("title") else ""
        print(f"{i:02d} {t:6.1f}s  +{s['dur']:5.1f}  {s['src']:26} {mark}")
        t += s["dur"]
    print(f"共 {len(segs)} 段，总时长 {t:.1f} 秒（{t/60:.2f} 分钟）")
'''


def main() -> int:
    segs = json.loads(SRC_JSON.read_text(encoding="utf-8"))
    body = "SEGMENTS: list[dict] = " + pprint.pformat(segs, width=96,
                                                      sort_dicts=False) + "\n"
    OUT.write_text(HEADER + body + FOOTER, encoding="utf-8")
    print(f"已重建 {OUT}（{len(segs)} 段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
