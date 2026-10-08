"""图片标签工坊 离线安装程序（图形界面，纯本地，无网络请求）。

流程：检测硬件 → 显示推荐挡位（可改）→ 选择安装路径 → 复制程序与模型 →
解压内置 Python → 用本地 wheels 离线安装依赖 → 写配置/快捷方式/卸载器。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent          # 安装包所在目录（含 payload）
PAYLOAD = ROOT / "payload"
APP_NAME = "图片标签工坊"


def human(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n > 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except Exception:
            pass
    return total


class InstallWorker(QThread):
    log = Signal(str)
    progress = Signal(int, str)
    done = Signal(bool, str)

    def __init__(self, target: Path, mode: str, make_shortcut: bool, keep_models_in_dir: bool,
                 install_dlc: bool = True):
        super().__init__()
        self.target = target
        self.mode = mode
        self.make_shortcut = make_shortcut
        self.models_in_dir = keep_models_in_dir
        self.install_dlc = install_dlc

    def run(self) -> None:
        try:
            self._run()
            self.done.emit(True, str(self.target))
        except Exception as e:  # noqa: BLE001
            import traceback
            self.log.emit(traceback.format_exc())
            self.done.emit(False, str(e))

    def _run(self) -> None:
        t = self.target
        t.mkdir(parents=True, exist_ok=True)
        # 1) 程序文件
        self.progress.emit(2, "复制程序文件…")
        for name in ("app", "tools", "assets"):
            src, dst = PAYLOAD / name, t / name
            if src.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                self.log.emit(f"复制 {name}/ 完成")
        for name in ("README.md", "requirements.txt", "run.cmd"):
            if (PAYLOAD / name).exists():
                shutil.copy2(PAYLOAD / name, t / name)
        # 1b) 可选扩展包（DLC）：勾了才复制；不勾就跳过（已装过的会被保留，不动它）
        if self.install_dlc and (PAYLOAD / "dlc").is_dir():
            shutil.copytree(PAYLOAD / "dlc", t / "dlc", dirs_exist_ok=True)
            self.log.emit("已安装可选扩展包（DLC）：AI 生图助手（在程序里「更多 ▾ → 扩展包（DLC）…」启用）")
        elif (PAYLOAD / "dlc").is_dir():
            self.log.emit("按你的选择跳过扩展包（DLC）；以后可用更新包或手动放入 dlc\\ 目录再启用")
        for pdf in PAYLOAD.glob("*.pdf"):           # 说明书
            shutil.copy2(pdf, t / pdf.name)
        for exe in PAYLOAD.glob("*.exe"):          # 启动器 exe
            shutil.copy2(exe, t / exe.name)
        # 2) 模型
        self.progress.emit(10, "复制本地模型（约 3 GB，首次稍慢）…")
        models_src = PAYLOAD / "models"
        models_dst = (t / "models") if self.models_in_dir else (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ImageTagStudio" / "models")
        if models_src.exists():
            shutil.copytree(models_src, models_dst, dirs_exist_ok=True)
            self.log.emit(f"模型已安装到 {models_dst}")
        # 3) 内置 Python
        self.progress.emit(45, "解压内置 Python 环境…")
        py_src = PAYLOAD / "python"
        py_dst = t / "python"
        if py_src.exists():
            shutil.copytree(py_src, py_dst, dirs_exist_ok=True)
        pyexe = py_dst / "python.exe"
        if not pyexe.exists():
            raise RuntimeError("安装包不完整：缺少 payload\\python")
        # 嵌入式 Python 的 ._pth 会隔离 sys.path（连 PYTHONPATH 都不生效），
        # 必须把安装目录显式写进去，程序才能 import app 包。
        for pth in py_dst.glob("python*._pth"):
            txt = pth.read_text(encoding="utf-8")
            if "import site" not in txt:
                txt = txt.rstrip() + "\nimport site\n"
            # 用相对路径（python 目录的上一级=安装目录），这样整个安装目录搬走也还能用
            if ".." not in txt.split("\n"):
                txt = txt.rstrip() + "\n..\n"
            if str(t) not in txt:
                txt = txt.rstrip() + f"\n{t}\n"
            pth.write_text(txt, encoding="utf-8")
            self.log.emit(f"已把安装目录写入 {pth.name}")
        site_pkgs = py_dst / "Lib" / "site-packages"
        site_pkgs.mkdir(parents=True, exist_ok=True)
        (site_pkgs / "imtag_path.pth").write_text(str(t), encoding="utf-8")
        # 4) 离线安装依赖
        wheels = PAYLOAD / "wheels"
        pip_whl = next(iter(sorted(wheels.glob("pip-*.whl"))), None)
        if pip_whl is None:
            raise RuntimeError("安装包不完整：payload\\wheels 里没有 pip wheel")
        self.progress.emit(52, "初始化 pip…")
        self._bootstrap_pip(pyexe, wheels, pip_whl)
        self.progress.emit(58, "离线安装运行库（约 3 GB，耐心等几分钟）…")
        self._pip(pyexe, ["-r", str(t / "requirements.txt")])
        self.progress.emit(92, "写配置与快捷方式…")
        # 5) 配置
        (t / "launcher.ini").write_text(
            f"[app]\npython = {py_dst / 'pythonw.exe'}\nproject = {t}\n", encoding="utf-8")
        (t / "启动.cmd").write_text(
            "@echo off\r\n"
            f'start "" "{py_dst / "pythonw.exe"}" -m app.main\r\n', encoding="utf-8")
        from app import hardware, perf  # 由 PyInstaller 一起打包
        hw = hardware.detect(t)
        rec = hardware.recommend(hw)
        if self.mode and self.mode != "auto":
            rec["mode"] = self.mode
        hardware.write_profile(t / "perf_profile.json",
                               hardware.build_profile(hw, rec))
        self.log.emit(f"已按硬件写入推荐参数：{rec['mode']} {rec['overrides']}")
        # 6) 快捷方式 + 卸载器
        self._write_uninstaller(t)
        if self.make_shortcut:
            self._shortcut(t)
        self.progress.emit(100, "完成")
        self.log.emit("安装完成")

    def _pip(self, pyexe: Path, args: list[str]) -> None:
        cmd = [str(pyexe), "-m", "pip", "install", "--no-index", "--no-warn-script-location",
               f"--find-links={PAYLOAD / 'wheels'}"] + args
        self._spawn(cmd)

    def _bootstrap_pip(self, pyexe: Path, wheels: Path, pip_whl: Path) -> None:
        """嵌入式 Python 里没有 pip：把 pip 的 wheel 直接解压进 site-packages（纯 Python 包，最稳）。"""
        import zipfile
        site = pyexe.parent / "Lib" / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pip_whl) as zf:
            zf.extractall(site)
        self.log.emit(f"已内置 {pip_whl.name} → {site}")
        # 装包工具链（有些依赖是源码包，需要 setuptools）
        self._pip(pyexe, ["setuptools", "wheel"])

    def _spawn(self, cmd: list[str]) -> None:
        self.log.emit("$ " + " ".join(cmd[:6]) + " …")
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env=env,
                                creationflags=0x08000000)
        for line in proc.stdout or []:
            line = line.rstrip()
            if line:
                self.log.emit(line)
        if proc.wait() != 0:
            raise RuntimeError("pip 安装失败，详见日志")

    def _shortcut(self, t: Path) -> None:
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        lnk = desktop / f"{APP_NAME}.lnk"
        exe = t / f"{APP_NAME}.exe"
        ps = ("$W=New-Object -ComObject WScript.Shell;"
              f"$S=$W.CreateShortcut('{lnk}');"
              f"$S.TargetPath='{exe}';$S.WorkingDirectory='{t}';"
              f"$S.IconLocation='{exe},0';$S.Save()")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=0x08000000, timeout=30)
            self.log.emit(f"桌面快捷方式：{lnk}")
        except Exception as e:  # noqa: BLE001
            self.log.emit(f"创建快捷方式失败（可手动拖）：{e}")

    def _write_uninstaller(self, t: Path) -> None:
        """卸载器：删程序目录（保留用户数据目录，避免误删数据库/图库缩略图）。"""
        bat = t / "卸载.cmd"
        bat.write_text(
            "@echo off\r\nchcp 65001 >nul\r\n"
            f"echo 即将卸载 {APP_NAME}（安装目录：{t}）\r\n"
            "echo 用户数据（数据库、缩略图）在 %%LOCALAPPDATA%%\\ImageTagStudio，本脚本不会删除。\r\n"
            "pause\r\n"
            f'rmdir /s /q "{t}"\r\n'
            f'del "%USERPROFILE%\\Desktop\\{APP_NAME}.lnk" 2>nul\r\n'
            "echo 已卸载。\r\npause\r\n", encoding="utf-8")
        self.log.emit(f"卸载器：{bat}")


class Installer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} 安装程序（离线版）")
        self.resize(880, 700)
        v = QVBoxLayout(self)
        head = QLabel(f"<h2>{APP_NAME} · 离线安装</h2>"
                      "<p style='color:#8f96a3'>安装包内含完整运行库与本地模型，全程不联网；"
                      "会自动识别本机硬件并写好推荐性能参数。</p>")
        head.setWordWrap(True)
        v.addWidget(head)

        box = QGroupBox("1. 本机硬件检测")
        bv = QVBoxLayout(box)
        self.hw_label = QLabel("检测中…")
        self.hw_label.setWordWrap(True)
        bv.addWidget(self.hw_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("性能挡位："))
        self.mode = QComboBox()
        row.addWidget(self.mode, 1)
        b_re = QPushButton("重新检测")
        b_re.clicked.connect(self.detect)
        row.addWidget(b_re)
        bv.addLayout(row)
        self.reasons = QLabel("")
        self.reasons.setWordWrap(True)
        self.reasons.setStyleSheet("color:#ffcc66;")
        bv.addWidget(self.reasons)
        v.addWidget(box)

        box2 = QGroupBox("2. 安装位置")
        b2 = QVBoxLayout(box2)
        row2 = QHBoxLayout()
        self.path_edit = QLineEdit(str(self.default_dir()))
        row2.addWidget(self.path_edit, 1)
        b_browse = QPushButton("浏览…")
        b_browse.clicked.connect(self.browse)
        row2.addWidget(b_browse)
        b2.addLayout(row2)
        self.space = QLabel("")
        b2.addWidget(self.space)
        self.cb_shortcut = QCheckBox("创建桌面快捷方式")
        self.cb_shortcut.setChecked(True)
        b2.addWidget(self.cb_shortcut)
        self.cb_models = QCheckBox("模型装到安装目录（取消勾选则放到 %LOCALAPPDATA%\\ImageTagStudio\\models）")
        self.cb_models.setChecked(True)
        b2.addWidget(self.cb_models)
        self.cb_run = QCheckBox("安装完成后立即启动")
        self.cb_run.setChecked(True)
        b2.addWidget(self.cb_run)
        # 可选扩展包（DLC）：默认勾上（才 8 MB），装了也要在程序里手动启用才生效
        dlc_size = 0
        try:
            dlc_dir = PAYLOAD / "dlc"
            if dlc_dir.is_dir():
                dlc_size = sum(f.stat().st_size for f in dlc_dir.rglob("*") if f.is_file())
        except Exception:
            dlc_size = 0
        self.cb_dlc = QCheckBox(
            f"安装可选扩展包「AI 生图助手」（ComfyUI 生图，{dlc_size / 1024 ** 2:.0f} MB；"
            "装完在程序里「更多 ▾ → 扩展包（DLC）…」启用）")
        self.cb_dlc.setChecked(True)
        self.cb_dlc.setEnabled(dlc_size > 0)
        b2.addWidget(self.cb_dlc)
        v.addWidget(box2)

        self.bar = QProgressBar()
        v.addWidget(self.bar)
        self.status = QLabel("")
        v.addWidget(self.status)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(220)
        v.addWidget(self.log_view)
        row3 = QHBoxLayout()
        row3.addStretch(1)
        self.b_install = QPushButton("开始安装")
        self.b_install.clicked.connect(self.install)
        row3.addWidget(self.b_install)
        b_quit = QPushButton("退出")
        b_quit.clicked.connect(self.close)
        row3.addWidget(b_quit)
        v.addLayout(row3)
        self.detect()

    # ---------- 硬件 ----------
    def detect(self) -> None:
        from app import hardware, perf
        self.hw = hardware.detect(Path(self.path_edit.text() or "C:\\"))
        gpu = self.hw["gpus"][0]["name"] if self.hw["gpus"] else "无独显"
        vram = self.hw["gpus"][0]["vram_mb"] if self.hw["gpus"] else 0
        self.hw_label.setText(
            f"CPU：{self.hw['cpu']}（{self.hw['cores_physical']} 核 / {self.hw['cores_logical']} 线程）<br>"
            f"内存：{self.hw['ram_gb']} GB<br>显卡：{gpu}（显存 {vram} MB）<br>"
            f"系统：{self.hw['os']}")
        rec = hardware.recommend(self.hw)
        self.rec = rec
        self.mode.clear()
        for key in perf.ORDER:
            self.mode.addItem(perf.PRESETS[key]["label"], key)
        idx = self.mode.findData(rec["mode"])
        self.mode.setCurrentIndex(idx if idx >= 0 else 1)
        self.reasons.setText("自动调参依据：<br>· " + "<br>· ".join(rec["reasons"]))
        self.update_space()

    def default_dir(self) -> Path:
        best, free = Path("C:\\"), -1
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{letter}:\\")
            if p.exists():
                try:
                    f = shutil.disk_usage(str(p)).free
                    if f > free:
                        best, free = p, f
                except Exception:
                    pass
        return best / "ImageTagStudio"

    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择安装位置", self.path_edit.text())
        if d:
            self.path_edit.setText(str(Path(d) / "ImageTagStudio"))
            self.update_space()

    def update_space(self) -> None:
        try:
            p = Path(self.path_edit.text())
            root = p.anchor or "C:\\"
            free = shutil.disk_usage(root).free
            need = 0
            if PAYLOAD.exists():
                need = dir_size(PAYLOAD / "models") + dir_size(PAYLOAD / "wheels") + 2 * 1024 ** 3
            ok = free > need * 1.05
            self.space.setText(f"目标盘剩余 {human(free)}，预计需要 {human(need)}"
                               + ("　✓ 空间充足" if ok else "　⚠ 空间可能不足"))
            self.space.setStyleSheet("color:#7ddc7d;" if ok else "color:#ff8a8a;")
        except Exception as e:  # noqa: BLE001
            self.space.setText(f"（无法检查空间：{e}）")

    # ---------- 安装 ----------
    def install(self) -> None:
        target = Path(self.path_edit.text().strip())
        if not target.is_absolute():
            QMessageBox.warning(self, "路径不对", "请选择绝对路径，例如 K:\\ImageTagStudio")
            return
        if target.exists() and any(target.iterdir()):
            if QMessageBox.question(self, "目录非空", f"{target} 里已有文件，继续安装会覆盖同名文件。继续？") \
                    != QMessageBox.Yes:
                return
        if not PAYLOAD.exists():
            QMessageBox.critical(self, "安装包不完整", f"找不到 payload 目录：{PAYLOAD}")
            return
        self.b_install.setEnabled(False)
        self.worker = InstallWorker(target, self.mode.currentData(), self.cb_shortcut.isChecked(),
                                    self.cb_models.isChecked(), self.cb_dlc.isChecked())
        self.worker.log.connect(self.log_view.append)
        self.worker.progress.connect(lambda v, s: (self.bar.setValue(v), self.status.setText(s)))
        self.worker.done.connect(self.finished)
        self.worker.start()

    def finished(self, ok: bool, msg: str) -> None:
        self.b_install.setEnabled(True)
        if not ok:
            QMessageBox.critical(self, "安装失败", msg)
            return
        t = Path(msg)
        QMessageBox.information(self, "安装完成",
                                f"已安装到：{t}\n\n桌面快捷方式：{APP_NAME}\n"
                                f"卸载：双击安装目录里的「卸载.cmd」\n\n"
                                f"性能挡位已按你的硬件自动设好，之后可在程序里随时切换。")
        if self.cb_run.isChecked():
            try:
                subprocess.Popen([str(t / f"{APP_NAME}.exe")], cwd=str(t),
                                 creationflags=0x08000000)
            except Exception:
                pass
        self.close()


def main() -> int:
    app = QApplication(sys.argv)
    ico = ROOT / "assets" / "icon.ico"
    if not ico.exists():
        ico = PAYLOAD / "assets" / "icon.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    w = Installer()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
