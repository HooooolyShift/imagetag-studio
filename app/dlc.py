"""可选安装的扩展包（DLC）宿主。

**为什么要有这层**：主程序要能"可选安装"一些重型功能（比如 AI 生图那套 ComfyUI 封装），
装了就多一个菜单/窗口，不装主程序照常跑。把"装载、开关、配置、安装/卸载"留在宿主里，
DLC 自己只关心业务，不用管这些。

目录约定：`<程序目录>\\dlc\\<dlc_id>\\`
清单文件：该目录下的 `dlc.json`，字段见下（缺字段一律有默认值，容错优先）：

    {
      "id": "comfyui_helper",
      "name": "AI 生图（ComfyUI 助手）",
      "version": "0.1.0",
      "description": "……",
      "entry": "main.py",              # 宿主会 import 它并调用 register(host)
      "requires": ["ComfyUI"],          # 只用于提示用户
      "settings": [                     # 宿主自动渲染成表单并持久化
        {"key": "output_dir", "type": "dir", "label": "生图输出路径", "default": ""},
        {"key": "comfy_url", "type": "str", "label": "ComfyUI 地址",
         "default": "http://127.0.0.1:8188"}
      ]
    }

DLC 入口的样子（main.py）：

    def register(host):
        host.add_action("AI 生图", lambda: host.open_window(MyWindow(host)))

宿主（`DlcHost`）提供：`config`（读写自己的配置）、`save_config()`、`add_action()`、
`open_window()`、`store` / `library` / `hub`（主程序的核心对象，可选）、`project_root()`。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "dlc.json"


def dlcs_dir() -> Path:
    """DLC 根目录：便携时在程序目录下，装到 Program Files 时回落到数据目录。"""
    from .config import data_dir, project_root
    portable = project_root() / "dlc"
    try:
        portable.mkdir(parents=True, exist_ok=True)
        probe = portable / ".writable"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return portable
    except Exception:
        d = data_dir() / "dlc"
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass
class DlcInfo:
    id: str
    name: str
    version: str = ""
    description: str = ""
    entry: str = "main.py"
    requires: list = field(default_factory=list)
    settings: list = field(default_factory=list)
    path: Path | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def entry_path(self) -> Path | None:
        return (self.path / self.entry) if self.path else None


def _norm_settings(raw) -> list[dict]:
    """把清单里的配置项归一成宿主认识的字段。

    兼容两种写法（AI 生图会话用的是后者，宿主最早提的是前者）：
      1) `"settings": [{"key","type":"dir|str|bool","label","default"}]`
      2) `"config_schema": [{"key","type":"path|string|bool","label","default","hint"}]`
    """
    type_map = {"path": "dir", "dir": "dir", "folder": "dir",
                "string": "str", "str": "str", "text": "str", "url": "str",
                "int": "int", "number": "int", "bool": "bool", "boolean": "bool"}
    out: list[dict] = []
    for item in (raw or []):
        if not isinstance(item, dict) or not item.get("key"):
            continue
        out.append({
            "key": str(item["key"]),
            "type": type_map.get(str(item.get("type") or "str").lower(), "str"),
            "label": str(item.get("label") or item["key"]),
            "default": item.get("default", ""),
            "hint": str(item.get("hint") or ""),
            "required": bool(item.get("required", False)),
        })
    return out


def scan_dlcs() -> list[DlcInfo]:
    """扫描 DLC 根目录（每个子目录一个 DLC）。坏清单不抛异常，只标 error。"""
    out: list[DlcInfo] = []
    root = dlcs_dir()
    if not root.is_dir():
        return out
    for sub in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        mf = sub / MANIFEST
        if not mf.exists():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            info = DlcInfo(
                id=str(data.get("id") or sub.name),
                name=str(data.get("name") or sub.name),
                version=str(data.get("version") or ""),
                description=str(data.get("description") or ""),
                # 入口：`entry`（宿主提案）与 `entry_module`（AI 生图会话）都能认。
                # 注意对方若写的是包名（如 "prompt_helper"），实际入口仍是同目录的 main.py。
                entry=str(data.get("entry") or "main.py"),
                requires=(list(data.get("requires") or [])
                          if not isinstance(data.get("requires"), dict)
                          else [str(k) for k in (data.get("requires") or {})]),
                settings=_norm_settings(data.get("settings") or data.get("config_schema")),
                path=sub,
            )
            if not info.entry_path().exists():
                info.error = f"入口文件不存在：{info.entry}"
            out.append(info)
        except Exception as exc:
            out.append(DlcInfo(id=sub.name, name=sub.name, path=sub, error=f"清单解析失败：{exc}"))
    return out


def enabled_ids(settings) -> list[str]:
    return [str(x) for x in (getattr(settings, "dlc_enabled", None) or [])]


def is_enabled(settings, dlc_id: str) -> bool:
    return dlc_id in enabled_ids(settings)


def set_enabled(settings, dlc_id: str, on: bool) -> None:
    ids = enabled_ids(settings)
    if on and dlc_id not in ids:
        ids.append(dlc_id)
    if not on and dlc_id in ids:
        ids.remove(dlc_id)
    settings.dlc_enabled = ids
    try:
        settings.save()
    except Exception:
        pass


def dlc_config(settings, info: DlcInfo) -> dict:
    """取该 DLC 的配置（按清单里的 settings 补默认值）。"""
    all_cfg = getattr(settings, "dlc_config", None)
    if not isinstance(all_cfg, dict):
        all_cfg = {}
    cfg = dict(all_cfg.get(info.id) or {})
    for item in info.settings:
        key = str(item.get("key") or "")
        if key and key not in cfg:
            cfg[key] = item.get("default", "")
    return cfg


def save_dlc_config(settings, dlc_id: str, cfg: dict) -> None:
    all_cfg = getattr(settings, "dlc_config", None)
    if not isinstance(all_cfg, dict):
        all_cfg = {}
    all_cfg[dlc_id] = dict(cfg)
    settings.dlc_config = all_cfg
    try:
        settings.save()
    except Exception:
        pass


def install_zip(zip_path, overwrite: bool = True) -> tuple[bool, str]:
    """从 zip 安装一个 DLC（zip 里可以直接是 dlc.json，也可以套一层目录）。"""
    import tempfile
    import zipfile
    src = Path(zip_path)
    if not src.exists():
        return False, "文件不存在"
    with tempfile.TemporaryDirectory(prefix="imtag_dlc_") as tmp:
        try:
            with zipfile.ZipFile(src) as zf:
                zf.extractall(tmp)
        except Exception as exc:
            return False, f"解压失败：{exc}"
        base = Path(tmp)
        if not (base / MANIFEST).exists():          # 套了一层目录
            subs = [p for p in base.iterdir() if p.is_dir()]
            hit = next((p for p in subs if (p / MANIFEST).exists()), None)
            if hit is None:
                return False, "压缩包里找不到 dlc.json"
            base = hit
        try:
            data = json.loads((base / MANIFEST).read_text(encoding="utf-8"))
            dlc_id = str(data.get("id") or base.name)
        except Exception as exc:
            return False, f"清单解析失败：{exc}"
        target = dlcs_dir() / dlc_id
        if target.exists():
            if not overwrite:
                return False, f"已存在：{dlc_id}"
            from .fsops import recycle
            recycle(target)
        try:
            shutil.copytree(base, target)
        except Exception as exc:
            return False, f"复制失败：{exc}"
        return True, dlc_id


def uninstall(dlc_id: str, settings=None) -> tuple[bool, str]:
    """卸载：目录进回收站（可还原），并从启用列表里摘掉。"""
    info = next((d for d in scan_dlcs() if d.id == dlc_id), None)
    if info is None or info.path is None:
        return False, "没找到这个 DLC"
    from .fsops import recycle
    ok = recycle(info.path)
    if settings is not None and is_enabled(settings, dlc_id):
        set_enabled(settings, dlc_id, False)
    return bool(ok), "已移入回收站" if ok else "删除失败"


class DlcHost:
    """交给 DLC 的宿主 API（DLC 只依赖这个，不直接碰主程序内部）。"""

    def __init__(self, info: DlcInfo, settings, add_action, open_window=None,
                 store=None, library=None, hub=None):
        self.info = info
        self.settings = settings
        self.store = store
        self.library = library
        self.hub = hub
        self._add_action = add_action
        self._open_window = open_window
        self.config = dlc_config(settings, info)

    def save_config(self) -> None:
        save_dlc_config(self.settings, self.info.id, self.config)

    def add_action(self, title: str, callback, tip: str = "") -> None:
        if callable(self._add_action):
            self._add_action(title, callback, tip)

    def open_window(self, widget) -> None:
        if callable(self._open_window):
            self._open_window(widget)

    def project_root(self) -> Path:
        from .config import project_root
        return project_root()

    # ---------- 给 DLC 的"共享能力"（别各自造轮子） ----------
    def lexicon(self):
        """共享标签词库：中文→标签、标签元信息、拼音联想、父子从属、热度、提示词规范化。

        生图那边的"中文提示词 → danbooru 标签"就用它，**不要再自带一份词表**——
        主程序这边已经有 `tag_zh_dict.json`(12.8 万条) + `app/booru_zh/*.csv`(danbooru 中文)
        + 拼音/联想引擎，两边共用一个来源才不会对不上。
        """
        from .taglex import get_lexicon
        return get_lexicon(self.store, self.library)

    def scan_into_library(self, paths) -> list[int]:
        """把一批磁盘文件（例如刚生成的图）扫进库并返回 file id；已存在则直接返回其 id。

        DLC 生成完图调它，就能立刻用主程序的打标/审核/检索链路。
        """
        if self.store is None or self.library is None:
            return []
        ids: list[int] = []
        for p in paths:
            sp = str(Path(p))
            row = self.store.one("SELECT id FROM files WHERE path=?", (sp,))
            if row is not None:
                ids.append(int(row["id"]))
                continue
            try:
                parent = str(Path(p).parent)
                root = self.store.one("SELECT id, path FROM roots WHERE path=?", (parent,)) or \
                    self.store.one("SELECT id, path FROM roots WHERE ? LIKE path || '%' ORDER BY LENGTH(path) DESC LIMIT 1",
                                   (sp,))
                if root is None:
                    continue
                fid = self.library.add_single_file(int(root["id"]), Path(p))
                if fid:
                    ids.append(int(fid))
            except Exception:
                continue
        return ids

    def tag_files(self, file_ids) -> None:
        """对一批文件跑一遍自动打标（WD14+CLIP+分级）——需要宿主有模型引擎（hub）。"""
        if self.library is None or self.hub is None or not file_ids:
            return
        ids = [int(i) for i in file_ids]

        def progress(_t, _f=0.0):
            pass

        try:
            s = self.settings
            if getattr(s, "wd14_enabled", True):
                self.library.run_wd14(ids, self.hub, progress, lambda: False)
            if getattr(s, "clip_enabled", True):
                self.library.ensure_clip_embeddings(ids, self.hub, progress, lambda: False)
                self.library.auto_tags_from_clip(self.hub, ids, progress, lambda: False)
            if getattr(s, "rating_enabled", True):
                self.library.run_rating(ids, self.hub, progress, lambda: False)
        except Exception:
            pass


def load_dlc(info: DlcInfo, settings, add_action, open_window=None,
             store=None, library=None, hub=None):
    """import 入口模块并调用 register(host)。返回 (host, 错误信息)。"""
    if not info.ok:
        return None, info.error
    ep = info.entry_path()
    mod_name = f"imtag_dlc_{info.id}"
    try:
        # 关键：把 DLC 目录当成"包"来加载（submodule_search_locations 指向它的目录），
        # 这样 DLC 内部写 `from .ui import X`、`from . import comfy` 这种相对导入才成立。
        # 否则入口模块是顶层模块，相对导入会直接 ImportError（2026-10-08 实测踩到）。
        spec = importlib.util.spec_from_file_location(
            mod_name, ep, submodule_search_locations=[str(info.path)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        if info.path is not None:
            pkg = str(mod_name)
            setattr(mod, "__path__", [str(info.path)])
            sys.modules[pkg] = mod
        spec.loader.exec_module(mod)                     # type: ignore[union-attr]
        host = DlcHost(info, settings, add_action, open_window, store, library, hub)
        reg = getattr(mod, "register", None)
        if not callable(reg):
            return None, "入口模块里没有 register(host) 函数"
        reg(host)
        return host, ""
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return None, str(exc)
