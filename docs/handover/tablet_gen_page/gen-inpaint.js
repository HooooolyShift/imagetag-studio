/**
 * 局部重绘面板（平板端参考实现）—— 拷到 webui/js/views/gen-inpaint.js 即可用。
 *
 * 用法：
 *   const inpaint = createInpaintPanel({ root: document.body, getClient: () => pcClient, toast });
 *   inpaint.open({ name: 'gen_20261008-123120_12345.png',   // 输出目录里的文件名（或 fileId）
 *                  imageUrl: pcClient.genFileUrl(name), prompt: '红色连衣裙' });
 *
 * 语义与 PC 端一致：**白色 = 要重绘**，黑色 = 保留；提交时把遮罩画成黑白 PNG（不带 alpha），
 * 走 POST /api/gen/inpaint（进度用你们已有的 gen_* SSE 事件显示）。
 */

export function createInpaintPanel({ root, getClient, toast = () => {}, onDone = null } = {}) {
  let panel = null;
  let cv = null, ctx2d = null;
  let img = null;
  let mask = null;              // 离屏 canvas：只画遮罩（白=重绘）
  let brush = 40;
  let drawing = false, erasing = false;
  let undoStack = [];
  let current = { name: '', fileId: null, imageUrl: '' };
  let scale = 1, panX = 0, panY = 0;
  const dpr = Math.min(2, window.devicePixelRatio || 1);

  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement('div');
    panel.className = 'inpaint-mask';
    panel.style.cssText = 'position:fixed;inset:0;z-index:80;display:none;'
      + 'background:rgba(12,13,17,.92);padding:12px;box-sizing:border-box;';
    panel.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;color:#cfd6e4;font:13px/1.4 system-ui">
        <b>局部重绘</b>
        <span id="ip-hint" style="color:#8f96a3">左键画（白=要重绘）· 右键擦 · 滚轮缩放 · Ctrl+Z 撤销 · C 清空</span>
        <span style="flex:1"></span>
        <button id="ip-undo" type="button">撤销</button>
        <button id="ip-clear" type="button">清空</button>
        <button id="ip-close" type="button">关闭</button>
      </div>
      <div style="display:flex;gap:12px;margin-top:10px;height:calc(100% - 52px)">
        <div style="flex:1;position:relative;overflow:hidden;background:#15171c;border-radius:10px">
          <canvas class="inpaint-canvas" id="ip-canvas"
                  style="display:block;width:100%;height:100%;touch-action:none;cursor:crosshair"></canvas>
        </div>
        <div class="inpaint-tools" style="width:280px;display:flex;flex-direction:column;gap:8px;color:#cfd6e4">
          <label style="font:12px system-ui">笔刷 <input id="ip-brush" type="range" min="4" max="400" value="40" style="width:100%"></label>
          <textarea id="ip-prompt" placeholder="这一块要画成什么？（例如：红色连衣裙、握着一把伞）"
                    style="height:84px;border-radius:8px;padding:8px;background:#1b1d22;color:#e8edf6;border:1px solid #2a2d35"></textarea>
          <label style="font:12px system-ui">重绘强度 <input id="ip-denoise" type="range" min="0.2" max="0.9" step="0.05" value="0.6" style="width:100%"></label>
          <label style="font:12px system-ui"><input id="ip-import" type="checkbox" checked> 生成后入库（进待审队列）</label>
          <button id="ip-run" type="button" style="padding:10px;border-radius:8px;border:0;background:#2f6b46;color:#e8fff0;font-weight:600">
            开始重绘
          </button>
          <div id="ip-status" style="font:12px system-ui;color:#8f96a3"></div>
        </div>
      </div>`;
    root.appendChild(panel);
    cv = panel.querySelector('#ip-canvas');
    ctx2d = cv.getContext('2d');
    panel.querySelector('#ip-close').onclick = close;
    panel.querySelector('#ip-clear').onclick = () => { pushUndo(); clearMask(); };
    panel.querySelector('#ip-undo').onclick = undo;
    panel.querySelector('#ip-brush').oninput = e => { brush = Number(e.target.value) || 40; };
    panel.querySelector('#ip-run').onclick = submit;
    bindPointer();
    window.addEventListener('resize', fit);
    return panel;
  }

  function fit() {
    if (!img) return;
    const box = cv.parentElement.getBoundingClientRect();
    cv.width = Math.max(64, Math.floor(box.width * dpr));
    cv.height = Math.max(64, Math.floor(box.height * dpr));
    scale = Math.min(cv.width / img.width, cv.height / img.height);
    scale = Math.max(0.05, scale * 0.98);
    panX = (cv.width - img.width * scale) / 2;
    panY = (cv.height - img.height * scale) / 2;
    draw();
  }

  function imgPos(ev) {
    const r = cv.getBoundingClientRect();
    const x = (ev.clientX - r.left) * dpr;
    const y = (ev.clientY - r.top) * dpr;
    return { x: (x - panX) / scale, y: (y - panY) / scale };
  }

  function draw() {
    ctx2d.setTransform(1, 0, 0, 1, 0, 0);
    ctx2d.clearRect(0, 0, cv.width, cv.height);
    if (!img) return;
    ctx2d.save();
    ctx2d.translate(panX, panY);
    ctx2d.scale(scale, scale);
    ctx2d.drawImage(img, 0, 0);
    // 遮罩半透明粉 + 原遮罩对比
    ctx2d.globalAlpha = 0.45;
    ctx2d.drawImage(mask, 0, 0);
    ctx2d.globalAlpha = 1;
    ctx2d.restore();
  }

  function paintAt(p) {
    const mctx = mask.getContext('2d');
    mctx.save();
    mctx.globalCompositeOperation = erasing ? 'destination-out' : 'source-over';
    mctx.fillStyle = '#fff';
    mctx.strokeStyle = '#fff';
    mctx.lineWidth = brush;
    mctx.lineCap = 'round';
    if (p.last) {
      mctx.beginPath(); mctx.moveTo(p.last.x, p.last.y); mctx.lineTo(p.x, p.y); mctx.stroke();
    }
    mctx.beginPath(); mctx.arc(p.x, p.y, brush / 2, 0, Math.PI * 2); mctx.fill();
    mctx.restore();
    p.last = { x: p.x, y: p.y };
    draw();
  }

  function pushUndo() {
    try {
      undoStack.push(mask.getContext('2d').getImageData(0, 0, mask.width, mask.height));
      if (undoStack.length > 12) undoStack.shift();
    } catch { /* 忽略 */ }
  }

  function undo() {
    const snap = undoStack.pop();
    if (!snap) return;
    mask.getContext('2d').putImageData(snap, 0, 0);
    draw();
  }

  function clearMask() {
    mask.getContext('2d').clearRect(0, 0, mask.width, mask.height);
    draw();
  }

  function bindPointer() {
    const pos = { last: null };
    cv.addEventListener('pointerdown', ev => {
      ev.preventDefault();
      pushUndo();
      drawing = true;
      erasing = ev.button === 2 || ev.altKey;
      pos.last = null;
      const p = imgPos(ev);
      pos.x = p.x; pos.y = p.y;
      paintAt(pos);
      cv.setPointerCapture(ev.pointerId);
    });
    cv.addEventListener('pointermove', ev => {
      if (!drawing) return;
      ev.preventDefault();
      const p = imgPos(ev);
      pos.x = p.x; pos.y = p.y;
      paintAt(pos);
    });
    const stop = ev => { ev.preventDefault(); drawing = false; pos.last = null; };
    cv.addEventListener('pointerup', stop);
    cv.addEventListener('pointercancel', stop);
    cv.addEventListener('contextmenu', ev => ev.preventDefault());
    cv.addEventListener('wheel', ev => {
      ev.preventDefault();
      scale = Math.max(0.05, Math.min(8, scale * (ev.deltaY < 0 ? 1.1 : 0.9)));
      draw();
    }, { passive: false });
  }

  /** 导出黑白 PNG（白=重绘）的 base64（不带 data: 前缀） */
  function maskBase64() {
    const out = document.createElement('canvas');
    out.width = mask.width; out.height = mask.height;
    const octx = out.getContext('2d');
    octx.fillStyle = '#000'; octx.fillRect(0, 0, out.width, out.height);   // 黑底
    octx.drawImage(mask, 0, 0);                                          // 白=重绘
    // 把透明处补黑：mask 用 destination-out 擦过的地方是透明
    const data = octx.getImageData(0, 0, out.width, out.height);
    for (let i = 0; i < data.data.length; i += 4) {
      const a = data.data[i + 3];
      if (a === 0) { data.data[i] = 0; data.data[i + 1] = 0; data.data[i + 2] = 0; }
    }
    octx.putImageData(data, 0, 0);
    return out.toDataURL('image/png').split(',')[1] || '';
  }

  function maskEmpty() {
    const data = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height).data;
    for (let i = 3; i < data.length; i += 4 * 37) {          // 抽样足够
      if (data[i] > 8) return false;
    }
    return true;
  }

  async function submit() {
    const client = getClient && getClient();
    if (!client) { toast('还没连上 PC'); return; }
    const prompt = (panel.querySelector('#ip-prompt').value || '').trim();
    if (!prompt) { toast('先写"这块要画成什么"'); return; }
    if (maskEmpty()) { toast('先画一块要重绘的区域'); return; }
    const body = {
      prompt,
      mask_base64: maskBase64(),
      denoise: Number(panel.querySelector('#ip-denoise').value) || 0.6,
      import: panel.querySelector('#ip-import').checked,
    };
    if (current.fileId) body.file_id = current.fileId;
    else body.name = current.name;
    panel.querySelector('#ip-status').textContent = '已提交，等 PC 出图…';
    try {
      const r = await client.genInpaint(body);
      if (r && r.started === false) {
        toast(`PC 端正忙：${r.reason || ''}`);
        panel.querySelector('#ip-status').textContent = 'PC 端有任务在跑，稍后再试';
      } else {
        toast('已提交局部重绘（进度看生图页进度条）');
      }
      if (onDone) onDone(r);
    } catch (err) {
      toast(`提交失败：${err.message || err}`);
      panel.querySelector('#ip-status').textContent = String(err.message || err);
    }
  }

  function open({ name = '', fileId = null, imageUrl = '', prompt = '' }) {
    ensurePanel();
    current = { name, fileId, imageUrl };
    panel.style.display = 'block';
    const im = new Image();
    im.crossOrigin = 'anonymous';
    im.onload = () => {
      img = im;
      mask = document.createElement('canvas');
      mask.width = im.naturalWidth; mask.height = im.naturalHeight;
      undoStack = [];
      panel.querySelector('#ip-prompt').value = prompt || '';
      panel.querySelector('#ip-status').textContent = `${im.naturalWidth}×${im.naturalHeight}`;
      fit();
    };
    im.onerror = () => { panel.querySelector('#ip-status').textContent = '底图加载失败'; };
    im.src = imageUrl;
  }

  function close() { if (panel) panel.style.display = 'none'; }

  return { open, close, maskBase64, get canvas() { return cv; }, get mask() { return mask; } };
}
