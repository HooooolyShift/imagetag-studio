# 技术文档 3 ｜ AI 生图（ComfyUI）

> 负责人：AI 生图会话（新建，2026-10-07）
> **规矩**：每次换模型/改提示词/改参数后，把"用的是哪个底模、什么参数、怎么复现、结果图在哪"写进本文件；
> 其他 4 份技术文档（PC 端 / 宣传片 / 平板端 / 手机端）随时可读。

## 一、环境

- ComfyUI（秋叶整合包）：`E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5`
  - 2026-10-06 已升级到官方最新（实测 **0.39.0**，commit `7a5dad69`）
  - 依赖补装：blake3 / comfy-aimdo / comfy-kitchen / comfyui-frontend-package 1.53.10
  - **torch 2.5.1+cu124 → 2.11.0+cu128**，xformers 0.0.35（新版 ComfyUI 要求新 torch，否则起不来）
  - 启动：`cd <ComfyUI 目录>; .\python\python.exe main.py --listen 127.0.0.1 --port 8188`
- 显卡：RTX 4070 Laptop 8 GB / 驱动 610.88；1216×512、60 步大约 20–30 秒一张

### 启动 / 让显存（遵照 AGENTS.md 的显存纪律）

- 启动 ComfyUI：`cd <ComfyUI 目录>; .\python\python.exe main.py --listen 127.0.0.1 --port 8188`
  （冷启动到能出图约 **55 秒**；确认服务起来用 `GET /system_stats`）
- 出完图让位：`POST http://127.0.0.1:8188/free`，body `{"unload_models":true,"free_memory":true}`
  → 底模占的约 **3.9 GB** 显存立刻还回，服务本身不用停（再出图时重新加载模型即可）
- 出图前先 `nvidia-smi` 看占用；用户已有进程占显存时不要抢，宁可等或改用更小的方案

## 二、底模（`models\checkpoints`）

| 文件 | 说明 |
|---|---|
| `NoobAI-XL-v1.1.safetensors`（6.61 GB） | **当前主用**：danbooru/e621 语料，角色还原与构图跟随最好 |
| `WAI-illustrious-SDXL-v17.safetensors`（6.46 GB） | **2026-10-07 新增，与 NoobAI 并行候选**：Illustrious 血统的社区热门合并模型，线条更干净、成图更"精致"，与 NoobAI 风格差异明显，提示词习惯完全兼容（同为 danbooru tag） |
| `animagine-xl-4.0.safetensors`（6.46 GB） | 备选：更"平涂"，但构图跟随弱一些 |
| 秋叶包自带若干 SD1.5 底模 | 仅遗留 |

### 怎么加新底模（`tools\fetch_model.py`）

```powershell
python tools\fetch_model.py <URL> "E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5\models\checkpoints\<文件名>.safetensors" [--sha256 <值>]
```

- **本机直连 civitai.com 不通**（curl 报 connect timeout），必须走系统代理 `http://127.0.0.1:29758`
  （和 git push 用的同一个）。`curl.exe` 不读 Windows 系统代理，`Invoke-WebRequest` 又不方便续传，
  所以自己写了 `tools\fetch_model.py`（urllib 会自动读系统代理 + 断点续传 + 重试 + 进度）。
- 下载完**必须核对**：文件字节数 == Civitai API 的 `sizeKB*1024`，`Get-FileHash -Algorithm SHA256`
  == 该文件版本 API 里的 `SHA256`（`AutoV2` 就是它的前 10 位）。6.5 GB 下载约 11 分钟（实测 ~10 MiB/s）。
- 放进 `models\checkpoints` 后 ComfyUI **不用重启**，再请求 `/object_info/CheckpointLoaderSimple` 就能看到新底模。

## 三、出图脚本 `tools/gen_splash.py`

```powershell
python tools\gen_splash.py [底模] [每张几版=2] [只跑某张,如 05 / 可用逗号] [宽度=1216]
# 例：python tools\gen_splash.py NoobAI-XL-v1.1.safetensors 3 "" 1536
```

