"""把截图 + 文字排成中文 PDF 说明书（reportlab）。由 make_manual.py 调用。"""
from __future__ import annotations

import time
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ACCENT = colors.HexColor("#2b6cb0")
GREY = colors.HexColor("#5b6270")


def register_fonts() -> str:
    """注册中文字体（微软雅黑优先，黑体兜底）。"""
    for name, path, idx in (("MSYH", r"C:\Windows\Fonts\msyh.ttc", 0),
                            ("MSYH", r"C:\Windows\Fonts\msyh.ttf", None),
                            ("SIMHEI", r"C:\Windows\Fonts\simhei.ttf", None)):
        try:
            p = Path(path)
            if not p.exists():
                continue
            pdfmetrics.registerFont(TTFont(name, str(p), subfontIndex=idx) if idx is not None
                                    else TTFont(name, str(p)))
            pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
            return name
        except Exception:
            continue
    return "Helvetica"


def styles(font: str) -> dict:
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName=font, fontSize=19, leading=26,
                             textColor=ACCENT, spaceBefore=10, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=font, fontSize=14, leading=20,
                             textColor=colors.HexColor("#1c3f66"), spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName=font, fontSize=10.5, leading=17,
                               alignment=TA_LEFT, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", parent=ss["BodyText"], fontName=font, fontSize=10.5, leading=17),
        "cap": ParagraphStyle("cap", parent=ss["BodyText"], fontName=font, fontSize=9, leading=13,
                              textColor=GREY, alignment=TA_CENTER, spaceBefore=2, spaceAfter=10),
        "cover": ParagraphStyle("cover", parent=ss["Title"], fontName=font, fontSize=30, leading=40,
                                alignment=TA_CENTER, textColor=ACCENT),
        "cover2": ParagraphStyle("cover2", parent=ss["BodyText"], fontName=font, fontSize=12, leading=20,
                                 alignment=TA_CENTER, textColor=GREY),
    }


def P(text: str, st) -> Paragraph:
    return Paragraph(text, st)


def bullets(items: list[str], st) -> ListFlowable:
    return ListFlowable([ListItem(Paragraph(t, st), leftIndent=12) for t in items],
                        bulletType="bullet", bulletFontSize=7, leftIndent=12, spaceBefore=2, spaceAfter=6)


def img_flow(path: Path | None, st, max_w: float = 500, max_h: float = 660, caption: str = ""):
    flow = []
    if path and Path(path).exists():
        from PIL import Image as PILImage
        w, h = PILImage.open(path).size
        scale = min(max_w / w, max_h / h)
        flow.append(KeepTogether([Image(str(path), width=w * scale, height=h * scale)]))
        if caption:
            flow.append(P(caption, st["cap"]))
    return flow


