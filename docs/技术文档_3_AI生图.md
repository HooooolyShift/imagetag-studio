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

## 八、SwarmUI 图形壳（2026-10-07 新增，给用户"少调参"的出图/改图界面）

> 用户反馈：ComfyUI 原界面参数太多、不方便。按"先找开源壳，没有再自建"的指示，
> 选定 **SwarmUI**（MIT，活跃，本质是 ComfyUI 的前端壳），已装好并接到本机 ComfyUI。

- **位置**：`E:\SwarmUI`（`git clone https://github.com/mcmonkeyprojects/SwarmUI`，v0.9.8.3；
  用 `dotnet build src/SwarmUI.csproj -c Release -o src/bin/live_release` 构建，本机 .NET 9 SDK 可编；
  官方提示**将来会要求 .NET 10 SDK**，到时再装）。
- **接后端**：`Data/Backends.fds` → `comfyui_api`，`Address: http://127.0.0.1:8188`
  （即我们自己管的那个 ComfyUI，不用 SwarmUI 再装一份；批量脚本与我这边共用同一后端）。
- **模型路径**：`Data/Settings.fds` → `Paths.ModelRoot = E:\ComfyUI\ComfyUI-aki-v1.5\ComfyUI-aki-v1.5\models`，
  子目录 `checkpoints` / `loras` / `vae` / `embeddings` / `controlnet` / `clip_vision` 沿用 ComfyUI 的命名，
  **不复制模型**。实测 `/API/ListModels` 已列出 16 个底模（含 NoobAI-XL v1.1、WAI-illustrious v17）。
- **界面语言/安装状态**：`DefaultUser.Language = zh`（默认中文）；`IsInstalled = true`、
  `InstallVersion = 0.9.8.3`、`LaunchMode = web`（跳过安装向导，启动即开浏览器页面）。
- **界面中文化（2026-10-07 二次修正）**：光改 `DefaultUser.Language` 不够——**已存在的 `local` 用户**
  仍是 en。用 API 直接改当前用户：`POST /API/ChangeUserSettings`，
  body `{"session_id":"…","settings":{"language":"zh"}}`（**注意**：文档写的是 `rawData`，实际 API 吃扁平的
  `settings`，写成 `rawData` 会 400）。验证：`POST /API/GetMyUserData` → `language = "zh"`。
  前端语言来源：`js/translator.js` 先读 cookie `display_language`，没有就取 `data.language`（即用户设置），
  所以刷新页面即为中文；万一你以前手动切过英文，清掉该 cookie 或 Ctrl+F5 强刷即可。
- **避免浏览器自动翻译**：SwarmUI 原有页面是裸 `<html>`（没有 lang），浏览器会猜语言并弹翻译。
  已在 `src/Pages/Shared/_Layout.cshtml` 本地改成 `<html lang="zh-CN" translate="no">`
  并加 `<meta name="google" content="notranslate" />`，改完 `dotnet build` 重建 + 重启。
  ⚠️ 这是我们**改动过上游文件**：以后 `git pull` 升级 SwarmUI 时，如果这两行被覆盖，按这个再补一次。
- **启动**：桌面快捷方式 **「图片标签工坊 生图台」**（图标取自 `A绘世启动器.exe`）；
  旧的桌面 `A绘世启动器.lnk` 已移到 `E:\SwarmUI\_backup_desktop_A绘世启动器.lnk`（可恢复）。
  底脚本 `E:\SwarmUI\start-sd.cmd`：先确保 ComfyUI 在 8188 跑着（没跑就用干净模式拉起），再开 SwarmUI。
- **端到端验证**：`POST /API/GenerateText2Image`（WAI-illustrious v17，1024×576，24 步）出图成功，
  产物 `E:\SwarmUI\Output\local\raw\2026-10-07\2146001-...png`；后端 `status = running`。
- **本地 API 免登录**：`POST /API/GetNewSession`（空 body）直接给出 `user_id = local` 与全部权限，
  可脚本化配置/出图（改后端、列模型、生成都用它）。

### 覆盖度评估（"ComfyUI 的功能能不能都用上"）

