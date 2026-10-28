"""图片标签工坊 离线安装程序（Tk 界面，纯本地，无网络请求）。

安装器刻意不依赖 PySide6/Qt：Qt DLL 在少数 Windows 机器上可能被旧运行库、
安全软件或系统组件干扰，导致安装器在复制任何文件前就无法启动。Tk 使用
Windows 自带的系统组件，因此更适合承担“安装应用”这一步。
"""
from __future__ import annotations

import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, filedialog, font as tkfont, messagebox, scrolledtext, ttk
import tkinter as tk

APP_NAME = "图片标签工坊"


def app_root() -> Path:
    """返回安装包所在目录。

    PyInstaller 单文件运行时 ``__file__`` 位于临时解包目录，不能用来找
    payload；必须使用真实 exe 所在目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_root()
PAYLOAD = ROOT / "payload"
LOG_PATH = Path(os.environ.get("TEMP", str(Path.home()))) / "ImageTagStudio_installer.log"


def human(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n > 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def dir_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return total
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def append_log(text: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except OSError:
        pass


def fatal_dialog(message: str) -> None:
    append_log(message)
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{message}\n\n诊断日志：\n{LOG_PATH}",
            f"{APP_NAME} 安装程序",
            0x10,
        )
    except Exception:
        pass


def current_windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def check_supported_windows() -> tuple[bool, str]:
    build = current_windows_build()
    if not build:
        return True, "无法读取 Windows 版本"
    if build < 17763:
        return False, (
            f"当前 Windows 内部版本为 {build}，低于 Windows 10 1809（17763）。\n\n"
            "程序依赖 Qt 6/PySide6，只支持 Windows 10 1809 及以上的 64 位系统；"
            "安装 Visual C++ 运行库无法补回旧系统缺失的系统 API。"
        )
    return True, f"Windows build {build}"


class InstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} 安装程序（离线版）")
        self.root.geometry("920x760")
        self.root.minsize(820, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self.on_tk_error

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.installing = False
        self.worker: threading.Thread | None = None
        self.hw: dict | None = None
        self.rec: dict | None = None
        self.mode_map: dict[str, str] = {}

        self.path_var = tk.StringVar(value=str(self.default_dir()))
        self.mode_var = tk.StringVar()
        self.shortcut_var = tk.BooleanVar(value=True)
        self.models_var = tk.BooleanVar(value=True)
        self.run_var = tk.BooleanVar(value=True)
        self.dlc_var = tk.BooleanVar(value=True)
        self.hw_text = tk.StringVar(value="检测中…")
        self.reason_text = tk.StringVar(value="")
        self.space_text = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="准备就绪")

        self._build_ui()
        self.root.after(80, self._drain_events)
        self.root.after(150, self.detect_hardware)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(
            outer,
            text=f"{APP_NAME} · 离线安装",
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="安装包内含完整运行库与本地模型，全程不联网；会自动识别硬件并写好推荐性能参数。",
            foreground="#666666",
        ).pack(anchor="w", pady=(3, 14))

        hw_box = ttk.LabelFrame(outer, text="1. 本机硬件检测", padding=12)
        hw_box.pack(fill=X, pady=(0, 10))
        ttk.Label(hw_box, textvariable=self.hw_text, justify=LEFT).pack(fill=X, anchor="w")
        mode_row = ttk.Frame(hw_box)
        mode_row.pack(fill=X, pady=(8, 2))
        ttk.Label(mode_row, text="性能挡位：").pack(side=LEFT)
        self.mode_combo = ttk.Combobox(
            mode_row, textvariable=self.mode_var, state="readonly", width=32
        )
        self.mode_combo.pack(side=LEFT, fill=X, expand=True, padx=(4, 8))
        self.detect_btn = ttk.Button(mode_row, text="重新检测", command=self.detect_hardware)
        self.detect_btn.pack(side=RIGHT)
        ttk.Label(
            hw_box, textvariable=self.reason_text, justify=LEFT, foreground="#9a6512"
        ).pack(fill=X, anchor="w", pady=(5, 0))

        path_box = ttk.LabelFrame(outer, text="2. 安装位置", padding=12)
        path_box.pack(fill=X, pady=(0, 10))
        path_row = ttk.Frame(path_box)
        path_row.pack(fill=X)
        self.path_entry = ttk.Entry(path_row, textvariable=self.path_var)
        self.path_entry.pack(side=LEFT, fill=X, expand=True)
        self.browse_btn = ttk.Button(path_row, text="浏览…", command=self.browse)
        self.browse_btn.pack(side=RIGHT, padx=(8, 0))
        self.path_var.trace_add("write", lambda *_: self.update_space())
        ttk.Label(path_box, textvariable=self.space_text).pack(anchor="w", pady=(6, 4))

        ttk.Checkbutton(
            path_box, text="创建桌面快捷方式", variable=self.shortcut_var
        ).pack(anchor="w")
        ttk.Checkbutton(
            path_box,
            text="模型装到安装目录（取消勾选则放到 %LOCALAPPDATA%\\ImageTagStudio\\models）",
            variable=self.models_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            path_box, text="安装完成后立即启动", variable=self.run_var
        ).pack(anchor="w")

        dlc_size = dir_size(PAYLOAD / "dlc")
        self.dlc_check = ttk.Checkbutton(
            path_box,
            text=(
                "安装可选扩展包「AI 生图助手」"
                f"（ComfyUI 生图，{dlc_size / 1024 ** 2:.0f} MB；"
                "装完在程序「更多 ▾ → 扩展包（DLC）…」启用）"
            ),
            variable=self.dlc_var,
        )
        self.dlc_check.pack(anchor="w")
        if dlc_size <= 0:
            self.dlc_var.set(False)
            self.dlc_check.state(["disabled"])

        progress_box = ttk.LabelFrame(outer, text="3. 安装进度", padding=12)
        progress_box.pack(fill=BOTH, expand=True)
        self.progress = ttk.Progressbar(progress_box, maximum=100, mode="determinate")
        self.progress.pack(fill=X)
        ttk.Label(progress_box, textvariable=self.status_var).pack(anchor="w", pady=(5, 6))
        self.log_view = scrolledtext.ScrolledText(
            progress_box,
            height=10,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_view.pack(fill=BOTH, expand=True)

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=(12, 0))
        self.exit_btn = ttk.Button(actions, text="退出", command=self.on_close)
        self.exit_btn.pack(side=RIGHT)
        self.install_btn = ttk.Button(actions, text="开始安装", command=self.install)
        self.install_btn.pack(side=RIGHT, padx=(0, 8))

    def post(self, kind: str, value: object = None) -> None:
        self.events.put((kind, value))

    def log(self, text: str) -> None:
        self.post("log", text)

    def progress_cb(self, value: int, text: str) -> None:
        self.post("progress", (value, text))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append_ui_log(str(value))
                elif kind == "progress":
                    pct, status = value  # type: ignore[misc]
                    self.progress["value"] = int(pct)
                    self.status_var.set(str(status))
                elif kind == "hardware":
                    hw, rec, labels = value  # type: ignore[misc]
                    self._apply_hardware(hw, rec, labels)
                elif kind == "hardware_error":
                    self.hw_text.set(f"硬件检测失败：{value}")
                    self.reason_text.set("仍可继续安装；程序首次启动时会再次检测。")
                    self.detect_btn.state(["!disabled"])
                elif kind == "done":
                    self._finished(*value)  # type: ignore[misc]
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)

    def _append_ui_log(self, text: str) -> None:
        append_log(text)
        self.log_view.configure(state="normal")
        self.log_view.insert(END, text.rstrip() + "\n")
        self.log_view.see(END)
        self.log_view.configure(state="disabled")

    def on_tk_error(self, exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        self._append_ui_log(text)
        messagebox.showerror(
            "安装程序错误",
            f"{exc_value}\n\n诊断日志：\n{LOG_PATH}",
            parent=self.root,
        )

    # ---------- 硬件 ----------
    def detect_hardware(self) -> None:
        self.detect_btn.state(["disabled"])
        self.hw_text.set("检测中…")
        self.reason_text.set("")

        def work() -> None:
            try:
                from app import hardware, perf

                target = Path(self.path_var.get().strip() or "C:\\")
                hw = hardware.detect(target)
                rec = hardware.recommend(hw)
                labels = [(perf.PRESETS[key]["label"], key) for key in perf.ORDER]
                self.post("hardware", (hw, rec, labels))
            except Exception as exc:  # noqa: BLE001
                append_log(traceback.format_exc())
                self.post("hardware_error", str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _apply_hardware(self, hw: dict, rec: dict, labels: list[tuple[str, str]]) -> None:
        self.hw = hw
        self.rec = rec
        gpu = hw["gpus"][0] if hw.get("gpus") else {}
        gpu_name = gpu.get("name") or "无独显"
        vram = int(gpu.get("vram_mb") or 0)
        self.hw_text.set(
            f"CPU：{hw.get('cpu', '未知')}"
            f"（{hw.get('cores_physical', 0)} 核 / {hw.get('cores_logical', 0)} 线程）\n"
            f"内存：{hw.get('ram_gb', 0)} GB\n"
            f"显卡：{gpu_name}（显存 {vram} MB）\n"
            f"系统：{hw.get('os', '未知')}"
        )
        self.reason_text.set("自动调参依据：\n· " + "\n· ".join(rec.get("reasons", [])))
        self.mode_map = dict(labels)
        self.mode_combo["values"] = [label for label, _ in labels]
        selected = next((label for label, key in labels if key == rec.get("mode")), "")
        if not selected and labels:
            selected = labels[1][0] if len(labels) > 1 else labels[0][0]
        self.mode_var.set(selected)
        self.detect_btn.state(["!disabled"])
        self.update_space()

    def default_dir(self) -> Path:
        best, free = Path("C:\\"), -1
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if not drive.exists():
                continue
            try:
                current_free = shutil.disk_usage(str(drive)).free
                if current_free > free:
                    best, free = drive, current_free
            except OSError:
                continue
        return best / "ImageTagStudio"

    def browse(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择安装位置",
            initialdir=self.path_var.get().strip() or str(Path.home()),
        )
        if selected:
            self.path_var.set(str(Path(selected) / "ImageTagStudio"))

    def update_space(self) -> None:
        try:
            target = Path(self.path_var.get().strip())
            root = target.anchor or "C:\\"
            free = shutil.disk_usage(root).free
            need = dir_size(PAYLOAD / "models") + dir_size(PAYLOAD / "wheels") + 2 * 1024 ** 3
            ok = free > need * 1.05
            self.space_text.set(
                f"目标盘剩余 {human(free)}，预计需要 {human(need)}"
                + ("　✓ 空间充足" if ok else "　⚠ 空间可能不足")
            )
        except Exception as exc:  # noqa: BLE001
            self.space_text.set(f"无法检查空间：{exc}")

    # ---------- 安装 ----------
    def install(self) -> None:
        if self.installing:
            return
        target_text = self.path_var.get().strip()
        target = Path(target_text)
        if not target.is_absolute():
            messagebox.showwarning(
                "路径不对", "请选择绝对路径，例如 D:\\ImageTagStudio", parent=self.root
            )
            return
        if target.exists() and any(target.iterdir()):
            if not messagebox.askyesno(
                "目录非空",
                f"{target} 里已有文件，继续安装会覆盖同名文件。是否继续？",
                parent=self.root,
            ):
                return
        if not PAYLOAD.is_dir():
            messagebox.showerror(
                "安装包不完整",
                f"找不到 payload 目录：\n{PAYLOAD}\n\n"
                "请确认安装器 exe、payload 文件夹和分卷解压目录完整。",
                parent=self.root,
            )
            return
        if not (PAYLOAD / "python" / "python.exe").is_file():
            messagebox.showerror(
                "安装包不完整",
                f"找不到内置 Python：\n{PAYLOAD / 'python' / 'python.exe'}",
                parent=self.root,
            )
            return

        mode_key = self.mode_map.get(self.mode_var.get(), "auto")
        self.installing = True
        self.install_btn.state(["disabled"])
        self.browse_btn.state(["disabled"])
        self.detect_btn.state(["disabled"])
        self.progress["value"] = 0
        self.status_var.set("开始安装…")
        self._append_ui_log(f"安装目标：{target}")
        self._append_ui_log(f"日志文件：{LOG_PATH}")

        options = (
            target,
            mode_key,
            self.shortcut_var.get(),
            self.models_var.get(),
            self.dlc_var.get(),
            self.run_var.get(),
        )
        self.worker = threading.Thread(target=self._install_worker, args=options, daemon=True)
        self.worker.start()

    def _install_worker(
        self,
        target: Path,
        mode: str,
        make_shortcut: bool,
        models_in_dir: bool,
        install_dlc: bool,
        run_after: bool,
    ) -> None:
        try:
            self._copy_and_install(target, mode, make_shortcut, models_in_dir, install_dlc)
            self.post("done", (True, str(target), run_after))
        except Exception as exc:  # noqa: BLE001
            details = traceback.format_exc()
            append_log(details)
            self.log("")
            self.log(details)
            self.post("done", (False, str(exc), False))

    def _copy_and_install(
        self,
        target: Path,
        mode: str,
        make_shortcut: bool,
        models_in_dir: bool,
        install_dlc: bool,
    ) -> None:
        target.mkdir(parents=True, exist_ok=True)
        self.progress_cb(2, "复制程序文件…")
        for name in ("app", "tools", "assets"):
            src, dst = PAYLOAD / name, target / name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                self.log(f"复制 {name}/ 完成")
        for name in ("README.md", "requirements.txt", "run.cmd"):
            src = PAYLOAD / name
            if src.is_file():
                shutil.copy2(src, target / name)
        for pattern in ("*.pdf", "*.exe"):
            for src in PAYLOAD.glob(pattern):
                if src.is_file():
                    shutil.copy2(src, target / src.name)

        if install_dlc and (PAYLOAD / "dlc").is_dir():
            shutil.copytree(PAYLOAD / "dlc", target / "dlc", dirs_exist_ok=True)
            self.log("已安装扩展包（DLC）：AI 生图助手")
        elif (PAYLOAD / "dlc").is_dir():
            self.log("按选择跳过扩展包（DLC）；已有 dlc 目录不会删除")

        self.progress_cb(10, "复制本地模型（约 3 GB，首次稍慢）…")
        models_src = PAYLOAD / "models"
        models_dst = (
            target / "models"
            if models_in_dir
            else Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "ImageTagStudio"
            / "models"
        )
        if models_src.is_dir():
            shutil.copytree(models_src, models_dst, dirs_exist_ok=True)
            self.log(f"模型已安装到 {models_dst}")

        self.progress_cb(45, "解压内置 Python 环境…")
        py_src = PAYLOAD / "python"
        py_dst = target / "python"
        if py_src.is_dir():
            shutil.copytree(py_src, py_dst, dirs_exist_ok=True)
        pyexe = py_dst / "python.exe"
        if not pyexe.is_file():
            raise RuntimeError("安装包不完整：缺少 payload\\python\\python.exe")

        for pth in py_dst.glob("python*._pth"):
            content = pth.read_text(encoding="utf-8")
            lines = content.splitlines()
            if "import site" not in lines:
                lines.append("import site")
            if ".." not in lines:
                lines.append("..")
            if str(target) not in lines:
                lines.append(str(target))
            pth.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            self.log(f"已把安装目录写入 {pth.name}")
        site_pkgs = py_dst / "Lib" / "site-packages"
        site_pkgs.mkdir(parents=True, exist_ok=True)
        (site_pkgs / "imtag_path.pth").write_text(str(target), encoding="utf-8")

        wheels = PAYLOAD / "wheels"
        pip_whl = next(iter(sorted(wheels.glob("pip-*.whl"))), None)
        if pip_whl is None:
            raise RuntimeError("安装包不完整：payload\\wheels 里没有 pip wheel")
        self.progress_cb(52, "初始化 pip…")
        self._bootstrap_pip(pyexe, pip_whl)
        self.progress_cb(58, "离线安装运行库（约 3 GB，耐心等几分钟）…")
        self._pip(pyexe, ["-r", str(target / "requirements.txt")])

        self.progress_cb(91, "检查 Qt 运行环境…")
        self._spawn(
            [
                str(pyexe),
                "-c",
                "from PySide6.QtCore import QCoreApplication; "
                "print('PySide6/QtCore OK', QCoreApplication.applicationName())",
            ]
        )

        self.progress_cb(94, "写配置、快捷方式与硬件挡位…")
        (target / "launcher.ini").write_text(
            f"[app]\npython = {py_dst / 'pythonw.exe'}\nproject = {target}\n",
            encoding="utf-8",
        )
        (target / "启动.cmd").write_text(
            "@echo off\r\n"
            'chcp 65001 >nul\r\n'
            f'start "" "{py_dst / "pythonw.exe"}" -m app.main\r\n',
            encoding="utf-8",
        )

        from app import hardware

        hw = hardware.detect(target)
        rec = hardware.recommend(hw)
        if mode and mode != "auto":
            rec["mode"] = mode
        hardware.write_profile(
            target / "perf_profile.json", hardware.build_profile(hw, rec)
        )
        self.log(f"已按硬件写入推荐参数：{rec['mode']} {rec['overrides']}")

        self._write_uninstaller(target)
        if make_shortcut:
            self._shortcut(target)
        self.progress_cb(100, "完成")
        self.log("安装完成")

    def _pip(self, pyexe: Path, args: list[str]) -> None:
        cmd = [
            str(pyexe),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-warn-script-location",
            f"--find-links={PAYLOAD / 'wheels'}",
        ] + args
        self._spawn(cmd)

    def _bootstrap_pip(self, pyexe: Path, pip_whl: Path) -> None:
        import zipfile

        site = pyexe.parent / "Lib" / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pip_whl) as zf:
            zf.extractall(site)
        self.log(f"已内置 {pip_whl.name} → {site}")
        self._pip(pyexe, ["setuptools", "wheel"])

    def _spawn(self, cmd: list[str]) -> None:
        self.log("$ " + " ".join(cmd[:6]) + (" …" if len(cmd) > 6 else ""))
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        creationflags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        for line in proc.stdout or []:
            line = line.rstrip()
            if line:
                self.log(line)
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"命令执行失败（退出码 {code}）：{cmd[0]}")

    def _shortcut(self, target: Path) -> None:
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        lnk = desktop / f"{APP_NAME}.lnk"
        exe = target / f"{APP_NAME}.exe"
        quote = lambda p: str(p).replace("'", "''")
        ps = (
            "$W=New-Object -ComObject WScript.Shell;"
            f"$S=$W.CreateShortcut('{quote(lnk)}');"
            f"$S.TargetPath='{quote(exe)}';"
            f"$S.WorkingDirectory='{quote(target)}';"
            f"$S.IconLocation='{quote(exe)},0';"
            "$S.Save()"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                creationflags=0x08000000 if os.name == "nt" else 0,
                timeout=30,
                check=False,
            )
            self.log(f"桌面快捷方式：{lnk}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"创建快捷方式失败（可手动拖）：{exc}")

    def _write_uninstaller(self, target: Path) -> None:
        bat = target / "卸载.cmd"
        bat.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f"echo 即将卸载 {APP_NAME}（安装目录：{target}）\r\n"
            "echo 用户数据在 %LOCALAPPDATA%\\ImageTagStudio，本脚本不会删除。\r\n"
            "pause\r\n"
            f'rmdir /s /q "{target}"\r\n'
            f'del "%USERPROFILE%\\Desktop\\{APP_NAME}.lnk" 2>nul\r\n'
            "echo 已卸载。\r\n"
            "pause\r\n",
            encoding="utf-8",
        )
        self.log(f"卸载器：{bat}")

    def _finished(self, ok: bool, message: str, run_after: bool) -> None:
        self.installing = False
        self.install_btn.state(["!disabled"])
        self.browse_btn.state(["!disabled"])
        self.detect_btn.state(["!disabled"])
        if not ok:
            self.status_var.set("安装失败")
            messagebox.showerror(
                "安装失败",
                f"{message}\n\n详细日志：\n{LOG_PATH}",
                parent=self.root,
            )
            return

        target = Path(message)
        self.status_var.set("安装完成")
        messagebox.showinfo(
            "安装完成",
            f"已安装到：{target}\n\n"
            f"桌面快捷方式：{APP_NAME}\n"
            "卸载：双击安装目录里的「卸载.cmd」\n\n"
            "性能挡位已按硬件写好，之后可在程序设置中随时切换。",
            parent=self.root,
        )
        if run_after:
            exe = target / f"{APP_NAME}.exe"
            try:
                subprocess.Popen(
                    [str(exe)],
                    cwd=str(target),
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning(
                    "启动失败",
                    f"{exc}\n\n可双击安装目录里的「启动.cmd」。",
                    parent=self.root,
                )
        self.root.after(150, self.root.destroy)

    def on_close(self) -> None:
        if self.installing:
            if not messagebox.askyesno(
                "安装仍在进行",
                "安装仍在进行。现在退出可能留下不完整文件。确定退出？",
                parent=self.root,
            ):
                return
        self.root.destroy()


def main() -> int:
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    def excepthook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        fatal_dialog(text)

    sys.excepthook = excepthook

    supported, os_message = check_supported_windows()
    if not supported:
        fatal_dialog(os_message)
        return 2

    root = tk.Tk()
    try:
        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(font_name).configure(family="Microsoft YaHei UI", size=10)
    except tk.TclError:
        pass
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    icon = ROOT / "assets" / "icon.ico"
    if not icon.is_file():
        icon = PAYLOAD / "assets" / "icon.ico"
    if icon.is_file():
        try:
            root.iconbitmap(default=str(icon))
        except Exception:
            pass
    append_log("=" * 72)
    append_log(f"{APP_NAME} 安装程序启动")
    append_log(f"安装包目录：{ROOT}")
    append_log(f"payload：{PAYLOAD}")
    append_log(os_message)
    InstallerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
