"""服务端图谱布局：给移动端直接下发坐标（PC 与移动端同一份，保证长得一样）。

按 PC 的思路做"多中心放射 + 扇形分区"：
  1. **按权重分扇区**：每个分类按"它有多少标签"分到一块扇形角度（空分类也保底一小块），
     20 个分类正好铺满一整圈——这就是界面上看到的"分区边缘"。
  2. **分类中心**放在自己扇形的中角线上，半径用 `ring_radius`（默认 900）。
     库里已经有坐标的分类，直接用库里的（和 PC 上看到的一致）。
  3. **标签摆在自己分类的扇形里**：从半径 `r0` 起逐圈往外，
     每圈能放几个 = ⌊扇形角度 × 半径 / spacing⌋，圈内角度在扇形内均匀分布。
     因为角度被限制在本扇区内，**不同分类的簇不会互相压**（旧版就是这里撞在一起，
     实测 8000+ 对重叠）。
  4. 没有父分类的标签单独占一块"未分组"扇区。

所有位置都在**全局极坐标**下算（每个标签的 r 是"离原点"的距离），所以视觉上就是
从中心放射出去的一片片扇区，和 PC 的楔形分区一致。
"""
from __future__ import annotations

import math

TAG_R0 = 260.0          # 标签第一圈的"全局半径"（分类中心 900，故标签在分类内侧一圈起）
TAG_RING_STEP = 120.0   # 每往外一圈增加多少（≥ 标签直径 68 + 间隙）
TAG_SPACING = 96.0      # 同一圈相邻标签的最小弧长：必须 ≥ 节点直径(68) + 间隙(28)
GROUP_RADIUS = 900.0    # 分类中心所在半径
SECTOR_PAD = 0.07       # 每个扇区两侧留的角隙（弧度），让分区边界看得出来、也避免相邻扇区贴太近
MIN_SECTOR = 0.24       # 每个分类保底扇形角度（弧度≈13.7°）：
                        # 分类圆心在半径 ~720 上，相邻分类圆心间距 ≈ span*720，
                        # 要 ≥ 两个分类直径(118) 才不会互相压，故不能小于 ~0.17


def _sector_place(items: list, a0: float, a1: float, r_outer_base: float = TAG_R0,
                  step: float = TAG_RING_STEP, spacing: float = TAG_SPACING) -> dict:
    """把 items 铺在一段扇形里（全局极坐标）：逐圈往外，圈内角度在扇区内均匀分布。"""
    out: dict[str, tuple[float, float]] = {}
    span = max(1e-3, a1 - a0)
    i, ring = 0, 0
    while i < len(items):
        r = r_outer_base + ring * step
        cap = max(1, int(span * r / spacing))
        chunk = items[i:i + cap]
        for k, key in enumerate(chunk):
            ang = a0 + span * (k + 0.5) / max(1, len(chunk))
            out[key] = (r * math.cos(ang), r * math.sin(ang))
        i += cap
        ring += 1
    return out


def place_graph(group_nodes: list[dict], tag_nodes: list[dict],
                ring_radius: float = GROUP_RADIUS) -> dict:
    """返回 {节点 id: (x, y)}（分类 + 标签，全部全局坐标）。

    group_nodes: [{id, name, count?}]；tag_nodes: [{id, count, parents:[group id…]}]
    """
    pos: dict[str, tuple[float, float]] = {}
    buckets: dict[str, list[dict]] = {g["id"]: [] for g in group_nodes}
    orphans: list[dict] = []
    for t in tag_nodes:
        parents = [p for p in (t.get("parents") or []) if p in buckets]
        if parents:
            buckets[parents[0]].append(t)
        else:
            orphans.append(t)
    # 扇区按"标签数"加权；空分类与"未分组"也给保底角度
    entries = [(gid, items) for gid, items in buckets.items()]
    if orphans:
        entries.append(("__orphan__", orphans))
    weights = [max(1.0, len(items)) for _gid, items in entries]
    total_w = sum(weights)
    # 先按权重分，再保证最小值，多余的角度按比例收回
    spans = [max(MIN_SECTOR, 2.0 * math.pi * w / total_w) for w in weights]
    over = sum(spans) - 2.0 * math.pi
    if over > 0:                       # 保底角度导致超了 → 从大的扇区里按比例扣
        room = [max(0.0, s - MIN_SECTOR) for s in spans]
        room_sum = sum(room) or 1.0
        spans = [s - over * (rm / room_sum) for s, rm in zip(spans, room)]
    ang = -math.pi / 2.0               # 从正上方开始，顺时针铺
    for (gid, items), span in zip(entries, spans):
        a0, a1 = ang + SECTOR_PAD, ang + span - SECTOR_PAD
        mid = (a0 + a1) / 2.0
        # 标签从这一圈起往外铺；分类节点摆**它自己标签环的内侧一圈**，
        # 这样分类圆和自家标签至少隔一个环距（120 > 59+34），也不会压到邻区节点。
        items.sort(key=lambda t: (-int(t.get("count") or 0), str(t.get("id"))))
        if gid == "__orphan__":
            base_r = ring_radius + 700.0
        else:
            base_r = max(TAG_R0, ring_radius - 1.5 * TAG_RING_STEP)
            r_group = max(120.0, base_r - TAG_RING_STEP)
            pos[gid] = (r_group * math.cos(mid), r_group * math.sin(mid))
        pos.update(_sector_place([t["id"] for t in items], a0, a1, r_outer_base=base_r))
        ang += span
    return pos