| 能力 | SwarmUI 现状 |
|---|---|
| 文生图 / 图生图 / 批量 / 种子与变体 | 自带，参数可折叠，还能用 Preset 简化 |
| LoRA、Refiner、高清放大、FreeU、Seamless | 自带 |
| ControlNet（含预处理器、多重叠加） | 自带（`comfyui_controlnet_aux` 在我们后端里，预处理器可用） |
| IP-Adapter / 参考图（风格与角色一致） | 自带（`ComfyUI_IPAdapter_plus` + `ip-adapter_xl.pth` + `clip_h.pth` 都在后端） |
| 局部重绘 / 扩图（画遮罩） | 自带 Image Editor / Mask 工具 |
| **任意 ComfyUI 原生工作流** | 自带 **Comfy Workflow Editor**：可以手搭/导入任意工作流，并把其中的参数绑定成界面控件 |
| 我们装的所有 custom_nodes | 后端就是我们的 ComfyUI，节点都在（除"干净模式"启动时被禁用的那批） |
| **中文自然语言 → 提示词** | ❌ **没有**：SwarmUI 的 LLM 支持目前是占位（`LLMs/LLMParamInput.cs` 自己写着 TODO），只有实验性文本生成 |
| 我们固化的开屏参数（1536×648 / 60 步 / CFG 5.5 / 长负向词） | ❌ 没现成的，需要做成 Preset/Style |

**结论**：底层能力 = ComfyUI 的全部（因为后端就是它，且能用原生工作流编辑器）；
缺口只有两处"上层便利性"，由我来补：

1. **中文自然语言入口**：写一个 SwarmUI 扩展（或在界面旁挂一个小服务），
   用本机 Ollama（qwen3-8b，离线）把中文描述转成 danbooru tag → 填进提示词框。
2. **项目预设**：把"开屏 1536×648 + 60 步 + dpmpp_2m/karras + CFG 5.5 + 我们那套负向词"做成 Preset/Style；
   换装/局部重绘也各做一个预设（遮罩 + denoise 0.5~0.65 + 模板提示词）。

（可选补充：**Krita + Krita AI Diffusion**（GPL-3，持续更新）连同一个 ComfyUI 后端，
画笔式局部重绘/换装体验最好，需要另外装 Krita（约 250 MB）——用户想要再加。）

### 8.1 语言与界面改造（2026-10-07，用户要求"直接用中文、收起复杂项"）

- **中文没生效的真正原因**：SwarmUI 自带 `languages/zh.json` 其实**是完整的**（672 条，含 234 条参数说明），
  但前端 `js/translator.js` 第一行优先读浏览器 cookie `display_language`，残留 `en` 就会一直英文；
  且原版只对带 `.translate` 类的元素做替换，大量参数 tooltip / placeholder 根本不在替换范围内。
- **本机改动（都可被 git pull 覆盖，覆盖后照此重打）**：
  1. `src/Pages/Shared/_Layout.cshtml`：`<html lang="zh-CN" translate="no">` + `<meta name="google" content="notranslate">`
     （防止浏览器弹"是否翻译"）。
  2. `src/wwwroot/js/genpage/main.js`：语言以服务端用户设置为准（`language = data.language || language`）并把 cookie 同步成该值。
  3. `src/wwwroot/js/translator.js`：替换范围从 `.translate` 扩大到 `.translate, [title], [placeholder]`。
  4. `POST /API/ChangeUserSettings`（**扁平** `settings`，不是文档写的 `rawData`）把 local 用户设为 `zh`。
- **补译工具**：`tools/translate_swarmui_zh.py` —— 用本机 Ollama（`qwen3-8b`，本机最好且能进 8G 显存）批量翻译
  界面字符串，支持断点续跑（`--cache .verify_pics/zh_new.json`）与 `--merge` 合并进 `languages/zh.json`（先备份）。
  界面字符串由无头浏览器导出到 `.verify_pics/dom_strings2.json`（按 `T:` title / `P:` placeholder / `X:` 元素文本三种查表形态）。
  实测界面需翻译 1087 条（自带 zh.json 只覆盖 7 条命中）。

### 8.2 本地扩展：中文自然语言 → 提示词（`src/Extensions/PromptHelper/`）

- 界面右下角"提示词助手"面板：写中文 → 选「正向 / 只翻服装（换装）/ 负向」→ 点按钮，
  用本机 Ollama 转成 danbooru tag 串，直接填进 Prompt 框（可选"追加"）。
