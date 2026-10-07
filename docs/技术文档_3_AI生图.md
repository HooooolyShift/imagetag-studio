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

### 批量出图：一律交给计划任务跑（长任务保命）

踩过的坑：**本机 Agent 会话的工具调用被新消息打断时，它启动的进程会被一起杀掉**，
十几分钟的出图很容易半路死（实测死过两次，日志停在半途、没有任何报错）。
所以长任务走 `tools\batch_gen.py` + Windows 计划任务，脱离会话进程树：

```powershell
$py  = "C:\Users\27838\.conda\envs\imtag\python.exe"
$arg = 'tools\batch_gen.py --models NoobAI-XL-v1.1.safetensors,WAI-illustrious-SDXL-v17.safetensors --variants 2 --width 1536 --log "E:\文档\ChatGPT\图片标签分类\.splash_batch.log" --free'
$action  = New-ScheduledTaskAction -Execute $py -Argument $arg -WorkingDirectory "E:\文档\ChatGPT\图片标签分类"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddYears(1)
Register-ScheduledTask -TaskName "ImageTagSplashBatch" -Action $action -Trigger $trigger -Force
Start-ScheduledTask -TaskName "ImageTagSplashBatch"     # 用完可 Unregister-ScheduledTask -TaskName ImageTagSplashBatch -Force 删掉
```

