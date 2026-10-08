# DLC：ComfyUI 生图助手（comfyui_helper）

给「图片标签工坊」主程序用的**可选安装 DLC**：把本机 ComfyUI 变成"选模型 → 写提示词 → 出图 → 入库"的一条龙，
参数已按本机实测结论固化，用户不用碰采样器。

## 这个 DLC 提供什么

| 能力 | 位置 | 说明 |
|---|---|---|
| **面向用户的接入文档** | `docs/用户手册_接入ComfyUI.md` | 教最终用户怎么把 ComfyUI 接上、模型放哪、怎么切模型、常见问题 |
| **自动接入** | `autoconnect.py` + 界面「ComfyUI 接入」栏 | 自动找安装目录（常见盘符/整合包/用户目录）+ 探测 8188/8189/8000 上是否已在跑；一键后台启动 |
| **模型切换** | 界面「模型类型」+「底模」下拉 | SDXL（checkpoints）/ Anima（diffusion_models + Qwen 编码器 + Qwen VAE）随时切换，参数自动套预设 |
| **更多功能面板** | `extras.py` | 预设下拉、批量队列（可取消 + 断点续跑）、**可选**自动入库/自动打标、局部重绘换装、参考图（IP-Adapter）、姿势（ControlNet）、模型下载/校验 |
| **放大 / 图生图 / 融合** | `app/comfy_client.py` | `upscale_workflow`（ESRGAN 干净超分）/ `hires_workflow`（潜空间放大，标注彩噪风险）/ `img2img_workflow`（含两图融合） |
| **尺寸按架构守规矩** | `ui.py` | 尺寸预设档 + 256–2048/步进 8 + SDXL 低于 ~0.85MP 黄字提醒（避免出色块）；默认底模已是 SDXL |
| **能力探测（能连上就用，缺啥就提示）** | `comfy.py::capability_report()` | 依赖自定义节点/模型的功能会先探测；缺失时弹窗列出**具体缺哪一项 + 怎么补**，并给「用完整模式重启 ComfyUI」按钮 |
| **中文 → danbooru 提示词** | `prompt_helper/` | 本机 Ollama（离线）+ **23.3 万条 danbooru 词表校验**：能匹配就必须用词表里的规范写法，匹配不到才保留原词并单独回报 |
| **一次给出正向 + 负向** | `generate_both()` | 用户在原生窗口点一下「中文→正+负标签」就同时填好两个框，不用切模式 |
| **悬浮英文 tag 显示中文** | `ui.py` 的 `TagHoverTextEdit` | 鼠标停在正向/负向框里的英文 tag 上，气泡显示中文（**只显示词表命中的**）；中文来源=项目词典 + 主程序 `app/tag_zh_dict.json`（合计 12.6 万条） |
| 提示词 HTTP 服务 | `python -m prompt_helper.service` | 默认 `127.0.0.1:8199`，`GET /health`、`POST /prompt`、`POST /validate`，任何前端都能调 |
| Anima 工作流模板 | `workflows/anima_aesthetic.api.json` | UNETLoader + ModelSamplingAuraFlow + CLIPLoader(type=stable_diffusion) + VAELoader |
| 固化参数预设 | `workflows/presets.json` | 开屏 1536×648 / 双人防克隆 / Anima 默认，含实测结论与负向词 |
| 模型清单 | `models.json` | 文件名/来源/体积/SHA256/目标目录；**模型不进包**，按需下载 |
| 下载与批量出图 | `scripts/` | `fetch_model.py`（断点续传，走系统代理）、`gen_splash.py`（批量出图）、`batch_gen.py`（可断点重跑）、`fetch_anima.py` |
| 词表 | `wordlists/` | gzip 压缩后 **8.07 MB**（原始 19.21 MB），含项目词典（中文/别名）与 NoobAI/Illustrious/Anima 各自的 tag 表 |
| （可选组件）SwarmUI 中文化 | `swarmui_component/` | 中文化补丁 + PromptHelper 扩展源码 + 翻译脚本；**主界面不用它**，只作备选 |

> **与主程序的词库分工（2026-10-08 与 PC 端会话约定）**：
> 中文 ↔ 英文映射、分类、从属、热度**一律以主程序的 `host.lexicon()`（`app/taglex.py`）为准**，
> `prompt_helper/dictionary.py` 通过 `attach_lexicon(lex)` 委托它；
> 本 DLC 的 `wordlists/`（NoobAI/Illustrious/Anima 各自训练用的 tag 表）只负责回答
> "**这个 tag 在目标模型里认不认**"，两件事不重叠。

## 依赖

- **ComfyUI**（本机已有；DLC 只走 HTTP API `127.0.0.1:8188`，不打包、不修改它）
- **Ollama**（`D:\LocalAI`，离线）+ 一个本地模型 —— 只有"中文→提示词"需要；**优先用本机已有模型**
  （自动在已装模型里挑最合适的，本机一个都没有时才提示下载），没有它其余功能照常
- Python 3.10+，**只用标准库**（无需 pip 安装任何东西）

## 快速用法

```powershell
# 1) 中文 → 提示词（命令行）
cd dlc\comfyui_helper
python -m prompt_helper "初音未来站在贴满照片的墙前，微笑看镜头，冷色调，半身"
python -m prompt_helper "红色连衣裙，蕾丝边，过膝" --mode outfit      # 换装用：只翻服装

# 2) 起本地服务给原生界面用（宿主自己决定何时拉起/关掉）
python -m prompt_helper.service --port 8199
curl -s http://127.0.0.1:8199/health
curl -s -X POST http://127.0.0.1:8199/prompt -H "Content-Type: application/json" -d '{\"text\":\"初音未来海边微笑\",\"mode\":\"prompt\"}'

# 3) 下载模型（示例：WAI-illustrious，断点续传）
python scripts\fetch_model.py "<url>" "E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5\models\checkpoints\WAI-illustrious-SDXL-v17.safetensors"
python scripts\fetch_anima.py            # Anima 三件套
```

## 目录结构（宿主按 `dlc.json` 读取）

```
dlc/comfyui_helper/
  dlc.json                    ← 清单（id/版本/入口/依赖/可选组件/体积/配置项）
  README.md
  models.json                 ← 模型与词表清单（体积、来源、SHA256）
  prompt_helper/              ← 中文→提示词（可 import，也可起 HTTP 服务）
  workflows/                  ← ComfyUI API 工作流模板 + 固化预设
  scripts/                    ← 下载 / 批量出图 / 计划任务批量脚本
  wordlists/                  ← 词表（.csv.gz）
  swarmui_component/          ← 可选：SwarmUI 中文化补丁与扩展源码（默认不装）
```

## 工作流占位符约定

`workflows/*.api.json` 里的 `{{...}}` 由宿主替换后直接 POST 到 ComfyUI `/prompt`：
`{{unet}} {{clip}} {{vae}} {{positive}} {{negative}} {{width}} {{height}} {{batch}} {{seed}} {{steps}} {{cfg}} {{sampler}} {{scheduler}} {{prefix}}`。

## 已知注意点（都是踩过的坑）

- CLIPLoader 的 `type` 对 Anima 必须是 **stable_diffusion**（不是 qwen_image），否则文本编码不对；
- 二段放大/锐化**一律关**（SDXL 平涂模型会重影/彩噪）；CFG 5.5 比 6.0 少 halo；
- 长负向词会把 SDXL 打爆，负向保持精简；
- 双人题材别把照片墙放进背景（模型会把照片也画成同一个角色 → "克隆人"）；
- Ollama 不运行时 `/prompt` 返回 503，提示"先启动 Ollama"。
