"""清理内置汉化词典：删掉"本来就不该翻译"和"等于没翻"的条目。

用法：
    python tools\\prune_tag_zh.py            # 干跑，只报告
    python tools\\prune_tag_zh.py --apply    # 真删

删掉的三类：
  1) 译文里没有中文（纯数字/年份/拉丁原样回填）——留着等于没翻；
  2) 标签本身是数字、单字母、纯符号（2007、0_0、...）——没有可翻的东西；
  3) 常用外来词/品牌（emoji、cos、pov、live2d、pixiv…）——中文圈本来就照写英文。
译文里带中文的、真正翻译过的条目一律保留。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DICT = HERE / "app" / "tag_zh_dict.json"

CJK = re.compile(r"[\u4e00-\u9fff]")

# 中文圈习惯直接写英文的常用词/品牌/缩写（不翻译更自然）
KEEP_AS_IS = {
    "emoji", "cos", "cosplay", "coser", "gif", "jpeg", "jpg", "png", "webp", "url", "uri",
    "id", "ai", "oc", "se", "bgm", "op", "ed", "cv", "3d", "4k", "8k", "2d", "pc", "tv",
    "vtuber", "vt", "vr", "ar", "xr", "pov", "bdsm", "nsfw", "sfw", "qr", "api", "cpu", "gpu",
    "ram", "os", "ui", "ux", "usb", "hdmi", "wifi", "bluetooth", "mp3", "wav", "flac", "mp4",
    "twitter", "pixiv", "patreon", "github", "discord", "youtube", "twitch", "weibo", "bilibili",
    "niconico", "fanbox", "skeb", "deviantart", "artstation", "pinterest", "instagram", "tiktok",
    "steam", "nintendo", "playstation", "xbox", "android", "ios", "windows", "macos", "linux",
    "cg", "rpg", "fps", "moba", "dlc", "pvp", "pve", "npc", "hp", "mp", "xp", "lv", "exp",
    "seiyuu", "cv.", "live2d", "spine", "sd", "ai绘画",
}

NUMBERISH = re.compile(r"^[\s\d.,:_\-/年月日]+$")
SYMBOLISH = re.compile(r"^[\W_]+$", re.UNICODE)


def main() -> int:
    apply = "--apply" in sys.argv
    raw = json.loads(DICT.read_text(encoding="utf-8"))
    drop: dict[str, tuple[str, str]] = {}
    for key, val in list(raw.items()):
        k = str(key).strip().lower()
        v = str(val).strip()
        if not v:
            drop[key] = (v, "空译文")
        elif k in KEEP_AS_IS:
            drop[key] = (v, "常用外来词/品牌，保留英文")
        elif NUMBERISH.match(k) or SYMBOLISH.match(k):
            drop[key] = (v, "标签本身是数字/符号，没什么可翻")
        elif not CJK.search(v):
            drop[key] = (v, "译文里没有中文（等于没翻）")
        elif v.lower() == k:
            drop[key] = (v, "译文和原文一样")
    print(f"词典共 {len(raw)} 条，建议删除 {len(drop)} 条：")
    by_reason: dict[str, list[str]] = {}
    for k, (v, why) in drop.items():
        by_reason.setdefault(why, []).append(f"{k}={v!r}")
    for why, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"  · {why}：{len(items)} 条，例如 {', '.join(items[:6])}")
    if apply:
        for k in drop:
            raw.pop(k, None)
        DICT.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已删除，词典现在 {len(raw)} 条")
    else:
        print("（干跑，加 --apply 才会真删）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
