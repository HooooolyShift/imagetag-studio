# DLC：AI 生图助手（comfyui_helper）待办与分工

> 来源：AI 生图会话 2026-10-08 回的清单（含工作量估算）；状态由 PC 端主程序会话维护。
> 宿主（可选安装机制）在 `app/dlc.py` + `app/ui/dlc_ui.py`，DLC 内容在 `dlc/comfyui_helper/`。

## 已完成（宿主侧）

- ✅ DLC 扫描 / 启用停用 / zip 安装 / 卸载进回收站 / 配置持久化（`settings.dlc_enabled`、`dlc_config`）
- ✅ 主窗口「更多 ▾ → 扩展包（DLC）…」管理窗口；**生图输出路径**就在里面配置（`output_dir`）
- ✅ `DlcHost` 给 DLC 的能力：`config/save_config`、`add_action`、`open_window`、
  `lexicon()`（共享词库）、`scan_into_library()`（生成结果入库）、`tag_files()`（自动打标）、`store/library/hub`
- ✅ DLC 当"包"加载（支持 `from .ui import …` 相对导入）
- ✅ 原生生图窗口骨架：模型/参数/输出路径/批量/结果/一键入库 + 词库按钮（中文→标签 / 词表校验 / 插入热词）
- ✅ **生图遥控接口（供平板/手机用，算力在 PC）**：`GET /api/gen/info`、
  `POST /api/gen/run`（进度走 SSE `gen_*`，`import:true` 自动入库）、`GET /api/gen/results`、
  `GET /api/gen/file`；共用词库 `GET /api/lex/zh`、`POST /api/lex/prompt_fix`。
  实测：底模 16 个 / 预设 3 个；512×512 8 步 **15 秒**出图并 `file_id=113` 入库；越界文件名 400。
- ✅ `Library.scan_paths_into_library(paths)`：把生成结果扫进库（DLC 的"一键入库"与
  `/api/gen/run import:true` 共用）

## 已完成（内容侧，AI 生图会话）

- ✅ `prompt_helper/`：中文 → danbooru 提示词（本机 Ollama 离线 + 23.3 万词表校验），附 HTTP 服务（8199）
- ✅ `workflows/anima_aesthetic.api.json`（UNETLoader + ModelSamplingAuraFlow + CLIPLoader）+ `workflows/presets.json`
- ✅ `models.json` 模型清单（SHA256；**模型不进包**，按需下载）
- ✅ `scripts/`：断点续传下载、批量出图、Anima 三件套
- ✅ `wordlists/`（gzip 8.07 MB）+ `swarmui_component/`（可选组件，默认不装）
- ✅ 中文映射已改为委托 `host.lexicon()`（`dictionary.py` 的 `attach_lexicon`）

## 待办（按建议顺序）

| # | 功能 | 说明 | 预估 | 状态 |
|---|---|---|---|---|
| 1 | 工作流下拉 | 把 `workflows/presets.json` 做成界面可选预设（现在只有模型类型切换 + 内置默认） | 2h | 待做 |
| 2 | 批量队列 | 队列化 + 可取消 + 断点续跑（`scripts/batch_gen.py` 已有跳过已出图的思路） | 3h | 待做 |
| 3 | 一键入库并自动打标 | 入库后接 `host.tag_files()` | 1h | 待做 |
| 4 | 按模型过滤词表 | NoobAI 14.2 万 / Illustrious 9.4 万 / Anima 10.8 万，加"当前模型认不认"的开关 | 2h | 待做 |
| 5 | 模型下载/校验入口 | **PC 侧已完成**（见下）；DLC 自己的界面入口待接 | 2h | 宿主已完成 |
| 6 | 参考图 / 姿势控制 | **PC 侧已完成**（`/api/gen/caps` + `/api/gen/ipadapter` + `/api/gen/controlnet`）；DLC 界面待接 | 5h | 宿主已完成 |
| 7 | 局部重绘 / 换装 | **PC 侧已完成**（`POST /api/gen/inpaint` + `MaskCanvas`）；DLC 界面待接 | 6h | 宿主已完成 |
| — | SwarmUI 组件 | 保持"可选组件、默认不装" | — | 维持 |

合计约 **20~25 小时**，可按批次给。

## 宿主侧 2026-10-08 新增（生图高级功能一并打通）

> 生图端只管 DLC 自己的窗口；**凡是"PC 出图 + 移动端遥控"要用的，宿主这边都做成接口了**，
> 移动端不必等 DLC 界面。生图端的待办只剩"给自己界面加按钮"。

