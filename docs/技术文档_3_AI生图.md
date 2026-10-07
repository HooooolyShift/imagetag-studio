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

## 二、底模（`models\checkpoints`）

| 文件 | 说明 |
|---|---|
| `NoobAI-XL-v1.1.safetensors`（6.61 GB） | **当前主用**：danbooru/e621 语料，角色还原与构图跟随最好 |
| `animagine-xl-4.0.safetensors`（6.46 GB） | 备选：更"平涂"，但构图跟随弱一些 |
| 秋叶包自带若干 SD1.5 底模 | 仅遗留 |

## 三、出图脚本 `tools/gen_splash.py`

```powershell
python tools\gen_splash.py [底模] [每张几版=2] [只跑某张,如 05 / 可用逗号] [宽度=1216]
# 例：python tools\gen_splash.py NoobAI-XL-v1.1.safetensors 3 "" 1536
```

开屏封面规范：**1536×648（≈2.37:1，与开屏图片区同比例直出，不裁切）**，成品放 `assets\splash\`。

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
