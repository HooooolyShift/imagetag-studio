# 生图对接页 · 平板端参考（局部重绘 + 接口对齐）

> 给平板端会话的**可直接落地的参考实现**。你们的 `webui/js/views/gen.js` 已经做了
> 环境/模型/预设/提示词/出图/结果/词库，这里补的是**还没做的局部重绘**，以及几处接口对齐。
> 文件都在本目录：`gen-inpaint.js`（新页面模块）、`pc-client-additions.js`（客户端要加的方法）。

## 一、PC 侧最近的变化（你们要对齐）

1. **入库一定能成功**：以前"生成输出目录不在图库根里 → 勾了入库也没反应"。现在 PC 侧改为
   `Library.import_generated()`：在库根内的就地索引，**不在库根内的会复制到 `<库根>/AI生成/` 再入库**。
   → `gen.js` 里那条"可能不入库"的提示可以删掉，改成正常成功提示。
   实测：库外文件 `outside.png` → `E:\ImageTagsBeta\AI生成\outside.png`，`file_id=115`。
2. **新增局部重绘接口**：`POST /api/gen/inpaint`
   ```
   { file_id?: <库里图片id> | name?: <输出目录里的文件名>, mask_base64: <PNG, 白=重绘>,
     prompt: "...", negative?: "...", steps?: 28, cfg?: 6.0, denoise?: 0.55,
     grow_mask?: 6, seed?: 0, import?: true }
   ```
   - 语义：**白色 = 要重绘**、黑色 = 保留（PC 端走 `LoadImage → ImageToMask(red) → VAEEncodeForInpaint`，
     不依赖 PNG 的 alpha，和你们上传的黑白图完全对得上）。
   - 进度/结果沿用同一套 SSE：`gen_inpaint_started`（以 `gen_` 开头，会被你们 `app.js` 分发给 `genView.onEvent`）、
     `gen_progress{elapsed}`、`gen_done{files,file_ids,imported,kind:"inpaint"}` / `gen_failed{error}`。
   - 实测：512×512、denoise 0.75、12 步 → **6 秒**出图、自动入库 `file_id=114`，**遮罩外像素未动**；
     空遮罩 → 400 `bad_mask`。
3. **共享词库接口**（你们已接）：`GET /api/lex/zh?text=`、`POST /api/lex/prompt_fix {text}` → `{fixed, unknown}`。

## 二、把局部重绘接进你们的生图页（4 步）

1. 把 `gen-inpaint.js` 拷到 `webui/js/views/gen-inpaint.js`；
2. 把 `pc-client-additions.js` 里的 `genInpaint()` 贴进 `webui/js/data/pc-client.js` 的 `PcClient` 类（挨着 `genRun` 放）；
3. 在 `gen.js` 里加一个入口按钮（例如结果缩略图右键菜单 / 结果卡片上放「局部重绘」），点击时：
   ```js
   import { createInpaintPanel } from './gen-inpaint.js';
   const inpaint = createInpaintPanel({ root: document.body, getClient: () => client, toast });
   // 结果列表里点「局部重绘」：
   inpaint.open({ name: f.name, imageUrl: client.genFileUrl(f.name), prompt: '' });
   ```
4. 页面里要有的样式类（沿用你们现有命名即可）：`.inpaint-mask`（容器）、`.inpaint-canvas`（画布）、
   `.inpaint-tools`（工具条）。参考实现用的是行内样式 + 你们的 `toast()`，不依赖新 CSS 文件。

## 三、交互要求（和 PC 端 MaskCanvas 保持一致，用户已经熟悉）

- 左键画（白），右键擦；滚轮缩放；`[` `]` 调笔刷；`Ctrl+Z` 撤销；`C` 清空；
- 提交前**遮罩必须非空**（PC 端会返回 400 `bad_mask`，前端最好先挡一次）；
- 底图直接取生成结果（`name`），库里已入库的图也可以传 `file_id`；
- 平板触摸：画布要 `touch-action: none`，用 Pointer Events 统一鼠标/触摸/笔。

## 四、参考图 / 姿势控制 / 模型管理（2026-10-08 已补齐，可以直接接）

### 4.1 能力探测（一进生图页调一次）

