"""全局配置：目录、设置项、标签分类定义。"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "图片标签工坊"
APP_ID = "ImageTagStudio"
VERSION = "1.4.0"

# 支持的图片后缀
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".webp", ".bmp", ".dib", ".gif",
    ".tif", ".tiff", ".avif", ".heic", ".heif", ".hif", ".jxl", ".jp2", ".j2k", ".jpf",
    ".psd", ".ico", ".cur", ".tga", ".dds", ".pcx", ".ppm", ".pgm", ".pbm", ".pnm",
    ".sgi", ".ras", ".xbm", ".xpm", ".wbmp", ".svg",
}

# 标签分类（分类只影响界面分组与默认阈值，不影响检索）
TAG_CATEGORIES = {
    "character": "人物/角色",
    "real_person": "真人",
    "clothing": "服装",
    "count": "人数",
    "pose": "姿势/体位",
    "body": "身体/部位",
    "scene": "场景/背景",
    "style": "画风/质量",
    "action": "动作/互动",
    "series": "系列/作品",
    "other": "其它",
    "rating": "分级",
}
CATEGORY_ORDER = list(TAG_CATEGORIES.keys())
DEFAULT_CATEGORIES = TAG_CATEGORIES          # 内置类型（首次运行会写进数据库，之后以库里的为准）

# 每个类型默认的 CLIP 提示词模板（{} 代表标签名）；用户可以在「类型管理」里覆盖
DEFAULT_CATEGORY_TEMPLATES: dict[str, list[str]] = {
    "character": ["{}", "the anime character {}", "a picture of {}", "{} (anime)"],
    "real_person": ["{}", "a photo of {}", "a portrait of {}", "the person named {}"],
    "clothing": ["{}, clothing", "wearing {}", "clothed in {}", "a person wearing {}"],
    "count": ["{}", "{} people", "a picture with {}"],
    "pose": ["{}, pose", "{} pose", "a person in {} pose", "{}"],
    "body": ["{}", "{} of the body", "visible {}"],
    "scene": ["{}", "in a {}", "{} background", "a scene of {}"],
    "style": ["{}", "{} art style", "an image in {} style"],
    "action": ["{}", "a person {}", "{} action"],
    "series": ["{}", "from the series {}"],
    "rating": ["{}"],
    "other": ["{}", "a photo of {}", "{} object"],
}

# 自动识别来源
SOURCE_LABELS = {
    "manual": "手动",
    "wd14": "WD14",
    "clip": "CLIP",
    "clip_region": "区域精修",
    "rating": "分级",
    "face": "人脸",
    "series": "系列",
    "filename": "文件名",
    "filename_parent": "文件名(推断)",
    "probe": "自训练",
}

# 分级（向 pixiv 看齐：全年齢 / R-15 / R-18 / R-18G）
RATING_LEVELS = ["all_ages", "r15", "r18", "r18g"]
RATING_LABELS = {
    "all_ages": "全年龄",
    "r15": "R15（轻度暗示）",
    "r18": "R18（成人向）",
    "r18g": "R18G（猎奇）",
}
RATING_TAG = {"all_ages": "全年龄", "r15": "R15", "r18": "R18", "r18g": "R18G"}
RATING_COLOR = {"all_ages": "#7ddc7d", "r15": "#ffe066", "r18": "#ff8a5c", "r18g": "#c07bff"}


def project_root() -> Path:
    """程序所在目录（源码目录，或打包后 exe 所在目录）。"""
    env = os.environ.get("IMGTAG_HOME")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """用户数据目录：数据库、缩略图缓存、日志。"""
    env = os.environ.get("IMGTAG_DATA")
    base = Path(env) if env else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_models_dir() -> Path:
    d = os.environ.get("IMGTAG_MODELS")
    p = Path(d) if d else project_root() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def thumbs_dir() -> Path:
    p = data_dir() / "thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_hf_env() -> None:
    """国内网络下走 hf-mirror 下载模型；用户可用环境变量覆盖。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass
