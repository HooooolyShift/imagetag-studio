"""宣传片分镜脚本（单一事实来源）。

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

SEGMENTS: list[dict] = [{'src': 'card/title', 'dur': 4.0, 'card_sub': '图片标签工坊 1.2'},
 {'src': 'assets/icon', 'dur': 2.0, 'zoom': 'in', 'sub': '给自己的图库，装一个能搜的脑子'},
 {'src': 'motion/04_scroll',
  'fps': 15,
  'dur': 7.0,
  'zoom': 'none',
  'title': '一、图库越大，越找不着东西',
  'sub': '几千张图，想找「初音未来的泳装立绘」| 只能一张张往下翻'},
 {'src': 'shots/01_main_window', 'dur': 5.0, 'zoom': 'in', 'sub': '文件名没规律、截图没标签，搜索框里打什么都是空的'},
 {'src': 'shots/02_main_selection',
  'dur': 5.0,
  'zoom': 'in',
  'title': '二、它是什么',
  'sub': '图片标签工坊：把「AI 打标 → 人工审核 → 标签检索」缝成一条闭环'},
 {'src': 'shots/04_manual_tag',
  'dur': 5.5,
  'zoom': 'focus',
  'focus': [0.5, 0.45, 1.1],
  'sub': 'WD14 覆盖动漫一万多个标签（含角色名）| CLIP 认你自己写的中文标签'},
 {'src': 'shots/13_preview',
  'dur': 5.0,
  'zoom': 'in',
  'sub': '人脸聚类管真人、分级识别对齐 pixiv | 全部在本机跑，不联网'},
 {'src': 'shots/07_import',
  'dur': 6.5,
  'zoom': 'in',
  'title': '三、导入：Lightroom 那样挑图',
  'sub': '选源文件夹 → 缩略图预览 → 勾选要的 → 一键入库'},
 {'src': 'shots/10_persons',
  'dur': 4.5,
  'zoom': 'in',
  'sub': '导入时可以顺手打标签，也能让它立刻自动打标 | 重复图自动跳过'},
 {'src': 'motion/01_tagging',
  'fps': 12,
  'dur': 9.0,
  'zoom': 'none',
  'title': '四、自动打标',
  'sub': '点一下，WD14 + CLIP + 分级识别一次跑完'},
 {'src': 'shots/03_filter_tags',
  'dur': 5.5,
  'zoom': 'focus',
  'focus': [0.5, 0.45, 1.08],
  'sub': '角色、服装、姿势、场景、画风、人数、分级，一张图一次给全'},
 {'src': 'shots/01_main_window',
  'dur': 5.0,
  'zoom': 'focus',
  'focus': [0.5, 0.55, 1.08],
  'sub': '实测 RTX 4070 Laptop：WD14 约 16 张/秒，CLIP 每秒 100～200 张'},
 {'src': 'motion/02_review',
  'fps': 15,
  'dur': 11.5,
  'zoom': 'none',
  'title': '五、人工审核：AI 提议，你定案',
  'sub': '每个标签旁边就是「通过 / 否决」，鼠标一点就是一票'},
 {'src': 'shots/04_review',
  'dur': 7.0,
  'zoom': 'focus',
  'focus': [0.65, 0.35, 1.12],
  'sub': '认错的标签直接否决，系统把它当作负样本记下来'},
 {'src': 'shots/13_preview',
  'dur': 4.5,
  'zoom': 'focus',
  'focus': [0.5, 0.5, 1.1],
  'sub': '还能在图上框选，告诉模型「这个 tag 对应画面哪一块」'},
 {'src': 'shots/02_main_selection',
  'dur': 5.0,
  'zoom': 'focus',
  'focus': [0.5, 0.45, 1.06],
  'sub': '确认 = 正样本，否决 = 负样本 | 够数就训练分类器，越用越准'},
 {'src': 'motion/03_filter',
  'fps': 13,
  'dur': 8.5,
  'zoom': 'none',
  'title': '六、检索：左边勾标签，网格瞬间出结果',
  'sub': '多选是「同时满足」，也能切成「满足任一」'},
 {'src': 'shots/03_filter_tags',
  'dur': 5.5,
  'zoom': 'focus',
  'focus': [0.38, 0.5, 1.12],
  'sub': '标签显示成「中文备注（英文标签）」| 内置 10275 条中文词典，覆盖 95%'},
 {'src': 'shots/09_categories', 'dur': 4.5, 'zoom': 'in', 'sub': '标签会写进文件名，手机文件管理器里也能直接搜到'},
 {'src': 'shots/05_taxonomy_graph',
  'dur': 7.0,
  'zoom': 'in',
  'title': '七、标签体系图谱',
  'sub': '类型能自定义，一个标签可以同时属于多个分类'},
 {'src': 'shots/08_tag_manager', 'dur': 4.5, 'zoom': 'in', 'sub': '改名、合并、删除都只动标签，不影响已经打好的图'},
 {'src': 'shots/14_series',
  'dur': 6.5,
  'zoom': 'in',
  'title': '八、系列：多页合并成一本',
  'sub': '多页合并成系列，页码自动编号'},
 {'src': 'manual/01_主界面', 'dur': 4.0, 'zoom': 'in', 'sub': '拖动缩略图就能改顺序，文件名跟着重排'},
 {'src': 'shots/06_duplicates',
  'dur': 7.5,
  'zoom': 'in',
  'title': '九、查重：感知哈希 + pHash + CLIP',
  'sub': '缩放、轻微裁剪、改色都能抓出来'},
 {'src': 'manual/09_查重与保留选择',
  'dur': 5.5,
  'zoom': 'in',
  'sub': '选一张保留，其余移到隔离区（可恢复）| 误报可反馈「不是重复」'},
 {'src': 'shots/01_main_window',
  'dur': 5.0,
  'zoom': 'focus',
  'focus': [0.5, 0.6, 1.08],
  'title': '十、分级与隐私',
  'sub': '分级识别对齐 pixiv，等级直接标在缩略图上'},
 {'src': 'shots/12_settings', 'dur': 5.0, 'zoom': 'in', 'sub': '敏感内容可以一键打码，公共场合翻图库不尴尬'},
 {'src': 'shots/07_import',
  'dur': 5.5,
  'zoom': 'focus',
  'focus': [0.62, 0.4, 1.1],
  'title': '十一、多盘图库',
  'sub': '像 Steam 库一样，每个盘一个专用目录，收录后检索只在图库内'},
 {'src': 'shots/12_settings',
  'dur': 5.0,
  'zoom': 'focus',
  'focus': [0.5, 0.5, 1.06],
  'title': '十二、性能与离线安装',
  'sub': '四档性能：榨干硬件 / 均衡 / 节能 / 只用 CPU'},
 {'src': 'shots/11_models',
  'dur': 4.5,
  'zoom': 'in',
  'sub': '离线完整包 7.2 GB：目标机器不用预装 Python / torch'},
 {'src': 'manual/11_设置', 'dur': 4.5, 'zoom': 'in', 'sub': '断网也能装，任务断点续跑'},
 {'src': 'card/summary', 'dur': 9.0, 'zoom': 'in', 'card_sub': '功能一览'},
 {'src': 'card/title', 'dur': 4.0, 'card_sub': '图片标签工坊 1.2'},
 {'src': 'card/github',
  'dur': 8.0,
  'zoom': 'in',
  'card_sub': 'github.com/HooooolyShift/imagetag-studio'}]


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
