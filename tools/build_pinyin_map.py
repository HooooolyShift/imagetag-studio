"""构建离线拼音表：app/pinyin_zh.json（只保留我们词表/标签里用到的汉字）。

数据源：mozillazg/pinyin-data 的 pinyin.txt（Unihan 拼音）。
用途：中文标签支持拼音搜索（输入 you 也能搜到「憂」）。

用法： python tools\\build_pinyin_map.py
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
OUT = HERE / "app" / "pinyin_zh.json"
URL = "https://raw.githubusercontent.com/mozillazg/pinyin-data/master/pinyin.txt"
CJK = re.compile(r"[\u4e00-\u9fff]")


def main() -> int:
    from app import tag_i18n
    from app.store import Store
    # 1) 收集我们用到/可能会用到的汉字（词典 + 库里标签）
    chars: set[str] = set()
    for name, zh in tag_i18n.MODEL_DICT.items():
        chars.update(CJK.findall(str(zh)))
    try:
        s = Store()
        for r in s.query("SELECT name, zh FROM tags"):
            chars.update(CJK.findall(f"{r['name']}{r['zh'] or ''}"))
    except Exception:
        pass
    print(f"需要注音的字：{len(chars)} 个")
    # 2) 拉取全量表
    req = urllib.request.Request(URL, headers={"User-Agent": "ImageTagStudio/1.4"})
    with urllib.request.urlopen(req, timeout=180) as r:
        text = r.read().decode("utf-8", "replace")
    table: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^U\+([0-9A-Fa-f]+):\s*([^\s#]+)", line)
        if not m:
            continue
        ch = chr(int(m.group(1), 16))
        if ch not in chars:
            continue
        py = m.group(2).split(",")[0].strip()          # 多音字取第一个
        # 先把带调的字母（á ō ǔ …）拆成 ASCII + 组合符号，再去掉符号 —— 否则 á 会被整段删掉
        py = unicodedata.normalize("NFD", py)
        py = "".join(c for c in py if not unicodedata.combining(c))
        py = re.sub(r"[^a-zA-Z]", "", py).lower()
        if py:
            table[ch] = py
    OUT.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    print(f"已写入 {OUT.name}：{len(table)} 个字（覆盖 {len(table) * 100 // max(1, len(chars))}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
