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

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QDoubleSpinBox, QTextEdit, QVBoxLayout,
                               QWidget)

from .comfy import ComfyClient, ComfyError


class _PromptHelperWorker(QThread):
    """后台跑"中文 → danbooru 标签"，别卡住界面（本机 Ollama 一次几秒到几十秒）。"""

    ok = Signal(dict)
    err = Signal(str)

    def __init__(self, text: str, mode: str, model: str):
        super().__init__()
        self.text = text
        self.mode = mode
        self.model = model

    def run(self) -> None:                       # noqa: D102
        try:
            try:
                from .prompt_helper import generate
            except ImportError:                  # 被当成普通脚本直接跑时
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from prompt_helper import generate
            r = generate(self.text, self.mode, model=self.model)
            self.ok.emit(r.to_dict())
        except Exception as exc:                 # noqa: BLE001
            self.err.emit(str(exc))


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

        split = QHBoxLayout()
        left = QVBoxLayout()
        form = QGroupBox("参数")
        f = QFormLayout(form)
        self.model = QComboBox()
        self.model.setMinimumWidth(260)
        f.addRow("底模", self.model)
        self.pos = QTextEdit()
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
        self.helper_mode = QComboBox()
        self.helper_mode.addItems(["正向", "负向", "服装（换装）"])
        b_helper = QPushButton("中文→标签")
        b_helper.setToolTip("调本机 Ollama，把中文描述转成 danbooru 标签（会自动用 23 万条词表校验）")
        b_helper.clicked.connect(self.run_prompt_helper)
        hr.addWidget(self.helper_text, 1)
        hr.addWidget(self.helper_mode)
        hr.addWidget(b_helper)
        f.addRow("提示词助手", helper_row)
        self.neg = QTextEdit()
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
        ph.addWidget(QLabel("步数")); ph.addWidget(self.steps)
        ph.addWidget(QLabel("CFG")); ph.addWidget(self.cfg_s)
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

    # ---------- 词库联动（用主程序那份词表：host.lexicon()） ----------
    def _lex(self):
        try:
            return self.host.lexicon()
        except Exception:
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
    def run_prompt_helper(self) -> None:
        text = self.helper_text.text().strip()
        if not text:
            QMessageBox.information(self, "提示词助手", "先在「提示词助手」那一行写一句中文描述。")
            return
        mode = {"正向": "prompt", "负向": "negative", "服装（换装）": "outfit"}.get(
            self.helper_mode.currentText(), "prompt")
        model = str(self.cfg.get("ollama_model") or "qwen3-8b:latest")
        self.status.setText("提示词助手：正在生成（本机 Ollama）…")
        worker = _PromptHelperWorker(text, mode, model)
        self._helper_worker = worker
        worker.ok.connect(self._on_helper_ok)
        worker.err.connect(self._on_helper_err)
        worker.start()

    def _on_helper_ok(self, data: dict) -> None:
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
        msg = f"提示词助手：写入 {n} 个标签"
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
        self.status.setText(("✅ " if ok else "❌ ") + info)

    def check_comfy(self) -> None:
        self.refresh_status()
        if self.status.text().startswith("❌"):
            QMessageBox.warning(self, "ComfyUI", self.status.text() +
                                "\n\n先启动 ComfyUI（例如运行 tools\\start_comfyui.cmd）。")

    def reload_models(self) -> None:
        try:
            names = self.client.checkpoints()
        except ComfyError as exc:
            QMessageBox.warning(self, "模型列表", str(exc))
            return
        cur = self.model.currentText() or str(self.cfg.get("default_model") or "")
        self.model.clear()
        self.model.addItems(names)
        if cur and cur in names:
            self.model.setCurrentText(cur)
        self.status.setText(f"共 {len(names)} 个底模")

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
            wf = ComfyClient.workflow(self.model.currentText(), self.pos.toPlainText().strip(),
                                      self.neg.toPlainText().strip(), self.w.value(), self.h.value(),
                                      self.steps.value(), self.cfg_s.value(), seed)
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
        wf = ComfyClient.workflow(self.model.currentText(), self.pos.toPlainText().strip(),
                                  self.neg.toPlainText().strip(), self.w.value(), self.h.value(),
                                  self.steps.value(), self.cfg_s.value(), seed)
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
