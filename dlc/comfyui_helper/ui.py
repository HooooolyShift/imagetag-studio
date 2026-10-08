"""AI 生图窗口（原生 Qt，不依赖 SwarmUI 页面）。

现在这一版是"能跑通链路的最小可用版"：选底模 / 写提示词 / 设参数与输出路径 / 出图 / 看图。
接下来由 AI 生图会话补内容：中文→danbooru 标签的提示词助手、工作流模板（含 Anima 那套
UNETLoader/ModelSamplingAuraFlow/CLIPLoader 写法）、模型清单与下载、批量出图队列、
一键入库/打标。宿主只管把窗口托起来与配置持久化。
"""
from __future__ import annotations

import datetime
import random
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QDoubleSpinBox, QTextEdit, QToolTip,
                               QVBoxLayout, QWidget)

import re

from .comfy import ComfyClient, ComfyError
from .extras import MorePanel, keep_worker, wait_workers


class _StatusProbeWorker(QThread):
    """后台探测 ComfyUI / Ollama（打开窗口时**不能同步做**）。

    2026-10-08 用户报"启动生图界面可能会卡住"：原来 `GenWindow.__init__` 末尾直接
    `refresh_status()` → `ComfyClient.ping()`（5 秒超时）+ `ollama.is_up()`（3 秒），
    两个都在**界面线程**上。服务没起、正在启动、或者正忙着出图时，点开菜单就要冻 2~8 秒。
    现在探测放到这个线程里，窗口先画出来、状态先显示"检测中…"，结果回来再填。
    """

    done = Signal(bool, str)

    def __init__(self, comfy_url: str, ollama_url: str = ""):
        super().__init__()
        self.comfy_url = comfy_url
        self.ollama_url = ollama_url

    def run(self) -> None:                       # noqa: D102
        try:
            ok, info = ComfyClient(self.comfy_url, timeout=3).ping(timeout=2.0)
        except Exception as exc:                 # noqa: BLE001
            ok, info = False, f"连不上 ComfyUI：{exc}"
        text = ("✅ " if ok else "❌ ") + info
        try:
            from .prompt_helper import ollama as _ollama
        except ImportError:
            try:
                from prompt_helper import ollama as _ollama
            except ImportError:
                _ollama = None
        if _ollama is not None:
            host = self.ollama_url or _ollama.DEFAULT_HOST
            try:
                if _ollama.is_up(host, timeout=1.5):
                    names = _ollama.list_models(host, timeout=3.0)
                    text += f"｜提示词模型：{_ollama.pick_best_model(names, host)}"
                else:
                    text += "｜提示词模型：未启动 Ollama"
            except Exception:                    # noqa: BLE001
                pass
        self.done.emit(ok, text)


class _PreloadWorker(QThread):
    """后台把"迟早要用"的东西先读进来，别等用户点按钮才读。

    · `kind="hot"`：通用热词兜底（要读 DLC 的 wordlists，8MB gzip，首次约 2~3 秒）；
    · `kind="dict"`：DLC 词表本体（中文→标签、词表校验 都靠它）。
    两者以前都在界面线程上按需同步读，小屏/老机器上就是"点开生图界面卡住"。
    """

    done = Signal(str, list)

    def __init__(self, win, kind: str, n: int = 0):
        super().__init__()
        self.win = win
        self.kind = kind
        self.n = n

    def run(self) -> None:                       # noqa: D102
        try:
            if self.kind == "hot":
                self.done.emit(self.kind, list(self.win._generic_hot_tags(self.n)))
            else:
                self.win._dict()                 # 读进来即可，结果走 win._dict_cache
                self.done.emit(self.kind, [])
        except Exception:                        # noqa: BLE001
            self.done.emit(self.kind, [])


class _DetectWorker(QThread):
    """后台跑 autoconnect.detect()（内部会起 PowerShell 查进程，最坏 20 秒）。"""

    done = Signal(dict)

    def run(self) -> None:                       # noqa: D102
        try:
            try:
                from . import autoconnect
            except ImportError:
                import autoconnect
            self.done.emit(dict(autoconnect.detect() or {}))
        except Exception:                        # noqa: BLE001
            self.done.emit({})


class _StartComfyWorker(QThread):
    """后台启动 ComfyUI。

    `autoconnect.start_comfy()` 会**最多等 2 分钟**（每 3 秒探一次端口），同步调用就是
    点一下按钮界面冻两分钟 —— 这是用户报的"卡住"里最狠的一处。
    """

    done = Signal(bool, str)

    def __init__(self, path: str, port: int):
        super().__init__()
        self.path = path
        self.port = port

    def run(self) -> None:                       # noqa: D102
        try:
            try:
                from . import autoconnect
            except ImportError:
                import autoconnect
            ok, msg = autoconnect.start_comfy(self.path, port=self.port)
        except Exception as exc:                 # noqa: BLE001
            ok, msg = False, f"启动失败：{exc}"
        self.done.emit(bool(ok), str(msg))


class _PromptHelperWorker(QThread):
    """后台跑"中文 → danbooru 标签"，别卡住界面（本机 Ollama 一次几秒到几十秒）。
    both=True 时一次给出正向 + 负向两组（默认，不用手动切模式）。"""

    ok = Signal(dict)
    err = Signal(str)

    def __init__(self, text: str, mode: str, model: str, both: bool = True, lexicon=None,
                 model_filter: str = ""):
        super().__init__()
        self.text = text
        self.mode = mode
        self.model = model
        self.both = both
        self.lexicon = lexicon
        self.model_filter = model_filter

    def run(self) -> None:                       # noqa: D102
        try:
            try:
                from .prompt_helper import generate, generate_both, load_dictionary
            except ImportError:                  # 被当成普通脚本直接跑时
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from prompt_helper import generate, generate_both, load_dictionary
            d = load_dictionary()
            if self.lexicon is not None:
                # 中文 ↔ 英文映射交给主程序共享词库（host.lexicon()），本模块只管"模型认不认这个 tag"
                d.attach_lexicon(self.lexicon)
            if self.model_filter:
                d.set_model(self.model_filter)
            if self.both:
                data = generate_both(self.text, dictionary=d, model=self.model)
                data["both"] = True
                self.ok.emit(data)
            else:
                r = generate(self.text, self.mode, dictionary=d, model=self.model)
                payload = r.to_dict()
                payload["both"] = False
                self.ok.emit(payload)
        except Exception as exc:                 # noqa: BLE001
            self.err.emit(str(exc))