- `batch_gen.py`：ComfyUI 不在就自己拉起并等就绪；逐个底模出图；**已存在的图直接跳过**（打断后重跑安全，
  重出某张就先把它从 `.splash_gen\<底模>\` 移走，或设 `SPLASH_FORCE=1`）；`--free` 结束后卸模型还显存；
  日志末尾写 `ALL DONE` 便于外部轮询。
- 进度看 `.splash_batch.log`；ComfyUI 自己崩了（外部杀掉）重跑一次即可，已出的图不会重做。

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
| ComfyUI 自定义节点 | 秋叶包里的 Advanced-ControlNet / AnimateDiff / Impact-Pack 会 monkey-patch 采样链，**连续出图时硬崩过一次**（faulthandler 堆栈停在采样函数里）。批量出图请用 `--disable-all-custom-nodes` 干净模式（`tools\batch_gen.py` 默认就这么拉起） |
| 1536×648 出图耗时 | 实测 **39~45 秒/张**（RTX 4070 Laptop 8 GB，60 步）；1216×512 是 27~33 秒，按需选 |
| 克隆人（多人题材） | 两个来源：①**背景里挂的照片/相框又画了同一个角色**（看着就是一排克隆）；②提示词只写 `2girls`，但没把两个角色各自锚死，模型直接复制同一张脸。对策见 `JOBS` 里的 `duo_bunny2`：**背景换成纯色摄影棚**（并负向 `photo, framed picture, picture frame, portrait`），两个角色分别写 `(hatsune miku:1.1)`/`(kasane teto:1.1)` + 各自发型特征，负向压 `3girls, 4girls, extra girls, extra person, clone, duplicated, twins`。实测 WAI **3/3 干净**；NoobAI 仍会多画人（3 张里 1 张画成三个女孩、1 张有漂浮兔耳瑕疵） |

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
- 2026-10-07（双底模全量 A/B）：按用户"并行出图、由我挑图"的要求，7 个题材 × 2 版 × 2 底模 = **28 张**，
  全部 **1536×648**（最终规格，挑中的可直接进 `assets\splash\`）。参数同固化值（60 步、CFG 5.5、
  dpmpp_2m/karras、关二段放大与锐化），seed = `20261006 + 题材号*977 + 版号*41`（**跨底模同 seed**，便于 A/B）。
  产物：`.splash_gen\NoobAI-XL-v1.1\`（14 张）+ `.splash_gen\WAI-illustrious-SDXL-v17\`（14 张）；
  对比大图 `montage_对比_NoobAI_vs_WAI.png`（2088×1818，每行 4 格 = NoobAI a/b、WAI a/b）。
  耗时 39~45 秒/张、总计约 19 分钟；跑完已 `POST /free` 还显存。
  **观察（供挑图参考）**：WAI 线条更细、脸和手更稳、背景（海滩/货架/看板）更"像样"，整体更精致干净；
  NoobAI 更暖、更有体积感，但更容易把双马尾画爆（01-a）或比例夸张。两个底模对
  "character on the left half" 的跟随都偏弱，人物普遍居中偏右——**后续想让主体偏左，得改提示词策略**
  （比如换 `character on the right` + 明确留白，或干脆接受居中，挑图时注意左侧留白是否够放文字）。
  过程中的坑与修复：第一批出到第 10 张时 **ComfyUI 硬崩**（stderr 里有 faulthandler 致命堆栈，
  停在自定义节点打补丁的采样链上）。修复 ①`gen_splash.py` 连续 5 次连不上就快速失败并返回 2，
  不再傻等 300 秒；②`tools\batch_gen.py` 负责"缺 ComfyUI 就拉起、崩了就重启重跑"，**已存在的图跳过**；
  ③批量出图改用 `--disable-all-custom-nodes` 干净模式——改完后 28 张一次跑完没有崩。
  另：长任务一律走 Windows 计划任务（见上文"批量出图"），会话进程被打断会连坐杀掉子进程。
- 2026-10-07（用户反馈 → 07 防克隆重做）：用户看完 A/B 后的结论——**整体 WAI-illustrious v17 胜出**；
  唯一例外是 **NoobAI 的 01-a 构图用户"喜欢"，保留为候选**（此前"疑似"是打字错误）；07 出现**克隆人**，要求避免。
  做法：新增 `JOBS[7] = duo_bunny2`（提示词/负向见第四节"克隆人"行）——背景改纯色摄影棚（去掉照片墙）、
  两个角色分别用 `(hatsune miku:1.1)` / `(kasane teto:1.1)` 锚定、负向压 3girls/4girls/clone/相框人像。
  结果（各 3 版、1536×648，21:26 跑完并已还显存）：**WAI 3/3 干净**（`08_duo_bunny2_a/b/c.png`）；
  **NoobAI 3 版里 1 版画成三个女孩、1 版有漂浮兔耳瑕疵** → 多人题材优先用 WAI。
  旧版 07 留在 `.splash_gen\<底模>\07_duo_bunny_*.png` 只作对照，不要再用。

## 七、项目规矩（2026-10-07 起，来自用户与 PC 端主程序会话）

1. **只有用户明确说"正式更新 / 推送 / 发版"时，才允许 `git push`、更新 GitHub release、
   同步 D 盘正式版副本（`D:\图片标签分类`）**。平时改完、验证完只做**本地 commit** 记录改动，
   然后把结果汇报给用户等指示。（此条覆盖早期"每版做完主动发布"的旧约定。）
2. **413 巡查不用你跑（2026-10-07 用户明确：所有会话各自查会乱套）**：巡查只由 PC 端主程序会话
   统一负责（每日定时 + 只在「危」时提醒用户）。你只要控制贴图量；如果出现"发不出消息 / 一读图就报错"，
   把线索报给用户或 PC 端会话即可。**裁剪（`trim-rollout-images.ps1 -Apply`）只能由用户在 PC 端会话同意后执行。**
   ——出图对比**不要在本会话反复贴大图**（base64 会把历史撑爆），图一律落盘再引用路径：
   工作图 `.splash_gen\<底模名>\`，成品 `assets\splash\`。
3. **22:30 起不要再起新的出图批次**（这台机器**每晚 23:00 断电**）：批次要么能在 22:30 前收尾，
   要么拆小分批跑；模型下载必须能续传（`tools\fetch_model.py` 已支持）；
   22:30 前把当天结论写进本文档并**本地 commit**；不要留下改了一半、跑不起来的脚本。