- 组件：`PromptHelperExtension.cs`（注册 `POST /API/PromptHelper`，调 `http://127.0.0.1:11434/api/chat`）、
  `Assets/prompt_helper.js` / `.css`、`csproj`。
- 两个坑：①**命名空间不能以 `SwarmUI.` 开头**，否则会被当成内置扩展去 `src/BuiltinExtensions` 找；
  ②路由名 = 方法名（方法必须叫 `PromptHelper`），且扩展 DLL 缓存在
  `src/bin/extensions/SwarmExtension<文件夹名>/…-<hash>.dll`，**改完源码必须删掉/移走这个缓存**才会重新编译。

### 8.3 danbooru 全量词表接进补全（2026-10-07）

- 项目自带词表 `app/booru_zh/*.csv`（列：tag,category,aliases,zh,count,notes）共 **75,069 条**
  （character 35,384 / general 30,691 / copyright 8,414 / meta 580，每条都有中文名、别名、热度、备注）。
- 工具 `tools/build_swarm_autocomplete.py` 把它转成 SwarmUI 的补全词表
  `E:\SwarmUI\Data\Autocompletions\danbooru_zh.csv`（列：tag,category,count, → 分类着色 + 热度排序）。
- 用户设置（本机 local 用户已设好）：`autocomplete.source = danbooru_zh.csv`、
  `autocomplete.sortmode = Frequency`、`spacingmode = Spaces`、`suffix = ", "`。
  实测 `POST /API/GetMyUserData` 返回 **75,067** 条补全项 → 提示词框里中英 tag 都能补全。

### 8.4 中文化结果（实测）

- `languages/zh.json` 从 672 条扩到 **1,751 条**（备份 `zh.json.bak-20261007-220459`）。
- 无头浏览器实测：参数 tooltip **916/916 全中文**（改之前只有 27/358），
  参数标签 592/684 中文（剩下的多是 ReVision / IP-Adapter 这类专有名词）。
- 截图（简易页）：`.verify_pics\swarm_ui.png`；界面为中文，右下角有"提示词助手"面板。

### 8.5 提示词必须落在 danbooru 词表里（2026-10-07 用户要求）

用户明确：**"自然语言生成提示词要优先输出 danbooru 里有的，而不是自己捏造；能匹配上就必须用匹配的，
实在匹配不到才允许自造"**。做法（`BooruDictionary.cs`）：