开屏封面规范：**1536×648（≈2.37:1，与开屏图片区同比例直出，不裁切）**，成品放 `assets\splash\`。

**产物目录（2026-10-07 起）**：按底模分子目录 `.splash_gen\<底模名>\`，
多个底模并行出图时不会互相覆盖；**同一 seed 在不同底模下构图接近，可直接 A/B 对比**。
想指定目录就再加第 5 个参数。例：

```powershell
python tools\gen_splash.py NoobAI-XL-v1.1.safetensors 3
python tools\gen_splash.py WAI-illustrious-SDXL-v17.safetensors 3
```

## 四、实测参数与坑（重要）

| 项 | 结论 |
|---|---|
| 二段放大 | **关**。LatentUpscale + img2img 会重影/彩噪/形体扭曲（SDE 更严重），开屏尺寸用不上 |
| 锐化后处理 | **不要**。ImageSharpen 或加 `clean lineart/crisp` 只会线更粗、更像过度锐化 |
| 采样 | `dpmpp_2m` + `karras`，**60 步**收敛（80 步无提升）；CFG 5.5 比 6.0 少 halo；`euler_ancestral` 会画坏脸 |
| 画风 tag | `anime coloring, flat color, cel shading`（对比后用户选定的画风，线最细、最不发腻） |
| 长负向词 | **会打爆 SDXL**：把 SD1.5 那套 `no humans / scenery only / flat shading …` 直接搬过来会出大面积彩噪甚至近空白，必须精简 |
| wide shot / full body | 2.37:1 横幅再要求全身远景 → 空旷大厅、人物蚂蚁大；改 `half body` / `medium shot` |
| 头发（钻头马尾） | **平铺写法** `twintails, drill hair`，给头发加权重或堆同义词（drill twintails/spiral curls/ringlets）会长出 4~6 个钻头；靠负向 `extra hair, multiple twintails, four twintails, extra drills` 压 |
| 网袜 | 画不好（糊成白块），改 `black thighhighs` 并把 `fishnet` 写进负向 |
| 兔女郎服 | 要 `strapless`（无肩带抹胸）+ `high-cut leotard` + `rabbit tail`，并把 `shoulder straps/halterneck` 写进负向 |

## 五、当前成品

- `assets\splash\`：14 张 1536×648 初音未来 / 重音テト开屏图（另有单独打包：`K:\ImageTagStudio\开屏图（14张）\` 与 `开屏图_14张.zip`）
- 生成与挑选记录见仓库根目录 `开发计划.md`

## 六、变更记录

- 2026-10-07：技术文档建立；记录 ComfyUI 升级、NoobAI 主用、二段放大/锐化/发型等实测结论。
- 2026-10-07（接手自检）：读完本文档与技术文档 1 后做了一次流水线冒烟测试——
  ComfyUI 冷启动成功（**0.39.0**，torch **2.11.0+cu128**，前端 1.53.10，CUDA `cudaMallocAsync`，
  空闲显存 7068 MiB）；用 `gen_splash.py` 的固化参数跑 `JOBS[0]`（miku_wall，NoobAI-XL-v1.1、
  1216×512、60 步、CFG 5.5、dpmpp_2m/karras、关二段放大与锐化、seed 20261983）出一张，
  **33.2 秒**（含首次加载底模），画风/比例/分辨率与 `assets\splash\` 既有 14 张一致，
  确认升级后整条链路没坏。
  产物（临时，勿当成品）：`.splash_gen_smoke\smoke_01_a.png`，被 `.gitignore` 的 `.splash_gen*/` 覆盖。
  出图后调 `/free` 卸模型，显存从 5812 MiB 回落到 **2037 MiB（空闲 5912 MiB）**；
  ComfyUI 仍在 `127.0.0.1:8188` 常驻（不占显存），随时可继续出图。
- 2026-10-07（新增并行底模）：按用户要求"再弄一个 SDXL 和 NoobAI 并行、由用户挑图"，
  新增 **WAI-illustrious-SDXL v17**（Civitai 模型页 id `827184`，
  下载 URL `https://civitai.com/api/download/models/2883731?fileId=2763986`，
  文件名 `WAI-illustrious-SDXL-v17.safetensors`，6.46 GiB / 6938040682 字节，fp16、base Illustrious），
  落在 `E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5\models\checkpoints\`。
  完整性已核对：字节数与 API 的 `sizeKB*1024` 一致，`SHA256=F116B0C78FF441467B0CDC8F1936E1ED18EA31E9997C7B132B1B8DB533F0BD04`
  与官方一致；safetensors 头可解析（2515 个张量，`conditioner` / `first_stage_model` / `model.diffusion_model` 齐全）。
  新增工具 `tools\fetch_model.py`（带断点续传、自动走系统代理，详见上文"怎么加新底模"）。
  `gen_splash.py` 改为按底模输出到 `.splash_gen\<底模名>\`（多模型并行不覆盖，同 seed 可 A/B）。
  冒烟对比（同一 seed 20261983、job 01 miku_wall、1216×512、60 步、CFG 5.5、dpmpp_2m/karras、
  关二段放大与锐化）：`NoobAI .splash_gen_smoke\smoke_01_a.png`（33s）、
  `WAI .splash_gen_smoke\wai_01_a.png`（27s）——均正常出图，WAI 线条更干净、色调更"冷"更平，
  NoobAI 观感更暖更"厚"一点。此外 WAI 只需再请求一次 `/object_info` 就被 ComfyUI 识别（无需重启）。

## 七、项目规矩（2026-10-07 起，来自用户与 PC 端主程序会话）

1. **只有用户明确说"正式更新 / 推送 / 发版"时，才允许 `git push`、更新 GitHub release、
   同步 D 盘正式版副本（`D:\图片标签分类`）**。平时改完、验证完只做**本地 commit** 记录改动，
   然后把结果汇报给用户等指示。（此条覆盖早期"每版做完主动发布"的旧约定。）
2. **留意 413（请求体过大）风险**：定期跑
   `pwsh -NoProfile -File "$env:USERPROFILE\.codex\skills\codex-session-trim\scripts\scan-sessions.ps1" -IncludeArchived`
   看本项目线程有没有被标「危」（请求体 ≥40 MiB，或 ≥20 MiB 且历史报过 413）；有就报给用户。
   **裁剪（`trim-rollout-images.ps1 -Apply`）必须先经用户同意。**
   ——出图对比**不要在本会话反复贴大图**（base64 会把历史撑爆），图一律落盘再引用路径：
   工作图 `.splash_gen\<底模名>\`，成品 `assets\splash\`。