| 接口 | 作用 |
|---|---|
| `GET /api/gen/caps` | 能力探测（局部重绘/参考图/姿势控制各自 ok、缺什么、提示）+ 可选文件列表 + 按架构给的默认选项；60 秒缓存，`?fresh=1` 强制重探 |
| `GET /api/gen/models` | 模型清单 + **本机是否已有**（含实际字节数、绝对路径、能不能直链下载） |
| `POST /api/gen/models/fetch` | 后台下缺失模型（复用 `scripts/fetch_model.py`，断点续传），进度走 SSE `gen_dl_started/progress/done/failed` |
| `POST /api/gen/ipadapter` | 参考图：`{ref_file_id｜ref_name｜ref_base64, prompt, weight?}`，可 `import:true` 入库 |
| `POST /api/gen/controlnet` | 姿势/线稿：`{pose_file_id｜pose_name｜pose_base64, prompt, strength?, preprocessor?}` |
| `POST /api/gen/img2img` | 图生图 / **图融合**：`{file_id｜name, prompt, denoise?, blend_file_id?, blend_factor?, blend_mode?}` |
| `POST /api/gen/upscale` | **纯放大**（ESRGAN 超分，不出彩噪）：`{file_id｜name, scale?}`；实测 1024→4096 只要 9 秒 |
| `POST /api/gen/hires` | 潜空间放大 + 低 denoise 重采样（会长细节，但边缘有彩噪风险）：`{file_id｜name, prompt, scale?}` |
| `GET /api/gen/status`、`POST /api/gen/interrupt` | 任务状态 / **定向取消**（`/queue delete` + `/interrupt {prompt_id}`：排队中的也能停、不误伤别的客户端；空闲时不动 ComfyUI）。2026-10-08 晚修了竞态，见联调报告「一·补」 |

### 两个"踩过才知道"的坑，已经写进代码

1. **IP-Adapter 和 ControlNet 都必须跟底模架构配套**：
   - ControlNet 配错 → ComfyUI 直接报 `y is None, did you try using a controlnet for SDXL on SD1?`；
   - IP-Adapter 配错 → **不报错，只出噪声图**（更难发现）。
   - 宿主现在会读 safetensors 头部判断底模是 SDXL 还是 SD1.5（`ComfyClient.checkpoint_arch()`：
     优先 metadata `modelspec.architecture`，没有就看交叉注意力上下文维度 768/2048），再自动挑对应文件。
     实测：SD1.5 底模（AWPainting）→ `ip-adapter_sd15_plus.pth` + `control_v11p_sd15_openpose_fp16`；
     SDXL 底模（WAI-illustrious）→ `ip-adapter-plus_sdxl_vit-h` + `controlnet++_union_sdxl_promax`。两组都出图正常。
2. **`/history` 里"执行失败"和"跑完没图"长得一样**：原来的 `ComfyClient.wait()` 只数 `outputs`，
   遇到执行报错会静默返回空列表。现在会解析 `status.messages` 里的 `execution_error` 并抛出真实原因。
3. `models.json` 的 `target` 字段是**相对 ComfyUI 根目录**的（`models/checkpoints`），
   拼路径时不能再补一层 `models`，否则会下到 `models\models\vae\…`（已修，误建目录已清理）。
4. **新版 ComfyUI 的下拉定义换了写法**：`["COMBO", {"options":[…]}]` 取代了老的 `[[选项…]]`。
   旧解析会把字符串 `"COMBO"` 拆成 `['C','O','M','B','O']`（实测把放大模型名解析成了 `"C"`，
   提交工作流直接失败）。现在两种写法都认（`ComfyClient._parse_enum`）。
5. **要更大的图别硬开大分辨率**：8GB 显存上 SDXL 出 2048² 基本必 OOM，超训练分辨率还容易出
   重复肢体。正解是"先出 1024，再 `/api/gen/upscale` 放大"。
6. **分辨率的正确量级**：SDXL 原生 1024，**低于 ~0.6MP（比如 512×512）会糊成一团**——
   之前 PC 侧接口冒烟测试就踩了这个（用了 SD1.5 时代的 512/8 步参数），
   现在接口会自动抬到 ~1024 并在响应里回 `warning`。

## 打包结论

- `wordlists/` **随 DLC 分发**：gzip 后 8.07 MB（原始 19.21 MB），整个 DLC 目录 8.23 MB，**不用分卷**。
- 安装器提供"可选安装 DLC"；更新包同步带 DLC 目录（见发布流程）。

## 词库共享（避免两套词表）

- **中文↔英文 / 分类 / 从属 / 热度**：一律走 `host.lexicon()`（`app/taglex.py`）。
- **"这个 tag 在目标模型里认不认"**：留在 DLC 的 `wordlists/`（模型相关知识）。
- 人工口头说法（danbooru 没覆盖的）：放共享别名表 `app/tag_zh_aliases.json`（两边共用，别再各写一份）。

### 2026-10-08 修的两个词库 bug（生图端报的）

1. `zh_to_tag("半身")` → `newhalf`：根因是 `tag_zh_dict.json` 里 **`newhalf` 被错译成"半身"**
   （`newhalf` 应为"人妖"）。已在 `tag_zh_corrections.json` 修正并应用（121 条），
   现在「半身」→ `upper_body`、「人妖」→ `newhalf`。
2. 长短语乱配（`"微笑看镜头"→newhalf`、`"半身"→asahina mirai`）：根因是 `zh_to_tag` 会退回
   带模糊/子串的解析。现在改成**只认精确匹配**，另加 `segment_zh()` 贪心切词：
   `"微笑看镜头"` → `smile, looking_at_viewer`；`prompt_fix("微笑看镜头，半身，冷色调")`
   → `smile, looking_at_viewer, upper_body, cool_colors`（0 未命中）。
3. 英文输入不再反查（`zh_to_tag("newhalf")` → `newhalf`，不会被别名劫持成别的标签）。