```
GET /api/gen/caps            # 60 秒缓存；?fresh=1 强制重探
→ { ok, comfy_url, online, detail,
    capabilities: { "局部重绘 / 换装": {ok, missing[], hint},
                    "参考图（IP-Adapter）": {ok, missing[], per_arch:{sdxl,sd15}, hint},
                    "姿势/线稿（ControlNet）": {ok, missing[], hint} },
    files: { ipadapter:[…], clip_vision:[…], control_net:[…] },     # 下拉框直接用
    defaults: { ipadapter_file, ipadapter_file_sdxl, ipadapter_file_sd15,
                clip_vision, control_net, control_net_sdxl, control_net_sd15 },
    models_ready: { ipadapter:bool, clip_vision:bool, control_net:bool },
    arch_ready: { sdxl:bool, sd15:bool } }
```

**用法**：`capabilities["参考图（IP-Adapter）"].ok === false` 就把按钮置灰并把 `missing.join('；')` 显示出来；
`ok === true` 时**不用自己挑文件**，请求里省略 `ipadapter_file` / `control_net` 即可，PC 会按底模架构自动配对。

> 为什么强调"自动配对"：**IP-Adapter / ControlNet 跟底模架构不配套时，ControlNet 会直接报错，
> 而 IP-Adapter 不报错、只出噪声图**。PC 侧会读底模 safetensors 判断 SDXL/SD1.5 再挑文件，
> 所以别在客户端硬编码文件名（`ip-adapter_xl.pth` 只能配 SDXL 底模）。

### 4.2 参考图（IP-Adapter）

```
POST /api/gen/ipadapter
{ ref_file_id?: <库里图片id> | ref_name?: <输出目录里的文件名> | ref_base64?: <PNG/JPG base64>,
  prompt: "...", negative?: "...", model?: "...",
  weight?: 0.8, width?: <默认按参考图长宽比缩到约 1MP>, height?: …,
  steps?: 28, cfg?: 6.0, seed?: 0, ipadapter_file?: <一般不用传>, clip_vision?: …,
  import?: true }
→ { ok:true, started:true, kind:"ipadapter", model, source, width, height, ipadapter_file, clip_vision, weight }
```

### 4.3 姿势 / 线稿（ControlNet）

```
POST /api/gen/controlnet
{ pose_file_id? | pose_name? | pose_base64?,   # 三种给法，同上面
  prompt: "...", negative?, model?, strength?: 0.8, preprocessor?: "openpose",
  width/height/steps/cfg/seed/import 同上 }
→ { ok:true, started:true, kind:"controlnet", control_net, strength, preprocessor, … }
```

进度/结果事件和出图完全一样，只是多一个 `kind` 字段用于区分：
`gen_advanced_started{kind,source,model}` → `gen_progress{elapsed,kind}` →
`gen_done{files,file_ids,imported,kind}` / `gen_failed{error,kind}`。

**校验（PC 会返回 400，前端可以先挡）**：两个接口都要求 `prompt` 非空、输入图必须给；
底模架构已知但本机没有配套模型时返回 `no_ipadapter_for_arch` / `missing_controlnet_model`。

### 4.4 模型管理（"要下什么" + 一键补）

```
GET  /api/gen/models
→ { ok, comfyui_path, configured, models_dir, fetching,
    models: [ { id, name, file, target, size_bytes, present, actual_bytes, path,
                can_fetch, download_url, recommended? } ],
    wordlists: [...], requirements: {...} }

POST /api/gen/models/fetch      # { ids: ["ipadapter-plus-sdxl", ...] } 或 { all: true }
→ { ok, started, count, ids, models_dir }
→ 已是完整文件时：{ ok:true, started:false, reason:"already_complete" }
```

下载进度走 SSE：`gen_dl_started{count,ids}` → `gen_dl_progress{id,file,index,total_models,got,total,pct}`（约每 3 秒一条）
→ `gen_dl_done{ids,models_dir,hint}` / `gen_dl_failed{ids|error,done}`。
断点续传，中途断电重发同一个请求就接着下。

> 客户端建议：`present === false` 才显示"下载"；`configured === false`（PC 没配 ComfyUI 目录）
> 就只提示去找 PC 端设置，别发下载请求（会返回 400 `no_comfyui_path`）。

### 4.5 还没做的

