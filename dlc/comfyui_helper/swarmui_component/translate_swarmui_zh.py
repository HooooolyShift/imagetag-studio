"""用本机 Ollama 把 SwarmUI 界面缺的中文补上（补齐 languages/zh.json）。

背景：SwarmUI 自带的 zh.json 只有 672 条，界面里真正会去查的字符串有 1000+，
所以大量参数注释/提示一直是英文。这个脚本把界面字符串抓下来（由浏览器侧导出 JSON），
用本机 Ollama 翻译，合并进 zh.json（会先备份）。

用法：
    # 1) 先翻译（可反复运行，已翻过的会跳过；断电中断也能接着跑）
    python tools\\translate_swarmui_zh.py --dom-file .verify_pics\\dom_strings2.json --cache .verify_pics\\zh_new.json
    # 2) 翻译完合并进 SwarmUI（会备份成 zh.json.bak-<时间>）
    python tools\\translate_swarmui_zh.py --dom-file .verify_pics\\dom_strings2.json --cache .verify_pics\\zh_new.json --merge

说明：
  · 字符串以 "T:"(title) / "P:"(placeholder) / "X:"(元素文本) 前缀区分，合并时按 SwarmUI
    实际查表的形态写入：title/placeholder 用原文，元素文本也用原文（SwarmUI 查 textContent）。
  · 模型默认 qwen3-8b（本机最好且能塞进 8G 显存）；Ollama 在 http://127.0.0.1:11434。
  · 翻译要求：术语不译（LoRA/ControlNet/CFG/VAE/CLIP/IP-Adapter/Sampler/Prompt/Seed 等），
    保留占位符与数字，语气贴近软件 UI。
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434/api/chat"
DEFAULT_ZH = Path(r"E:\SwarmUI\languages\zh.json")

SYSTEM = (
    "你是 Stable Diffusion / AI 绘图软件界面的本地化译者。把用户给的英文界面文本翻译成简体中文。"
    "规则：1) 专有名词保持原样或按中文社区惯例：LoRA、ControlNet、CFG、VAE、CLIP、IP-Adapter、"
    "Sampler、Scheduler、Prompt、Negative Prompt、Seed、Batch、Refiner、Upscale、SDXL、Flux、"
    "SwarmUI、ComfyUI、TensorRT、API；2) 界面语气简洁，'Enable/disable X' 译作 '启用/禁用X'；"
    "3) 保留括号、占位符、数字、单位、路径；4) 只输出 JSON 对象，键与输入一致，值只放译文，不要解释。"
)


def ollama_chat(model: str, payload: dict) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 3000},
    }
    req = urllib.request.Request(
        OLLAMA, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.load(resp)
    return data["message"]["content"]


def parse_json_reply(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回复里没有 JSON")
    return json.loads(text[start:end + 1])


def looks_translatable(s: str) -> bool:
    if not re.search(r"[A-Za-z]", s):
        return False
    if re.search(r"[\u4e00-\u9fff]", s):        # 已经是中文，跳过
        return False
    if len(s) > 800:
        return False
    if re.fullmatch(r"[\d\s.,:%/()\[\]+\-]+", s):
        return False
    if re.search(r"\.(safetensors|ckpt|pt|pth|png|jpe?g|webp|json|yaml|txt)$", s, re.I):
        return False
    if s.startswith(("http://", "https://", "C:\\", "E:\\", "--")):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dom-file", required=True, help="浏览器导出的界面字符串 JSON（带 T:/P:/X: 前缀）")
    ap.add_argument("--cache", required=True, help="翻译结果缓存（可断点续跑）")
    ap.add_argument("--model", default="qwen3-8b:latest")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0, help="本次最多翻多少条（0=全部）")
    ap.add_argument("--zh-file", default=str(DEFAULT_ZH))
    ap.add_argument("--merge", action="store_true", help="把缓存合并进 SwarmUI 的 zh.json")
    args = ap.parse_args()

    cache_path = Path(args.cache)
    cache: dict[str, str] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    if args.merge:
        zh_path = Path(args.zh_file)
        zh = json.loads(zh_path.read_text(encoding="utf-8"))
        keys = zh["keys"]
        added = 0
        for raw, trans in cache.items():
            text = raw[2:] if raw[:2] in ("T:", "P:", "X:") else raw
            if text and trans and keys.get(text) != trans:
                keys[text] = trans
                added += 1
        backup = zh_path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_text(zh_path.read_text(encoding="utf-8"), encoding="utf-8")
        zh_path.write_text(json.dumps(zh, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"合并完成：新增/更新 {added} 条，共 {len(keys)} 条；备份 {backup.name}")
        return 0

    dom = json.loads(Path(args.dom_file).read_text(encoding="utf-8"))
    todo = [s for s in dom if s not in cache and looks_translatable(s[2:] if s[:2] in ("T:", "P:", "X:") else s)]
    print(f"待翻译 {len(todo)} 条（缓存已有 {len(cache)} 条）", flush=True)
    if args.limit:
        todo = todo[:args.limit]

    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        payload = {str(n): (s[2:] if s[:2] in ("T:", "P:", "X:") else s) for n, s in enumerate(batch)}
        for attempt in range(1, 4):
            try:
                reply = ollama_chat(args.model, payload)
                got = parse_json_reply(reply)
                break
            except Exception as exc:                      # noqa: BLE001
                print(f"  批次 {i//args.batch+1} 第 {attempt} 次失败：{exc}", flush=True)
                time.sleep(3)
        else:
            print(f"  批次 {i//args.batch+1} 放弃", flush=True)
            continue
        for n, src in enumerate(batch):
            trans = got.get(str(n))
            if isinstance(trans, str) and trans.strip():
                cache[src] = trans.strip()
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  批次 {i//args.batch+1}/{(len(todo)+args.batch-1)//args.batch} 完成，"
              f"累计 {len(cache)} 条", flush=True)
    print(f"翻译结束，缓存共 {len(cache)} 条 → {cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
