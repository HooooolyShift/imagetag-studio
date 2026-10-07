"""Premiere MCP Bridge 的 Python 客户端。

通过 CEP 面板暴露的本地 HTTP 端点（127.0.0.1:3000）把 ExtendScript 送进
Premiere Pro 执行，并取回 JSON 结果。协议与 pymiere link 面板兼容。

用法：
    from premiere_client import PremiereClient
    pr = PremiereClient()
    pr.alive()                      # 桥是否在线
    pr.eval("app.version;")         # 直接跑 ExtendScript
"""
from __future__ import annotations

import json
from typing import Any

import requests

BRIDGE_URL = "http://127.0.0.1:3000"

# ExtendScript 里没有内建 JSON，桥面板已注入 ExtendJSON（json2 改）。
# 统一把结果 stringify 成 JSON 文本回传；出错则回传带 error=true 的对象。
# code 必须是「表达式」或「以表达式收尾的语句块」（IIFE 最常用），不能用 return。
_WRAP = """try{{
ExtendJSON.stringify(eval({code_json}));
}}catch(e){{e.error=true;ExtendJSON.stringify(e);}}"""


class PremiereError(RuntimeError):
    """ExtendScript 执行出错。"""


class PremiereClient:
    def __init__(self, url: str = BRIDGE_URL, timeout: float = 120.0) -> None:
        self.url = url
        self.timeout = timeout

    # ----- 基础通信 -----
    def alive(self) -> bool:
        try:
            resp = requests.get(self.url, timeout=5)
        except requests.RequestException:
            return False
        return resp.content.decode("utf-8", "replace").strip() == "Premiere is alive"

    def eval(self, code: str, decode_json: bool = True) -> Any:
        """把 ExtendScript 代码送到 Premiere 执行。

        code 写成一个表达式（或 IIFE）。返回值会被 JSON 序列化后取回。
        """
        payload = {"to_eval": _WRAP.format(code_json=json.dumps(code, ensure_ascii=False))}
        resp = requests.post(self.url, json=payload, timeout=self.timeout)
        text = resp.content.decode("utf-8", "replace")
        if not decode_json:
            return text
        try:
            result = json.loads(text)
        except Exception:  # noqa: BLE001 - 非 JSON 就原样返回
            return text
        if isinstance(result, dict) and result.get("error") is True:
            raise PremiereError(
                "{name}: {message} (line {line})\n{source}".format(
                    name=result.get("name"),
                    message=result.get("message"),
                    line=result.get("line"),
                    source=(result.get("source") or "").strip(),
                )
            )
        return result

    # ----- 常用封装 -----
    def version(self) -> str:
        return str(self.eval("app.version"))

    def project_name(self) -> str:
        return str(self.eval("app.project.name"))

    def new_project(self, path: str) -> Any:
        return self.eval(f"app.newProject({es(path)})")

    def open_project(self, path: str) -> Any:
        return self.eval(f"app.openDocument({es(path)})")

    def save_project(self) -> Any:
        return self.eval("app.project.save()")

    def import_files(self, paths: list[str], bin_name: str | None = None) -> Any:
        arr = "[" + ",".join(es(p) for p in paths) + "]"
        target = "null"
        if bin_name:
            target = (
                "(function(){var b=app.project.rootItem.createBin(%s);return b;})()"
                % es(bin_name)
            )
        return self.eval(
            f"app.project.importFiles({arr}, true, {target}, false)"
        )

    def sequence_info(self) -> dict:
        return self.eval(
            "(function(){var s=app.project.activeSequence;"
            "if(!s){return {none:true};}"
            "return {name:s.name,"
            "timebase:String(s.timebase),"
            "start:String(s.zeroPoint),"
            "duration:String(s.end),"
            "videoTracks:s.videoTracks.numTracks,"
            "audioTracks:s.audioTracks.numTracks};})()"
        )

    def project_summary(self) -> dict:
        return self.eval(
            "(function(){"
            "var items=[];var root=app.project.rootItem;"
            "for(var i=0;i<root.children.numItems;i++){var c=root.children[i];"
            "items.push({name:c.name,type:c.type,"
            "children:(c.type==='BIN'?c.children.numItems:0)});}"
            "var seqs=[];for(var j=0;j<app.project.sequences.numSequences;j++){"
            "seqs.push(app.project.sequences[j].name);}"
            "return {name:app.project.name,path:app.project.path,"
            "items:items,sequences:seqs};})()"
        )


def es(value: Any) -> str:
    """把 Python 值转成 ExtendScript 字面量（字符串走 JSON 转义，避免中文/引号踩坑）。"""
    return json.dumps(value, ensure_ascii=False)