class Settings:
    # 库
    roots: list[str] = field(default_factory=list)
    # 模型
    models_dir: str = ""
    device: str = "auto"  # auto / cuda / cpu
    # WD14（二次元自动打标）
    wd14_enabled: bool = True
    wd14_model: str = "SmilingWolf/wd-swinv2-tagger-v3"
    wd14_threshold: float = 0.35          # 通用标签阈值
    wd14_char_threshold: float = 0.85     # 角色标签阈值
    wd14_max_tags: int = 40
    wd14_keep_rating: bool = False        # 是否保留分级(rating)标签
    wd14_source_probe: bool = False       # CLI 用（不受 UI 影响）
    # CLIP 零样本（自定义 tag 的核心）
    clip_enabled: bool = True
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_threshold: float = 0.65
    clip_prompt_template: str = "a photo of {}"
    # 人脸（真人）
    face_enabled: bool = True
    face_det_threshold: float = 0.5
    face_min_size: int = 32
    face_cluster_eps: float = 0.45        # 余弦距离阈值
    # 写回磁盘的标签存储方式
    tag_storage: str = "filename"          # filename / sidecar / db
    sidecar_name: str = ".imtag_tags.json"
    series_folder_trailing_tags: bool = True
    page_digits: int = 3
    series_move_mode: str = "copy"         # copy / move
    disk_max_tags: int = 12                # 写回文件名时最多写几个标签（避免路径超长）
    rename_keep_original: bool = False     # 写回文件名时是否保留原文件名（默认只用标签命名）
    tag_most_specific_on_disk: bool = True  # 写回文件名时父子标签只留最具体的（有白裙子就不再写裙子）
    infer_parent_tags: bool = True          # 扫描时从文件名读回标签后，按从属关系自动补回父标签
    series_folder_max_tags: int = 8        # 系列文件夹名最多带几个标签
    # 专用图库（Steam 式多库）
    library_dir_name: str = "ImageTags"    # 每个盘下的图库目录名，例如 E:\ImageTags
    library_only_search: bool = True       # 检索是否只在图库内（不包含扫描来源）
    import_subdir: str = ""                # 审核通过后自动入库到图库下的哪个子目录（空=用原文件夹名）
    auto_import_after_review: bool = False # 审核完成后自动把图片收进图库
    dup_threshold: int = 6                 # 感知哈希汉明距离阈值（越小越严格）
    dup_use_clip: bool = True              # 是否用 CLIP 再兜一层
    # 分级识别
    rating_enabled: bool = True
    rating_auto_confirm: bool = False      # 分级标签也要过审核（默认进待审核队列）
    rating_blur: bool = False              # 浏览时对 R18/R18G 缩略图打码
    rating_questionable_as_r18: bool = False  # WD14 的 questionable 算 R18（更严格）还是 R15
    auto_rescore_on_new_tag: bool = True   # 新建带提示词的标签后，自动全库扫描一遍
    # 性能挡位：max(榨干硬件) / balanced(均衡) / eco(节能) / cpu(只用CPU)
    perf_mode: str = "balanced"
    # 界面
    thumb_size: int = 170
    grid_columns_hint: int = 0
    show_auto_tags: bool = True
    confirm_before_write: bool = False
    show_startup_tip: bool = True          # 启动时是否提示"开始使用"（可勾选不再显示）
    show_model_tip: bool = True            # 启动时是否提示"模型未下载"（可勾选不再提示）
    graph_show_all_tags: bool = False      # 图谱是否画出全部标签（默认只画常用的前 600 个）
    graph_expand_reset_done: bool = False  # 是否已清掉老版本"自动折叠"留下的标记（一次性）
    zh_housekeeping_done: bool = False     # 标签整理/中文名补齐是否已跑过（一次性，跑过就不必每次启动重来）
    # 图谱可调参数
    graph_ring_radius: float = 900.0       # 第一层中心所在的圆半径（越大越松）
    graph_anim_ms: int = 340               # 布局变化/拖拽回弹的动画时长（毫秒）
    graph_drag_limit: float = 240.0        # 拖拽节点最多能拉离原位多少像素（弹簧极限）
    graph_label_len: int = 22              # 节点名字最多显示多少字
    # 扫描
    ignore_dirs: list[str] = field(default_factory=lambda: [".imtag", "@eaDir", ".git", "$RECYCLE.BIN", "System Volume Information"])

    # ---------- 读写 ----------
    @staticmethod
    def path() -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        raw = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        known = {f for f in cls.__dataclass_fields__}
        obj = cls(**{k: v for k, v in raw.items() if k in known})
        for k, v in raw.items():            # 老版本留下的"字段外"键也读回来，别丢
            if k not in known:
                setattr(obj, k, v)
        return obj

    def save(self) -> None:
        # 用 __dict__ 而不是 asdict()：asdict 只序列化**声明过的数据类字段**，
        # 以前代码里给设置对象挂的非字段属性（如 zh_housekeeping_done）会被悄悄丢掉，
        # 于是"下次不再显示"这类选项每次重启都回到默认。
        data = dict(asdict(self))
        data.update({k: v for k, v in vars(self).items() if k not in data})
        self.path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 每次写盘都留一行日志：以后"设置到底存没存"直接看这个文件，不用再猜
        try:
            from datetime import datetime
            line = "%s  写入 %d 项  保留原文件名=%s  缩略图=%s  性能档=%s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(data),
                data.get("rename_keep_original"), data.get("thumb_size"), data.get("perf_mode"))
            with open(self.path().with_name("settings.log"), "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass

    def models_path(self) -> Path:
        p = Path(self.models_dir) if self.models_dir else default_models_dir()
        p.mkdir(parents=True, exist_ok=True)
        return p
