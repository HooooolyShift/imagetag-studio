"""用本机 ComfyUI 出开屏封面图（初音未来 / 重音テト），成品放到 .splash_gen/<底模名>/ 供挑选。

**每个底模一个子目录**（2026-10-07 起）：多个底模并行出图时产物不互相覆盖，
同一 seed 在不同底模下构图接近，方便直接对比挑图。

尺寸直接按开屏封面区的比例出（560x236 = 2.3729:1），不减裁：
1216x512 = 2.375:1，偏差 0.09%，肉眼无差异（画布用 KeepAspectRatioByExpanding，
溢出不到 1px，不会切到人）。

提示词按 SDXL / Animagine XL 的 danbooru tag 习惯写：
  · 负向词保持精简（长负向词会把 SDXL 画面打爆，出彩噪或接近空白）；
  · 不堆 "flat shading / official art style" 之类 SD1.5 时代的玄学词；
  · 构图靠 wide shot / full body / character on the left 这类 tag 控制。

用法： python tools\\gen_splash.py [底模文件名] [每张出几张=2] [只跑指定任务,如 05] [宽度] [输出目录]

宽度默认 1216（对应 1216x512）。高分屏上开屏窗口大约是屏幕 1/3 宽，
想要更锐利就出更大：python tools\\gen_splash.py NoobAI-XL-v1.1.safetensors 3 "" 1536
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:8188"
OUT = Path(__file__).resolve().parent.parent / ".splash_gen"


def out_dir(ckpt: str, override: str = "") -> Path:
    """底模各自的产物目录：多模型对比时不会互相覆盖（可用第 5 个参数指定）。"""
    if override:
        return Path(override).resolve()
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", Path(ckpt).stem) or "model"
    return OUT / stem
W, H = 1216, 512                # 默认尺寸：与开屏封面比例一致（2.375:1），直出不裁切
ASPECT = 1216 / 512             # 开屏封面长宽比；改宽度时按它算高度
STEPS = 60                      # 单次出图步数
CFG = 5.5                       # 稍微降 CFG，减少过饱和/halo 伪影
DEFAULT_CKPT = "NoobAI-XL-v1.1.safetensors"   # 角色还原/构图跟随能力比 Animagine 强
HIRES = 1.0                     # 默认关闭二段放大
HIRES_DENOISE = 0.3

# 【为什么默认关掉二段放大】
# 实测「LatentUpscale + img2img」在这个平涂模型上会留下重影/彩噪：
#   dpmpp_2m 二段 → 轮廓一圈重影；dpmpp_2m_sde 二段 → 大面积彩色噪点；2× 更糊。
# 而开屏封面显示尺寸只有 560x236，直出 1216x512 已经是 2.17 倍，够用。
# 需要更大图时再手动传 hires（脚本 build(hires=1.5)），但别用在最终交付图上。

QUALITY = ("masterpiece, best quality, very aesthetic, absurdres, newest, "
           "anime coloring, flat color, cel shading, half body, ")
NEG = ("lowres, worst quality, low quality, bad anatomy, bad hands, extra digits, "
       "fewer digits, extra limbs, extra arms, extra legs, deformed, watermark, "
       "signature, username, artist name, text, logo, jpeg artifacts, cropped, "
       "out of frame, mature female, tall, huge breasts, realistic, 3d, cgi, "
       # 开屏图要干净：NoobAI 用了 e621 语料，压一下露骨内容
       "nsfw, explicit, nude, nipples, pussy, sex, cum, censored")
# 钻头马尾（重音テト）专用负向词：多出来的发束/多余钻头主要靠这个压住，
# 正向只要平铺 "twintails, drill hair" 就好——给头发堆权重反而会长出 4~6 个钻头。
HAIR_NEG = ("straight hair, long straight hair, wavy hair, half-updo, "
            "extra hair, multiple twintails, four twintails, extra drills, "
            "extra hair bunches, extra hair strands")

# (键名, 正向提示词, 该张额外的负向词)
JOBS = [
    ("miku_wall",
     "1girl, solo, hatsune miku, aqua twintails, very long hair, detached sleeves, "
     "necktie, white shirt, black skirt, looking at viewer, smiling, "
     "photo studio, corkboard covered with polaroid photos behind her, desk with printed "
     "photos, warm light, teal color theme, character on the left half of the image",
     ""),
    ("miku_desk",
     "1girl, solo, hatsune miku, aqua twintails, very long hair, headphones around neck, "
     "sitting at a desk, looking at viewer, computer monitor showing image thumbnails, "
     "small simple keyboard in front of the monitor, holding printed photo, warm desk lamp, "
     "cozy room, night, character on the left, monitor on the right",
     "messy lines, scribbled details, tangled wires, cables, cluttered desk, "
     "garbled keys, extra keys, duplicated keys"),
    ("miku_swim",
     "1girl, solo, hatsune miku, aqua twintails, very long hair, (bikini:1.5), "
     "(string bikini:1.35), (triangle bikini top:1.3), side-tie bikini bottom, "
     "halterneck bikini top, bare shoulders, navel, standing, "
     "looking at viewer, beach, sand, ocean, blue sky, sunny, day, seagulls, "
     "character on the left, sea on the right",
     "dress, one-piece swimsuit, swimsuit, leotard, school uniform, skirt, clothes, "
     "jacket, shirt, covered navel"),
    ("teto_baguette",
     "1girl, solo, kasane teto, red hair, very long hair, ahoge, twintails, drill hair, "
     "school uniform, holding baguette, bread, looking at viewer, bakery, "
     "wooden shelves with bread behind, warm light, character on the left",
     HAIR_NEG),
    ("teto_bunny",
     "1girl, solo, kasane teto, red hair, very long hair, ahoge, twintails, drill hair, "
     "bunny girl, "
     "black leotard, (high-cut leotard:1.2), patent leather leotard, strapless, "
     "bare shoulders, rabbit ears, rabbit tail, bow tie, black thighhighs, "
     "lying on stomach, prone, propped up on elbows, hand supporting head, chin on hand, "
     "looking at viewer, head tilt, medium shot, studio, spotlight, red stage backdrop, "
     "character on the left",
     "shoulder straps, halterneck, skirt, standing, kneeling, sleeves, "
     "card, sign, blank frame, black box, rectangle, poster, "
     "fishnet, fishnet stockings, netting, " + HAIR_NEG),
    ("teto_tree",
     "1girl, solo, kasane teto, red hair, very long hair, ahoge, twintails, drill hair, "
     "casual clothes, "
     "holding photo cards, looking at viewer, big tag tree diagram on a kanban board behind, "
     "whiteboard with connected cards, indoor, red color theme, "
     "character on the left",
     HAIR_NEG),
    ("duo_bunny",
     "2girls, hatsune miku, kasane teto, bunny girl, black leotard, "
     "(high-cut leotard:1.2), patent leather leotard, strapless, bare shoulders, "
     "rabbit ears, rabbit tail, bow tie, black thighhighs, aqua twintails, "
     "red drill twintails, standing side by side, holding hands, interlocked fingers, "
     "looking at viewer, photo cards on the wall behind, studio lighting, medium shot, "
     "character on the left",
     "shoulder straps, halterneck, skirt, solo, 1girl, sleeves, "
     "fishnet, fishnet stockings, netting"),
]


def api(path: str, data=None):
    url = HOST + path
    if data is None:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


class ComfyDown(RuntimeError):
    """ComfyUI 进程没了（连续连不上）——调用方重启它之后再重跑即可。"""


def _snap(n: float) -> int:
    return max(64, int(round(n / 8)) * 8)


def build(ckpt: str, prompt: str, neg_extra: str, seed: int,
          hires: float = HIRES, denoise: float = HIRES_DENOISE,
          steps: int = STEPS, sampler: str = "dpmpp_2m", cfg: float = CFG,
          sharpen: tuple[float, float, float] | None = None) -> dict:
    """底图 + 二段放大（img2img 小幅重绘）：构图基本不变，细节和线条更干净。"""
    neg = NEG + (", " + neg_extra if neg_extra else "")
    g = {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
            "scheduler": "karras", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": QUALITY + prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
    }
    last = "3"
    if hires and hires > 1.01:
        g["10"] = {"class_type": "LatentUpscale", "inputs": {
            "upscale_method": "bislerp", "width": _snap(W * hires), "height": _snap(H * hires),
            "crop": "disabled", "samples": ["3", 0]}}
        g["11"] = {"class_type": "KSampler", "inputs": {
            "seed": seed + 7, "steps": max(12, int(steps * 0.6)), "cfg": 5.5,
            "sampler_name": sampler, "scheduler": "karras", "denoise": denoise,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}}
        last = "11"
    g["8"] = {"class_type": "VAEDecode", "inputs": {"samples": [last, 0], "vae": ["4", 2]}}
    src = "8"
    if sharpen:
        radius, sigma, alpha = sharpen
        g["20"] = {"class_type": "ImageSharpen", "inputs": {
            "image": ["8", 0], "sharpen_radius": int(radius),
            "sigma": float(sigma), "alpha": float(alpha)}}
        src = "20"
    g["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "splash", "images": [src, 0]}}
    return g


def wait_and_save(pid: str, dst: Path) -> bool:
    t0 = time.time()
    fails = 0
    while time.time() - t0 < 300:
        time.sleep(3)
        try:
            hist = api(f"/history/{pid}")
            fails = 0
        except Exception:
            fails += 1
            if fails >= 5:                  # ComfyUI 崩了：别在这儿干等 300 秒
                raise ComfyDown("ComfyUI 连续 5 次连不上，判定已崩")
            continue
        if pid in hist:
            for node in hist[pid].get("outputs", {}).values():
                for img in node.get("images", []):
                    q = urllib.parse.urlencode({"filename": img["filename"],
                                                "subfolder": img.get("subfolder", ""),
                                                "type": img.get("type", "output")})
                    with urllib.request.urlopen(f"{HOST}/view?{q}", timeout=120) as r:
                        dst.write_bytes(r.read())
                    return True
            return False
    return False


def main() -> int:
    global W, H
    args = sys.argv[1:]
    ckpt = args[0] if args else DEFAULT_CKPT
    variants = int(args[1]) if len(args) > 1 else 2
    only = [s.strip() for s in (args[2].split(",") if len(args) > 2 and args[2] else []) if s.strip()]
    if len(args) > 3 and args[3]:
        W = max(512, int(args[3]))
        H = int(round(W / ASPECT / 8)) * 8
    out = out_dir(ckpt, args[4] if len(args) > 4 else "")
    try:
        names = api("/object_info/CheckpointLoaderSimple")["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        if ckpt not in names:
            print("没找到底模", ckpt, "，可用：", names[:8])
            return 1
    except Exception as exc:
        print("连不上 ComfyUI:", exc)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    print(f"底模: {ckpt}  尺寸: {W}x{H}  每张 {variants} 版  产物目录: {out}")
    letters = "abcdefgh"
    force = os.environ.get("SPLASH_FORCE", "") not in ("", "0", "false")
    for i, (tag, prompt, neg_extra) in enumerate(JOBS, 1):
        if only and not any(tag.startswith(o) or f"{i:02d}" == o for o in only):
            continue
        for v in range(variants):
            seed = 20261006 + i * 977 + v * 41
            dst = out / f"{i:02d}_{tag}_{letters[v]}.png"
            # 已出过的直接跳过：被打断后重跑不浪费（要覆盖就设环境变量 SPLASH_FORCE=1）
            if dst.exists() and dst.stat().st_size > 0 and not force:
                print(f"[{i}/{len(JOBS)}] {tag}_{letters[v]} 已存在，跳过 ({dst.name})")
                continue
            try:
                res = api("/prompt", {"prompt": build(ckpt, prompt, neg_extra, seed)})
                pid = res["prompt_id"]
                t0 = time.time()
                ok = wait_and_save(pid, dst)
            except ComfyDown as exc:
                print(f"[{i}/{len(JOBS)}] {tag}_{letters[v]} 中止：{exc}（重跑会跳过已出的图）")
                return 2
            except Exception as exc:
                print(f"[{i}/{len(JOBS)}] {tag}{letters[v]} 提交失败: {exc}")
                continue
            print(f"[{i}/{len(JOBS)}] {tag}_{letters[v]} "
                  f"{'完成' if ok else '失败'} {time.time()-t0:.0f}s → {dst.name}")
    print("全部完成，产物在:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
