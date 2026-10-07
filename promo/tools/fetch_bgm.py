"""从塞壬唱片官网（monster-siren.hypergryph.com）下载指定曲目（官方源，可分发使用）。

官网接口（2026-10 实测）：
    /api/albums                全部专辑（cid / name / coverUrl / artistes）
    /api/album/{cid}/detail    专辑详情（含 songs，但只有 cid / name / artistes）
    /api/song/{sid}            单曲详情（**sourceUrl 在这里**，通常是 WAV）

用法：
    python fetch_bgm.py endospore endospore      # 专辑关键字 曲名关键字
产物：promo\\audio\\BGM_<曲名>.wav + bgm_source.json（记录来源，便于追溯授权）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
OUT = PROJECT / "promo" / "audio"
BASE = "https://monster-siren.hypergryph.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0",
           "Referer": "https://monster-siren.hypergryph.com/"}

WANT_ALBUM = sys.argv[1] if len(sys.argv) > 1 else "endospore"
WANT_SONG = sys.argv[2] if len(sys.argv) > 2 else "endospore"


def get(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    albums = get(f"{BASE}/albums")["data"]
    print(f"官网专辑 {len(albums)} 张，找专辑关键字：{WANT_ALBUM}")
    hits = [a for a in albums if WANT_ALBUM.lower() in str(a["name"]).lower()]
    if not hits:
        print("专辑没命中，改为在前 200 张里搜曲名 …")
        for a in albums[:200]:
            try:
                detail = get(f"{BASE}/album/{a['cid']}/detail")["data"]
            except Exception:  # noqa: BLE001
                continue
            if any(WANT_SONG.lower() in str(s["name"]).lower()
                   for s in detail.get("songs", [])):
                hits.append(a)
                break
    for album in hits:
        print(f"  命中专辑：{album['name']}（cid={album['cid']}）")
    if not hits:
        print("没找到目标专辑/曲目")
        return 1

    for album in hits:
        detail = get(f"{BASE}/album/{album['cid']}/detail")["data"]
        print(f"专辑《{detail['name']}》共 {len(detail.get('songs', []))} 首")
        target = next((s for s in detail["songs"]
                       if WANT_SONG.lower() in str(s["name"]).lower()), None)
        if target is None and len(detail["songs"]) == 1:
            target = detail["songs"][0]
        if target is None:
            continue
        song = get(f"{BASE}/song/{target['cid']}")["data"]
        url = song.get("sourceUrl")
        if not url:
            print(f"  {target['name']} 没有可下载音频")
            continue
        print(f"下载 {song['name']} …")
        resp = requests.get(url, headers=HEADERS, timeout=300)
        resp.raise_for_status()
        ext = Path(url.split("?")[0]).suffix or ".mp3"
        dst = OUT / f"BGM_{song['name']}{ext}"
        dst.write_bytes(resp.content)
        (OUT / "bgm_source.json").write_text(json.dumps({
            "album": detail["name"], "album_cid": detail["cid"],
            "song": song["name"], "song_cid": song["cid"],
            "artists": song.get("artists"), "url": url,
            "note": "塞壬唱片官网官方发行，下载自 monster-siren.hypergryph.com",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已保存 {dst}  {len(resp.content)/1024/1024:.1f} MB")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
