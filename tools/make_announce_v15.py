"""把 v1.5 更新公告排成可投 B 站专栏的 Word（封面 + 真实界面截图 + 表格）。

用法： python tools\make_announce_v15.py [输出.docx]
素材：公告_v1.5\公告_v1.5_封面.png、manual_shots\*.png（跑 make_manual.py 会刷新）
说明：排版沿用 v1.3/v1.4 那套（复用 tools/make_article_docx.py 的样式函数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))

import make_article_docx as A            # noqa: E402  （复用字体/段落/表格/配图等样式函数）
from docx import Document                # noqa: E402
from docx.shared import Pt               # noqa: E402

SHOTS = HERE / "manual_shots"
COVER = HERE / "公告_v1.5" / "公告_v1.5_封面.png"
OUT_DEFAULT = HERE / "公告_v1.5" / "公告_v1.5_专栏版.docx"


def build(out: Path) -> None:
    doc = Document()
    A.drop_title_border(doc)

    if COVER.exists():
        doc.add_picture(str(COVER), width=Pt(430))

    A.para(doc, "图片标签工坊 v1.5 更新公告", 22, True, A.BLACK, space_after=6)
    A.para(doc, "发布日期：2026-10-08　内容：程序文件（不含模型与依赖）", 10, False,
           A.META_GREY, space_after=10)
    A.para(doc, "v1.4 之后的所有改动统一归到这一版。这一版不是「又加了几个按钮」，"
                "而是把单机工具变成了「PC + 安卓端」：平板和手机能连上 PC 遥控全流程，"
                "另外还加了一个可选安装的 AI 生图扩展包。", 11.5, False, A.BODY_GREY, space_after=10)

    # ---------------- 一、局域网遥控 + 安卓端 ----------------
    A.heading(doc, "一、局域网遥控 + 安卓端（平板 / 手机）")
    A.heading(doc, "连接：自动发现，双向确认", 2)
    A.bullet(doc, "PC 端开一个局域网服务（HTTP 47824 / UDP 发现 47823）；同机跑正式版+测试版时端口自动顺延，"
                  "不会再出现「两个实例抢一个端口、手机连错人家」。")
    A.bullet(doc, "安卓端自动查找设备、选中即连，不用手输 IP。")
    A.bullet(doc, "双向确认：移动端要带 PC 上显示的 4 位对码，PC 端还会弹框让你点「同意」，"
                  "之后按设备号记住授权；对码错的直接拒，未授权请求一律 401。")
    A.bullet(doc, "授权后除「离开局域网 / 设备关机」外不会掉线；心跳改成真事件后，"
                  "移动端能判断连接已半死并自动重连。")

    A.heading(doc, "平板端：全功能遥控，不是阉割版", 2)
    A.bullet(doc, "看图库：和 PC 同一口径——系列算一条（封面=第一页、带页数、点开看内页），"
                  "缩略图可一次打包多张，原图走 HTTP Range 流式。")
    A.bullet(doc, "审核台：与 PC 完全同一份数据（服务端算好再下发），中英对照 + 来源 + 置信度 + "
                  "✓/✗/⊘ 三态按钮，支持撤销；多设备同时审不会互相覆盖（版本对不上直接 409）。")
    A.bullet(doc, "打标 / 写回文件名 / 查重 / 相似图 / 框选区域与区域精修 / 人物 / 学习包导出 / 导出 YOLO 与字幕。")
    A.bullet(doc, "目录操作：新建、改名、删除文件夹、移动图片（系列整组移动）、收录到图库；"
                  "删除一律进回收站，越界路径直接 403。")
    if (SHOTS / "14_大图预览与框选.png").exists():
        A.figure(doc, SHOTS / "14_大图预览与框选.png", "大图页：缩放、平移、框选区域（平板端同款交互）", 5.6)

    A.heading(doc, "图谱在移动端重做", 2)
    A.para(doc, "关系视图 / 热度视图（带图例）、分类大圆 + 标签小圆、折叠（状态记住）、"
                "搜索命中带缓动运镜、拖拽是弹簧回弹、按分类做扇形分区——实测 4311 节点重叠 0 对。"
                "坐标由 PC 算好下发（0.1 秒算完），移动端不用自己摆。", 11, False, A.BODY_GREY, space_after=6)
    if (SHOTS / "08_标签体系图谱.png").exists():
        A.figure(doc, SHOTS / "08_标签体系图谱.png", "标签体系图谱（PC 侧；移动端同款视觉与交互）", 5.6)

    A.heading(doc, "手机端与安装", 2)
    A.bullet(doc, "手机端不再单独开发，改做「精简版平板端」：只保留图片浏览 + 图谱，计划已列。")
    A.bullet(doc, "平板端是安卓 APK（arm64-v8a、横屏），在 MuMu 模拟器（Android 15）上真机界面验收通过："
                  "浏览 / 审核 / 生图 / 放大 / 参考图 / 姿势 / 框选 / 区域精修 / 相似图 / 写回文件名全部走通。")

    # ---------------- 二、AI 生图扩展包 ----------------
    A.heading(doc, "二、AI 生图扩展包（可选安装）")
    A.para(doc, "装程序时（或之后在「更多 ▾ → 扩展包（DLC）…」）可以可选安装一个「AI 生图助手」，"
                "把本机 ComfyUI 变成「写中文 → 出图 → 自动入库」的一条龙。默认不启用，不装完全不影响主程序；"
                "模型本体不进包，按需下载（断点续传，18MB 起）。", 11, False, A.BODY_GREY, space_after=6)

    A.heading(doc, "写中文就能出图（完全离线）", 2)
    A.bullet(doc, "提示词框可直接写中文（「初音未来海边微笑，半身，冷色调」），"
                  "背后是本机 Ollama + 23 万条 danbooru 词表离线转换与校验，不联网也能用。")
    A.bullet(doc, "可按目标模型过滤词表（NoobAI / Illustrious / Anima 各自认哪些词），也能一键插入你图库里的热词。")

    A.heading(doc, "出图与批量", 2)
    A.bullet(doc, "支持 SDXL 与 Anima 两个模型家族；尺寸给的是档位 + 自定义（256~2048、8 的倍数）。")
    A.bullet(doc, "会自动拦掉 SDXL 的糊图档：低于约 0.6MP 的请求被自动抬到约 1024 并说明原因"
                  "（实测 512×512 出来是色块）；默认底模优先挑 SDXL（SD1.5 不再作为默认）。")
    A.bullet(doc, "批量队列：可排队、可取消、断电后接着跑（跳过已经出好的）。")

    A.heading(doc, "改图四件套", 2)
    A.bullet(doc, "局部重绘 / 换装：界面上涂哪改哪（白=重绘），实测 512×512 约 6 秒出图、遮罩外像素不动。")
    A.bullet(doc, "参考图（IP-Adapter）：给一张参考图影响风格/角色。")
    A.bullet(doc, "姿势 / 线稿（ControlNet）：给一张姿势图，姿势照着走。")
    A.bullet(doc, "图生图 / 图融合：只给底图就是改造这张图（denoise 控强度）；再给第二张就是两张融成一张，"
                  "界面里能调混合比例与混合模式。")
    A.para(doc, "参考图与姿势控制会按底模架构自动配对（SDXL 配 SDXL、SD1.5 配 SD1.5）。"
                "这不是小事：配错时 ControlNet 会直接报错，而 IP-Adapter 不报错、只出一张噪声图——"
                "现在由程序判断，不用你自己记文件名。", 10.5, False, A.CAP_GREY, space_after=6)

    A.heading(doc, "放大：要更大的图别再硬开分辨率", 2)
    A.bullet(doc, "4× 纯放大（Real-ESRGAN 超分）：1024 → 4096 实测 9 秒，画面内容不变、只是变清楚，无彩噪。")
    A.bullet(doc, "另有「高分修复」（潜空间放大 + 低强度重采样）：会长新细节，但头发边缘可能出彩噪，标注为可选项。")
    A.bullet(doc, "为什么不让你直接开 2048² 出图：8GB 显存基本必爆，超出训练分辨率还容易出多手多脚。"
                  "先出 1024 再放大才稳。")

    A.heading(doc, "模型下载与移动端遥控", 2)
    A.bullet(doc, "扩展包自带模型清单（NoobAI-XL、WAI-illustrious、Anima 三件套、IP-Adapter 两件套、放大模型…），"
                  "一键下载、断点续传，界面能看到「本机已有 / 还缺什么 / 多大」。")
    A.bullet(doc, "平板/手机连上后可出图 / 局部重绘 / 参考图 / 姿势 / 图生图融合 / 放大 / 取消，算力全在 PC；"
                  "生成结果可自动入库，于是在平板图库里直接能看到。")
    A.bullet(doc, "取消是定向取消：正在跑的、还在排队的都能停，而且不会误伤别的客户端正在跑的任务。")

    # ---------------- 三、PC 端本体 ----------------
    A.heading(doc, "三、PC 端本体的改动")
    A.heading(doc, "启动开屏", 2)
    A.para(doc, "启动时有一个类 Adobe 的过渡窗口：宽度取屏幕 1/3，图片区高度按图的比例自适应（完整显示不裁切），"
                "Win11 风格圆角 + 下方独立白底信息条（应用名 / 版本 / 启动进度）。封面是本项目自己生成的 14 张，"
                "伪随机轮换（一轮内不重复），可在设置里换成你自己的图。", 11, False, A.BODY_GREY, space_after=6)

    A.heading(doc, "图库：浏览模式", 2)
    A.bullet(doc, "左键单击进入浏览模式：只留图片和标签；滚轮以鼠标位置为中心缩放、左键拖动平移、"
                  "「适应窗口 / 放大 / 缩小」加持，支持翻页（按钮 + ←/→/PgUp/PgDn/空格 + 第 n/N 张）。")
    A.bullet(doc, "框选标注、区域精修挪进右键菜单，界面干净很多。")
    if (SHOTS / "01_主界面.png").exists():
        A.figure(doc, SHOTS / "01_主界面.png", "主界面：工具栏按流程排布，右侧是标签与批量操作", 5.6)

    A.heading(doc, "标签体系图谱（PC 侧）", 2)
    A.bullet(doc, "每个标签都在图里（本库 4311 个节点 / 4826 条边），打开约 1.3 秒；坐标一次算完（0.1 秒）。")
    A.bullet(doc, "搜索命中带缓动运镜、自动展开祖先；分类与标签列表联动（审核台、图库管理、导入、标签编辑都按分类过滤）。")
    A.bullet(doc, "缩放后拖不动的老问题修好了（原来在 0.5× 下可平移余量是 -203px，等于拖不动）。")

    A.heading(doc, "审核台", 2)
    A.bullet(doc, "同一张图上父子标签同时在待审时只显示子标签；通过子标签 = 同时通过父标签，"
                  "否决子标签则把父标签放回来单独判。")
    A.bullet(doc, "可撤销：按钮 + Ctrl+Z，最多连撤 50 次（标签、分级、已审标记一起还原，模型反馈也退回）。")
    A.bullet(doc, "关窗不再静默保存（保留「下次进来状态归零」），只有点「审核完毕 / 保存」才真正写库并反馈给模型。")
    if (SHOTS / "05_审核台.png").exists():
        A.figure(doc, SHOTS / "05_审核台.png",
                 "审核台：三态判断 + 中英对照 + 来源与置信度（平板端同一份数据）", 5.6)

    A.heading(doc, "系列与其它改进", 2)
    A.bullet(doc, "拖动排序可视化（带缩略图、双击看大图、F2/右键改名），合并与重排共用一套界面；"
                  "拖动系列 = 整组移动；系列图重新打标/审核后重算系列标签并刷新文件夹名。")
    A.bullet(doc, "写回文件名：中文名 + 首位固定分级 + 只写最具体的子标签（父标签检索照样命中）；"
                  "分级别名归一（15禁→R15 等）并清理历史脏标签。")
    A.bullet(doc, "一次性导入几千张不再卡死（实测列表填充 0.08s / 全选 0.05s / 网格刷新 0.03s）；"
                  "打码不再把非 1:1 图拉变形；待审队列过长时缩略图不显示、点进去大图空白——都修了。")
    A.bullet(doc, "新增「最小化到托盘」与「已连接设备…」菜单；多开时只弹一个启动开屏。")

    # ---------------- 四、修掉的硬骨头 ----------------
    A.heading(doc, "四、修掉的几个硬骨头")
    A.make_table(doc, ["问题", "根因（一句话）"],
                 [["设备发现服务从来没启动过，配对码一直是 0000",
                   "函数里 socket 名字写错，异常又被静默吞掉"],
                  ["未定级的图首次审核必失败（409）",
                   "队列端与提交端算版本号时一个传 None、一个传空串，哈希对不上"],
                  ["实时事件丢名字，移动端「没反应」",
                   "事件名被调用方的 kind 参数覆盖，客户端按事件名分派时整条丢弃"],
                  ["标签图谱放大后完全拖不动",
                   "低缩放下可平移余量是负的（-203px）"],
                  ["AI 生图窗口在笔记本上放不下",
                   "窗口最低高度 1213px；现在按屏幕定尺寸 + 分区滚动 + 可折叠（最低 324px）"],
                  ["「停止生成」偶发不生效",
                   "只发了打断令，而它只能打断正在执行的那个；现在改为定向取消（清排队 + 定点打断）"]],
                 [2.7, 3.1])

    # ---------------- 五、升级说明 ----------------
    A.heading(doc, "五、升级说明")
    A.bullet(doc, "更新包：只含程序文件，解压覆盖到程序目录即可（保留设置、图库索引、已装模型）。")
    A.bullet(doc, "完整包：首次安装用；安装时可勾选「安装可选扩展包（AI 生图助手）」，"
                  "装完在「更多 ▾ → 扩展包（DLC）…」里启用。")
    A.bullet(doc, "数据兼容：库文件、标签体系、系列、审核记录全部向后兼容；老版本写回过的文件名照样能扫回来。")
    A.bullet(doc, "要注意：写回文件名会覆盖原文件名（默认只用标签命名），首次建议拿一小批试；"
                  "设置里可以勾回「保留原文件名」。")
    A.bullet(doc, "扩展包需要本机已有 ComfyUI；提示词助手需要本机 Ollama（都可离线，离线安装包不含模型）。")

    A.heading(doc, "六、已知限制")
    A.bullet(doc, "CLIP 零样本对小众具体物体（比如某种特定玩具）仍不可靠，这类标签要靠框选 + 以图找图 + 样本积累。")
    A.bullet(doc, "区域精修目前是固定候选窗口 + 位置先验，不是真正的目标检测器；"
                  "框选数据可导出成 YOLO 数据集，将来训练小检测器会更准。")
    A.bullet(doc, "人脸聚类是离线批量聚类，人特别多时建议分库。")
    A.bullet(doc, "手机端目前只做「图片浏览 + 图谱」，生图只留了计划。")
    A.bullet(doc, "移动端用的是 PC 的算力，PC 关机或离开局域网就用不了（设计如此）。")

    A.para(doc, "反馈随时提。这一版改动很大（尤其是局域网与生图扩展包），用着不顺手的地方直接说，下个版本接着改。",
           11, False, A.BODY_GREY, space_after=4)
    A.para(doc, "项目地址：github.com/HooooolyShift/imagetag-studio", 10.5, False, A.META_GREY)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print("已生成:", out)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    build(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
