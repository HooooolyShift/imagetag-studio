"""把 v1.5 宣传片要用的素材统一收进 promo/v15_shots/，并生成清单。

来源（全部是现成的真实素材，不重新生成）：
  * PC 界面      manual_shots/（13 张，1500x950+）
  * 平板/手机    E:\\文档\\ChatGPT\\图片标签工坊_移动端\\shots\\**（MuMu 真机截图）
  * AI 生图      E:\\ComfyUI\\...\\output\\（imtag_* / hires_* / ipadapter_* / pose_* / up_*）
  * 开屏封面     K:\\ImageTagStudio\\开屏图（14张）\\（1536x648 横构图）

输出：promo/v15_shots/<类别>__<原名>.png + promo/v15_shots/manifest.json
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "v15_shots"

MOBILE = Path(r"E:\文档\ChatGPT\图片标签工坊_移动端\shots")
COMFY = Path(r"E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5\output")
SPLASH = Path(r"K:\ImageTagStudio\开屏图（14张）")

# 平板/手机截图挑选表：源相对路径 -> 目标名（这些是验收过的真机界面）
MOBILE_PICKS = {
    r"mumu\m1-browse.png": "tablet_01_图库浏览",
    r"mumu\m2-review.png": "tablet_02_审核台",
    r"mumu\m3-gen.png": "tablet_03_遥控生图",
    r"mumu\m4-viewer.png": "tablet_04_大图",
    r"mumu\m5-viewer-real.png": "tablet_05_原图",
    r"mumu\m6-region-drag.png": "tablet_06_框选区域",
    r"mumu\m7-region-saved.png": "tablet_07_区域精修完成",
    r"mumu\m8-viewer.png": "tablet_08_看图",
    r"apk\t1-browse.png": "tablet_09_APK浏览",
    r"apk\t2-tools.png": "tablet_10_工具箱",
    r"apk\t3-gen.png": "tablet_11_生图页",
    r"apk\t4-dupes.png": "tablet_12_查重",
    r"apk\t5-review.png": "tablet_13_审核",
    r"apk\t6-inpaint.png": "tablet_14_局部重绘",
    r"apk\t7-gen-advanced.png": "tablet_15_生图高级",
    r"apk\t8-gen-models.png": "tablet_16_模型清单",
    "graph.png": "tablet_17_图谱",
    "graph-spacing.png": "tablet_18_图谱分区",
    "devices.png": "tablet_19_设备发现",
    "settings.png": "tablet_20_设置",
    "viewer.png": "tablet_21_看图",
    "viewer-similar-live.png": "tablet_22_相似图",
    "tablet-l-browse.png": "tablet_23_横屏图库",
    "tablet-p-browse.png": "tablet_24_竖屏图库",
    "phone-browse.png": "phone_01_手机浏览",
    r"pc\pc-browse.png": "pcweb_01_浏览",
    r"pc\pc-review.png": "pcweb_02_审核",
    r"pc\pc-viewer.png": "pcweb_03_大图",
    "inpaint-masked.png": "gen_01_涂遮罩",
    "inpaint-done.png": "gen_02_重绘完成",
    "gen-ops.png": "gen_03_操作条",
    "gen-stop.png": "gen_04_停止",
}

# 生图素材挑选表：ComfyUI 输出（今天实测出的图）
GEN_PICKS = {
    "imtag_00026_.png": "ai_01_出图",
    "imtag_00025_.png": "ai_02_出图",
    "imtag_00024_.png": "ai_03_出图",
    "imtag_00022_.png": "ai_04_出图",
    "hires_20261008-184857_00001_.png": "ai_05_高分修复",
    "ipadapter_20261008-185118_00001_.png": "ai_06_参考图",
    "pose_20261008-185154_00001_.png": "ai_07_姿势控制",
    "up_20261008-183747_00001_.png": "ai_08_放大",
}


def copy_into(src: Path, dst_name: str, rows: list[dict], category: str) -> None:
    if not src.exists():
        print(f"  缺：{src}")
        return
    ext = src.suffix.lower() or ".png"
    dst = OUT / f"{dst_name}{ext}"
    shutil.copy2(src, dst)
    rows.append({"name": dst_name, "file": str(dst), "category": category,
                 "src": str(src), "size_kb": round(dst.stat().st_size / 1024)})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    print("--- PC 界面（manual_shots）")
    for p in sorted((PROJECT / "manual_shots").glob("*.png")):
        copy_into(p, f"pc_{p.stem}", rows, "pc")
    print("--- 平板/手机/生图遥控（移动端 shots）")
    for rel, name in MOBILE_PICKS.items():
        copy_into(MOBILE / rel, name, rows, "mobile")
    print("--- AI 生图（ComfyUI 输出）")
    for src_name, name in GEN_PICKS.items():
        copy_into(COMFY / src_name, name, rows, "ai_gen")
    print("--- 开屏封面（14 张）")
    for i, p in enumerate(sorted(SPLASH.glob("*.png"))[:14], 1):
        copy_into(p, f"splash_{i:02d}", rows, "splash")
    (OUT / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print(f"\n共 {len(rows)} 张 -> {OUT}")
    for k, v in by_cat.items():
        print(f"   {k:8} {v} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
