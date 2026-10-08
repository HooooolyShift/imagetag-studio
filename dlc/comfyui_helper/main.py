"""DLC 入口：宿主会 import 这个文件并调用 register(host)。

要做的事就两件：
  1) 往主程序「更多 ▾」里注册一个菜单项；
  2) 点开时弹出自己的窗口（窗口里的配置通过 host.config / host.save_config() 持久化，
     例如"生图输出路径"就是 settings 里的 output_dir）。

**给 AI 生图会话的接手指南**（这份骨架是可运行的最小链路，剩下内容往 `ui.py` / 新文件里补）：
  - 中文 → danbooru 标签的提示词助手：建议做成 `prompt_helper.py`（直连本机 Ollama 11434，
    或直接复用你们那套词表校验逻辑），在 ui.py 里给正向输入框加一个"中文→标签"按钮；
  - 工作流模板：把 Anima 那套（UNETLoader / ModelSamplingAuraFlow / CLIPLoader + qwen vae）
    以及其它常用工作流做成 `workflows/*.json`，在 ui.py 里加"工作流"下拉，按需替换 comfy.workflow()；
  - 模型清单与下载：把你们的下模型脚本搬进来（进 DLC 目录），界面上给"下载/校验模型"入口；
  - 批量出图队列：现在 ui.py 是顺序跑，改成队列 + 可取消 + 断点续跑；
  - 一键入库/打标：ui.py 已有 import_results()，可再加"入库并自动打标"。
"""
from __future__ import annotations


def register(host) -> None:
    """宿主调用入口。"""
    def open_gen() -> None:
        from .ui import GenWindow
        host.open_window(GenWindow(host))

    host.add_action("AI 生图（DLC）", open_gen,
                    "用本机 ComfyUI 生图：选底模/参数/输出路径，结果可一键入库")
