/**
 * 平板端 PcClient 要补的方法（贴到 webui/js/data/pc-client.js 的 PcClient 类里，挨着 genRun 放）。
 *
 * 注意字段名：PC 侧 `/api/gen/inpaint` 收的是 `mask_base64`（PNG，**白=要重绘**）与 `import`，
 * 底图二选一：`file_id`（库里图片）或 `name`（输出目录里的文件名）。
 */

// ---- 片段 1：局部重绘 ----
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

// ---- 片段 2（可选）：词库单词查询，已在你们文件里是 lexZh / lexPromptFix，保持即可 ----
// lexZh(text)       → GET  /api/lex/zh?text=      （返回 {tag, segment}）
// lexPromptFix(text)→ POST /api/lex/prompt_fix    （返回 {fixed, unknown}）

// ---- 片段 3（可选）：生图结果大图 URL（已有 genFileUrl）----
// genFileUrl(name)  → `${this.base}/api/gen/file?name=...&code=...&device=...`

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
*/
