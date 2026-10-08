"""把项目自带的 danbooru 中英词表转成 SwarmUI 的提示词自动补全词表。

输入：app/booru_zh/{character,copyright,general,meta}.csv，列：tag,category,aliases,zh,count,notes
输出：E:\\SwarmUI\\Data\\Autocompletions\\<名字>.csv，列：tag,category,count,（SwarmUI 认这四列；
      category 0-5 用于分类着色，count 用于按热度排序）

用法：python tools\\build_swarm_autocomplete.py [输出文件名=danbooru_zh.csv]
（生成后在 SwarmUI 的 User Settings → AutoCompletionsSource 里选这个文件；
  或由本仓库的脚本直接写进 Settings.fds，见 docs/技术文档_3_AI生图.md）
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "booru_zh"
OUT_DIR = Path(r"E:\SwarmUI\Data\Autocompletions")


def rows_from(name: str):
    path = SRC / f"{name}.csv"
    if not path.exists():
        return []
    text = path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.DictReader(text.split("\n"))
    out = []
    for row in reader:
        tag = (row.get("tag") or "").strip()
        if not tag or tag == "tag":
            continue
        cat = (row.get("category") or "0").strip() or "0"
        try:
            count = int(float((row.get("count") or "0").strip() or 0))
        except ValueError:
            count = 0
        out.append((tag, cat, count))
    return out


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "danbooru_zh.csv"
    all_rows = []
    for part in ("character", "copyright", "general", "meta"):
        part_rows = rows_from(part)
        print(f"{part}: {len(part_rows)} 条")
        all_rows.extend(part_rows)
    # 去重（同一 tag 取最大 count）
    best: dict[str, tuple[str, int]] = {}
    for tag, cat, count in all_rows:
        old = best.get(tag)
        if old is None or count > old[1]:
            best[tag] = (cat, count)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / name
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for tag, (cat, count) in sorted(best.items()):
            fh.write(f"{tag},{cat},{count},\n")
    print(f"写出 {out_path}：{len(best)} 条（去重后）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
