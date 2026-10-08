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
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QDoubleSpinBox, QTextEdit, QToolTip,
                               QVBoxLayout, QWidget)

import re

from .comfy import ComfyClient, ComfyError


class _PromptHelperWorker(QThread):
    """后台跑"中文 → danbooru 标签"，别卡住界面（本机 Ollama 一次几秒到几十秒）。
    both=True 时一次给出正向 + 负向两组（默认，不用手动切模式）。"""

    ok = Signal(dict)
    err = Signal(str)

    def __init__(self, text: str, mode: str, model: str, both: bool = True, lexicon=None):
        super().__init__()
        self.text = text
        self.mode = mode
        self.model = model
        self.both = both
        self.lexicon = lexicon

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
        self.client = ComfyClient(self.cfg.get("comfy_url") or "http://127.0.0.1:8188")
        self.setWindowTitle("AI 生图（ComfyUI 助手）")
        self.resize(1080, 720)
        self._busy = False

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#8f96a3;")
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
        self.hot.setMinimumWidth(150)
        self.hot.setToolTip("库里确认过的高频标签：插入它，让生成风格更贴近你自己的库")
        b_hot = QPushButton("插入热词")
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
        self.w = QSpinBox(); self.w.setRange(256, 4096); self.w.setSingleStep(64); self.w.setValue(1024)
        self.h = QSpinBox(); self.h.setRange(256, 4096); self.h.setSingleStep(64); self.h.setValue(1024)
        sh.addWidget(self.w); sh.addWidget(QLabel("×")); sh.addWidget(self.h)
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
        ph.addWidget(self.sampler_box); ph.addWidget(self.sched_box)
        f.addRow("采样", p_row)
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
        split.addLayout(left, 1)

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
        self._saved: list[Path] = []
        self._helper_worker = None
        self.reload_hot_words()
        self.refresh_status()

    # ---------- ComfyUI 接入 ----------
    def apply_api_url(self) -> None:
        url = self.api_row.text().strip() or "http://127.0.0.1:8188"
        self.client = ComfyClient(url)
        self.cfg["comfy_url"] = url
        self.host.save_config()
        self.refresh_status()

    def auto_detect(self) -> None:
        """在本机找 ComfyUI：已运行的 API + 安装目录。"""
        try:
            from . import autoconnect
        except ImportError:
            import autoconnect
        rep = autoconnect.detect()
        if rep.get("api"):
            self.api_row.setText(rep["api"])
            self.client = ComfyClient(rep["api"])
            self.cfg["comfy_url"] = rep["api"]
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
        try:
            from . import autoconnect
        except ImportError:
            import autoconnect
        port = 8188
        try:
            port = int(self.api_row.text().rsplit(":", 1)[-1].strip("/ "))
        except Exception:
            pass
        self.status.setText(f"正在后台启动 ComfyUI（{path}）…")
        QApplication.processEvents()
        ok, msg = autoconnect.start_comfy(path, port=port)
        self.status.setText(msg)
        if ok:
            self.cfg["comfyui_path"] = path
            self.host.save_config()
        self.refresh_status()

    def test_conn(self) -> None:
        self.apply_api_url()
        ok, info = self.client.ping()
        QMessageBox.information(self, "ComfyUI 连接", ("✅ 连接正常：" if ok else "❌ ") + info)

    # ---------- 模型家族切换 ----------
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
        self.reload_models()

    def _pick_default(self, combo: QComboBox, keyword: str) -> None:
        for i in range(combo.count()):
            if keyword in combo.itemText(i).lower():
                combo.setCurrentIndex(i)
                return

    def _build_workflow(self, seed: int) -> dict:
        """按当前"模型类型"出对应的工作流（SDXL checkpoint / Anima）。"""
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
        """热词 = 主程序库里确认过的标签（按图片数排序）——让生成风格贴近用户自己的库。"""
        self.hot.clear()
        lex = self._lex()
        if lex is None:
            return
        try:
            self.hot.addItems(lex.hot_tags(40))
        except Exception:
            pass

    def insert_hot(self) -> None:
        tag = self.hot.currentText().strip()
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
        worker = _PromptHelperWorker(text, mode, model, both=both, lexicon=self._lex())
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
        ok, info = self.client.ping()
        text = ("✅ " if ok else "❌ ") + info
        # 顺带报一下"中文→标签"用的本机模型（优先用本机已有的，不会自己下新的）
        try:
            from .prompt_helper import ollama as _ollama
        except ImportError:
            try:
                from prompt_helper import ollama as _ollama
            except ImportError:
                _ollama = None
        if _ollama is not None:
            try:
                if _ollama.is_up():
                    names = _ollama.list_models()
                    text += f"｜提示词模型：{_ollama.pick_best_model(names)}"
                else:
                    text += "｜提示词模型：未启动 Ollama"
            except Exception:
                pass
        self.status.setText(text)

    def check_comfy(self) -> None:
        self.refresh_status()
        if self.status.text().startswith("❌"):
            QMessageBox.warning(self, "ComfyUI", self.status.text() +
                                "\n\n先启动 ComfyUI（例如运行 tools\\start_comfyui.cmd）。")

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
        self.status.setText("提交中…")

        def one(i: int) -> None:
            seed = int(seed_txt) if seed_txt not in ("", "-1") else random.randint(1, 2 ** 31 - 1)
            wf = self._build_workflow(seed)
            pid = self.client.submit(wf)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = f"gen_{stamp}_{seed}"
            files = self.client.wait(pid, out, base,
                                     on_tick=lambda s: self.status.setText(
                                         f"第 {i+1}/{self.count.value()} 张生成中… {s:.0f}s"))
            for fp in files:
                it = QListWidgetItem(fp.name)
                pm = QPixmap(str(fp))
                if not pm.isNull():
                    it.setIcon(pm.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                it.setData(Qt.UserRole, str(fp))
                self.results.addItem(it)
                self._saved.append(fp)
            self.status.setText(f"第 {i+1}/{self.count.value()} 张完成 → {out}")
            QTimer.singleShot(10, lambda: self._next(i + 1))

        self._tot = self.count.value()
        one(0)

    def _next(self, i: int) -> None:
        if i >= self._tot:
            self._busy = False
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
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        files = self.client.wait(pid, Path(self.out_dir.text()), f"gen_{stamp}_{seed}",
                                 on_tick=lambda s: self.status.setText(
                                     f"第 {i+1}/{self._tot} 张生成中… {s:.0f}s"))
        for fp in files:
            it = QListWidgetItem(fp.name)
            pm = QPixmap(str(fp))
            if not pm.isNull():
                it.setIcon(pm.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            it.setData(Qt.UserRole, str(fp))
            self.results.addItem(it)
            self._saved.append(fp)
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