class TagHoverTextEdit(QTextEdit):
    """提示词输入框：鼠标停在**英文 tag** 上时，气泡显示词表里的中文（只显示词表命中的）。

    只认"逗号/换行分隔的一段"；带权重写法 (tag:1.2) 会去掉权重再查。
    """

    def __init__(self, zh_lookup=None, parent=None):
        super().__init__(parent)
        self._zh_lookup = zh_lookup
        self.setMouseTracking(True)

    def _tag_at(self, pos) -> str:
        text = self.toPlainText()
        idx = self.cursorForPosition(pos).position()
        idx = max(0, min(idx, len(text)))
        start = max(text.rfind(",", 0, idx), text.rfind("\n", 0, idx)) + 1
        ends = [p for p in (text.find(",", idx), text.find("\n", idx)) if p != -1]
        end = min(ends) if ends else len(text)
        tag = text[start:end].strip()
        if not tag:
            return ""
        m = re.match(r"^\((.*):[\d.]+\)$", tag)
        if m:
            tag = m.group(1).strip()
        tag = tag.strip("()").replace("\\", "").strip()
        # 只对英文 tag 做悬浮（中文输入不弹）
        if not re.search(r"[A-Za-z]", tag) or re.search(r"[\u4e00-\u9fff]", tag):
            return ""
        return tag

    def event(self, event):                       # noqa: D102
        if event.type() == QEvent.ToolTip and self._zh_lookup is not None:
            tag = self._tag_at(event.pos())
            zh = None
            if tag:
                try:
                    zh = self._zh_lookup(tag)
                except Exception:                 # noqa: BLE001
                    zh = None
            if zh:
                QToolTip.showText(event.globalPos(), f"{tag}\n{zh}", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class GenWindow(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cfg = host.config
        # 配置键有历史包袱：清单（dlc.json）与宿主接口读的是 `comfyui_url`，
        # 但这个窗口早期写的是 `comfy_url`（2026-10-08 在 D 副本上实测踩到：
        # 宿主写进去的 comfyui_url 窗口读不到，只认默认值）。两个都认，写的时候两个都写。
        self._CFG_URL_KEYS = ("comfyui_url", "comfy_url")
        self.client = ComfyClient(self._cfg_url())
        self.setWindowTitle("AI 生图（ComfyUI 助手）")
        # 别照 1080×720 硬开：下面「参数」+「更多」加起来最低要 1200+ 像素，
        # 窗口会被自己的 minimumSizeHint 顶高，小屏上直接露到屏幕外（实测 1280×800 会切掉
        # 「开始生成」和整个「更多」面板）。这里按屏幕可用区域定初始尺寸，两边都包滚动区。
        self._fit_to_screen()
        self._busy = False

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#8f96a3;")
        # 这行会显示很长的状态（显卡名 / 当前提示词模型…），不设下限的话它会把整窗最小宽度顶到 1200+
        self.status.setMinimumWidth(120)
        self.status.setToolTip("状态栏：ComfyUI 连接、当前底模/提示词模型、队列进度等")
        b_check = QPushButton("检查 ComfyUI")
        b_check.clicked.connect(self.check_comfy)
        b_models = QPushButton("拉取模型列表")
        b_models.clicked.connect(self.reload_models)
        bar.addWidget(b_check)
        bar.addWidget(b_models)
        bar.addWidget(self.status, 1)
        root.addLayout(bar)

        conn = QGroupBox("ComfyUI 接入")
        cf = QFormLayout(conn)
        path_row = QWidget()
        pr = QHBoxLayout(path_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.comfy_path = QLineEdit(str(self.cfg.get("comfyui_path") or ""))
        self.comfy_path.setPlaceholderText("ComfyUI 安装目录（含 main.py），可点「自动检测」")
        b_detect = QPushButton("自动检测")
        b_detect.setToolTip("在本机常见位置找 ComfyUI 安装目录，并探测已在运行的服务")
        b_detect.clicked.connect(self.auto_detect)
        b_start = QPushButton("启动 ComfyUI")
        b_start.setToolTip("用上面这个目录后台拉起 ComfyUI（干净模式，不带自定义节点）")
        b_start.clicked.connect(self.start_comfy)
        b_test = QPushButton("测试连接")
        b_test.clicked.connect(self.test_conn)
        pr.addWidget(self.comfy_path, 1)
        pr.addWidget(b_detect)
        pr.addWidget(b_start)
        pr.addWidget(b_test)
        cf.addRow("安装目录", path_row)
        self.api_row = QLineEdit(self.client.url)
        self.api_row.setToolTip("ComfyUI 的 HTTP API 地址，默认 http://127.0.0.1:8188")
        self.api_row.editingFinished.connect(self.apply_api_url)
        cf.addRow("API 地址", self.api_row)
        root.addWidget(conn)

        split = QHBoxLayout()
        left = QVBoxLayout()
        form = QGroupBox("参数")
        f = QFormLayout(form)
        self.model_kind = QComboBox()
        self.model_kind.addItems(["SDXL（checkpoints）", "Anima（diffusion_models）"])
        self.model_kind.setToolTip("切换模型家族：SDXL 走 CheckpointLoader；Anima 走 UNETLoader + Qwen 文本编码器 + Qwen VAE")
        self.model_kind.currentIndexChanged.connect(self.on_kind_changed)
        f.addRow("模型类型", self.model_kind)
        self.model = QComboBox()
        self.model.setMinimumWidth(260)
        f.addRow("底模", self.model)
        self.anima_clip = QComboBox()
        self.anima_clip.setToolTip("Anima 的文本编码器（qwen_3_06b_base.safetensors）")
        self.anima_vae = QComboBox()
        self.anima_vae.setToolTip("Anima 的 VAE（qwen_image_vae.safetensors）")
        self.anima_clip_row = self.anima_clip
        self.anima_vae_row = self.anima_vae
        f.addRow("Anima 文本编码器", self.anima_clip)
        f.addRow("Anima VAE", self.anima_vae)
        self.anima_clip.setVisible(False)
        self.anima_vae.setVisible(False)
        self.pos = TagHoverTextEdit(self.zh_for_tag)
        self.pos.setPlaceholderText("正向提示词（英文 danbooru 标签；提示词助手接手后可写中文）")
        self.pos.setPlainText("masterpiece, best quality, very aesthetic, absurdres")
        self.pos.setFixedHeight(90)
        f.addRow("正向", self.pos)
        lex_row = QWidget()
        lh = QHBoxLayout(lex_row)
        lh.setContentsMargins(0, 0, 0, 0)
        b_zh = QPushButton("中文→标签")
        b_zh.setToolTip("把提示词里的中文按主程序词库换成规范英文标签（取 danbooru 投稿数最大的那个）")
        b_zh.clicked.connect(self.fix_prompt)
        b_chk = QPushButton("词表校验")
        b_chk.setToolTip("检查词在主程序词表里能不能对上；对不上的会列出来，避免自造 tag")
        b_chk.clicked.connect(self.check_prompt)
        self.hot = QComboBox()
        self.hot.setMinimumWidth(120)
        self.hot.setToolTip("库里确认过的高频标签：插入它，让生成风格更贴近你自己的库")
        b_hot = QPushButton("插入热词")
        self.b_hot = b_hot
        b_hot.clicked.connect(self.insert_hot)
        lh.addWidget(b_zh)
        lh.addWidget(b_chk)
        lh.addWidget(self.hot, 1)
        lh.addWidget(b_hot)
        f.addRow("词库", lex_row)
        helper_row = QWidget()
        hr = QHBoxLayout(helper_row)
        hr.setContentsMargins(0, 0, 0, 0)
        self.helper_text = QLineEdit()
        self.helper_text.setPlaceholderText("用中文描述画面，例如：初音未来海边微笑，半身，冷色调")
        b_helper = QPushButton("中文→正+负标签")
        b_helper.setToolTip("调本机 Ollama，一次把中文描述转成「正向 + 负向」两组 danbooru 标签（自动用 23 万条词表校验）")
        b_helper.clicked.connect(lambda: self.run_prompt_helper(both=True))
        b_outfit = QPushButton("只翻服装")
        b_outfit.setToolTip("换装用：只把中文服装描述转成服装类标签，写进正向")
        b_outfit.clicked.connect(lambda: self.run_prompt_helper(both=False, mode="outfit"))
        hr.addWidget(self.helper_text, 1)
        hr.addWidget(b_helper)
        hr.addWidget(b_outfit)
        f.addRow("提示词助手", helper_row)
        self.neg = TagHoverTextEdit(self.zh_for_tag)
        self.neg.setPlainText(str(self.cfg.get("neg_prompt") or ""))
        self.neg.setFixedHeight(60)
        f.addRow("负向", self.neg)
        size_row = QWidget()
        sh = QHBoxLayout(size_row)
        sh.setContentsMargins(0, 0, 0, 0)
        # 尺寸按 ComfyUI/PC 侧的 limits 来：256~2048、步进 8；**不提供 512/768 档**（SDXL 低于约 0.8MP 会出色块）
        self.size_preset = QComboBox()
        self.size_preset.addItem("自定义", None)
        for label, pw, ph in (("1024×1024 方图", 1024, 1024), ("1216×832 横图", 1216, 832),
                              ("1536×648 开屏比例", 1536, 648), ("832×1216 竖图", 832, 1216),
                              ("1024×1536 竖版海报", 1024, 1536)):
            self.size_preset.addItem(label, (pw, ph))
        self.size_preset.setToolTip("直接选档位最省事；SDXL 请保持 ≥1024 边长，低于约 0.8MP 会出色块")
        self.size_preset.currentIndexChanged.connect(self.apply_size_preset)
        self.w = QSpinBox(); self.w.setRange(256, 2048); self.w.setSingleStep(8); self.w.setValue(1024)
        self.h = QSpinBox(); self.h.setRange(256, 2048); self.h.setSingleStep(8); self.h.setValue(1024)
        for _s in (self.w, self.h):        # 别让 4 位数把这一行撑太宽（横向截断的来源之一）
            _s.setMinimumWidth(84)
        self.w.valueChanged.connect(self._size_guard)
        self.h.valueChanged.connect(self._size_guard)
        self.size_warn = QLabel("")
        self.size_warn.setStyleSheet("color:#d08a2a;")
        sh.addWidget(self.size_preset); sh.addWidget(self.w); sh.addWidget(QLabel("×")); sh.addWidget(self.h)
        sh.addWidget(self.size_warn, 1)
        f.addRow("尺寸", size_row)
        p_row = QWidget()
        ph = QHBoxLayout(p_row)
        ph.setContentsMargins(0, 0, 0, 0)
        self.steps = QSpinBox(); self.steps.setRange(4, 150); self.steps.setValue(30)
        self.cfg_s = QDoubleSpinBox(); self.cfg_s.setRange(1.0, 20.0); self.cfg_s.setSingleStep(0.5)
        self.cfg_s.setValue(5.5)
        self.sampler_box = QComboBox()
        self.sampler_box.addItems(["dpmpp_2m", "euler", "euler_ancestral", "dpmpp_2m_sde", "ddim", "uni_pc"])
        self.sched_box = QComboBox()
        self.sched_box.addItems(["karras", "simple", "normal", "beta", "sgm_uniform"])
        ph.addWidget(QLabel("步数")); ph.addWidget(self.steps)
        ph.addWidget(QLabel("CFG")); ph.addWidget(self.cfg_s)
        ph.addStretch(1)
        f.addRow("采样", p_row)
        # 采样器/调度器单独一行：原来 6 个控件挤一行，这一行的最小宽度有 515px，
        # 直接把整个左栏撑到 ~640px（1280 宽的屏幕上右半边就被切掉了）。
        samp_row = QWidget()
        samp = QHBoxLayout(samp_row)
        samp.setContentsMargins(0, 0, 0, 0)
        self.sampler_box.setToolTip("采样器（dpmpp_2m 是 SDXL 二次元的稳妥选择）")
        self.sched_box.setToolTip("调度器（karras 最通用）")
        samp.addWidget(self.sampler_box, 1)
        samp.addWidget(self.sched_box, 1)
        f.addRow("采样器", samp_row)
        n_row = QWidget()
        nh = QHBoxLayout(n_row)
        nh.setContentsMargins(0, 0, 0, 0)
        self.count = QSpinBox(); self.count.setRange(1, 50); self.count.setValue(1)
        self.seed = QLineEdit("-1")
        self.seed.setToolTip("-1 = 每张随机；填固定数字则所有张同种子")
        nh.addWidget(QLabel("张数")); nh.addWidget(self.count)
        nh.addWidget(QLabel("种子")); nh.addWidget(self.seed, 1)
        f.addRow("批量", n_row)
        out_row = QWidget()
        oh = QHBoxLayout(out_row)
        oh.setContentsMargins(0, 0, 0, 0)
        self.out_dir = QLineEdit(str(self.cfg.get("output_dir") or ""))
        b_pick = QPushButton("选择…")
        b_pick.clicked.connect(self.pick_out_dir)
        oh.addWidget(self.out_dir, 1); oh.addWidget(b_pick)
        f.addRow("输出路径", out_row)
        left.addWidget(form)
        b_go = QPushButton("开始生成")
        b_go.clicked.connect(self.generate)
        left.addWidget(b_go)
        b_import = QPushButton("把生成结果入库（收录到图库）")
        b_import.clicked.connect(self.import_results)
        left.addWidget(b_import)
        left.addStretch(1)
        # 左侧「参数」表单最高能到 700+ 像素，包一层滚动区，窗口矮的时候能滚（不截断）。
        #
        # 宽度也要管：这一列原来是"一行塞四五个控件"（词库行 4 个、采样行 6 个、助手行 3 个），
        # 整列最小宽度 667px；而 split 里左右是 1:2，1280 宽的屏幕上左栏只分到 ~350px，
        # 于是右边被切掉（用户 2026-10-08 报"参数页横向显示不全"）。
        # 两步解决：①把按钮/下拉的下限压到"还看得清文字"的程度；②把左栏最小宽度锁成
        # 压缩后的内容宽度，让 split 给它留够位置；实在窄了还有横向滚动条兜底。
        for _b in form.findChildren(QPushButton):
            _b.setMinimumWidth(min(_b.minimumSizeHint().width(), 92))
        for _c in form.findChildren(QComboBox):
            if _c is not self.model:              # 底模独占一行，名字要能看全，不压
                _c.setMinimumWidth(min(_c.minimumSizeHint().width(), 132))
        self.left_host = QWidget()
        self.left_host.setLayout(left)
        self.left_scroll = QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setFrameShape(QFrame.NoFrame)
        self.left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.left_scroll.setWidget(self.left_host)
        # 记一下"内容真正需要多宽"，宽度不够时按它撑开（上限 760，再宽就靠横向滚动条）
        self._left_need = self.left_host.minimumSizeHint().width() + 14
        self.left_scroll.setMinimumWidth(min(760, self._left_need))
        split.addWidget(self.left_scroll, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("生成结果"))
        self.results = QListWidget()
        self.results.setIconSize(QPixmap().size().expandedTo(QPixmap(160, 160).size()))
        self.results.setViewMode(QListWidget.IconMode)
        self.results.setResizeMode(QListWidget.Adjust)
        self.results.setSpacing(6)
        self.results.itemDoubleClicked.connect(self.open_result)
        right.addWidget(self.results, 1)
        split.addLayout(right, 2)
        root.addLayout(split, 1)
        # 「更多」面板（预设/批量队列/重绘/参考图/姿势/模型下载）自己就 500+ 像素：
        # 收进一个限高滚动区 + 折叠开关，默认展开（和改之前一样看得见），需要时能折起来。
        self.more = MorePanel(self)
        self._MORE_TITLE = "更多功能（预设 / 批量队列 / 局部重绘 / 参考图 / 姿势线稿 / 模型下载）"
        self.more_toggle = QPushButton(self._MORE_TITLE + "  ▾")
        self.more_toggle.setCheckable(True)
        self.more_toggle.setChecked(True)
        self.more_toggle.toggled.connect(self._toggle_more)
        self.more_scroll = QScrollArea()
        self.more_scroll.setWidgetResizable(True)
        self.more_scroll.setFrameShape(QFrame.NoFrame)
        self.more_scroll.setWidget(self.more)
        self.more_scroll.setMaximumHeight(320)
        more_wrap = QWidget()
        mw = QVBoxLayout(more_wrap)
        mw.setContentsMargins(0, 0, 0, 0)
        mw.setSpacing(4)
        mw.addWidget(self.more_toggle)
        mw.addWidget(self.more_scroll)
        root.addWidget(more_wrap)
        self._saved: list[Path] = []
        self._helper_worker = None
        self._cancel_requested = False
        self._current_pid = ""          # 当前提交给 ComfyUI 的 prompt_id（定向取消用）
        # ↓ 打开窗口这一路径上**只做便宜的活**：热词只查一条 SQL，网络探测与词表读取全部丢后台。
        #   以前这里是 reload_hot_words() + refresh_status() 同步跑完才返回，
        #   服务不可达/正忙时要冻 2~8 秒（用户 2026-10-08 报的"启动生图界面可能会卡住"）。
        self._status_worker = None
        self._preload_worker = None
        self._want_status_popup = False
        self.reload_hot_words()
        self.status.setText("检测中…（后台探测 ComfyUI / Ollama）")
        QTimer.singleShot(0, self._post_open)

    def _cfg_url(self) -> str:
        """当前 ComfyUI API 地址（`comfyui_url` / `comfy_url` 两个键都认）。"""
        for k in getattr(self, "_CFG_URL_KEYS", ("comfyui_url", "comfy_url")):
            v = str(self.cfg.get(k) or "").strip()
            if v:
                return v
        return "http://127.0.0.1:8188"

    def closeEvent(self, ev) -> None:            # noqa: N802
        """关窗时等一下后台探测线程（有界，最多几秒）。

        不等的话，窗口先析构、线程还在跑 → Qt 直接 abort（0xC0000409）。
        生成任务（MorePanel 里的出图线程）**不等**——那是用户主动取消/继续的事，
        它有 `_cancel` 与自己的收尾逻辑。
        """
        ws = [getattr(self, k, None) for k in ("_status_worker", "_preload_worker",
                                               "_detect_worker", "_start_worker")]
        more = getattr(self, "more", None)
        if more is not None:
            ws += [getattr(more, "_caps_worker", None), getattr(more, "_restart_worker", None)]
        wait_workers(ws, 4000)
        super().closeEvent(ev)

    def _set_cfg_url(self, url: str) -> None:
        """写地址时**两个键一起写**，免得"窗口里改了、宿主接口读不到"（或反过来）。"""
        url = str(url or "").strip()
        if not url:
            return
        for k in getattr(self, "_CFG_URL_KEYS", ("comfyui_url", "comfy_url")):
            self.cfg[k] = url

    def _post_open(self) -> None:
        """窗口已经画出来了，再做这些不阻塞界面的后台准备。"""
        self.refresh_status()            # 内部走 _StatusProbeWorker
        self._preload("dict")            # 先把词表读热，免得点"中文→标签"时卡一下

    def _preload(self, kind: str, n: int = 0) -> None:
        w = _PreloadWorker(self, kind, n)
        if kind == "hot":
            w.done.connect(self._on_generic_hot)
        if getattr(self, "_preload_worker", None) is not None and self._preload_worker.isRunning():
            return                       # 已经有一个在跑就别叠（省内存）
        self._preload_worker = w
        keep_worker(w)
        w.start()

    def _on_generic_hot(self, kind: str, items: list) -> None:
        """通用热词兜底回来了：把"加载中…"那行换掉，补进去。"""
        if kind != "hot":
            return
        if not items:
            self._drop_loading_placeholder()
        else:
            have = {str(self.hot.itemData(i) or "") for i in range(self.hot.count())}
            self._drop_loading_placeholder()
            added = 0
            for t in items:
                if t and t not in have:
                    self.hot.addItem(t, t)
                    added += 1
            if added:
                self.hot.setToolTip(self.hot.toolTip() +
                                    f"\n（另外补了 {added} 个词表高频通用标签：不是你的库数据，纯通用）")
        # 热词这条路会顺手把 DLC 词表读热；万一没有（库里热词够多），这里补一次，
        # 免得用户第一次点「中文→标签」才现读（2~3 秒）。
        if getattr(self, "_dict_cache", None) is None:
            self._preload("dict")

    def _drop_loading_placeholder(self) -> None:
        """去掉"正在补通用热词…"这类占位行（没有 itemData 的才是占位）。"""
        for i in range(self.hot.count() - 1, -1, -1):
            txt = self.hot.itemText(i)
            if self.hot.itemData(i) in (None, "") and ("加载中" in txt or "正在补" in txt):
                self.hot.removeItem(i)

    # ---------- ComfyUI 接入 ----------
    def _wait_image(self, pid: str, out: Path, base: str, on_tick=None):
        """等图：带取消钩子（排队项被删掉后 /history 不会出现，没钩子会白等到超时）。取消返回 None。"""
        try:
            return self.client.wait(pid, out, base, on_tick=on_tick,
                                    should_stop=lambda: self._cancel_requested)
        except ComfyError as exc:
            if self._cancel_requested:
                return None
            raise

    def _fit_to_screen(self) -> None:
        """按屏幕可用区域定窗口尺寸（小屏也能整个放进屏幕里）。

        这块是宿主侧 2026-10-08 加的：窗口原本固定 1080×720，但里面「参数」表单 + 「更多」
        面板的最低高度加起来有 1200+ 像素，Qt 会把窗口顶到那个高度，1280×800 的笔记本上
        「开始生成」和「更多」会被切到屏幕外。现在：两侧各包一个滚动区 + 这里按屏幕定尺寸，
        布局逻辑一行没动，各控件引用（self.pos / self.more …）全部保持原样。
        """
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        w, h = 1080, 760
        if avail is not None and avail.width() > 0 and avail.height() > 0:
            w = min(w, max(720, avail.width() - 80))
            h = min(h, max(520, avail.height() - 80))
        self.resize(w, h)
        # 允许缩到更小（默认 minimumSizeHint 会把窗口锁死在 780×1213）
        self.setMinimumSize(680, 480)

    def _toggle_more(self, on: bool) -> None:
        """「更多功能」折叠开关：折起来只留一行标题，把空间让给生成结果。"""
        self.more_scroll.setVisible(bool(on))
        self.more_toggle.setText(self._MORE_TITLE + ("  ▾" if on else "  ▸"))

    def _api_port(self) -> int:
        try:
            return int(self.api_row.text().rsplit(":", 1)[-1].strip("/ "))
        except Exception:
            return 8188

    def _add_thumb(self, fp: Path) -> None:
        """把生成结果加进右侧缩略图列表。"""
        it = QListWidgetItem(Path(fp).name)
        pm = QPixmap(str(fp))
        if not pm.isNull():
            it.setIcon(pm.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        it.setData(Qt.UserRole, str(fp))
        self.results.addItem(it)

    def apply_api_url(self) -> None:
        url = self.api_row.text().strip() or "http://127.0.0.1:8188"
        self.client = ComfyClient(url)
        self._set_cfg_url(url)
        self.host.save_config()
        self.refresh_status()

    def auto_detect(self) -> None:
        """在本机找 ComfyUI：已运行的 API + 安装目录。

        ⚠ 走后台：`autoconnect.detect()` 会起 PowerShell 查进程命令行（最坏 20 秒超时），
        同步跑就是把界面冻 20 秒 —— 和"打开生图窗口卡住"是同一类问题。
        """
        w = getattr(self, "_detect_worker", None)
        if w is not None and w.isRunning():
            return
        self.status.setText("自动检测中…（后台查进程与常见安装目录）")
        w = _DetectWorker()
        w.done.connect(self._on_detected)
        self._detect_worker = w
        keep_worker(w)
        w.start()

    def _on_detected(self, rep: dict) -> None:
        """自动检测回来了：填控件、存配置、必要时问要不要启动。"""
        if rep.get("api"):
            self.api_row.setText(rep["api"])
            self.client = ComfyClient(rep["api"])
            self._set_cfg_url(rep["api"])
        if rep.get("comfy_dir"):
            self.comfy_path.setText(rep["comfy_dir"])
            self.cfg["comfyui_path"] = rep["comfy_dir"]
        self.host.save_config()
        found = []
        if rep.get("comfy_dir"):
            found.append("安装目录：" + rep["comfy_dir"])
        if rep.get("api"):
            found.append("API：" + rep["api"] + ("（已在运行）" if rep.get("running") else ""))
        if rep.get("python"):
            found.append("Python：" + rep["python"])
        self.status.setText("自动检测：" + ("；".join(found) if found else "没找到 ComfyUI，请手动指定安装目录"))
        self.refresh_status()
        if not rep.get("api") and rep.get("comfy_dir"):
            if QMessageBox.question(self, "ComfyUI", "检测到安装目录但服务没在运行，现在启动它吗？") == QMessageBox.Yes:
                self.start_comfy()

    def start_comfy(self) -> None:
        path = self.comfy_path.text().strip() or str(self.cfg.get("comfyui_path") or "")
        if not path:
            QMessageBox.information(self, "ComfyUI", "先点「自动检测」或手动填 ComfyUI 安装目录。")
            return
        port = 8188
        try:
            port = int(self.api_row.text().rsplit(":", 1)[-1].strip("/ "))
        except Exception:
            pass
        # 后台启动：start_comfy() 最长等 2 分钟，同步跑会把界面冻住（见 _StartComfyWorker）
        w = getattr(self, "_start_worker", None)
        if w is not None and w.isRunning():
            return
        self.status.setText(f"正在后台启动 ComfyUI（{path}）…最长等 2 分钟，界面可以继续用")
        w = _StartComfyWorker(path, port)
        w.done.connect(self._on_comfy_started)
        self._start_worker = w
        keep_worker(w)
        w.start()

    def _on_comfy_started(self, ok: bool, msg: str) -> None:
        self.status.setText(msg)
        if ok:
            self.cfg["comfyui_path"] = self.comfy_path.text().strip() or str(self.cfg.get("comfyui_path") or "")
            self.host.save_config()
        self.refresh_status()

    def test_conn(self) -> None:
        self.apply_api_url()
        ok, info = self.client.ping()
        QMessageBox.information(self, "ComfyUI 连接", ("✅ 连接正常：" if ok else "❌ ") + info)

    # ---------- 模型家族切换 ----------
    def apply_size_preset(self) -> None:
        data = self.size_preset.currentData()
        if not data:
            return
        w, h = data
        self.w.setValue(int(w))
        self.h.setValue(int(h))
        self._size_guard()

    def _size_guard(self) -> None:
        """SDXL 低于约 0.8MP 会出色块（PC 侧踩过），这里给个黄字提醒；Anima 阈值低一些。"""
        mp = self.w.value() * self.h.value() / 1_000_000
        is_anima = self.model_kind.currentIndex() == 1
        floor = 0.5 if is_anima else 0.85
        self.size_warn.setText("" if mp >= floor else f"⚠️ 约 {mp:.2f}MP 偏小（{'Anima' if is_anima else 'SDXL'} 建议 ≥{floor}MP），容易出色块")

    def on_kind_changed(self) -> None:
        is_anima = self.model_kind.currentIndex() == 1
        self.anima_clip.setVisible(is_anima)
        self.anima_vae.setVisible(is_anima)
        # 套用实测过的预设参数
        if is_anima:
            self.w.setValue(768); self.h.setValue(768)
            self.steps.setValue(30); self.cfg_s.setValue(4.5)
            self.sampler_box.setCurrentText("euler"); self.sched_box.setCurrentText("simple")
        else:
            self.w.setValue(1536); self.h.setValue(648)
            self.steps.setValue(60); self.cfg_s.setValue(5.5)
            self.sampler_box.setCurrentText("dpmpp_2m"); self.sched_box.setCurrentText("karras")
        self._size_guard()
        self.reload_models()

    def _pick_default(self, combo: QComboBox, keyword: str) -> None:
        for i in range(combo.count()):
            if keyword in combo.itemText(i).lower():
                combo.setCurrentIndex(i)
                return

    def _build_workflow(self, seed: int) -> dict:
        """按当前"模型类型"出对应的工作流（SDXL checkpoint / Anima）。"""
        # 选了「工作流」下拉里的自定义模板就优先用它（占位符按界面当前参数填）
        try:
            custom = self.more.build_custom_workflow(seed)
        except Exception as exc:                             # noqa: BLE001
            QMessageBox.warning(self, "自定义工作流", f"模板填充失败：{exc}\n已改回内置工作流。")
            self.more.workflow_box.setCurrentIndex(0)
            custom = None
        if custom:
            return custom
        pos = self.pos.toPlainText().strip()
        neg = self.neg.toPlainText().strip()
        common = dict(positive=pos, negative=neg, width=self.w.value(), height=self.h.value(),
                      steps=self.steps.value(), cfg=self.cfg_s.value(), seed=seed)
        if self.model_kind.currentIndex() == 1:
            return ComfyClient.anima_workflow(
                self.model.currentText(), self.anima_clip.currentText(), self.anima_vae.currentText(),
                sampler=self.sampler_box.currentText(), scheduler=self.sched_box.currentText(), **common)
        return ComfyClient.workflow(
            self.model.currentText(), sampler=self.sampler_box.currentText(),
            scheduler=self.sched_box.currentText(), **common)

    # ---------- 词库联动（用主程序那份词表：host.lexicon()） ----------
    def _lex(self):
        try:
            return self.host.lexicon()
        except Exception:
            return None

    # ---------- 悬浮气泡：英文 tag → 中文 ----------
    def _dict(self):
        """DLC 自带词表（含项目词典的中文列），并按需合并主程序的 tag→中文 大表。"""
        if getattr(self, "_dict_cache", None) is None:
            try:
                from .prompt_helper import load_dictionary
            except ImportError:
                import sys
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from prompt_helper import load_dictionary
            d = load_dictionary()
            lex = self._lex()
            if lex is not None:
                d.attach_lexicon(lex)
            try:
                zh_file = Path(self.host.project_root()) / "app" / "tag_zh_dict.json"
                self._zh_map_added = d.load_zh_map(zh_file)
                alias_file = Path(self.host.project_root()) / "app" / "tag_zh_aliases.json"
                self._zh_alias_added = d.load_zh_aliases(alias_file)
            except Exception as exc:                      # noqa: BLE001
                self._zh_map_added = 0
                self._zh_map_error = f"{type(exc).__name__}: {exc}"
            self._dict_cache = d
        return self._dict_cache

    def zh_for_tag(self, tag: str):
        """给悬浮气泡用：英文 tag → 中文；查不到返回 None（就只显示英文）。"""
        if not tag:
            return None
        d = self._dict()
        zh = d.zh_for(tag)
        if zh:
            return zh
        lex = self._lex()
        if lex is not None:
            try:
                info = lex.meta(tag)
                if info.get("zh"):
                    return info["zh"]
            except Exception:
                pass
        return None

    def reload_hot_words(self) -> None:
        """热词 = 主程序库里确认过的标签（按图片数排序）——让生成风格贴近用户自己的库。

        注意两个容易误会的地方（2026-10-08 用户报"词库选项仅有全年龄"）：
        · 只统计 `file_tags.status='confirmed'` —— 待审(pending)的不算，否则会把错的标签喂进提示词；
        · 测试版(E:)和正式版(D:)各用各的库。E: 那个库被清空过一次，confirmed 只剩 1 行，
          于是下拉里真的只有一项。列表为空时这里会放一行说明，别让它看起来像坏了。
        """
        self.hot.clear()
        lex = self._lex()
        tags: list[str] = []
        if lex is not None:
            try:
                tags = [str(t) for t in (lex.hot_tags(40) or []) if str(t).strip()]
            except Exception:
                tags = []
        where = ""
        try:                       # 把"当前读的是哪个库"写进提示，免得测试版/正式版互相误会
            from app.config import data_dir
            where = f"\n当前图库数据目录：{data_dir()}"
        except Exception:
            where = ""
        if tags:
            for t in tags:
                self.hot.addItem(t, t)
            self.hot.setToolTip(f"库里「已确认」的标签（按图片数排序，最多 40 个，当前 {len(tags)} 个）；"
                                "插入它能让生成风格贴近你自己的库" + where)
        # 库里已确认的太少（比如测试库只有 1 条）时，补几条**词表里的高频通用标签**，
        # 让下拉不至于是空的；这些是"词表补充"而不是你库里的数据，插进去最多是通用好看，不会带偏。
        # 说明：只用词表统计（不碰 pending），所以不会把没审核的错标签喂进提示词。
        if len(tags) < 8:
            # 注意：这里**不能**同步算（要读 8MB 词表，首次 2~3 秒）——放后台，回来再补。
            self.hot.setToolTip(
                f"你库里「已确认」的标签只有 {len(tags)} 个，正在补词表高频通用标签…" + where)
            self.hot.addItem("（正在补通用热词…）", "")
            QTimer.singleShot(0, lambda: self._preload("hot", 12 - len(tags)))
        if self.hot.count() == 0:
            self.hot.addItem("（库里还没有已确认的标签，词表也没读到）", "")
            self.hot.setToolTip("热词只统计图库里「已确认」的标签（待审的不算）。"
                                "先去审核台通过一些标签就会出现；"
                                "另外测试版和正式版各用各的图库，标签不互通。" + where)
        if getattr(self, "b_hot", None) is not None:
            self.b_hot.setEnabled(self.hot.count() > 0)

    def _generic_hot_tags(self, n: int = 12) -> list[str]:
        """兜底用的"通用好看"标签：**人工白名单 + 按热度排序**。

        为什么不用"词表里 count 最高的 12 个"：那会混进 meta 词（commentary request）、
        e621 遗留（mammal / anthro）和露骨词（breasts），塞进提示词只会添乱。
        """
        if n <= 0:
            return []
        cache = getattr(self, "_generic_cache", None)
        if cache is None:
            candidates = [
                # 构图 / 镜头
                "upper body", "full body", "portrait", "cowboy shot", "from side", "from above",
                "looking at viewer", "looking away", "front view",
                # 表情
                "smile", "open mouth", "closed eyes", "blush", "grin",
                # 头发
                "long hair", "twintails", "ponytail", "short hair", "hair between eyes",
                # 服装
                "school uniform", "dress", "skirt", "thighhighs", "jacket", "shirt", "hat",
                # 姿势 / 动作
                "standing", "sitting", "holding hands", "holding book", "arms up", "hand on hip",
                # 背景 / 氛围
                "simple background", "white background", "outdoors", "night", "day", "sky",
                "cherry blossoms", "city", "forest", "beach", "indoor",
                # 光线 / 质感
                "detailed background", "soft lighting", "depth of field", "backlighting",
            ]
            try:
                d = self._dict()
                pairs = []
                for tag in candidates:
                    key = tag.replace(" ", "_")
                    if d.resolve(tag) or key in d.by_name:      # 词表里真有这个 tag 才用
                        pairs.append((d.counts.get(key, 0), tag))
                pairs.sort(reverse=True)
                cache = [t for _, t in pairs]
            except Exception:
                cache = []
            self._generic_cache = cache
        return cache[:n]
        if getattr(self, "b_hot", None) is not None:
            self.b_hot.setEnabled(bool(tags))

    def insert_hot(self) -> None:
        # 用 itemData：占位行（"（库里还没有已确认的标签）"）没有 data，点了也不会插进去
        tag = str(self.hot.currentData() or "").strip()
        if not tag:
            return
        cur = self.pos.toPlainText().strip().rstrip(",")
        self.pos.setPlainText((cur + ", " if cur else "") + tag)

    def fix_prompt(self) -> None:
        """把提示词里的中文按主程序词库换成规范英文标签（异常安全：没词库就提示）。"""
        lex = self._lex()
        if lex is None:
            QMessageBox.warning(self, "AI 生图", "主程序没提供词库服务（host.lexicon()）。")
            return
        fixed, unknown = lex.prompt_fix(self.pos.toPlainText())
        self.pos.setPlainText(fixed)
        self.status.setText("已按词库规范化提示词" +
                            (f"；{len(unknown)} 个词没对上：{', '.join(unknown[:6])}" if unknown else ""))

    def check_prompt(self) -> None:
        lex = self._lex()
        if lex is None:
            return
        _fixed, unknown = lex.prompt_fix(self.pos.toPlainText(), keep_unknown=True, keep_ascii=False)
        if unknown:
            QMessageBox.information(self, "词表校验",
                                    "这些词不在词表里（换掉，或先在主程序里建好标签）：\n\n"
                                    + "、".join(unknown[:30]))
        else:
            QMessageBox.information(self, "词表校验", "提示词里的词都能在词表里对上。")

    # ---------- 提示词助手（中文 → danbooru 标签） ----------
    def run_prompt_helper(self, both: bool = True, mode: str = "prompt") -> None:
        text = self.helper_text.text().strip()
        if not text:
            QMessageBox.information(self, "提示词助手", "先在「提示词助手」那一行写一句中文描述。")
            return
        # 留空 = 让 prompt_helper 自动挑**本机已有**的最合适模型（本机一个都没有时才会提示去下）
        model = str(self.cfg.get("ollama_model") or "")
        self.status.setText("提示词助手：正在生成正向+负向（本机 Ollama）…" if both
                            else "提示词助手：正在生成…")
        worker = _PromptHelperWorker(text, mode, model, both=both, lexicon=self._lex(),
                                     model_filter=(self.more.words_scope.currentData() or ""))
        self._helper_worker = worker
        worker.ok.connect(self._on_helper_ok)
        worker.err.connect(self._on_helper_err)
        worker.start()

    def _on_helper_ok(self, data: dict) -> None:
        if data.get("both"):
            pos = (data.get("positive") or "").strip()
            neg = (data.get("negative") or "").strip()
            self.pos.setPlainText(pos)
            if neg:
                self.neg.setPlainText(neg)
            n_pos = len([t for t in pos.split(",") if t.strip()])
            n_neg = len([t for t in neg.split(",") if t.strip()])
            inv = list(data.get("positive_invented") or [])
            used = data.get("model") or ""
            msg = f"提示词助手（本机 {used}）：正向 {n_pos} 个、负向 {n_neg} 个标签已填入"
            if inv:
                msg += f"；{len(inv)} 个不在词表（已保留）：{', '.join(inv)}"
            self.status.setText(msg)
            self.helper_text.setToolTip(", ".join(inv) or "全部命中词表")
            return
        tags = (data.get("result") or "").strip()
        invented = (data.get("invented") or "").strip()
        mode = data.get("mode", "prompt")
        target = self.neg if mode == "negative" else self.pos
        if mode == "negative":
            target.setPlainText(tags)
        else:
            cur = target.toPlainText().strip().rstrip(",")
            target.setPlainText(f"{cur}, {tags}" if cur else tags)
        n = len([t for t in tags.split(",") if t.strip()])
        msg = f"提示词助手（本机 {data.get('model') or ''}）：写入 {n} 个标签"
        if invented:
            msg += f"；{len(invented.split(','))} 个不在词表（已保留，建议手动替换）：{invented}"
        self.status.setText(msg)
        self.helper_text.setToolTip(invented or "全部命中词表")

    def _on_helper_err(self, message: str) -> None:
        self.status.setText("提示词助手失败")
        hint = ""
        if "11434" in message or "Ollama" in message:
            hint = "\n\n本机 Ollama 没在运行：双击 D:\\LocalAI\\start-ollama.cmd 启动即可。"
        QMessageBox.warning(self, "提示词助手", message + hint)

    # ---------- 小工具 ----------
    def refresh_status(self) -> None:
        """**异步**探 ComfyUI / Ollama：界面线程一秒都不等（见 _StatusProbeWorker 的说明）。

        同一时刻只允许一个探测在跑；已经在跑就忽略这次（用户连点"检查 ComfyUI"也不会叠线程）。
        """
        w = getattr(self, "_status_worker", None)
        if w is not None and w.isRunning():
            return
        self.status.setText("检测中…（后台探测 ComfyUI / Ollama）")
        ollama_url = str(self.cfg.get("ollama_url") or "")
        w = _StatusProbeWorker(self.client.url, ollama_url)
        w.done.connect(self._on_status)
        self._status_worker = w
        keep_worker(w)
        w.start()

    def _on_status(self, ok: bool, text: str) -> None:
        self.status.setText(text)
        if getattr(self, "_want_status_popup", False):
            self._want_status_popup = False
            if ok:
                QMessageBox.information(self, "ComfyUI", "连接正常。\n\n" + text)
            else:
                QMessageBox.warning(self, "ComfyUI", text +
                                    "\n\n先启动 ComfyUI（例如运行 tools\\start_comfyui.cmd），"
                                    "或在上面「安装目录」旁点「启动 ComfyUI」。")

    def check_comfy(self) -> None:
        """按钮：探一次，结果回来再弹窗（原来是同步探完立刻读 text()，会冻界面）。"""
        self._want_status_popup = True
        self.refresh_status()

    def reload_models(self) -> None:
        is_anima = self.model_kind.currentIndex() == 1
        try:
            names = self.client.unets() if is_anima else self.client.checkpoints()
            if is_anima:
                clips = self.client.clips()
                vaes = self.client.vaes()
        except ComfyError as exc:
            QMessageBox.warning(self, "模型列表", str(exc))
            return
        cur = self.model.currentText() or str(self.cfg.get("default_model") or "")
        self.model.clear()
        self.model.addItems(names)
        if cur and cur in names:
            self.model.setCurrentText(cur)
        elif is_anima:
            self._pick_default(self.model, "anima")
        elif self.cfg.get("default_model") in names:
            self.model.setCurrentText(str(self.cfg["default_model"]))
        if is_anima:
            self.anima_clip.clear(); self.anima_clip.addItems(clips)
            self.anima_vae.clear(); self.anima_vae.addItems(vaes)
            self._pick_default(self.anima_clip, "qwen_3_06b")
            self._pick_default(self.anima_vae, "qwen_image_vae")
            self.status.setText(f"Anima 类模型 {len(names)} 个、文本编码器 {len(clips)} 个、VAE {len(vaes)} 个")
        else:
            self.status.setText(f"共 {len(names)} 个底模（checkpoints）")

    def pick_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择生图输出路径", self.out_dir.text() or "")
        if d:
            self.out_dir.setText(d)
            self.cfg["output_dir"] = d
            self.host.save_config()

    def open_result(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---------- 生成 ----------
    def generate(self) -> None:
        if self._busy:
            QMessageBox.information(self, "AI 生图", "上一批还在跑，等它结束。")
            return
        if not self.model.currentText():
            QMessageBox.warning(self, "AI 生图", "先「拉取模型列表」并选一个底模。")
            return
        out = Path(self.out_dir.text().strip() or (self.host.project_root() / "outputs"))
        self.cfg["output_dir"] = str(out)
        self.cfg["comfy_url"] = self.client.url
        self.host.save_config()
        seed_txt = self.seed.text().strip()
        self._busy = True
        self._cancel_requested = False
        self.more.b_cancel.setEnabled(True)
        self.status.setText("提交中…")

        def one(i: int) -> None:
            seed = int(seed_txt) if seed_txt not in ("", "-1") else random.randint(1, 2 ** 31 - 1)
            wf = self._build_workflow(seed)
            pid = self.client.submit(wf)
            self._current_pid = pid
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = f"gen_{stamp}_{seed}"
            files = self._wait_image(pid, out, base,
                                     on_tick=lambda s: self.status.setText(
                                         f"第 {i+1}/{self.count.value()} 张生成中… {s:.0f}s"))
            if self._cancel_requested:
                self._busy = False
                self.more.b_cancel.setEnabled(False)
                self.status.setText("已取消（未出图）")
                return
            for fp in (files or []):
                self._add_thumb(fp)
                self._saved.append(fp)
                self.more._after_image(fp)
            self.status.setText(f"第 {i+1}/{self.count.value()} 张完成 → {out}")
            QTimer.singleShot(10, lambda: self._next(i + 1))

        self._tot = self.count.value()
        one(0)

    def _next(self, i: int) -> None:
        if self._cancel_requested:
            self._busy = False
            self.more.b_cancel.setEnabled(False)
            self.status.setText(f"已取消（已出 {len(self._saved)} 张）")
            return
        if i >= self._tot:
            self._busy = False
            self.more.b_cancel.setEnabled(False)
            self.status.setText(f"全部完成，共 {len(self._saved)} 张 → {self.out_dir.text()}")
            return
        try:
            self._run_one(i)
        except Exception as exc:
            self._busy = False
            QMessageBox.warning(self, "AI 生图", f"生成失败：{exc}")

    def _run_one(self, i: int) -> None:
        seed_txt = self.seed.text().strip()
        seed = int(seed_txt) if seed_txt not in ("", "-1") else random.randint(1, 2 ** 31 - 1)
        wf = self._build_workflow(seed)
        pid = self.client.submit(wf)
        self._current_pid = pid
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        files = self._wait_image(pid, Path(self.out_dir.text()), f"gen_{stamp}_{seed}",
                                 on_tick=lambda s: self.status.setText(
                                     f"第 {i+1}/{self._tot} 张生成中… {s:.0f}s"))
        if self._cancel_requested:
            self._busy = False
            self.more.b_cancel.setEnabled(False)
            self.status.setText("已取消（未出图）")
            return
        for fp in (files or []):
            self._add_thumb(fp)
            self._saved.append(fp)
            self.more._after_image(fp)
        self.status.setText(f"第 {i+1}/{self._tot} 张完成")
        QTimer.singleShot(10, lambda: self._next(i + 1))

    def import_results(self) -> None:
        """把当前列出的生成结果收进图库（复用主程序 library.import_to_library）。"""
        if not self._saved:
            QMessageBox.information(self, "AI 生图", "还没有生成结果。")
            return
        store = self.host.store
        lib = self.host.library
        if store is None or lib is None:
            QMessageBox.warning(self, "AI 生图", "宿主没提供图库对象，无法入库。")
            return
        ids = []
        for fp in self._saved:
            row = store.one("SELECT id FROM files WHERE path=?", (str(fp),))
            if row is None:
                continue
            ids.append(int(row["id"]))
        if not ids:
            QMessageBox.information(self, "AI 生图",
                                    "生成结果还没进库（先在主程序里扫描这个输出目录），然后再点这里。")
            return
        res = lib.import_to_library(ids, move=True)
        QMessageBox.information(self, "AI 生图", f"已收录 {res.get('moved', 0)} 张到图库。")
