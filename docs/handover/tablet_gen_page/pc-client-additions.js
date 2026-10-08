/**
 * 平板端 PcClient 要补的方法（贴到 webui/js/data/pc-client.js 的 PcClient 类里，挨着 genRun 放）。
 *
 * 注意字段名：PC 侧 `/api/gen/inpaint` 收的是 `mask_base64`（PNG，**白=要重绘**）与 `import`，
 * 底图二选一：`file_id`（库里图片）或 `name`（输出目录里的文件名）。
 *
 * 2026-10-08 下午更新：补齐 能力探测 / 模型清单与下载 / 参考图 / 姿势控制 / 图生图与图融合 /
 * 纯放大 / 高分修复 / 结果缩略图 这些方法的封装，下面是完整版，直接整段替换即可。
 */

// ---- 片段 0：能力探测与模型清单 ----
// 一进生图页调一次；caps 有 60 秒缓存，加了 ?fresh=1 强制重探。
genCaps({ fresh = false } = {}) {
  return this._get('/api/gen/caps', fresh ? { fresh: 1 } : {});
}

// 模型清单：models[].present 决定要不要显示"下载"；configured=false 表示 PC 没配 ComfyUI 目录。
genModels() {
  return this._get('/api/gen/models');
}

// 下载缺的模型（断点续传）。ids 来自 genModels 的 models[].id；进度走 SSE gen_dl_*。
genFetchModels({ ids = [], all = false } = {}) {
  return this._post('/api/gen/models/fetch', all ? { all: true } : { ids }, 'gen_fetch_failed');
}

// ---- 片段 1：局部重绘（换装） ----
genInpaint({ fileId = null, name = '', mask_base64 = '', prompt = '', negative = null,
             steps = 0, cfg = 0, denoise = 0, growMask = 0, seed = null,
             importToLibrary = true } = {}) {
  return this._post('/api/gen/inpaint', {
    file_id: fileId || null,
    name: name || null,
    mask_base64,
    prompt,
    negative: negative || null,
    steps: steps || null,
    cfg: cfg || null,
    denoise: denoise || null,
    grow_mask: growMask || null,
    seed,
    import: !!importToLibrary,
  }, 'gen_inpaint_failed');
}

// ---- 片段 2：参考图（IP-Adapter） ----
// ref 三种给法：refFileId（库里图片，推荐）/ refName（输出目录里的文件）/ refBase64（相机/相册）。
// 不传 ipadapterFile / clipVision，PC 会按底模架构（SDXL / SD1.5）自动配对——别自己硬编码。
genIpadapter({ refFileId = null, refName = '', refBase64 = '', prompt = '',
               negative = null, model = '', weight = 0, width = 0, height = 0,
               steps = 0, cfg = 0, seed = null, importToLibrary = true } = {}) {
  return this._post('/api/gen/ipadapter', {
    ref_file_id: refFileId || null,
    ref_name: refName || null,
    ref_base64: refBase64 || null,
    prompt,
    negative: negative || null,
    model: model || null,
    weight: weight || null,
    width: width || null,
    height: height || null,
    steps: steps || null,
    cfg: cfg || null,
    seed,
    import: !!importToLibrary,
  }, 'gen_ipadapter_failed');
}

// ---- 片段 3：姿势 / 线稿（ControlNet） ----
// 姿势图三种给法同上；preprocessor 一般固定 openpose（PC 侧工作流就是 openpose 预处理）。
genControlnet({ poseFileId = null, poseName = '', poseBase64 = '', prompt = '',
                negative = null, model = '', controlNet = '', strength = 0,
                preprocessor = 'openpose', width = 0, height = 0,
                steps = 0, cfg = 0, seed = null, importToLibrary = true } = {}) {
  return this._post('/api/gen/controlnet', {
    pose_file_id: poseFileId || null,
    pose_name: poseName || null,
    pose_base64: poseBase64 || null,
    prompt,
    negative: negative || null,
    model: model || null,
    control_net: controlNet || null,
    strength: strength || null,
    preprocessor,
    width: width || null,
    height: height || null,
    steps: steps || null,
    cfg: cfg || null,
    seed,
    import: !!importToLibrary,
  }, 'gen_controlnet_failed');
}

