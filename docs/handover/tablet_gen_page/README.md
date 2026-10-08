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

## 四、后续（等生图端确认后我会再同步）

- 工作流下拉（接 `workflows/presets.json` 里的 Anima 等预设）；
- 参考图（IP-Adapter）/ 姿势控制（ControlNet）——PC 侧 `ComfyClient` 已有
  `ipadapter_workflow / controlnet_workflow / capability_report`，接口还没暴露成 `/api/gen/*`，
  等生图端把界面做好我再补 LAN 接口。