- 工作流下拉（接 `workflows/presets.json` 里的 Anima 等预设）——归 DLC 自己界面；
- 参考图/姿势控制在**移动端**的交互（选图：可以从图库挑 `file_id`，也可以拍照/相册传 base64）。

## 五、分辨率 / 图生图 / 放大（2026-10-08 下午补，平板可以直接接）

### 5.1 先看尺寸边界

```
GET /api/gen/info  →  … "limits": { "min_side":256, "max_side":2048, "step":8,
                                    "sweet_spot":1024, "note":"…" },
                       "default_model": "...", "default_arch": "sdxl",
                       "model_arch": { "模型文件名": "sdxl"|"sd15" }
```

- **滑杆/输入框照 `limits` 画**（8 的整数倍）。超出范围 PC 会夹回来，并在 `/api/gen/run` 响应里回
  `width/height`（实际用的值）和 `warning`（做了什么修正），界面按响应显示即可。
- **SDXL 底模不要给 512**：SDXL 原生是 1024，低于 ~0.6MP 会糊成一团。PC 已兜底
  （请求 512 → 自动抬到 1024 并给 `warning`），但界面上最好直接别提供 512/768 这种档位。
- `model_arch` 可以给模型下拉加个"SDXL / SD1.5"小标记；SD1.5 已决定不再用（太老）。

### 5.2 图生图 / 图融合

```
POST /api/gen/img2img
{ file_id|name|base64,            # 底图
  prompt, negative?,
  denoise?: 0.6,                  # 0.3 微调 / 0.6 换衣服换背景 / 1.0 等于重画
  blend_file_id|blend_name|blend_base64?,   # 第二张图（可省）
  blend_factor?: 0.5, blend_mode?: "normal",  # normal/multiply/screen/overlay/soft_light/difference/add/lighten/darken
  steps?, cfg?, seed?, model?, import? }
→ { ok, started, kind:"img2img", model, source, blend, denoise, blend_factor, blend_mode }
```

**这是"图融合"的正解**：ComfyUI 原生节点 `ImageBlend` 先把两张图按 `blend_factor` 混，
再走 img2img 重绘。
**实测经验（别踩）**：`blend_factor` 0.4 + `denoise` 0.45 会出**双重曝光那种重影**——
那只是"两张图各占一半"，不是成画。想让融合结果成画，要么
`blend_factor` 0.25~0.3 + `denoise` 0.7~0.85（重画得多），
要么干脆用 IP-Adapter（`/api/gen/ipadapter`，参考图影响风格/角色，比硬混好看得多）。
只想**改造一张图**（换装/换场景）就别传 `blend_*`，直接 `denoise` 0.55~0.65。

### 5.3 想要比 2048 更大的图 → 用放大，不要硬开大分辨率

```
POST /api/gen/upscale      # 纯超分（推荐）：不重绘、不出彩噪
{ file_id|name|base64, scale?: 4（省略=模型原生倍率）, width?/height?（精确尺寸）, import? }
→ { ok, started, kind:"upscale", upscale_model, source_size, size }

POST /api/gen/hires        # 潜空间放大 + 低 denoise 重采样：会长新细节，但**有彩噪风险**
{ file_id|name|base64, prompt, scale?: 1.5, denoise?: 0.3, steps?, cfg?, model?, import? }
```

实测（RTX 4070 Laptop 8G）：`/api/gen/upscale` 1024→**4096 只用 9 秒**，画面内容不变、只是变清楚；
`/api/gen/hires` 1024→1536 用 54 秒，头发边缘会出现彩虹色噪点（denoise 调到 0.3 也还有）。
所以界面上"放大"按钮默认走 `upscale`，`hires` 作为可选项并标注"可能出彩噪"。
放大模型清单里已有 `upscale-anime-4x`（Real-ESRGAN anime 6B，18MB，`POST /api/gen/models/fetch` 可下）。

> 为什么不直接把 `width/height` 开到 4096：8GB 显存上 SDXL 出 2048² 基本必 OOM，
> 而且超出训练分辨率后容易出现重复肢体/多手多脚。**先出 1024 再放大**才是稳的路子。

进度事件与出图一致，`kind` 分别是 `img2img` / `upscale` / `hires`。
