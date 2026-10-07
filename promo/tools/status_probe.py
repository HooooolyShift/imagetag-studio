"""检查 Premiere 桥是否在线，并打印当前工程/序列状态。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from premiere_client import PremiereClient  # noqa: E402


def main() -> int:
    pr = PremiereClient(timeout=30)
    print("桥在线:", pr.alive())
    if not pr.alive():
        return 1
    code = (
        "(function(){"
        "if(!app.project){return {project:null, note:'没有打开任何工程'};}"
        "var o={project:app.project.name,path:app.project.path,seqs:[]};"
        "for(var i=0;i<app.project.sequences.numSequences;i++){"
        "  var s=app.project.sequences[i];"
        "  var n=0;"
        "  for(var t=0;t<s.videoTracks.numTracks;t++){n+=s.videoTracks[t].clips.numItems;}"
        "  o.seqs.push({name:s.name,w:s.frameSizeHorizontal,h:s.frameSizeVertical,"
        "   tb:String(s.timebase),dur:String(s.end.ticks),clips:n});}"
        "return o;})()"
    )
    try:
        print(json.dumps(pr.eval(code), ensure_ascii=False, indent=1))
    except Exception as exc:  # noqa: BLE001
        print("查询失败:", str(exc)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
