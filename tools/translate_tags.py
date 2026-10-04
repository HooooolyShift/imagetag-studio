"""用本机离线模型（D:\\LocalAI，qwen3-8b）把 WD14 英文标签批量翻成中文。

产物：app/tag_zh_dict.json —— 程序启动时自动加载，界面就显示「中文（英文）」。
特点：断点续跑（已翻过的跳过）、分批送模型、失败自动回退到规则翻译。

用法： python tools\\translate_tags.py [每批数量，默认 80]
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
DICT = HERE / "app" / "tag_zh_dict.json"
ASK = Path(r"D:\LocalAI\ask.py")
PY = Path(r"D:\Anaconda\python.exe")
CSV = HERE / "models" / "wd-swinv2-tagger-v3" / "selected_tags.csv"
TMP = HERE / "build" / "trans"
MODEL = os.environ.get("IMGTAG_TRANSLATE_MODEL", "qwen2.5-3b")   # 3B 更快；设空字符串用默认 8B

PROMPT = ("下面是英文图像标签（Danbooru 风格，用下划线连接）。"
          "请逐个翻译成简体中文，要求：\n"
          "1) 只输出「英文=中文」一行一个，顺序与输入完全一致，不要编号、不要解释、不要空行；\n"
          "2) 中文要短（2~6 字），是名词短语，不要翻译成句子；\n"
          "3) 专有名词/角色名/画师名如果无法翻译，右侧照抄英文原文。\n"
          "输入：\n")


def load_dict() -> dict:
    if DICT.exists():
        try:
            return json.loads(DICT.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def all_tags() -> list[str]:
    """待翻译标签：WD14 全词表（通用+角色）+ 本机数据库里出现过的标签。"""
    tags: list[str] = []
    if CSV.exists():
        for row in csv.DictReader(open(CSV, encoding="utf-8")):
            tags.append(row["name"])
    try:
        from app.store import Store
        s = Store()
        tags += [r["name"] for r in s.query("SELECT name FROM tags")]
    except Exception:
        pass
    seen, out = set(), []
    for t in tags:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def translate_batch(batch: list[str]) -> dict[str, str]:
    """把一批标签送本地模型，返回 {tag: 中文}。"""
    TMP.mkdir(parents=True, exist_ok=True)
    fin, fout = TMP / "in.txt", TMP / "out.txt"
    fin.write_text("\n".join(batch), encoding="utf-8")
    if fout.exists():
        fout.unlink()
    cmd = [str(PY), str(ASK), "-f", str(fin), "-o", str(fout)]
    if MODEL:
        cmd += ["-m", MODEL]
    cmd.append(PROMPT)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    out: dict[str, str] = {}
    if r.returncode != 0 or not fout.exists():
        return out
    for line in fout.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().strip('"\''), v.strip().strip('"\'')
        if k and v and not v.startswith("抱歉"):
            out[k.lower()] = v[:24]
    return out


def main() -> int:
    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    data = load_dict()
    tags = all_tags()
    todo = [t for t in tags if t.lower() not in data]
    print(f"标签总数 {len(tags)}，已有中文 {len(data)}，待翻 {len(todo)}", flush=True)
    from app import tag_i18n
    done0, t0 = 0, time.time()
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        got = {}
        for attempt in range(3):
            try:
                got = translate_batch(batch)
            except Exception as e:  # noqa: BLE001
                print(f"  批次 {i // batch_size + 1} 第 {attempt + 1} 次失败: {e}", flush=True)
                got = {}
            if len(got) >= max(1, len(batch) // 3):
                break
            time.sleep(3)
        # 模型没给出的，退回规则翻译（组合词表）
        for t in batch:
            v = got.get(t.lower())
            if not v:
                r = tag_i18n.translate(t)
                v = r if r != t else ""
            if v:
                data[t] = v
        DICT.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        done0 += len(batch)
        rate = done0 / max(1, time.time() - t0)
        print(f"  已处理 {done0}/{len(todo)}（本批模型返回 {len(got)}/{len(batch)}，"
              f"字典累计 {len(data)}，{rate:.1f} 个/秒）", flush=True)
    print(f"完成：字典 {len(data)} 条 → {DICT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