// ---- 片段 4：图生图 / 图融合 ----
// 只给底图 = 改造这张图（换装 denoise 0.55~0.65 最自然）；
// 再给 blendFileId/blendName = 两张图先按 blendFactor 混成一张，再重绘。
// ⚠️ 融合时 blendFactor 0.25~0.3 + denoise 0.7~0.85 才成画；0.4/0.45 会出双重曝光那种重影。
genImg2img({ fileId = null, name = '', base64 = '', prompt = '', negative = null,
             denoise = 0, blendFileId = null, blendName = '', blendBase64 = '',
             blendFactor = 0, blendMode = 'normal', model = '',
             steps = 0, cfg = 0, seed = null, importToLibrary = true } = {}) {
  return this._post('/api/gen/img2img', {
    file_id: fileId || null,
    name: name || null,
    base64: base64 || null,
    prompt,
    negative: negative || null,
    denoise: denoise || null,
    blend_file_id: blendFileId || null,
    blend_name: blendName || null,
    blend_base64: blendBase64 || null,
    blend_factor: blendFactor || null,
    blend_mode: blendMode || 'normal',
    model: model || null,
    steps: steps || null,
    cfg: cfg || null,
    seed,
    import: !!importToLibrary,
  }, 'gen_img2img_failed');
}

// ---- 片段 5：放大（两种） ----
// 默认用 genUpscale：纯超分，不重绘、不出彩噪，1024→4096 实测 9 秒。
// genHires 是"潜空间放大 + 低 denoise 重采样"，会长新细节但头发边缘可能出彩噪，做可选项。
genUpscale({ fileId = null, name = '', base64 = '', scale = 4, width = 0, height = 0,
             model = '', importToLibrary = true } = {}) {
  return this._post('/api/gen/upscale', {
    file_id: fileId || null,
    name: name || null,
    base64: base64 || null,
    scale,
    width: width || null,
    height: height || null,
    model: model || null,
    import: !!importToLibrary,
  }, 'gen_upscale_failed');
}

genHires({ fileId = null, name = '', base64 = '', prompt = '', negative = null,
           scale = 1.5, denoise = 0.3, model = '', steps = 0, cfg = 0, seed = null,
           importToLibrary = true } = {}) {
  return this._post('/api/gen/hires', {
    file_id: fileId || null,
    name: name || null,
    base64: base64 || null,
    prompt,
    negative: negative || null,
    scale,
    denoise,
    model: model || null,
    steps: steps || null,
    cfg: cfg || null,
    seed,
    import: !!importToLibrary,
  }, 'gen_hires_failed');
}

// ---- 片段 7（可选）：词库单词查询，已在你们文件里是 lexZh / lexPromptFix，保持即可 ----
// lexZh(text)       → GET  /api/lex/zh?text=      （返回 {tag, segment}）
// lexPromptFix(text)→ POST /api/lex/prompt_fix    （返回 {fixed, unknown}）

// ---- 片段 6：生图结果图 URL（已有 genFileUrl，建议加个缩略图重载） ----
// 列表里**一定要带 size**，否则每张都把整张 PNG 拉下来（缓存会爆、滚动会卡）。
// genThumbUrl(name, size = 340) → `${this.base}/api/gen/file?name=...&size=340&code=...`
genThumbUrl(name, size = 340) {
  return this.genFileUrl(name) + `&size=${size}`;
}

/*
生图页里加"局部重绘"入口的示例（gen.js 的结果卡片右侧加一个按钮）：

  import { createInpaintPanel } from './gen-inpaint.js';
  const inpaint = createInpaintPanel({ root: document.body, getClient: () => client, toast });

  // 结果列表渲染时：
  const b = document.createElement('button');
  b.textContent = '局部重绘';
  b.addEventListener('click', (ev) => {
    ev.stopPropagation();
    inpaint.open({ name: f.name, fileId: f.file_id || null, imageUrl: client.genFileUrl(f.name),
                   prompt: '' });
  });
  btn.querySelector('.genitem__tools').appendChild(b);

进度显示沿用你们现有的 SSE 分支（gen_inpaint_started / gen_progress / gen_done / gen_failed），
gen_done 里可以 `loadResults()` 刷新结果列表（入库后的图会带 file_id 与缩略图）。

新增功能的进度事件（都走同一套 SSE，用 kind 区分）：
  gen_advanced_started { kind: 'ipadapter' | 'controlnet' | 'img2img' | 'upscale' | 'hires', ... }
  gen_progress         { elapsed, kind }
  gen_done             { files, file_ids, imported, kind }        // hires 还带 source_size/size
  gen_failed           { error, kind }
模型下载另外一套：gen_dl_started / gen_dl_progress { id, file, index, total_models, got, total, pct }
  / gen_dl_done / gen_dl_failed。

分辨率相关（重要）：
  · 尺寸框照 `/api/gen/info → limits`（min_side 256 / max_side 2048 / step 8 / sweet_spot 1024）画；
  · **别给 SDXL 提供 512 档**（会糊成一团，PC 会兜底抬到 1024 并在 `genRun` 响应里回 warning）；
  · `genRun` 的响应现在带 `width/height/warning/model_arch`，按实际值刷新界面即可；
  · 模型下拉可以拿 `/api/gen/info → model_arch` 标出 SDXL / SD1.5（SD1.5 已弃用，只作展示）。
*/
