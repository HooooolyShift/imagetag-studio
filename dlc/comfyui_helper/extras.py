"""「更多功能」面板：预设 / 批量队列 / 自动入库+打标 / 局部重绘换装 / 参考图 / 姿势 / 模型下载。

设计原则（用户 2026-10-08 要求）：
  · 依赖 ComfyUI 组件（自定义节点或模型文件）的功能，**先探测**：能连上就能用；
    缺什么就明确提示缺哪一项、怎么补（并给"用完整模式重启 ComfyUI"的按钮）。
  · 自动入库 / 自动打标都是**可选项**（勾选框），默认关闭。
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from .comfy import ComfyClient, ComfyError


class _Worker(QThread):
    """把耗时动作（出图、下载、上传）扔到后台线程。"""

    done = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self) -> None:                       # noqa: D102
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:                 # noqa: BLE001
            self.fail.emit(str(exc))


class MorePanel(QGroupBox):
    """挂在生图窗口里的「更多功能」面板。"""

    def __init__(self, win):
        super().__init__("更多功能（依赖 ComfyUI 组件的功能会自动检测）")
        self.win = win
        self.cfg = win.cfg
        self.jobs: list[dict] = []
        self._queue_running = False
        self._cancel = False
        self._worker = None
        self._current_is_queue = False
        lay = QVBoxLayout(self)

        # ---------- 能力状态 ----------
        cap_row = QWidget()
        ch = QHBoxLayout(cap_row)
        ch.setContentsMargins(0, 0, 0, 0)
        self.cap_label = QLabel("未检测")
        self.cap_label.setWordWrap(True)
        b_cap = QPushButton("检测可用功能")
        b_cap.clicked.connect(self.refresh_caps)
        b_full = QPushButton("用完整模式重启 ComfyUI")
        b_full.setToolTip("当前 ComfyUI 若是「干净模式」启动的，自定义节点（IP-Adapter / ControlNet 预处理）会被禁用；\n点这里会用完整模式重启它（需要确认）")
        b_full.clicked.connect(self.restart_full)
        ch.addWidget(b_cap)
        ch.addWidget(b_full)
        lay.addWidget(cap_row)
        lay.addWidget(self.cap_label)

        form = QFormLayout()
        lay.addLayout(form)

        # ---------- 预设 ----------
        preset_row = QWidget()
        ph = QHBoxLayout(preset_row)
        ph.setContentsMargins(0, 0, 0, 0)
        self.preset_box = QComboBox()
        self.preset_box.addItems(["sdxl_splash_1536x648", "sdxl_duo_antibloom", "anima_default"])
        b_preset = QPushButton("套用预设")
        b_preset.clicked.connect(self.apply_preset)
        ph.addWidget(self.preset_box, 1)
        ph.addWidget(b_preset)
        form.addRow("预设", preset_row)

        # ---------- 自动入库 / 自动打标（可选项） ----------
        auto_row = QWidget()
        ah = QHBoxLayout(auto_row)
        ah.setContentsMargins(0, 0, 0, 0)
        self.auto_import = QCheckBox("生成后自动入库")
        self.auto_import.setChecked(bool(self.cfg.get("auto_import")))
        self.auto_tag = QCheckBox("入库后自动打标（WD14+CLIP+分级）")
        self.auto_tag.setChecked(bool(self.cfg.get("auto_tag")))
        self.auto_tag.setToolTip("打标会占用算力，需要主程序有模型引擎（hub）；失败会提示但不会中断出图")
        for box, key in ((self.auto_import, "auto_import"), (self.auto_tag, "auto_tag")):
            box.stateChanged.connect(lambda _s, b=box, k=key: self._save_flag(b, k))
            ah.addWidget(box)
        form.addRow("自动处理", auto_row)

        # ---------- 词表范围（问"目标模型认不认这个词"） ----------
        scope_row = QWidget()
        sh = QHBoxLayout(scope_row)
        sh.setContentsMargins(0, 0, 0, 0)
        self.words_scope = QComboBox()
        self.words_scope.addItem("全部词表（不限定模型）", "")
        for name in ("NoobAIXL1.1_underscore", "illustriousV1.0_underscore", "anima-1.0", "booru_zh"):
            self.words_scope.addItem(name, name)
        self.words_scope.setToolTip("选一个模型就只认它训练时见过的 tag——例如选 NoobAI 时，Anima 独有的 tag 会被判为“词表外”")
        sh.addWidget(QLabel("提示词词表范围")); sh.addWidget(self.words_scope, 1)
        form.addRow("", scope_row)

        # ---------- 队列 ----------
        q_row = QWidget()
        qh = QHBoxLayout(q_row)
        qh.setContentsMargins(0, 0, 0, 0)
        self.q_count = QSpinBox(); self.q_count.setRange(1, 200); self.q_count.setValue(4)
        self.q_seed = QLineEdit("")
        self.q_seed.setPlaceholderText("起始种子（留空=随机）")
        self.q_skip = QCheckBox("跳过已存在（断点续跑）")
        self.q_skip.setChecked(True)
        qh.addWidget(QLabel("张数")); qh.addWidget(self.q_count)
        qh.addWidget(self.q_seed, 1); qh.addWidget(self.q_skip)
        form.addRow("批量队列", q_row)
        q_row2 = QWidget()
        qh2 = QHBoxLayout(q_row2)
        qh2.setContentsMargins(0, 0, 0, 0)
        b_add = QPushButton("加入队列")
        b_add.clicked.connect(self.enqueue)
        self.b_run = QPushButton("开始队列")
        self.b_run.clicked.connect(self.run_queue)
        self.b_cancel = QPushButton("取消")
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self.cancel_queue)
        self.b_clear = QPushButton("清空")
        self.b_clear.clicked.connect(self.clear_queue)
        for b in (b_add, self.b_run, self.b_cancel, self.b_clear):
            qh2.addWidget(b)
        form.addRow("", q_row2)
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(90)
        lay.addWidget(self.queue_list)

        # ---------- 局部重绘 / 换装 ----------
        ip_row = QWidget()
        ih = QHBoxLayout(ip_row)
        ih.setContentsMargins(0, 0, 0, 0)
        self.inpaint_src = QLineEdit()
        self.inpaint_src.setPlaceholderText("要改的图（留空=用最近生成的那张）")
        b_src = QPushButton("选择…"); b_src.clicked.connect(lambda: self._pick(self.inpaint_src))
        ih.addWidget(self.inpaint_src, 1); ih.addWidget(b_src)
        form.addRow("重绘源图", ip_row)
        ip_row2 = QWidget()
        ih2 = QHBoxLayout(ip_row2)
        ih2.setContentsMargins(0, 0, 0, 0)
        self.inpaint_region = QComboBox()
        self.inpaint_region.addItems(["整图", "上半身", "下半身", "中间区域"])
        self.inpaint_region.setCurrentText("上半身")
        self.inpaint_denoise = QDoubleSpinBox(); self.inpaint_denoise.setRange(0.1, 1.0)
        self.inpaint_denoise.setSingleStep(0.05); self.inpaint_denoise.setValue(0.65)
        b_inpaint = QPushButton("执行重绘/换装")
        b_inpaint.clicked.connect(self.do_inpaint)
        ih2.addWidget(QLabel("区域")); ih2.addWidget(self.inpaint_region)
        ih2.addWidget(QLabel("重绘强度")); ih2.addWidget(self.inpaint_denoise)
        ih2.addWidget(b_inpaint); ih2.addStretch(1)
        form.addRow("局部重绘", ip_row2)

        # ---------- 参考图（IP-Adapter） ----------
        ref_row = QWidget()
        rh = QHBoxLayout(ref_row)
        rh.setContentsMargins(0, 0, 0, 0)
        self.ref_image = QLineEdit()
        self.ref_weight = QDoubleSpinBox(); self.ref_weight.setRange(0.1, 1.5)
        self.ref_weight.setSingleStep(0.1); self.ref_weight.setValue(0.8)
        b_ref = QPushButton("选择…"); b_ref.clicked.connect(lambda: self._pick(self.ref_image))
        b_ref_go = QPushButton("用参考图生成")
        b_ref_go.clicked.connect(self.do_reference)
        rh.addWidget(self.ref_image, 1); rh.addWidget(QLabel("权重")); rh.addWidget(self.ref_weight)
        rh.addWidget(b_ref); rh.addWidget(b_ref_go)
        form.addRow("参考图", ref_row)

        # ---------- 姿势（ControlNet） ----------
        pose_row = QWidget()
        ph2 = QHBoxLayout(pose_row)
        ph2.setContentsMargins(0, 0, 0, 0)
        self.pose_image = QLineEdit()
        self.pose_strength = QDoubleSpinBox(); self.pose_strength.setRange(0.1, 1.5)
        self.pose_strength.setSingleStep(0.1); self.pose_strength.setValue(0.8)
        self.pose_cn = QComboBox()
        b_pose = QPushButton("选择…"); b_pose.clicked.connect(lambda: self._pick(self.pose_image))
        b_pose_go = QPushButton("用姿势生成")
        b_pose_go.clicked.connect(self.do_pose)
        ph2.addWidget(self.pose_image, 1); ph2.addWidget(QLabel("强度")); ph2.addWidget(self.pose_strength)
        ph2.addWidget(self.pose_cn); ph2.addWidget(b_pose); ph2.addWidget(b_pose_go)
        form.addRow("姿势/线稿", pose_row)

        # ---------- 参考图用哪套 IP-Adapter / 图像编码器（自动挑配对的那套） ----------
        ipm_row = QWidget()
        imh = QHBoxLayout(ipm_row)
        imh.setContentsMargins(0, 0, 0, 0)
        self.ip_model = QComboBox()
        self.ip_clip = QComboBox()
        self.ip_model.setToolTip("SDXL 建议用 ip-adapter-plus_sdxl_vit-h.safetensors")
        self.ip_clip.setToolTip("要和 IP-Adapter 配套：ViT-H 版编码器（CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors）")
        imh.addWidget(QLabel("IP-Adapter 模型")); imh.addWidget(self.ip_model, 1)
        imh.addWidget(QLabel("图像编码器")); imh.addWidget(self.ip_clip, 1)
        form.addRow("", ipm_row)

        # ---------- 模型下载 ----------
        dl_row = QWidget()
        dh = QHBoxLayout(dl_row)
        dh.setContentsMargins(0, 0, 0, 0)
        self.dl_model = QComboBox()
        b_dl = QPushButton("下载 / 校验")
        b_dl.clicked.connect(self.download_model)
        dh.addWidget(self.dl_model, 1); dh.addWidget(b_dl)
        form.addRow("模型下载", dl_row)

        # ---------- 放大 ----------
        up_row = QWidget()
        uh = QHBoxLayout(up_row)
        uh.setContentsMargins(0, 0, 0, 0)
        self.upscale_src = QLineEdit()
        self.upscale_src.setPlaceholderText("要放大的图（留空=用最近生成的那张）")
        b_up_pick = QPushButton("选择…")
        b_up_pick.clicked.connect(lambda: self._pick(self.upscale_src))
        self.upscale_mode = QComboBox()
        self.upscale_mode.addItem("干净超分（ESRGAN，快、无彩噪）", "esrgan")
        self.upscale_mode.addItem("潜空间放大（可能出彩边，谨慎）", "hires")
        self.upscale_scale = QDoubleSpinBox(); self.upscale_scale.setRange(1.2, 4.0)
        self.upscale_scale.setSingleStep(0.1); self.upscale_scale.setValue(1.5)
        self.upscale_model_box = QComboBox()
        b_up = QPushButton("执行放大")
        b_up.clicked.connect(self.do_upscale)
        uh.addWidget(self.upscale_src, 1); uh.addWidget(b_up_pick)
        uh.addWidget(self.upscale_mode); uh.addWidget(QLabel("倍率")); uh.addWidget(self.upscale_scale)
        uh.addWidget(self.upscale_model_box); uh.addWidget(b_up)
        form.addRow("放大", up_row)

        # ---------- 图生图 / 融合 ----------
        i2_row = QWidget()
        i2h = QHBoxLayout(i2_row)
        i2h.setContentsMargins(0, 0, 0, 0)
        self.i2i_src = QLineEdit()
        self.i2i_src.setPlaceholderText("源图（留空=用最近生成的那张）")
        b_i2_src = QPushButton("选择…")
        b_i2_src.clicked.connect(lambda: self._pick(self.i2i_src))
        self.i2i_blend = QLineEdit()
        self.i2i_blend.setPlaceholderText("（可选）再融合一张图")
        b_i2_blend = QPushButton("选择…")
        b_i2_blend.clicked.connect(lambda: self._pick(self.i2i_blend))
        self.i2i_factor = QDoubleSpinBox(); self.i2i_factor.setRange(0.0, 1.0)
        self.i2i_factor.setSingleStep(0.05); self.i2i_factor.setValue(0.5)
        self.i2i_denoise = QDoubleSpinBox(); self.i2i_denoise.setRange(0.1, 1.0)
        self.i2i_denoise.setSingleStep(0.05); self.i2i_denoise.setValue(0.6)
        b_i2 = QPushButton("执行")
        b_i2.clicked.connect(self.do_img2img)
        i2h.addWidget(self.i2i_src, 1); i2h.addWidget(b_i2_src)
        i2h.addWidget(self.i2i_blend, 1); i2h.addWidget(b_i2_blend)
        i2h.addWidget(QLabel("融合")); i2h.addWidget(self.i2i_factor)
        i2h.addWidget(QLabel("重绘")); i2h.addWidget(self.i2i_denoise)
        i2h.addWidget(b_i2)
        form.addRow("图生图/融合", i2_row)

        self._load_models_json()
        QTimer.singleShot(800, self.refresh_caps)

    # ---------- 基础 ----------
    def _save_flag(self, box, key: str) -> None:
        self.cfg[key] = bool(box.isChecked())
        self.win.host.save_config()

    def _pick(self, line: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if path:
            line.setText(path)

    def _last_image(self) -> str:
        if self.win._saved:
            return str(self.win._saved[-1])
        return ""

    def _load_models_json(self) -> None:
        try:
            data = json.loads((Path(__file__).resolve().parent / "models.json").read_text(encoding="utf-8"))
            self._models = data.get("models", [])
        except Exception:
            self._models = []
        for m in self._models:
            size = m.get("size_bytes", 0) / (1024 ** 3)
            star = "★ " if m.get("recommended") else ""
            self.dl_model.addItem(f"{star}{m.get('name', m.get('id'))}（{size:.1f} GB）", m.get("id"))

    def current_arch(self) -> str:
        """按底模名字判断架构（SDXL 名字里基本都有 xl）；用来给 IP-Adapter / ControlNet 配对的模型。"""
        name = (self.win.model.currentText() or "").lower()
        return "sdxl" if ("xl" in name or not name) else "sd15"

    def _pick_paired(self) -> None:
        """按当前底模架构挑配对的 IP-Adapter / 图像编码器 / ControlNet（不硬编码文件名）。"""
        arch = self.current_arch()
        # IP-Adapter：SDXL → plus_sdxl / vit-h；SD1.5 → sd15
        def ip_score(t: str) -> int:
            t = t.lower()
            if arch == "sdxl":
                s = 3 if "plus_sdxl" in t else (2 if "vit-h" in t else (1 if "sdxl" in t else -1))
            else:
                s = 0 if "faceid" in t else (3 if "sd15_plus" in t else (2 if "ip-adapter_sd15" in t else -1))
            return s
        best = max(range(self.ip_model.count()), key=lambda i: ip_score(self.ip_model.itemText(i)), default=-1)
        if best >= 0 and ip_score(self.ip_model.itemText(best)) > 0:
            self.ip_model.setCurrentIndex(best)
        for i in range(self.ip_clip.count()):
            if "vit-h" in self.ip_clip.itemText(i).lower():
                self.ip_clip.setCurrentIndex(i)
                break
        # ControlNet：SDXL → sdxl/union；SD1.5 → sd15
        def cn_score(t: str) -> int:
            t = t.lower()
            if any(bad in t for bad in ("ip2p", "shuffle", "tile", "inpaint")):
                return -1
            want_arch = ("sdxl" in t or "union" in t) if arch == "sdxl" else ("sd15" in t and "sdxl" not in t)
            if not want_arch:
                return -1
            if "openpose" in t or "union" in t:
                return 3
            if any(k in t for k in ("depth", "canny", "lineart", "softedge", "normalbae")):
                return 2
            return 1
        best_cn = max(range(self.pose_cn.count()), key=lambda i: cn_score(self.pose_cn.itemText(i)), default=-1)
        if best_cn >= 0 and cn_score(self.pose_cn.itemText(best_cn)) > 0:
            self.pose_cn.setCurrentIndex(best_cn)

    # ---------- 能力探测 ----------
    def refresh_caps(self) -> None:
        try:
            rep = self.win.client.capability_report()
        except Exception as exc:                             # noqa: BLE001
            self.cap_label.setText(f"检测失败（ComfyUI 没连上？）：{exc}")
            return
        self._caps = rep
        parts = []
        for name, info in rep.items():
            parts.append(("✅ " if info["ok"] else "⚠️ ") + name + ("" if info["ok"] else f"（缺：{', '.join(info['missing'][:2])}）"))
        self.cap_label.setText("　".join(parts))
        # 有 ControlNet 模型时就填进下拉
        self.pose_cn.clear()
        try:
            for name in self.win.client._enum("ControlNetLoader", "control_net_name"):
                self.pose_cn.addItem(name)
            for i in range(self.pose_cn.count()):
                if "union" in self.pose_cn.itemText(i).lower() or "sdxl" in self.pose_cn.itemText(i).lower():
                    self.pose_cn.setCurrentIndex(i)
                    break
        except Exception:
            pass
        self._pick_paired()
        # 超分模型
        try:
            self.upscale_model_box.clear()
            for name in self.win.client.upscale_models():
                self.upscale_model_box.addItem(name)
            for i in range(self.upscale_model_box.count()):
                if "anime" in self.upscale_model_box.itemText(i).lower():
                    self.upscale_model_box.setCurrentIndex(i)
                    break
        except Exception:
            pass
        # IP-Adapter 两件套：优先挑配对（vit-h / plus_sdxl）
        try:
            self.ip_model.clear()
            for name in self.win.client._enum("IPAdapterModelLoader", "ipadapter_file"):
                self.ip_model.addItem(name)
            self.ip_clip.clear()
            for name in self.win.client._enum("CLIPVisionLoader", "clip_name"):
                self.ip_clip.addItem(name)
            for i in range(self.ip_model.count()):
                t = self.ip_model.itemText(i).lower()
                if "plus_sdxl" in t or "vit-h" in t:
                    self.ip_model.setCurrentIndex(i)
                    break
            for i in range(self.ip_clip.count()):
                if "vit-h" in self.ip_clip.itemText(i).lower():
                    self.ip_clip.setCurrentIndex(i)
                    break
        except Exception:
            pass

    def restart_full(self) -> None:
        path = self.win.comfy_path.text().strip() or str(self.cfg.get("comfyui_path") or "")
        if not path:
            QMessageBox.information(self, "ComfyUI", "先用「自动检测」或手动填 ComfyUI 安装目录。")
            return
        msg = ("会用**完整模式**重启 ComfyUI（不加 --disable-all-custom-nodes），\n"
               "这样 IP-Adapter、ControlNet 预处理等自定义节点才可用。\n\n"
               "重启会中断正在跑的出图任务。继续吗？")
        if QMessageBox.question(self, "重启 ComfyUI", msg) != QMessageBox.Yes:
            return
        from . import autoconnect
        port = self.win._api_port()
        self.cap_label.setText("正在以完整模式重启 ComfyUI…")
        QApplication_process = None
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass
        ok, text = autoconnect.restart_comfy_full(path, port=port)
        QMessageBox.information(self, "ComfyUI", text)
        self.refresh_caps()
        self.win.refresh_status()

    def _require(self, feature: str) -> bool:
        """能连上就用；缺东西就给明确提示。"""
        rep = getattr(self, "_caps", None) or {}
        info = rep.get(feature)
        if info is None:
            self.refresh_caps()
            info = (getattr(self, "_caps", {}) or {}).get(feature, {"ok": False, "missing": ["未知"], "hint": ""})
        if info.get("ok"):
            return True
        missing = "\n".join("· " + m for m in info.get("missing", []))
        hint = info.get("hint", "")
        QMessageBox.warning(self, feature, f"这个功能现在用不了，ComfyUI 里缺：\n\n{missing}\n\n"
                                           f"{hint}\n\n"
                                           "如果是自定义节点缺失，多半是 ComfyUI 以「干净模式」启动的——"
                                           "点上面的「用完整模式重启 ComfyUI」即可。")
        return False

    # ---------- 预设 ----------
    def apply_preset(self) -> None:
        name = self.preset_box.currentText()
        try:
            data = json.loads((Path(__file__).resolve().parent / "workflows" / "presets.json").read_text(encoding="utf-8"))
            p = data.get(name, {})
        except Exception as exc:                             # noqa: BLE001
            QMessageBox.warning(self, "预设", f"读 presets.json 失败：{exc}")
            return
        if not p:
            return
        if name.startswith("anima"):
            self.win.model_kind.setCurrentIndex(1)
        else:
            self.win.model_kind.setCurrentIndex(0)
        if p.get("width"):
            self.win.w.setValue(int(p["width"]))
        if p.get("height"):
            self.win.h.setValue(int(p["height"]))
        if p.get("steps"):
            self.win.steps.setValue(int(p["steps"]))
        if p.get("cfg"):
            self.win.cfg_s.setValue(float(p["cfg"]))
        if p.get("sampler"):
            self.win.sampler_box.setCurrentText(str(p["sampler"]))
        if p.get("scheduler"):
            self.win.sched_box.setCurrentText(str(p["scheduler"]))
        if p.get("negative"):
            self.win.neg.setPlainText(str(p["negative"]))
        self.win.status.setText(f"已套用预设 {name}")

    # ---------- 队列 ----------
    def enqueue(self) -> None:
        if not self.win.model.currentText():
            QMessageBox.warning(self, "队列", "先拉取模型列表并选一个底模。")
            return
        start = self.q_seed.text().strip()
        base = int(start) if start.isdigit() else random.randint(1, 2 ** 31 - 1)
        for i in range(self.q_count.value()):
            seed = base + i
            self.jobs.append({"seed": seed})
            self.queue_list.addItem(QListWidgetItem(f"seed {seed}"))
        self.win.status.setText(f"队列已有 {len(self.jobs)} 个任务")

    def clear_queue(self) -> None:
        self.jobs.clear()
        self.queue_list.clear()

    def cancel_queue(self) -> None:
        """取消当前任务（队列里的或单张/放大/图生图/重绘都算）——
        语义与宿主 /api/gen/interrupt 对齐：立刻转发 ComfyUI /interrupt；
        **还没提交的队列任务不再提交**；不把"用户主动取消"当成失败弹框。"""
        self._cancel = True
        try:
            self.win._cancel_requested = True
        except Exception:
            pass
        try:
            self.win.client.interrupt()
        except Exception:
            pass
        self.win.status.setText("已取消（当前任务停下，未提交的队列任务不再提交）")

    def run_queue(self) -> None:
        if self._queue_running:
            return
        if not self.jobs:
            QMessageBox.information(self, "队列", "队列是空的，先「加入队列」。")
            return
        self._queue_running = True
        self._cancel = False
        try:
            self.win._cancel_requested = False
        except Exception:
            pass
        self.b_run.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self._run_next()

    def _run_next(self) -> None:
        while self.jobs:
            job = self.jobs[0]
            out = Path(self.win.out_dir.text().strip() or (self.win.host.project_root() / "outputs"))
            dst = out / f"gen_s{job['seed']}.png"
            if self.q_skip.isChecked() and dst.exists() and dst.stat().st_size > 0:
                self.jobs.pop(0)
                self.queue_list.takeItem(0)
                self.win.status.setText(f"seed {job['seed']} 已存在，跳过")
                continue
            break
        if not self.jobs or self._cancel:
            self._finish_queue()
            return
        job = self.jobs[0]
        self.win.status.setText(f"队列：seed {job['seed']} 生成中…")
        wf = self.win._build_workflow(job["seed"])
        self._current_is_queue = True
        self._worker = _Worker(self._run_one_job, wf, job["seed"])
        self._worker.is_queue = True
        self._worker.done.connect(lambda p, w=self._worker: self._job_done(p, w))
        self._worker.fail.connect(lambda m, w=self._worker: self._job_fail(m, w))
        self._worker.start()

    def _run_one_job(self, wf: dict, seed: int) -> Path:
        out = Path(self.win.out_dir.text().strip() or (self.win.host.project_root() / "outputs"))
        client = self.win.client
        pid = client.submit(wf)
        files = client.wait(pid, out, f"gen_s{seed}", timeout=1800)
        if self._cancel or getattr(self.win, "_cancel_requested", False):
            return None                      # 用户取消：没出图是正常的，别当失败
        return files[0] if files else out / f"gen_s{seed}.png"

    def _job_done(self, path, worker=None) -> None:
        cancelled = self._cancel or getattr(self.win, "_cancel_requested", False)
        if cancelled and not path:
            self.win.status.setText("已取消（未出图）")
            if bool(getattr(worker, "is_queue", False)):
                self._cancel = False
                self._finish_queue()
            return
        if path:
            self.win._saved.append(Path(path))
            self.win._add_thumb(Path(path))
            self._after_image(Path(path))
        if bool(getattr(worker, "is_queue", False)):
            # 只有队列任务才动队列；放大/图生图/重绘这些"临时任务"共用同一个完成回调
            if self.jobs:
                self.jobs.pop(0)
            if self.queue_list.count():
                self.queue_list.takeItem(0)
            if self._cancel:
                self._finish_queue()
                return
            QTimer.singleShot(50, self._run_next)
        else:
            self.win.status.setText(f"完成 → {Path(path).name}" if path else "完成（没有产物）")

    def _job_fail(self, message: str, worker=None) -> None:
        cancelled = self._cancel or getattr(self.win, "_cancel_requested", False)
        if cancelled:
            # 用户主动取消：ComfyUI 会抛中断异常，按"已取消"处理，不弹失败框
            self.win.status.setText("已取消")
            if bool(getattr(worker, "is_queue", False)):
                self._cancel = False
                self._finish_queue()
            return
        if bool(getattr(worker, "is_queue", False)):
            self._finish_queue()
        QMessageBox.warning(self, "队列", f"这一张失败：{message}")

    def _finish_queue(self) -> None:
        self._queue_running = False
        self.b_run.setEnabled(True)
        self.b_cancel.setEnabled(False)
        if self._cancel:
            self.win.status.setText(f"已取消：已出 {len(self.win._saved)} 张，队列剩余 {len(self.jobs)} 个"
                                    "（再点「开始队列」可继续，已出过的会自动跳过）")
        else:
            self.win.status.setText(f"队列结束，剩余 {len(self.jobs)} 个任务")

    def _after_image(self, path: Path) -> None:
        """生成后：可选入库、可选打标（失败只提示，不打断）。"""
        if not self.auto_import.isChecked():
            return
        try:
            ids = self.win.host.scan_into_library([path])
        except Exception as exc:                             # noqa: BLE001
            self.win.status.setText(f"自动入库失败：{exc}")
            return
        if not ids:
            self.win.status.setText("自动入库：主程序没找到对应图库根目录（先在主程序里把输出目录设为来源/图库）")
            return
        self.win.status.setText(f"已自动入库 {len(ids)} 张")
        if self.auto_tag.isChecked():
            try:
                self.win.host.tag_files(ids)
                self.win.status.setText(f"已自动打标 {len(ids)} 张（进待审队列）")
            except Exception as exc:                         # noqa: BLE001
                self.win.status.setText(f"自动打标失败（不影响出图）：{exc}")

    # ---------- 重绘 / 参考图 / 姿势 ----------
    def _make_mask(self, src: Path, region: str, out: Path) -> Path:
        from PIL import Image, ImageDraw
        im = Image.open(src).convert("RGB")
        w, h = im.size
        m = Image.new("RGB", (w, h), (0, 0, 0))
        d = ImageDraw.Draw(m)
        if region == "整图":
            d.rectangle([0, 0, w, h], fill=(255, 255, 255))
        elif region == "上半身":
            d.rectangle([int(w * 0.05), int(h * 0.15), int(w * 0.95), int(h * 0.62)], fill=(255, 255, 255))
        elif region == "下半身":
            d.rectangle([int(w * 0.05), int(h * 0.50), int(w * 0.95), h], fill=(255, 255, 255))
        else:
            d.rectangle([int(w * 0.12), int(h * 0.18), int(w * 0.88), int(h * 0.85)], fill=(255, 255, 255))
        m.save(out)
        return out

    def do_inpaint(self) -> None:
        if not self._require("局部重绘 / 换装"):
            return
        src = self.inpaint_src.text().strip() or self._last_image()
        if not src or not Path(src).exists():
            QMessageBox.information(self, "局部重绘", "先选一张要改的图（或先生成一张）。")
            return
        ckpt = self.win.model.currentText() if self.win.model_kind.currentIndex() == 0 else ""
        if not ckpt:
            QMessageBox.information(self, "局部重绘", "重绘目前用 SDXL 底模，请把「模型类型」切回 SDXL 并选一个底模。")
            return
        out_dir = Path(self.win.out_dir.text().strip() or (self.win.host.project_root() / "outputs"))
        out_dir.mkdir(parents=True, exist_ok=True)
        mask = self._make_mask(Path(src), self.inpaint_region.currentText(), out_dir / "_imtag_mask.png")
        seed = random.randint(1, 2 ** 31 - 1)
        self.win.status.setText("局部重绘：上传图片与遮罩…")

        def job():
            c = self.win.client
            n1 = c.upload_image(src)
            n2 = c.upload_image(mask)
            wf = ComfyClient.inpaint_workflow(
                ckpt, n1, n2, self.win.pos.toPlainText().strip(), self.win.neg.toPlainText().strip(),
                self.win.w.value(), self.win.h.value(), self.win.steps.value(), self.win.cfg_s.value(),
                seed, denoise=self.inpaint_denoise.value(),
                sampler=self.win.sampler_box.currentText(), scheduler=self.win.sched_box.currentText())
            pid = c.submit(wf)
            files = c.wait(pid, out_dir, f"inpaint_s{seed}", timeout=1800)
            if self._cancel or getattr(self.win, "_cancel_requested", False):
                return None
            return files[0] if files else None

        self._worker = _Worker(job)
        self._worker.is_queue = False
        self._worker.done.connect(lambda p, w=self._worker: self._job_done(p, w))
        self._worker.fail.connect(lambda m, w=self._worker: self._job_fail(m, w))
        self._worker.start()

    # ---------- 放大 / 图生图 ----------
    def do_upscale(self) -> None:
        src = self.upscale_src.text().strip() or self._last_image()
        if not src or not Path(src).exists():
            QMessageBox.information(self, "放大", "先选一张要放大的图（或先生成一张）。")
            return
        mode = self.upscale_mode.currentData()
        ckpt = self.win.model.currentText()
        if mode == "hires" and not ckpt:
            QMessageBox.information(self, "放大", "潜空间放大要用当前 SDXL 底模，请先在「底模」里选一个。")
            return
        out_dir = Path(self.win.out_dir.text().strip() or (self.win.host.project_root() / "outputs"))
        out_dir.mkdir(parents=True, exist_ok=True)
        model = self.upscale_model_box.currentText()
        scale = self.upscale_scale.value()
        pos, neg = self.win.pos.toPlainText().strip(), self.win.neg.toPlainText().strip()
        steps, cfg = self.win.steps.value(), self.win.cfg_s.value()
        self.win.status.setText(f"放大中（{'干净超分' if mode == 'esrgan' else '潜空间放大'}）…")

        def job():
            c = self.win.client
            name = c.upload_image(src)
            if mode == "esrgan":
                if not model:
                    raise RuntimeError("没有可用的超分模型（models/upscale_models 为空）")
                wf = ComfyClient.upscale_workflow(name, model)
                prefix = f"upscale_{int(time.time())}"
            else:
                wf = ComfyClient.hires_workflow(ckpt, name, pos, neg, scale=scale,
                                                denoise=0.3, seed=random.randint(1, 2**31 - 1),
                                                steps=max(12, steps // 3), cfg=cfg)
                prefix = f"hires_{int(time.time())}"
            pid = c.submit(wf)
            files = c.wait(pid, out_dir, prefix, timeout=1800)
            if self._cancel or getattr(self.win, "_cancel_requested", False):
                return None
            return files[0] if files else None

        self._worker = _Worker(job)
        self._worker.is_queue = False
        self._worker.done.connect(lambda p, w=self._worker: self._job_done(p, w))
        self._worker.fail.connect(lambda m, w=self._worker: self._job_fail(m, w))
        self._worker.start()

    def do_img2img(self) -> None:
        src = self.i2i_src.text().strip() or self._last_image()
        if not src or not Path(src).exists():
            QMessageBox.information(self, "图生图", "先选源图（或先生成一张）。")
            return
        if self.win.model_kind.currentIndex() != 0 or not self.win.model.currentText():
            QMessageBox.information(self, "图生图", "图生图目前用 SDXL 底模，请把「模型类型」切到 SDXL 并选底模。")
            return
        out_dir = Path(self.win.out_dir.text().strip() or (self.win.host.project_root() / "outputs"))
        out_dir.mkdir(parents=True, exist_ok=True)
        ckpt = self.win.model.currentText()
        pos, neg = self.win.pos.toPlainText().strip(), self.win.neg.toPlainText().strip()
        blend = self.i2i_blend.text().strip()
        factor, denoise = self.i2i_factor.value(), self.i2i_denoise.value()
        steps, cfg = self.win.steps.value(), self.win.cfg_s.value()
        seed = random.randint(1, 2 ** 31 - 1)
        self.win.status.setText("图生图：上传图片…")

        def job():
            c = self.win.client
            name = c.upload_image(src)
            blend_name = c.upload_image(blend) if blend and Path(blend).exists() else ""
            wf = ComfyClient.img2img_workflow(ckpt, name, pos, neg, steps, cfg, seed,
                                              denoise=denoise, blend_name=blend_name,
                                              blend_factor=factor)
            pid = c.submit(wf)
            files = c.wait(pid, out_dir, f"i2i_s{seed}", timeout=1800)
            if self._cancel or getattr(self.win, "_cancel_requested", False):
                return None
            return files[0] if files else None

        self._worker = _Worker(job)
        self._worker.is_queue = False
        self._worker.done.connect(lambda p, w=self._worker: self._job_done(p, w))
        self._worker.fail.connect(lambda m, w=self._worker: self._job_fail(m, w))
        self._worker.start()

    def do_reference(self) -> None:
        if not self._require("参考图（IP-Adapter）"):
            return
        ref = self.ref_image.text().strip()
        if not ref or not Path(ref).exists():
            QMessageBox.information(self, "参考图", "先选一张参考图。")
            return
        self._run_special("参考图", "imtag_ref", ComfyClient.ipadapter_workflow, ref=ref)

    def do_pose(self) -> None:
        if not self._require("姿势/线稿（ControlNet）"):
            return
        pose = self.pose_image.text().strip()
        if not pose or not Path(pose).exists():
            QMessageBox.information(self, "姿势", "先选一张姿势/线稿图。")
            return
        cn = self.pose_cn.currentText()
        if not cn:
            QMessageBox.information(self, "姿势", "没有可用的 ControlNet 模型。")
            return
        self._run_special("姿势", "imtag_pose", ComfyClient.controlnet_workflow, ref=pose, cn=cn)

    def _run_special(self, title: str, prefix: str, builder, ref: str, cn: str = "") -> None:
        ckpt = self.win.model.currentText()
        if self.win.model_kind.currentIndex() != 0:
            QMessageBox.information(self, title, "这个功能目前用 SDXL 底模，请把「模型类型」切回 SDXL。")
            return
        out_dir = Path(self.win.out_dir.text().strip() or (self.win.host.project_root() / "outputs"))
        out_dir.mkdir(parents=True, exist_ok=True)
        seed = random.randint(1, 2 ** 31 - 1)
        self.win.status.setText(f"{title}：上传参考图…")
        pos = self.win.pos.toPlainText().strip()
        neg = self.win.neg.toPlainText().strip()
        w, h = self.win.w.value(), self.w.h.value()
        steps, cfg = self.win.steps.value(), self.win.cfg_s.value()
        sampler, sched = self.win.sampler_box.currentText(), self.win.sched_box.currentText()
        weight = self.ref_weight.value() if title == "参考图" else self.pose_strength.value()

        def job():
            c = self.win.client
            name = c.upload_image(ref)
            if title == "参考图":
                wf = builder(ckpt, name, pos, neg, w, h, steps, cfg, seed, weight=weight,
                             ipadapter_file=self.ip_model.currentText(),
                             clip_vision=self.ip_clip.currentText(),
                             sampler=sampler, scheduler=sched, prefix=prefix)
            else:
                wf = builder(ckpt, name, cn, pos, neg, w, h, steps, cfg, seed, strength=weight,
                             sampler=sampler, scheduler=sched, prefix=prefix)
            pid = c.submit(wf)
            files = c.wait(pid, out_dir, f"{prefix}_s{seed}", timeout=1800)
            if self._cancel or getattr(self.win, "_cancel_requested", False):
                return None
            return files[0] if files else None

        self._worker = _Worker(job)
        self._worker.is_queue = False
        self._worker.done.connect(lambda p, w=self._worker: self._job_done(p, w))
        self._worker.fail.connect(lambda m, w=self._worker: self._job_fail(m, w))
        self._worker.start()

    # ---------- 模型下载 ----------
    def download_model(self) -> None:
        idx = self.dl_model.currentIndex()
        if idx < 0 or not self._models:
            return
        m = self._models[idx]
        url = m.get("download_url")
        if not url:
            QMessageBox.information(self, "模型下载",
                                    f"{m.get('name')} 没有直链（需要手动到 Civitai/HF 下载）。\n"
                                    f"放到 ComfyUI 的 {m.get('target')} 目录即可。")
            return
        comfy_dir = self.win.comfy_path.text().strip() or str(self.cfg.get("comfyui_path") or "")
        if not comfy_dir:
            QMessageBox.information(self, "模型下载", "先设置 ComfyUI 安装目录。")
            return
        dst = Path(comfy_dir) / m.get("target", "models/checkpoints") / m.get("file", "model.safetensors")
        if dst.exists() and dst.stat().st_size > 0:
            size = dst.stat().st_size / (1024 ** 3)
            if QMessageBox.question(self, "模型下载", f"{dst.name} 已存在（{size:.2f} GB）。重新下载吗？") != QMessageBox.Yes:
                return
        script = Path(__file__).resolve().parent / "scripts" / "fetch_model.py"
        self.win.status.setText(f"开始下载 {m.get('file')} …（后台，可在状态栏看进度）")

        def job():
            import subprocess
            import sys
            out = subprocess.run([sys.executable, str(script), url, str(dst)],
                                 capture_output=True, text=True, timeout=7200)
            return (out.returncode, (out.stdout or "")[-800:], (out.stderr or "")[-400:])

        self._worker = _Worker(job)

        def done(res):
            code, out, err = res
            if code == 0:
                self.win.status.setText(f"下载完成：{dst.name}")
                self.win.reload_models()
            else:
                self.win.status.setText(f"下载失败（退出码 {code}）：{err or out}")

        self._worker.done.connect(done)
        self._worker.fail.connect(lambda msg: self.win.status.setText(f"下载出错：{msg}"))
        self._worker.start()