1. 词典来源（全部合并，实测 **233,796 个 tag / 143,087 个别名**）：
   - 项目词表 `app\booru_zh\*.csv`（带中文名与别名 → 中文能直接命中）；
   - **按模型分的官方/社区 tag 表**（`E:\SwarmUI\Data\Autocompletions\`）：
     `NoobAIXL1.1_underscore.csv`（141,801）、`illustriousV1.0_underscore.csv`（93,907）、
     `anima-1.0.csv`（108,256）、`anima-2.9B-preview-V1.csv`，
     来自 <https://github.com/BetaDoggo/danbooru-tag-list/releases>（就是各模型训练时用的 tag 全集）。
2. 逐条校验：先去权重括号、统一下划线/空格、转小写；命中词表 → **换成词表里的规范写法**；
   命中别名/中文名 → 换成对应规范 tag；对不上 → 保留原样并在结果里单独列出"词表外"。
3. 占位/废话词（bad tag / none / n/a…）直接丢弃；`masterpiece / best quality / absurdres` 这类
   非 danbooru 但画图要用的质量词走白名单保留。
4. 实测：一句中文 → `1girl, hatsune miku, smile, looking at viewer, upper body, holding photo,
   detailed background, soft lighting, absurdres, very aesthetic`（词表外的会单独列出，方便手动替换）。

### 8.6 Anima 模型（2026-10-07 新增，用户要求）

- 用户提到的"anima"= **Anima（CircleStone Labs × Comfy Org 合作，2B 参数，专精二次元）**，
  本地原先没有（只有 Animagine XL 4.0）→ 已下载：
  - `models\diffusion_models\anima-aesthetic-v1.1.safetensors`（3.9 GB）
  - `models\text_encoders\qwen_3_06b_base.safetensors`（1.14 GB）
  - `models\vae\qwen_image_vae.safetensors`（242 MB）
  下载脚本 `tools\fetch_anima.py`（走 hf-mirror；urllib 的 308 重定向已在 `fetch_model.py` 里补上）。
- 官方用法：ComfyUI 原生支持；turbo 版 CFG 1 / 8-12 步，常规版 30-50 步、CFG 4-5。
- **实测跑通**（2026-10-07 22:5x，768×768 / 24 步 / CFG 4.5 / euler+simple，57.7 秒，RTX 4070 Laptop）：
  产物 `.verify_pics\anima_test.png`。写 API 工作流的关键（照官方 `anima_comparison.json` 抄的）：
  ```json
  UNETLoader(anima-aesthetic-v1.1.safetensors)
    → ModelSamplingAuraFlow(shift=3.0) → KSampler
  CLIPLoader(qwen_3_06b_base.safetensors, type="stable_diffusion")   ← 注意 type 是 stable_diffusion，不是 qwen_image
  VAELoader(qwen_image_vae.safetensors) → VAEDecode
  ```
  观感：偏柔和/厚涂感，与 SDXL 系（NoobAI/WAI 的平涂）明显不同；官方建议常规变体 30-50 步、CFG 4-5，turbo 变体 CFG 1、8-12 步。
  待办：把它做成 SwarmUI 的自定义工作流预设，界面上也能直接选 Anima（SwarmUI 的模型下拉默认只认 checkpoints）。

### 8.7 "页面打不开 / 看不到提示词助手" 的根因与修法（2026-10-07 晚）

- **端口会断**：SwarmUI 是我在会话里起的进程，**我的会话结束/机器断电后它不会自己复活**，
  期间我又反复停服重建扩展，所以用户点的时候正好是"拒绝连接"。
  → 已注册**开机自启任务** `ImageTagSwarmAutoStart`：登录时静默拉起 ComfyUI(8188, 干净模式) + SwarmUI(7801)；
  脚本 `E:\SwarmUI\autostart-services.ps1`（纯 ASCII，避免 PS5.1 编码坑）。
- **扩展脚本没被页面加载**：SwarmUI 的 `GatherExtensionPageAdditions()` 只把**它自己启动时登记的**扩展资源
  挂进页面，我们后加的扩展 `/ExtensionFile/PromptHelperExtension/...` 一直 404，页面里也没有 `<script>`。
  → 改为走主站静态目录：把 JS/CSS 复制到 `src\wwwroot\js\itg_prompt_helper.js`、
  `src\wwwroot\css\itg_prompt_helper.css`，并在 `src\Pages\Shared\_Layout.cshtml` 里引用
  （和 lang/notranslate 一起属于上游文件的本地改动清单）。
- 验证：页面 HTML 里能搜到 `itg_prompt_helper.js`；无头浏览器实测
  `#prompt_helper_panel` 存在，面板截图 `.verify_pics\panel.png`。
- **注意**：提示词助手是 **SwarmUI（7801）** 的界面元素，不在 ComfyUI 原生界面（8188）里；
  打开 `http://localhost:7801/#simple`，面板固定在**右下角**（"提示词助手（中文 → 标签）"）。
- **提示词助手依赖本机 Ollama（11434）**：报 `调用本机 Ollama 失败…(127.0.0.1:11434)` 就是 Ollama 没在跑。
  它不在我这边的进程里，**机器重启/断电后不会自己起来**——已把 `D:\LocalAI\start-ollama.cmd`
  加进 `E:\SwarmUI\autostart-services.ps1`（登录自启任务 `ImageTagSwarmAutoStart` 现在一次拉起
  Ollama + ComfyUI + SwarmUI 三个服务，实测返回 0、三个端口都在听）。
  前端也在报错时补了一句"双击 `D:\LocalAI\start-ollama.cmd` 启动"的提示。
  显存纪律：启动前后看 `nvidia-smi`，qwen3-8b 约占 4.9 GB，用完 `ollama stop qwen3-8b` 让位。

### 8.8 生图 DLC（`dlc/comfyui_helper/`）与"额外功能"落地（2026-10-08）

