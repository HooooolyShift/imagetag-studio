"""批量出图（给计划任务调用，脱离 Agent 会话进程树，长任务不会被打断）。

用法：
    python tools\\batch_gen.py --models <底模1,底模2> [--variants 2] [--width 1536]
                              [--only 01,05] [--log <日志路径>] [--free]

它做四件事：
  1) ComfyUI 不在 127.0.0.1:8188 就按标准命令拉起来，等到就绪（最多 180 秒）；
  2) 对每个底模调用 `gen_splash.py`（**已存在的图会跳过**，所以被打断后重跑是安全的）；
  3) `--free` 时结束后 `POST /free` 把底模从显存卸掉，给用户让位；
  4) 全程写 `--log` 指定的日志，结尾写 `ALL DONE`，外部据此轮询进度。

为什么要有这个文件：本机 Agent 会话是"工具调用被新消息打断时，它启动的进程会被一起杀掉"，
直接跑几分钟的出图很容易半路死掉。交给 Windows 计划任务跑就与会话解耦了
（注册/启动命令见 `docs\\技术文档_3_AI生图.md`）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
COMFY = Path(r"E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5")
HOST = "http://127.0.0.1:8188"

import gen_splash  # noqa: E402


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:                        # noqa: BLE001
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:                        # noqa: BLE001
                pass


def comfy_up() -> bool:
    try:
        with urllib.request.urlopen(HOST + "/system_stats", timeout=4):
            return True
    except Exception:                                # noqa: BLE001
        return False


def ensure_comfy(log) -> bool:
    if comfy_up():
        print("[batch] ComfyUI 已在运行", file=log)
        return True
    print("[batch] 拉起 ComfyUI …", file=log)
    subprocess.Popen(
        [str(COMFY / "python" / "python.exe"), "main.py", "--listen", "127.0.0.1", "--port", "8188"],
        cwd=str(COMFY),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    for _ in range(36):                              # 最多等 180 秒
        time.sleep(5)
        if comfy_up():
            print("[batch] ComfyUI 就绪", file=log)
            return True
    print("[batch] ComfyUI 起不来，放弃", file=log)
    return False


def free_vram(log) -> None:
    body = b'{"unload_models":true,"free_memory":true}'
    req = urllib.request.Request(HOST + "/free", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            print("[batch] 已请求卸载模型、归还显存", file=log)
    except Exception as exc:                         # noqa: BLE001
        print(f"[batch] 释放显存失败：{exc}", file=log)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="逗号分隔的底模文件名")
    ap.add_argument("--variants", type=int, default=2)
    ap.add_argument("--width", type=int, default=1536)
    ap.add_argument("--only", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--free", action="store_true")
    args = ap.parse_args()

    log = open(args.log, "a", encoding="utf-8") if args.log else sys.stdout
    if args.log:
        sys.stdout = Tee(sys.__stdout__, log)
    try:
        print(f"\n===== [batch] 开始 {time.strftime('%F %T')} =====")
        if not ensure_comfy(sys.stdout):
            return 1
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            print(f"\n[batch] >>> 底模 {model}（每张 {args.variants} 版，宽 {args.width}）")
            sys.argv = [str(ROOT / "tools" / "gen_splash.py"), model,
                        str(args.variants), args.only, str(args.width)]
            try:
                gen_splash.main()
            except Exception as exc:                 # noqa: BLE001
                print(f"[batch] 底模 {model} 出图出错：{exc}")
        if args.free:
            free_vram(sys.stdout)
        print(f"===== [batch] ALL DONE {time.strftime('%F %T')} =====")
        return 0
    finally:
        if args.log:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
