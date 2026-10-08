"""v1.5 更新宣传片分镜脚本（单一事实来源）。

时长对准 BGM：塞壬唱片官网《离解复合》160.3s，成片目标 ≈155s（结尾留淡出）。
动图片段时长直接取自 v15_motion.CLIPS，保证渲染出来的帧序列和分镜一致。

字段同 promo_plan.py：src / dur / zoom / focus / title / sub / card_sub
  src 支持：motion_v15/<片段名>（3D 动效）、stills_v15/<名字>（静态图，带慢推）
"""
from __future__ import annotations

from v15_motion import CLIPS


def m(name: str, sub: str = "", title: str = "") -> dict:
    """动图片段（时长跟着 v15_motion 走）。"""
    seg = dict(src=f"motion_v15/{name}", dur=float(CLIPS[name]["dur"]),
               zoom="none", sub=sub)
    if title:
        seg["title"] = title
    return seg


SEGMENTS: list[dict] = [
    # ---------------------------------------------------------------- 片头
    dict(src="card/title_v15", dur=3.5, card_sub="v1.5"),
    m("splash", "v1.5：从单机工具，变成 PC + 安卓端"),

    # ---------------------------------------------------------------- 一、安卓端
    m("tab_devices", "局域网自动发现设备，选中即连 | 双向对码，配错直接拒",
      title="一、安卓端来了：平板 / 手机遥控 PC"),
    m("tab_browse", "平板连上就是全功能遥控：图库与 PC 同一口径"),
    m("tab_review", "审核台：中英对照 + 来源 + 置信度 + ✓/✗/⊘ 三态 + 撤销"),
    m("tab_region", "框选区域告诉模型「标签对应哪一块」"),
    m("tab_region2", "区域精修后立刻回写，平板和 PC 是同一份数据"),
    m("tab_graph", "图谱在移动端重做：关系视图 / 热度视图 + 折叠 + 搜索运镜"),
    m("tab_sector", "按分类扇形分区：4311 个节点，重叠 0 对"),
    m("tab_dupes", "查重、保留选择也能在平板做"),
    m("tab_similar", "相似图：以这张为例，在全库找"),
    m("tab_wide", "横屏图库：系列算一条，点开看内页"),
    m("phone", "手机端做精简版：只留浏览 + 图谱"),

    # ---------------------------------------------------------------- 二、AI 生图 DLC
    m("gen_out1", "中文写提示词 → 离线转 danbooru 标签 → 出图",
      title="二、AI 生图扩展包（可选安装）"),
    m("gen_ref", "参考图（IP-Adapter）：给一张图影响风格与角色"),
    m("gen_pose", "姿势 / 线稿（ControlNet）：按底模架构自动配对"),
    m("gen_mask", "局部重绘 / 换装：界面上涂哪改哪（白 = 重绘）"),
    m("gen_inpaint", "512×512 约 6 秒出图，遮罩外像素不动"),
    m("gen_hires", "4× 纯放大：1024 → 4096，实测 9 秒，画面不糊"),
    m("tab_gen", "平板也能遥控生图：出图 / 重绘 / 放大 / 定向取消"),

    # ---------------------------------------------------------------- 三、PC 本体
    m("pc_main", "图库更顺手：左键进浏览模式，只留图 + 标签",
      title="三、PC 端本体也大改"),
    m("pc_browse", "滚轮以鼠标位置缩放、拖动平移、适应窗口、翻页"),
    m("pc_review", "审核：PC 与移动端同一套口径，支持撤销（Ctrl+Z，50 步）"),
    m("pc_graph", "标签图谱：坐标一次算完 0.1 秒，缩放后也能拖"),
    m("pc_import", "导入几千张不再卡死"),
    m("pc_tags", "标签面板：批量打标 + 待审数量一目了然"),
    m("pc_person", "人物页：人脸聚类命名，自动给照片打人物标签"),
    m("pc_dup", "查重：感知哈希 + pHash + CLIP 三重比对"),
    m("pc_settings", "设置里可换开屏封面、打码、性能挡位"),

    # ---------------------------------------------------------------- 四、修掉的硬 bug
    dict(src="card/fixes", dur=7.0, zoom="in",
         title="四、顺手修掉的硬 bug",
         card_sub="设备发现服务从未启动 / 首次审核必 409 / SSE 事件丢失 / 图谱拖不动 / 生图窗口放不下 / 停止生成失效"),

    # ---------------------------------------------------------------- 收尾
    dict(src="card/summary_v15", dur=7.0, zoom="in", card_sub="v1.5 功能一览"),
    dict(src="card/github_v15", dur=5.5, zoom="in",
         card_sub="github.com/HooooolyShift/imagetag-studio · v1.5"),
]


def snap(t: float, beats: list[float], tol: float = 0.5) -> float:
    if not beats:
        return t
    best = min(beats, key=lambda b: abs(b - t))
    return best if abs(best - t) <= tol else t


def resolve(bgm_seconds: float | None = None,
            beats: list[float] | None = None, tol: float = 0.5) -> list[dict]:
    """给段落补 start、量化到帧格，并把章节切换吸附到节拍。"""
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
        seg["dur"] = q(float(seg["dur"]))
        t += seg["dur"]
    return segs


def total_duration() -> float:
    return sum(float(s["dur"]) for s in resolve())


if __name__ == "__main__":
    segs = resolve()
    t = 0.0
    for i, s in enumerate(segs, 1):
        mark = ("◆ " + s["title"]) if s.get("title") else ""
        print(f"{i:02d} {t:6.1f}s +{s['dur']:4.1f} {s['src']:26} {mark}")
        t += s["dur"]
    print(f"共 {len(segs)} 段，总时长 {t:.1f} 秒（BGM 160.3s）")
