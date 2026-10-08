"""用自建 CEP 桥把宣传片时间线铺出来并导出（预览 H.264）。

流程：建/开工程 → 导入素材 → 用 1920x1080 卡片建 1080p60 序列 → 按 promo_plan 铺镜头
（时间已按 beats.json 吸附到节拍、并量化到帧格）→ 叠字幕/章节标题 → 铺 A1 音频 → 导出。

实测注意（详见 skill `premiere-video-pipeline` 的 pitfalls）：
  * 不要用 createNewSequence(name, preset)：会弹模态框卡死
  * 时间必须落在帧格上，否则按 ticks 找不到刚放上去的镜头（这里已量化 + 容错一帧）
  * app.project.save() 可能弹框：包在 try 里，保存交给用户 Ctrl+S
  * 素材箱类型是数字 2；重复导入要按名字跳过
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from premiere_client import PremiereClient, es  # noqa: E402
from promo_plan_v15 import resolve  # noqa: E402

PROJECT = Path(r"E:\文档\ChatGPT\图片标签分类")
PROMO = PROJECT / "promo"
WORK = PROMO / "premiere"
PROJECT_FILE = WORK / "ImageTagStudio_v15.prproj"
AUDIO_MIX = PROMO / "audio" / "mix_v15.wav"

# 导出用系统自带预设（改 .epr 里的码率无效，实测过；要精确码率走 ProRes + NVENC）
EXPORT_PRESET = (r"D:\PR2024\Adobe Premiere Pro 2024\MediaIO\systempresets"
                 r"\3F3F3F3F_4D6F6F56\H264 Match Source - High bitrate.epr")

SEQ_NAME = "v1.5 更新宣传片"
TPS = 254_016_000_000
ONE_FRAME = 4_233_600_000      # 60fps 一帧的 ticks（容错用）

BIN_UI, BIN_MOTION, BIN_GFX, BIN_AUDIO = "素材-界面", "素材-动画", "素材-图形", "素材-音频"

_SIZE_CACHE: dict[str, tuple[int, int]] = {}


def log(msg: str) -> None:
    print(msg, flush=True)


def motion_frames(folder: Path) -> list[Path]:
    frames = sorted(folder.glob("*.png"))
    return frames or sorted(folder.glob("*.jpg"))


def image_size(path: str) -> tuple[int, int]:
    if path not in _SIZE_CACHE:
        try:
            with Image.open(path) as im:
                _SIZE_CACHE[path] = im.size
        except Exception:  # noqa: BLE001
            _SIZE_CACHE[path] = (1920, 1080)
    return _SIZE_CACHE[path]


def base_scale(path: str) -> float:
    """按 1920 宽铺满画面所需的 Motion Scale(%)。4K 截图 → 50，1080p → 100。"""
    w, _h = image_size(path)
    return round(1920.0 / max(1, w) * 100.0, 2)


def collect_assets() -> tuple[list[str], list[str], list[str], dict[str, str]]:
    shots = sorted((PROMO / "v15_shots").glob("*.png"))
    cards = sorted((PROMO / "cards_v15").glob("*.png"))
    overlays = sorted((PROMO / "overlays_v15").glob("*.png"))
    manual = sorted((PROJECT / "manual_shots").glob("*.png"))
    icon = [PROJECT / "assets" / "icon.png"]
    ui = [str(p) for p in shots + icon + manual if p.exists()]
    gfx = [str(p) for p in cards + overlays if p.exists()]
    motion_dirs = sorted((PROMO / "motion_seq_v15").glob("*"))
    motion = [str(motion_frames(d)[0]) for d in motion_dirs if motion_frames(d)]
    index: dict[str, str] = {}
    for p in shots:
        index[f"shots/{p.stem}"] = str(p)
        index[f"stills_v15/{p.stem}"] = str(p)
    for p in cards:
        index[f"card/{p.stem}"] = str(p)
    for p in manual:
        index[f"manual/{p.stem}"] = str(p)
    index["assets/icon"] = str(PROJECT / "assets" / "icon.png")
    for d in motion_dirs:
        index[f"motion_v15/{d.name}"] = str(d)
    for p in overlays:
        index[f"overlays/{p.stem}"] = str(p)
    return ui, motion, gfx, index


def _find_js() -> str:
    """JS 辅助函数：递归找素材项 + 按 ticks 找镜头 + 取 Motion 属性。

    都在 JS 侧定义好，Python 只拼"调用"，避免花括号转义写错（之前踩过）。
    素材箱类型是数字 2（不是字符串 'BIN'）。
    """
    return (
        "function find(bin,name){"
        " for(var i=0;i<bin.children.numItems;i++){var c=bin.children[i];"
        "  if(c.name===name){return c;}"
        "  if(c.children && c.children.numItems){var d=find(c,name);if(d){return d;}}}"
        " return null;}"
        "function findClip(track,ticks,tol){"
        " for(var i=0;i<track.clips.numItems;i++){var c=track.clips[i];"
        "  if(Math.abs(parseInt(c.start.ticks)-ticks)<=tol){return c;}}"
        " return null;}"
        "function motionProps(clip){var m=null;"
        " for(var j=0;j<clip.components.numItems;j++){"
        "  if(clip.components[j].displayName==='Motion'){m=clip.components[j];}}"
        " if(!m){return null;}var sc=null,po=null;"
        " for(var p=0;p<m.properties.numItems;p++){var pp=m.properties[p];"
        "  if(pp.displayName==='Scale'){sc=pp;} if(pp.displayName==='Position'){po=pp;}}"
        " return {sc:sc,po:po};}"
    )


def ensure_project(pr: PremiereClient) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        current = str(pr.eval("(function(){return (app.project && app.project.path) || '';})()") or "")
    except Exception:  # noqa: BLE001
        current = ""
    if current:
        log(f"沿用已经打开的工程：{current}")
        return
    if PROJECT_FILE.exists():
        pr.open_project(str(PROJECT_FILE))
        log(f"已打开工程 {PROJECT_FILE.name}")
    else:
        pr.new_project(str(PROJECT_FILE))
        log(f"新建工程 {PROJECT_FILE.name}")


def import_bins(pr: PremiereClient, ui, motion, gfx) -> None:
    """只导入素材箱里还没有的素材（避免重复堆副本）。"""
    js = (
        "(function(){var root=app.project.rootItem;"
        "function bin(name){for(var i=0;i<root.children.numItems;i++){var c=root.children[i];"
        " if(c.type===2 && c.name===name){return c;}}return root.createBin(name);}"
        "function has(b,n){for(var i=0;i<b.children.numItems;i++){if(b.children[i].name===n){return true;}}return false;}"
        "function missing(list,b){var out=[];for(var i=0;i<list.length;i++){"
        " var n=list[i].split('\\\\').pop(); if(!has(b,n)){out.push(list[i]);}}return out;}"
        "var b1=bin(%s), b2=bin(%s), b3=bin(%s);"
        "var m1=missing(%s,b1); if(m1.length){app.project.importFiles(m1,true,b1,false);}"
        "var m3=missing(%s,b3); if(m3.length){app.project.importFiles(m3,true,b3,false);}"
        "var res=[];var seqs=%s;"
        "for(var k=0;k<seqs.length;k++){var sn=seqs[k].split('\\\\').pop();"
        " if(has(b2,sn)){res.push('skip');continue;}"
        " try{app.project.importFiles([seqs[k]],true,b2,true);res.push('ok');}"
        " catch(e){res.push('x:'+e.message);}}"
        "return {ui:b1.children.numItems,motion:b2.children.numItems,gfx:b3.children.numItems,res:res.join(',')};})()"
        % (es(BIN_UI), es(BIN_MOTION), es(BIN_GFX), es(ui), es(gfx), es(motion))
    )
    log("导入：" + json.dumps(pr.eval(js), ensure_ascii=False))


def _motion_js(zoom: str, focus, base: float, media_w: int,
               media_h: int, settle: bool) -> str:
    """Motion 关键帧：缓慢推拉；章节切换处额外做一个很轻的 1.04→1.0 落定。"""
    if zoom == "none" and not settle:
        return ""
    if zoom == "focus" and focus:
        cx = max(0.35, min(0.65, float(focus[0])))
        cy = max(0.30, min(0.70, float(focus[1])))
        factor = min(1.12, max(1.0, float(focus[2])))
    else:
        cx, cy = 0.5, 0.5
        factor = 1.0 if zoom == "none" else (1.06 if zoom == "in" else 1.04)
    ux0 = base / 100.0
    uy0 = base / 100.0 * (media_h / media_w) * (16.0 / 9.0)
    s1 = base * factor
    p0x, p0y = 0.5 - (cx - 0.5) * ux0, 0.5 - (cy - 0.5) * uy0
    p1x, p1y = 0.5 - (cx - 0.5) * ux0 * factor, 0.5 - (cy - 0.5) * uy0 * factor
    js = "var mp=motionProps(placed);"
    if zoom != "none":
        js += (f"if(mp&&mp.sc){{mp.sc.setValueAtKey({base:.3f},startTicks,true);"
               f" mp.sc.setValueAtKey({s1:.3f},endTicks,true);}}"
               f"if(mp&&mp.po){{mp.po.setValueAtKey([{p0x:.4f},{p0y:.4f}],startTicks,true);"
               f" mp.po.setValueAtKey([{p1x:.4f},{p1y:.4f}],endTicks,true);}}")
    if settle:
        # 转场落定：起点放大 4%，0.45 秒回到正常
        js += (f"if(mp&&mp.sc){{mp.sc.setValueAtKey({base * 1.04:.3f},startTicks,true);"
               f" mp.sc.setValueAtKey({base:.3f},"
               f"String(parseInt(startTicks)+{int(0.45 * TPS)}),true);}}")
    return js


def _segment_code(n: int, seg: dict, index: dict[str, str]) -> str:
    start, dur = float(seg["start"]), float(seg["dur"])
    src = seg["src"]
    sub_path = index.get(f"sub:{n}", "")
    title_path = index.get(f"title:{n}", "")
    if src.startswith("motion_v15/"):
        frames = motion_frames(Path(index[src]))
        name_hint = frames[0].name if frames else ""
        media = image_size(str(frames[0])) if frames else (1920, 1080)
        base = base_scale(str(frames[0])) if frames else 100.0
    else:
        name_hint = Path(index[src]).name
        media = image_size(index[src])
        base = base_scale(index[src])
    motion_js = _motion_js(seg.get("zoom", "none"), seg.get("focus"), base,
                           media[0], media[1], bool(seg.get("title")))
    start_ticks, end_ticks = int(start * TPS), int((start + dur) * TPS)
    sub_ticks = int((start + 0.12) * TPS)
    sub_end = int((start + dur - 0.12) * TPS)
    title_end = int((start + min(4.2, dur)) * TPS)

    js = ("(function(){var root=app.project.rootItem;var seq=app.project.activeSequence;"
          + _find_js() +
          f"var target={es(name_hint)};"
          "var item=find(root,target);"
          "if(!item){throw new Error('素材没见过: '+target);}"
          f"var startTicks=String({start_ticks});"
          f"var endTicks=String({end_ticks});"
          f"var TOL={ONE_FRAME + 1};"
          "seq.videoTracks[0].overwriteClip(item,startTicks);"
          f"var placed=findClip(seq.videoTracks[0],{start_ticks},TOL);"
          "if(!placed){throw new Error('放上去但没找到镜头');}"
          "placed.end=endTicks;"
          "var subItem=null;var tItem=null;"
          + motion_js)
    if sub_path:
        js += (f"subItem=find(root,{es(Path(sub_path).name)});"
               f"if(subItem){{seq.videoTracks[1].overwriteClip(subItem,String({sub_ticks}));"
               f" var sc=findClip(seq.videoTracks[1],{sub_ticks},TOL);"
               f" if(sc){{sc.end=String({sub_end});}}}}")
    if title_path:
        js += (f"tItem=find(root,{es(Path(title_path).name)});"
               f"if(tItem){{seq.videoTracks[2].overwriteClip(tItem,String({start_ticks}));"
               f" var tc=findClip(seq.videoTracks[2],{start_ticks},TOL);"
               f" if(tc){{tc.end=String({title_end});}}}}")
    js += ("return {clip:String(placed.start.seconds.toFixed(2))+'~'+String(placed.end.seconds.toFixed(2))"
           "+', 字幕='+(subItem?'y':'n')+', 标题='+(tItem?'y':'n')};})()")
    return js


def build_timeline(pr: PremiereClient, index: dict[str, str]) -> None:
    info = json.loads((PROMO / "audio" / "beats_v15.json").read_text(encoding="utf-8"))
    segs = resolve(float(info["duration"]), info["beats"])
    for i, seg in enumerate(segs, 1):
        sub = PROMO / "overlays_v15" / f"sub_{i:03d}.png"
        ttl = PROMO / "overlays_v15" / f"title_{i:03d}.png"
        index[f"sub:{i}"] = str(sub) if (seg.get("sub") and sub.exists()) else ""
        index[f"title:{i}"] = str(ttl) if (seg.get("title") and ttl.exists()) else ""
    for n, seg in enumerate(segs, 1):
        try:
            log(f"  {n:02d} {float(seg['start']):7.2f}s  {seg['src']:<28} -> "
                + str(pr.eval(_segment_code(n, seg, index))))
        except Exception as exc:  # noqa: BLE001
            log(f"  {n:02d} !! 失败 {seg['src']}: {str(exc)[:220]}")
    (WORK / "timeline.json").write_text(json.dumps(
        [{k: s[k] for k in ("src", "start", "dur", "title") if k in s} for s in segs],
        ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"时间线铺完，总长 {sum(float(s['dur']) for s in segs):.2f}s，已写 timeline.json")


def place_audio(pr: PremiereClient) -> None:
    if not AUDIO_MIX.exists():
        log("没有 mix.wav，先跑 audio_build.py build")
        return
    timeline = json.loads((WORK / "timeline.json").read_text(encoding="utf-8"))
    total = max(float(s["start"]) + float(s["dur"]) for s in timeline)
    js = ("(function(){var root=app.project.rootItem;" + _find_js() +
          f"var bin=null;for(var i=0;i<root.children.numItems;i++){{"
          f" if(root.children[i].type===2 && root.children[i].name==={es(BIN_AUDIO)}){{bin=root.children[i];break;}}}}"
          f"if(!bin){{bin=root.createBin({es(BIN_AUDIO)});}}"
          f"var have=find(bin,{es(AUDIO_MIX.name)});"
          f"if(!have){{app.project.importFiles([{es(str(AUDIO_MIX))}],true,bin,false);"
          f" have=find(bin,{es(AUDIO_MIX.name)});}}"
          "if(!have){throw new Error('音频没导入');}"
          "var seq=app.project.activeSequence;seq.audioTracks[0].overwriteClip(have,'0');"
          "var c=null;for(var m=0;m<seq.audioTracks[0].clips.numItems;m++){"
          " var cc=seq.audioTracks[0].clips[m];if(String(cc.start.ticks)==='0'){c=cc;break;}}"
          f"if(c){{c.end=String({int(total * TPS)});}}"
          "return {clips:seq.audioTracks[0].clips.numItems,"
          " len:c?String(c.duration.seconds.toFixed(2)):'-'};})()")
    try:
        log("音频已铺到 A1：" + json.dumps(pr.eval(js), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        log(f"音频铺设失败：{str(exc)[:200]}")


def main() -> int:
    pr = PremiereClient(timeout=180)
    if not pr.alive():
        log("桥不在线：先启动 Premiere（面板会自动打开），确认 127.0.0.1:3000 可用")
        return 1
    ui, motion, gfx, index = collect_assets()
    log(f"素材：界面 {len(ui)} / 动画 {len(motion)} / 图形 {len(gfx)}")
    if not (PROMO / "shots").exists():
        log("还没有 promo/shots 截图（抓图脚本先跑），也能继续，但画面会很空")
    ensure_project(pr)
    import_bins(pr, ui, motion, gfx)

    card = str(PROMO / "cards_v15" / "title_v15.png")
    info = pr.eval(
        "(function(){var root=app.project.rootItem;" + _find_js() +
        f"var item=find(root,{es(Path(card).name)});"
        "if(!item){throw new Error('卡片没导入');}"
        f"var name={es(SEQ_NAME)};var seq=null;"
        "for(var s=0;s<app.project.sequences.numSequences;s++){"
        " if(app.project.sequences[s].name===name){seq=app.project.sequences[s];}}"
        "if(!seq){seq=app.project.createNewSequenceFromClips(name,[item],root);}"
        "app.project.activeSequence=seq;"
        "try{seq.videoTracks.addTracks(3,0,0);}catch(e){}"
        "return {name:seq.name,w:seq.frameSizeHorizontal,h:seq.frameSizeVertical,"
        " fps:254016000000/parseInt(seq.timebase),vtracks:seq.videoTracks.numTracks};})()")
    log("序列：" + json.dumps(info, ensure_ascii=False))

    build_timeline(pr, index)
    place_audio(pr)
    try:
        pr.eval("app.project.save()", decode_json=False)
        log("工程已保存")
    except Exception as exc:  # noqa: BLE001
        log(f"自动保存跳过（建议手动 Ctrl+S）：{str(exc)[:100]}")

    out = WORK / "v15_preview.mp4"
    if out.exists():
        out.unlink()
    log("导出 H.264 预览 …")
    log(str(pr.eval("(function(){var seq=app.project.activeSequence;"
                    f"return 'ret:'+seq.exportAsMediaDirect({es(str(out))},{es(EXPORT_PRESET)},0);}})()")))
    for _ in range(60):
        time.sleep(5)
        if out.exists() and out.stat().st_size > 50_000:
            break
    log(f"预览：{out}  存在={out.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
