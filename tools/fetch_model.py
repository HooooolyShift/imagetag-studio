"""下载大模型文件到本机（断点续传 + 自动走系统代理），给 ComfyUI 增加底模/VAE/LoRA 用。

用法：
    python tools\\fetch_model.py <URL> <目标文件路径> [--sha256 <期望值>] [--proxy <地址>]

例：
    python tools\\fetch_model.py "https://civitai.com/api/download/models/2883731?fileId=2763986" ^
        "E:\\ComfyUI\\ComfyUI-aki-v1.5\\ComfyUI-aki-v1.5\\models\\checkpoints\\WAI-illustrious-SDXL-v17.safetensors"

为什么自己写这个小工具（2026-10-07 实测）：
  · 本机**直连 civitai.com 不通**（curl 报 connect timeout），但系统代理可用
    （`http://127.0.0.1:29758`，与 git push 用的是同一个）；`curl.exe` 不读 Windows 系统代理设置，
    必须手动 `-x`，`Invoke-WebRequest` 又不方便续传；
  · `urllib` 在 Windows 上会自动读注册表里的代理设置，再自己实现续传/重试/进度最省事。
6.5 GB 的底模必下 20~40 分钟，断点续传是刚需（网络抖一下就白下）。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1 << 20          # 1 MiB
UA = "Mozilla/5.0 (fetch_model.py)"

# 日志里有中文（进度/重试提示），管道下默认可能是 GBK 导致乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                    # noqa: BLE001
    pass


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def hms(seconds: float) -> str:
    if seconds <= 0 or seconds > 86400 * 3:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _request(url: str, pos: int) -> urllib.request.Request:
    headers = {"User-Agent": UA}
    if pos:
        headers["Range"] = f"bytes={pos}-"
    return urllib.request.Request(url, headers=headers)


def _total(resp, pos: int, fallback: int) -> int:
    """返回文件总长度（拿不到就返回 0）。"""
    cr = resp.headers.get("Content-Range")
    if cr and "/" in cr:
        try:
            return int(cr.rsplit("/", 1)[1])
        except ValueError:
            return 0
    cl = resp.headers.get("Content-Length")
    if cl:
        try:
            return pos + int(cl)
        except ValueError:
            return fallback
    return fallback


def download(url: str, dst: Path, sha256: str = "", proxy: str = "",
             max_retry: int = 100) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    attempt = 0
    while True:
        pos = dst.stat().st_size if dst.exists() else 0
        if total and pos >= total:
            break
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy
            else urllib.request.ProxyHandler())
        try:
            resp = opener.open(_request(url, pos), timeout=60)
        except Exception as exc:                       # noqa: BLE001
            attempt += 1
            if attempt > max_retry:
                raise
            print(f"[重试 {attempt}/{max_retry}] 连接失败：{exc}", flush=True)
            time.sleep(min(30, 5 * attempt))
            continue
        with resp:
            code = getattr(resp, "status", resp.getcode())
            if pos and code == 200:                    # 服务器不支持续传 → 从头来
                print("服务器不支持续传，从头开始", flush=True)
                dst.unlink()
                pos = 0
            total = _total(resp, pos, total)
            mode = "ab" if pos else "wb"
            done = pos
            t0 = time.time()
            last = t0
            try:
                with open(dst, mode) as fh:
                    while True:
                        buf = resp.read(CHUNK)
                        if not buf:
                            break
                        fh.write(buf)
                        done += len(buf)
                        now = time.time()
                        if now - last >= 5:            # 每 5 秒报一次进度
                            speed = (done - pos) / max(1e-6, now - t0)
                            pct = f"{(done / total * 100):5.1f}%" if total else "  ?  "
                            eta = (f"剩 {hms((total - done) / speed)}"
                                   if total and speed > 0 else "")
                            print(f"{pct} {human(done)}/{human(total) if total else '?'} "
                                  f"@{human(speed)}/s {eta}", flush=True)
                            last = now
            except Exception as exc:                   # noqa: BLE001
                attempt += 1
                if attempt > max_retry:
                    raise
                print(f"[重试 {attempt}/{max_retry}] 传输中断：{exc}", flush=True)
                time.sleep(min(30, 5 * attempt))
                continue
        attempt = 0
        pos = dst.stat().st_size
        if total and pos < total:
            print(f"未收全（{human(pos)}/{human(total)}），继续续传", flush=True)
            continue
        break
    size = dst.stat().st_size
    print(f"完成：{dst}  {human(size)}", flush=True)
    if sha256:
        print("校验 sha256 …", flush=True)
        h = hashlib.sha256()
        with open(dst, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 22), b""):
                h.update(block)
        got = h.hexdigest()
        if got.lower() == sha256.lower():
            print("sha256 一致", flush=True)
        else:
            print(f"sha256 不一致！期望 {sha256}，实际 {got}", flush=True)
            return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="带断点续传的模型下载器")
    ap.add_argument("url")
    ap.add_argument("dst", help="目标文件（父目录会自动创建）")
    ap.add_argument("--sha256", default="", help="下载后校验（可选）")
    ap.add_argument("--proxy", default="", help="留空=用系统代理（推荐）")
    args = ap.parse_args()
    try:
        return download(args.url, Path(args.dst), args.sha256, args.proxy)
    except KeyboardInterrupt:
        print("已中断（下次运行会自动续传）", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