> 用户决定：把原来那套 SwarmUI 封装整体改成主程序的**可选安装 DLC**，主界面重做成原生窗口；
> "浏览器页面不要了"。分工：宿主（PC 端会话）做 DLC 机制/原生窗口骨架/共享词库，
> 生图端（本会话）做提示词助手、工作流、额外功能与文档。**ComfyUI 客户端实现统一在
> `app/comfy_client.py`**（DLC 的 `comfy.py` 只是转发，别再复制一份）。

- **中文 → 提示词**：一次输出**正向 + 负向**（`generate_both`，不用切模式）；词表校验规则按用户要求——
  能对上就用词表的规范写法、对不上保留并回报；质量词白名单（masterpiece/absurdres…）不受模型过滤影响。
  界面上新增「提示词词表范围」＝ 全部 / NoobAI / Illustrious / Anima（回答"这个 tag 目标模型认不认"）。
- **悬浮英文 tag 显示中文**：`TagHoverTextEdit`（只显示词表命中的），中文来源 = 项目词典 + 主程序
  `app/tag_zh_dict.json`，实测 12.6 万条；人工口头说法统一走共享表 `app/tag_zh_aliases.json`
  （本会话往里补了 34 条：大头照/侧面/背面/看镜头/闭眼/微笑/坐站躺趴/手牵手/冷色调/逆光/柔光/白天夜景/海边/室内外/雪雨樱花/纯色背景…）。
- **自动接入**：`dlc/comfyui_helper/autoconnect.py` 找安装目录 + 探活 8188/8189/8000 + 一键后台启动/停止/完整模式重启。
- **模型切换**：SDXL（checkpoints）/ Anima（diffusion_models + Qwen3 编码器 + Qwen-Image VAE），切家族自动套预设
  （SDXL 1536×648/60/5.5/dpmpp_2m+karras；Anima 768×768/30/4.5/euler+simple）。Anima 实跑 34.4s。
- **额外功能（都做成"能连上就用、缺啥就提示"）**：
  - 能力探测 `ComfyClient.capability_report()`；面板常驻 ✅/⚠️；点用不了的功能弹窗列出**缺什么 + 怎么补**；
    还给了「用完整模式重启 ComfyUI」按钮（本机原来为了批量稳定一直是 `--disable-all-custom-nodes` 干净模式，
    自定义节点被禁用，IP-Adapter/预处理自然就没了）。
  - **局部重绘/换装**：`inpaint_workflow`（LoadImage → ImageToMask(red) → VAEEncodeForInpaint），白=重画；
    实测 512² 换装 39 秒、遮罩外不动（对比图 `.verify_pics\inpaint_compare.jpg`）。
  - **参考图 IP-Adapter**：走**手动加载**（`IPAdapterModelLoader` + `CLIPVisionLoader` + `IPAdapterAdvanced`），
    **不要用 UnifiedLoader**——它按预设名找固定文件名，会报 `ClipVision model not found`。
  - **姿势 ControlNet**：`OpenposePreprocessor` + `ControlNetLoader(controlnet++ union SDXL)` + `ControlNetApplyAdvanced`；
    实测 512²/12 步 72.6 秒出图。
  - **批量队列**：可取消 + **断点续跑**（产物名 `gen_s<种子>.png`，存在就跳过；实测重跑 0.5 秒全跳过）。
  - **自动入库 / 自动打标**：两个**可选项**（勾选框），调 `host.scan_into_library()` / `host.tag_files()`，失败只提示不打断。
  - **模型下载/校验**：`models.json` 驱动，后台 `scripts/fetch_model.py` 断点续传，完成后自动刷新列表。
- **本机补齐的东西（2026-10-08）**：自定义节点包其实早就装好（`ComfyUI_IPAdapter_plus` / `comfyui_controlnet_aux` 等），
  缺的是 **①完整模式启动 ②配对的 IP-Adapter 两件套**：
  `ip-adapter-plus_sdxl_vit-h.safetensors`（808MB，models/ipadapter）+
  `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`（2411MB，models/clip_vision）。
  旧组合 `ip-adapter_xl.pth` + `clip_h.pth` 会报 `size mismatch ... [8192,1280] vs [8192,1024]`（编码器维度不对）。
  下载脚本 `tools/fetch_ipadapter.py`（DLC 里也有一份）。开机自启与 SwarmUI 启动脚本已从干净模式改为**完整模式**。

