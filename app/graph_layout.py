"""服务端图谱布局：给移动端直接下发坐标，省得它自己摆（摆得还不一致）。

原则：
  · **分类节点**用 PC 端已经算好并存在库里的坐标（`nodes.x/y`）——和 PC 图谱一致；
    没算过的（x/y 为空）按圆周均匀铺开。
  · **标签节点**围绕**它的父分类中心**做环形分布：按图片数从多到少，
    逐圈往外铺（每圈能放几个按周长算），同一父节点下的标签角度均匀、顺序稳定
    （同样输入永远得到同样的坐标，移动端可以缓存）。
  · 没有父分类的标签丢到最外圈，不丢节点。

这是一个"够用且稳定"的近似布局：PC 端自己的 `radial_layout()` 更精细（自适应扇形面积），
移动端用这里的坐标就能和 PC 大体一致，不必再自己实现布局算法。
"""
from __future__ import annotations

import math

TAG_R0 = 150.0          # 第一圈半径（离分类中心）
TAG_RING_STEP = 110.0   # 每往外一圈增加多少
TAG_SPACING = 62.0      # 同一圈上相邻标签的最小弧长（按节点视觉大小估的）
GROUP_FALLBACK_R = 900.0  # 分类没坐标时，按这个半径铺圆


def _ring_place(items: list, cx: float, cy: float, r0: float = TAG_R0,
                step: float = TAG_RING_STEP, spacing: float = TAG_SPACING) -> dict:
    """把 items（按给定顺序）从内圈到外圈铺在 (cx,cy) 周围。"""
    out: dict[str, tuple[float, float]] = {}
    i = 0
    ring = 0
    while i < len(items):
        r = r0 + ring * step
        cap = max(6, int(2.0 * math.pi * r / spacing))
        chunk = items[i:i + cap]
        # 每圈从正上方开始顺时针；同一圈内角度均匀
        for k, key in enumerate(chunk):
            ang = -math.pi / 2.0 + 2.0 * math.pi * k / max(1, len(chunk))
            out[key] = (cx + r * math.cos(ang), cy + r * math.sin(ang))
        i += cap
        ring += 1
    return out


def place_graph(group_nodes: list[dict], tag_nodes: list[dict]) -> dict:
    """返回 {节点 id: (x, y)}。

    group_nodes: [{id, x, y, name, count?}]；tag_nodes: [{id, count, parents:[group id…]}]
    """
    pos: dict[str, tuple[float, float]] = {}
    # 1) 分类：优先用库里已有的坐标
    missing = []
    for g in group_nodes:
        x, y = g.get("x"), g.get("y")
        if x is None or y is None:
            missing.append(g)
        else:
            pos[g["id"]] = (float(x), float(y))
    if missing:
        n = max(1, len(missing))
        for i, g in enumerate(missing):
            ang = -math.pi / 2.0 + 2.0 * math.pi * i / n
            pos[g["id"]] = (GROUP_FALLBACK_R * math.cos(ang), GROUP_FALLBACK_R * math.sin(ang))
    if not group_nodes:
        pos["__origin__"] = (0.0, 0.0)

    # 2) 标签：按"第一个父分类"分组
    buckets: dict[str, list[dict]] = {}
    orphans: list[dict] = []
    for t in tag_nodes:
        parents = [p for p in (t.get("parents") or []) if p in pos]
        if parents:
            buckets.setdefault(parents[0], []).append(t)
        else:
            orphans.append(t)
    for gid, items in buckets.items():
        items.sort(key=lambda t: (-int(t.get("count") or 0), str(t.get("id"))))
        cx, cy = pos[gid]
        pos.update(_ring_place([t["id"] for t in items], cx, cy))
    if orphans:
        orphans.sort(key=lambda t: (-int(t.get("count") or 0), str(t.get("id"))))
        pos.update(_ring_place([t["id"] for t in orphans], 0.0, 0.0,
                               r0=GROUP_FALLBACK_R + 700.0, step=120.0))
    return pos
