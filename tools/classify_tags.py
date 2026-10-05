"""用本机离线模型把「其它」分类的标签归到已有类型里。

用法： python tools\\classify_tags.py [每批数量，默认 60]
结果直接写回 tags.category（只动 category='other' 的标签）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from app.config import TAG_CATEGORIES  # noqa: E402
from app.store import Store            # noqa: E402
from app import categories as _cats    # noqa: E402

ASK = Path(r"D:\LocalAI\ask.py")
PY = Path(r"D:\Anaconda\python.exe")
MODEL = os.environ.get("IMGTAG_CLASSIFY_MODEL", "qwen2.5-3b")
TMP = HERE / "build" / "classify"

def build_prompt(store) -> tuple[str, list[str]]:
    """按数据库里的类型表生成分类说明（新增类型会自动带上）。"""
    items = [(c["key"], c["label"]) for c in _cats.ordered(store)]
    rubric = "\n".join(f"- {k} = {l}" for k, l in items)
    prompt = ("你是图像标签分类器。下面每行是一个英文图像标签（下划线连接，来自 Danbooru/WD14 词表）。"
              "请把它归到下面某一类里，只能选一个：\n" + rubric + "\n"
              "判断要点：具体物品（苹果、相机、汽车、蜡烛、衣架…）归 object；"
              "性器官/身体细节归 body_detail；界面文字/水印/表情符号归 text_ui；"
              "节日活动相关归 event；确实无法归类才写 other。\n"
              "性行为/亲密互动（groping、incest、dildo、threesome、paizuri…）归 interaction；"
              "镜头角度与构图（dutch_angle、close-up、from_above、wide_shot…）归 view。\n"
              "输出格式：每行写「标签=类别key」，顺序与输入一致，不要解释、不要编号。\n输入：\n")
    return prompt, [k for k, _ in items]


def classify_batch(tags: list[str], prompt: str, valid: list[str]) -> dict[str, str]:
    TMP.mkdir(parents=True, exist_ok=True)
    fin, fout = TMP / "in.txt", TMP / "out.txt"
    fin.write_text("\n".join(tags), encoding="utf-8")
    if fout.exists():
        fout.unlink()
    cmd = [str(PY), str(ASK), "-f", str(fin), "-o", str(fout)]
    if MODEL:
        cmd += ["-m", MODEL]
    cmd.append(prompt)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1200)
    out: dict[str, str] = {}
    if r.returncode != 0 or not fout.exists():
        return out
    for line in fout.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if "=" not in line:
            continue
        k, v = [x.strip().strip('"\'') for x in line.split("=", 1)]
        v = v.lower().replace(" ", "_")
        if not k:
            continue
        if v not in valid:                          # 模型可能直接写中文类别名
            for key, label in TAG_CATEGORIES.items():
                if label in v or v in label.lower():
                    v = key
                    break
        if v in valid:
            out[k.lower()] = v
    return out


def main() -> int:
    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    s = Store()
    # 1) 补上需要的新类型（道具/身体细节/文本界面/节日）
    for label, key, tmpl in (("道具/物品", "object", ["a {} object", "{}", "an image of {}"]),
                             ("身体细节", "body_detail", ["{}", "close-up of {}"]),
                             ("文本/界面", "text_ui", ["{}", "{} text or interface element"]),
                             ("节日/活动", "event", ["{}", "a {} themed image"]),
                             ("性行为/互动", "interaction", ["{}", "{} between two people", "sexual {}"]),
                             ("视角/构图", "view", ["{}", "{} camera angle", "{} composition"])):
        if not s.one("SELECT 1 FROM categories WHERE key=?", (key,)):
            _cats.add(s, label, tmpl, key=key)
            print(f"新增类型：{label}({key})", flush=True)
    prompt, valid = build_prompt(s)
    rows = s.query("SELECT id,name FROM tags WHERE category IN ('other','') ORDER BY name")
    print(f"待分类标签 {len(rows)} 个", flush=True)
    done, t0 = 0, time.time()
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        names = [r["name"] for r in batch]
        got = {}
        for attempt in range(3):
            try:
                got = classify_batch(names, prompt, valid)
            except Exception as e:  # noqa: BLE001
                print(f"  批次 {i // batch_size + 1} 失败: {e}", flush=True)
                got = {}
            if len(got) >= max(1, len(batch) // 3):
                break
            time.sleep(3)
        n = 0
        for r in batch:
            v = got.get(r["name"].lower())
            if v and v != "other":
                s.update_tag(int(r["id"]), category=v)
                n += 1
        s.refresh_counts()
        done += len(batch)
        rate = done / max(1, time.time() - t0)
        print(f"  已处理 {done}/{len(rows)}（本批归类 {n}/{len(batch)}，{rate:.1f} 个/秒）", flush=True)
    dist = {r["category"]: r["c"] for r in
            s.query("SELECT category, COUNT(*) c FROM tags GROUP BY category ORDER BY c DESC")}
    print("最终分类分布:", dist, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
