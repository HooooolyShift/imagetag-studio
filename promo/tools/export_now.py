"""把当前 Premiere 时间线导出成文件。

用法：
    python export_now.py [输出文件名] [预设路径]
      · 不传输出文件名 -> promo/premiere/v15_master.mov
      · 不传预设        -> 系统的 H.264 "Match Source - High bitrate"
    ProRes 母版（给 NVENC 用）：
    python export_now.py v15_master.mov "D:\\PR2024\\...\\Apple ProRes 422 HQ.epr"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from premiere_client import PremiereClient, es  # noqa: E402

WORK = Path(r"E:\文档\ChatGPT\图片标签分类\promo\premiere")
DEFAULT_OUT = "v15_master.mov"
STOCK_H264 = (r"D:\PR2024\Adobe Premiere Pro 2024\MediaIO\systempresets"
              r"\3F3F3F3F_4D6F6F56\H264 Match Source - High bitrate.epr")


def main() -> int:
    pr = PremiereClient(timeout=900)
    if not pr.alive():
        print("桥不在线：先启动 Premiere")
        return 1
    out = WORK / (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
    preset = sys.argv[2] if len(sys.argv) > 2 else STOCK_H264
    try:
        info = pr.eval("(function(){var s=app.project.activeSequence;"
                       "if(!s){return 'no sequence';}"
                       "var n=0;for(var t=0;t<s.videoTracks.numTracks;t++){n+=s.videoTracks[t].clips.numItems;}"
                       "return s.name+' | 镜头 '+n+' | '+String(254016000000/parseInt(s.timebase))+'fps';})()")
        print("序列:", info)
    except Exception as exc:  # noqa: BLE001 - 状态只是参考，取不到也继续导出
        print("序列状态查询跳过:", str(exc)[:120])
    print("预设:", Path(preset).name)
    if out.exists():
        out.unlink()
    print("开始导出 …", flush=True)
    print(str(pr.eval("(function(){var seq=app.project.activeSequence;"
                      f"return 'ret:'+seq.exportAsMediaDirect({es(str(out))},{es(preset)},0);}})()")))
    for i in range(180):
        time.sleep(5)
        if out.exists() and out.stat().st_size > 100_000:
            print(f"  [{i}] {out.stat().st_size/1024/1024:.0f} MB")
    print(f"完成：{out}  存在={out.exists()}  "
          f"{(out.stat().st_size/1024/1024 if out.exists() else 0):.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