def hw_table(hw: dict, rec: dict, st) -> Table:
    gpu = hw["gpus"][0] if hw.get("gpus") else {}
    rows = [
        ["项目", "本机实测环境"],
        ["CPU", f"{hw.get('cpu', '?')}（{hw.get('cores_physical')} 核 / {hw.get('cores_logical')} 线程）"],
        ["内存", f"{hw.get('ram_gb')} GB"],
        ["显卡", f"{gpu.get('name', '无独显')}（显存 {gpu.get('vram_mb', 0)} MB，驱动 {gpu.get('driver', '?')}）"],
        ["系统", hw.get("os", "")],
        ["安装时自动选定的挡位", f"{rec.get('mode')} — " + "；".join(rec.get("reasons", [])[:2])],
    ]
    t = Table(rows, colWidths=[110, 380])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), st["body"].fontName),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#eef3fa")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def perf_table(st) -> Table:
    rows = [["挡位", "做法要点", "WD14 速度", "CLIP 速度", "适合场景"],
            ["榨干硬件", "线程用满 + 高优先级 + cudnn 穷举 + 缩略图 6 线程", "16.4 张/秒", "145 张/秒", "离开电脑前全速跑完"],
            ["均衡（默认）", "线程按物理核、正常优先级、缩略图 4 线程", "16.0 张/秒", "227 张/秒", "日常使用"],
            ["节能", "小批次 4/8、线程 1/3、进程降优先级、批间停 50ms", "17.0 张/秒", "82 张/秒", "边用电脑边跑、笔记本省电"],
            ["只用 CPU", "完全不用独显，显存零占用", "2.9 张/秒", "50 张/秒", "同时在打游戏/渲染"]]
    t = Table(rows, colWidths=[70, 175, 60, 60, 125], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), st["body"].fontName),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3e0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fc")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def build_pdf(out: Path, shots: dict[str, Path], hw: dict, rec: dict) -> None:
    font = register_fonts()
    st = styles(font)
    doc = SimpleDocTemplate(str(out), pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            title="图片标签工坊 · 使用说明书", author="ImageTagStudio")
    S: list = []
    icon = Path(__file__).resolve().parent.parent / "assets" / "icon.png"

    # ---------------- 封面 ----------------
    S.append(Spacer(1, 60))
    if icon.exists():
        S += img_flow(icon, st, max_w=150, max_h=150)
    S.append(P("图片标签工坊", st["cover"]))
    S.append(Spacer(1, 8))
    S.append(P("本地离线图片打标 · 检索 · 审核 · 图库管理", st["cover2"]))
    S.append(Spacer(1, 24))
    S.append(P(f"使用说明书　v1.0　（{time.strftime('%Y-%m-%d')} 生成）", st["cover2"]))
    S.append(Spacer(1, 26))
    S.append(hw_table(hw, rec, st))
    S.append(Spacer(1, 16))
    S.append(P("本说明书中的所有截图均来自真实程序运行界面（离屏渲染抓取），非示意图。", st["cap"]))
    S.append(PageBreak())

    def section(title: str, body: list, images: list[tuple[str, str]] | None = None):
        S.append(P(title, st["h1"]))
        for para in body:
            S.append(P(para, st["body"]))
        for key, cap in (images or []):
            S.extend(img_flow(shots.get(key), st, caption=cap))

    # ---------------- 1 概览 ----------------
    section("一、这是什么", [
        "图片标签工坊是一套<b>完全本地运行</b>的图片管理工具：AI 自动给图片打标签（二次元角色、服装、"
        "人数、体位、场景、画风…），你审核确认后才正式生效；支持按标签检索、系列管理、重复图清理、"
        "分级（全年龄 / R15 / R18 / R18G），并把标签写进文件名，手机上用文件管理器也能搜到。",
        "所有推理都在本机完成（显卡加速），不联网、不上传图片，也不受内容审核影响。",
    ])
    S.append(P("核心能力", st["h2"]))
    S.append(bullets([
        "<b>自动打标</b>：WD14（动漫 1 万+ 标签，含角色名）+ CLIP 零样本（你自己的中文标签）+ 人脸聚类（真人）",
        "<b>人工审核</b>：AI 结果先进待审核队列，确认/拒绝都会回馈模型，越用越准",
        "<b>标签体系</b>：类型可自定义、可多分类、可连线的图谱，图片只存标签，改层级不影响图片",
        "<b>图库</b>：像 Steam 库一样，每个盘一个专用目录，收录后源文件消失，检索只在图库内",
        "<b>系列</b>：漫画多页合并成一个文件夹，页码自动编号，拖动即可改顺序并重命名",
        "<b>查重</b>：感知哈希 + CLIP 双检，选一张保留、其余隔离/删除，误报可反馈、还可一键判定为系列",
        "<b>分级</b>：全年龄 / R15 / R18 / R18G，可对 R18 缩略图打码",
        "<b>离线</b>：模型与依赖全部打包在安装包里，装完即可断网使用",
    ], st["bullet"]))
    S += img_flow(shots.get("main"), st, caption="图 1-1　主界面：左侧标签筛选 / 中间缩略图网格 / 右侧标签编辑")

    # ---------------- 2 安装 ----------------
    section("二、安装（离线、可选路径、自动适配硬件）", [
        "双击安装包里的 <b>图片标签工坊_安装程序.exe</b>。安装程序会先检测本机 CPU / 内存 / 显卡 / 显存 / "
        "剩余空间，给出<b>推荐性能挡位</b>与理由（可以手动改），再让你选择安装位置。",
        "安装内容：程序文件 + 本地模型（约 3 GB）+ 内置 Python 运行库（约 3 GB），"
        "<b>全程不联网</b>；安装完桌面会有快捷方式，卸载执行安装目录里的「卸载.cmd」。",
    ])
    S.append(P("安装步骤", st["h2"]))
    S.append(bullets([
        "① 运行安装程序，等硬件检测完成（CPU/内存/显卡/显存/剩余空间都会列出来）",
        "② 在「性能挡位」里确认推荐挡位（默认已按你的显存和核心数选好）",
        "③ 选择安装位置：默认选剩余空间最大的盘，也可以点「浏览…」自己选",
        "④ 勾选是否创建桌面快捷方式、模型是否装在安装目录、装完是否立即启动",
        "⑤ 点「开始安装」，等待复制文件与离线安装依赖（约 5~15 分钟，视磁盘速度）",
        "⑥ 完成后双击桌面图标即可使用；首次启动会自动加载模型（几秒）",
    ], st["bullet"]))
    S.append(P("自动调参是怎么算的", st["h2"]))
    S.append(bullets([
        "显存 ≥10 GB：允许更大批次 + 高优先级全速；8 GB 左右：批 8/16（实测批 8 已把 GPU 跑满）；"
        "4 GB 以下：小批次（批 3/6）防显存溢出；没有 N 卡：自动选「只用 CPU」挡",
        "物理核数决定 ONNX / torch 线程数；内存 <8 GB 自动降低批次与缩略图线程，避免换页卡顿",
        "检测结果写入安装目录的 <b>perf_profile.json</b>，之后可在程序里随时改挡位",
    ], st["bullet"]))

    # ---------------- 3 快速上手 ----------------
    section("三、五分钟上手", [
        "标准流程：<b>导入图片 → 自动打标 → 审核 → 收录到图库 → 按标签检索</b>。下面逐步说明。",
    ])
    S.append(P("步骤 1　导入图片", st["h2"]))
    S.append(P("点工具栏左端「<b>导入图片…</b>」→ 选源文件夹 → 列表会显示缩略图，"
               "与图库重复的会标黄并默认不勾选 → 右侧可顺手给这批图加标签、勾选导入后自动打标 → "
               "点「开始导入」。导入 = 把文件<b>移动</b>到同盘图库目录（如 E:\\ImageTags），源文件随之消失，"
               "所以下次扫描不会重复。", st["body"]))
    S += img_flow(shots.get("import"), st, caption="图 3-1　导入界面：预览勾选 + 目标图库 + 导入时打标签")
    S.append(P("步骤 2　自动打标", st["h2"]))
    S.append(P("选中图片（或直接对当前列表全部）→ 点「<b>自动打标</b>」。一次跑完 WD14 + CLIP + 分级识别，"
               "结果显示为待审核。左下角状态栏会显示进度，可随时点「停止」。", st["body"]))
    S += img_flow(shots.get("filter"), st, caption="图 3-2　左侧按标签筛选（可多选、支持只看待审核）")
    S.append(P("步骤 3　审核", st["h2"]))
    S.append(P("点「<b>审核待定标签 (N)</b>」。左边是待审图片队列，中间大图，右边逐个标签点 ✓ 或 ✗；"
               "点标签行会在图上高亮它对应的区域框，框不准可「重新框选」。"
               "快捷键：A 全接受、R 全拒、数字键切换、空格保存并下一张。", st["body"]))
    S += img_flow(shots.get("review"), st, caption="图 3-3　审核台：待审队列 + 大图 + 逐个标签决定")
    S.append(P("步骤 4　收录到图库 / 检索", st["h2"]))
    S.append(P("审核完点「<b>收录到图库</b>」把图片正式收进图库（源位置文件消失）。之后左侧勾选标签即可检索，"
               "中间网格显示缩略图；双击进图看大图。", st["body"]))
    S += img_flow(shots.get("tagpanel"), st, caption="图 3-4　选中图片后在右侧批量加标签（可指定类型）")

    S.append(PageBreak())
    # ---------------- 4 界面详解 ----------------
    S.append(P("四、界面详解", st["h1"]))
    S.append(P("工具栏（按流程排）", st["h2"]))
    S.append(bullets([
        "<b>导入图片…</b>：Lightroom 式导入（选文件夹→勾选→导入+可打标）",
        "<b>自动打标</b>：WD14 + CLIP + 分级一次跑完（分级是必备标签，已并入这个流程）",
        "<b>审核待定标签 (N)</b>：人工过一遍 AI 结果",
        "<b>收录到图库</b>：移动到同盘图库目录",
        "<b>查重 / 保留选择</b>：重复与近似图处理（可判定为系列）",
        "<b>合并为系列 / 写回文件名 / 停止</b>：系列整理、标签落盘、中断任务",
        "<b>更多 ▾</b>：分级识别（单独补跑）、区域精修、CLIP 重打分、人脸检测、查找相似图片、导出框选标注、添加/重新扫描来源",
        "<b>右侧下拉「性能」</b>：榨干硬件 / 均衡 / 节能 / 只用 CPU，一键切换",
    ], st["bullet"]))
    S.append(P("三栏布局", st["h2"]))
    S.append(bullets([
        "左：标签筛选树（按类型分组，勾选即筛选，支持「只看待审核」「只检索图库」「分级」下拉）与文件夹树",
        "中：缩略图网格（滚轮缩放、Ctrl/Shift 多选、双击进图或进系列、右键菜单）",
        "右：选中图片的标签面板（勾选增删、新增标签并指定类型、相似图推荐、写回文件名）",
    ], st["bullet"]))
    S += img_flow(shots.get("main"), st, caption="图 4-1　主界面全貌")
    S += img_flow(shots.get("preview"), st, caption="图 4-2　大图预览：可开启框选标注，把标签对应到画面区域")

    # ---------------- 5 标签与类型 ----------------
    S.append(P("五、标签与类型", st["h1"]))
    S.append(P("标签类型不只是分组，它还决定「用 CLIP 找同类时套什么提示词」。类型存库、可自定义、可排序。", st["body"]))
    S += img_flow(shots.get("catmgr"), st, caption="图 5-1　类型管理：新增/改名/删除、给每个类型配提示词模板")
    S.append(bullets([
        "新建标签时可直接选已有类型，也可以当场输入新类型名（会弹出小窗配置提示词模板）",
        "提示词模板用 <b>{}</b> 代表标签名，用 | 分隔多条，例如道具类：<code>a {} object | {} | holding {}</code>",
        "给标签填 WD14 英文名（如 hatsune_miku）可让 WD14 的结果直接映射到你的中文标签",
        "「前置条件」用于局部标签：例如「领带」要求同时出现「人物」，不满足就不打",
        "标签改名会同步所有图片；可选同时改写文件名",
    ], st["bullet"]))
    S += img_flow(shots.get("tagmgr"), st, caption="图 5-2　标签管理：批量改类型、改提示词、合并标签")

    # ---------------- 6 标签体系 ----------------
    S.append(P("六、标签体系（图谱）", st["h1"]))
    S.append(P("一个标签可以同时属于多个分类，所以是「图」而不是树；分类还能挂到更大的分类下，"
               "节点可拖动、可自动排列、可手动连线。这一层完全独立于图片——改层级不会影响图片。", st["body"]))
    S += img_flow(shots.get("taxonomy"), st, caption="图 6-1　标签体系图谱：左树 + 中图 + 右属性")

    S.append(PageBreak())
    # ---------------- 7 审核 ----------------
    S.append(P("七、审核：AI 结果不直接生效", st["h1"]))
    S.append(P("自动打标的结果先进入「待审核」，只有确认过的才算正式生效（才写文件名、才参与检索）。", st["body"]))
    S.append(hw_table(hw, rec, st) if False else P("", st["body"]))
    rows = [["状态", "含义", "影响"],
            ["待审核", "AI 刚打上的", "缩略图有黄色「待审N」角标，标签行带 ? 前缀；不写文件名、不算正式标签"],
            ["已生效", "你确认或手动加的", "正常检索、写文件名、参与模型训练"],
            ["已拒绝", "你判断「不是这个标签」", "不再出现，并作为负样本喂给模型"]]
    t = Table(rows, colWidths=[60, 120, 310], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), st["body"].fontName), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    S.append(t)
    S.append(Spacer(1, 8))
    S.append(P("审核台还能做的事", st["h2"]))
    S.append(bullets([
        "补标签：输入名字回车即生效（可先在旁边选类型）；「新建标签（类型/提示词）」适合以后要靠 CLIP 找的标签",
        "相似图片推荐：从库里相似图片的标签里双击采纳",
        "选中标签即可高亮它对应的区域框；「重新框选」替换旧框，模型立刻重学",
        "下方「已有系列」面板：把当前图片加入某系列、或用它新建系列、或移出系列",
        "重复审核不会冲突：审核只改数据库状态，从不重写图片文件",
    ], st["bullet"]))

    # ---------------- 8 图库 ----------------
    S.append(P("八、图库（Steam 式多库）", st["h1"]))
    S.append(bullets([
        "每个盘一个图库目录，默认 <code>&lt;盘符&gt;:\\ImageTags</code>（可在设置里改名）",
        "「收录到图库」= 移动到同盘图库目录（同盘是毫秒级 rename），源文件消失，扫描不会重复",
        "图库目录会自动创建；不可写的盘（只读盘/光盘/无权限）会跳过并提示，不会中断整批、也不会动你的文件",
        "左侧「只检索图库」默认开启；删除/查重产生的文件放在图库下的 <code>.removed</code> 隔离区，可手动恢复",
    ], st["bullet"]))

    # ---------------- 9 查重与系列 ----------------
    S.append(P("九、查重与系列", st["h1"]))
    S.append(P("查重：感知哈希（缩放/压缩/轻微改动都能抓到）+ CLIP 特征兜底，按组展示，"
               "默认自动选分辨率最高、体积最大的那张作为保留建议。", st["body"]))
    S.append(bullets([
        "三个动作：保留选中（其余移入隔离区或删除）、不是重复（误报反馈，以后不再提示）、"
        "<b>判定为系列</b>（这些其实是同一系列的不同页 → 按顺序并进同一文件夹，不删文件）",
        "同一系列内部的相似不会再报警，只有「一方是系列页、另一方是单图」这种不对称相似才提示",
        "合并标签：被删图片的标签与框选区域会并入保留的那张，不丢信息",
    ], st["bullet"]))
    S += img_flow(shots.get("dupes"), st, caption="图 9-1　查重界面：按组比对，选保留 / 误报 / 判定为系列")
    S.append(P("系列：拖动改顺序，自动重命名", st["h2"]))
    S.append(P("双击进入系列后，网格按页码排列（缩略图左下角有 P001 角标）；直接拖动缩略图到目标位置，"
               "松手即按新顺序重排页码并重命名成 001 / 002 / 003…（扩展名保持不变，"
               "两阶段改名所以互换不会互相覆盖）。可多选一起拖。", st["body"]))
    S += img_flow(shots.get("series"), st, caption="图 9-2　系列视图：按页码排列，拖动即可改顺序并自动重命名")

    S.append(PageBreak())
    # ---------------- 10 分级 ----------------
    S.append(P("十、分级识别（对齐 pixiv）", st["h1"]))
    S.append(P("动漫图用 WD14 自带的 4 类分级概率，真人照片用 CLIP 的成人/猎奇提示词兜底，融合后输出四级："
               "<b>全年龄 / R15 / R18 / R18G</b>，并自动打上对应标签（分类为「分级」），可当筛选墙用。", st["body"]))
    S.append(bullets([
        "左侧有分级下拉筛选，缩略图右下角显示彩色等级角标",
        "设置 → 分级里可把 WD14 的 questionable（半露/暗示）算成 R15（默认）或 R18（更严格）",
        "可开启「浏览时对 R18/R18G 缩略图打码」，公开场合翻库不尴尬",
        "分级标签默认直接生效（不进审核队列），方便当筛选墙",
    ], st["bullet"]))

    # ---------------- 11 性能 ----------------
    S.append(P("十一、性能挡位与实测数据", st["h1"]))
    S.append(P("工具栏右侧「性能」下拉可一键切换（立即生效）；安装时已按你的硬件自动选好。", st["body"]))
    S.append(perf_table(st))
    S.append(Spacer(1, 6))
    S.append(bullets([
        "换算整库：1000 张图跑 WD14+CLIP，均衡约 70 秒、节能约 70 秒（系统占用低得多）、只用 CPU 约 6 分钟",
        "WD14 是瓶颈（约 16 张/秒），CLIP 很快（100~200 张/秒）且图片特征只算一次，之后改标签是秒级",
        "批大小不是越大越好：这台机器批 8 就把 GPU 跑满了，堆到 16/32 反而更慢（实测）",
        "三个 GPU 挡位的吞吐差别很小，真正区别是「对系统的打扰程度」；只有 CPU 挡明显慢 5~6 倍",
    ], st["bullet"]))

    # ---------------- 12 命令行 ----------------
    S.append(P("十二、命令行与自检", st["h1"]))
    S.append(P("安装目录里带有 <code>python\\python.exe</code>，可以不开界面批量处理：", st["body"]))
    code = [["命令", "作用"],
            ["python -m app.cli models", "下载/校验模型"],
            ["python -m app.cli add \"E:\\图库\"", "加入库并扫描"],
            ["python -m app.cli tag --all --wd14", "全库 WD14 打标"],
            ["python -m app.cli search 泳装 初音未来", "多标签检索（同时满足）"],
            ["python -m app.cli writeback", "标签写回文件名"],
            ["python tools\\selftest.py", "全流程自检"],
            ["python tools\\bench_perf.py 60", "性能实测"]]
    t = Table(code, colWidths=[240, 250], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), st["body"].fontName), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3e0")),
    ]))
    S.append(t)

    # ---------------- 13 FAQ ----------------
    S.append(P("十三、常见问题", st["h1"]))
    faq = [
        ("一次性导入几千张会不会卡？", "不会。列表分块载入、缩略图在后台线程生成，3000 张实测填充 0.08 秒、全选 0.05 秒、"
                                  "主网格刷新 0.03 秒。导入前还会自动停掉缩略图读取，避免文件被占用。"),
        ("导入时报 WinError 32（文件被占用）？", "已内置处理：导入前释放文件句柄 + 移动失败自动重试。"
                                          "如果仍出现，通常是杀毒软件正在扫描，稍等或把图库目录加入白名单。"),
        ("文件名太长导致写入失败？", "写回文件名时会自动过滤噪声标签、限制 12 个标签、总长 ≤150 字符。"),
        ("图片被删了但记录还在？", "重新扫描会把丢失的文件标记为 missing，不再显示；检索与查重都会跳过。"),
        ("换电脑怎么迁移？", "拷贝安装目录（含 models 与 python）与数据目录 %LOCALAPPDATA%\\ImageTagStudio 即可，"
                        "或在新机器上重跑安装包后覆盖数据目录。"),
        ("怎么彻底删除数据？", "删除 %LOCALAPPDATA%\\ImageTagStudio（数据库、缩略图、设置）与安装目录即可；"
                          "图库目录里的图片是你自己的文件，请自行决定是否保留。"),
    ]
    for q, a in faq:
        S.append(P(f"<b>Q：{q}</b>", st["body"]))
        S.append(P(f"A：{a}", st["body"]))

    # ---------------- 附录 ----------------
    S.append(PageBreak())
    S.append(P("附录 A　目录与数据位置", st["h1"]))
    S.append(bullets([
        "安装目录：程序、内置 Python、模型（models/）、性能档案（perf_profile.json）、卸载.cmd",
        "数据目录：<code>%LOCALAPPDATA%\\ImageTagStudio</code> —— library.db（索引）、thumbs/（缩略图）、settings.json",
        "图库目录：<code>&lt;盘符&gt;:\\ImageTags</code> —— 正式存放图片；查重/删除的文件进 <code>.removed</code>",
        "标签写回文件名约定：<code>&lt;原名&gt; [tag1 tag2].jpg</code>；系列为 <code>&lt;系列名&gt; [标签]\\001.jpg</code>",
    ], st["bullet"]))
    S.append(P("附录 B　快捷键与技巧", st["h1"]))
    S.append(bullets([
        "审核台：A 全接受｜R 全拒｜1~9 切换第 n 个标签｜空格 保存并下一张｜← 上一张",
        "网格：Ctrl/Shift 多选｜双击进图或进系列｜右键菜单（打标/收录/查重/相似图/资源管理器）",
        "系列视图：直接拖动缩略图改顺序（自动重命名）",
        "预览：滚轮缩放｜打开「框选标注」在图上拖框，把标签对应到画面区域",
        "新建标签时输入不存在的类型名即可当场创建类型",
    ], st["bullet"]))
    S.append(Spacer(1, 14))
    S.append(P(f"—— 本说明书由程序自动生成（tools/make_manual.py），截图来自 {time.strftime('%Y-%m-%d %H:%M')} 的真实运行界面。",
               st["cap"]))

    doc.build(S)
