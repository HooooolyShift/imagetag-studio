/* 图片标签工坊 · PC 端图谱（app/ui/taxonomy.py）的 Web 移植件
 * ------------------------------------------------------------------
 * 这是把 PC 端 QGraphicsScene 那套图谱"照抄"成 Canvas 2D 的版本：
 * 常量、配色公式、层级渐变、热度插值、节点画法（圆 + 圈内三行字）、
 * 连线与箭头、弹簧拖拽、缩放阈值、径向布局——全部按 PC 的实际数值来。
 *
 * 直接把这个文件丢进移动端工程（例如 webui/js/graph/pc-graph.js）即可用：
 *   const g = new PcGraph(canvas);         // canvas 元素
 *   g.setData({nodes, edges});             // 用 /api/graph 的返回
 *   g.fit();                               // 适应窗口
 *   // 交互：滚轮缩放 / 拖动平移 / 拖节点弹簧回弹 / 点节点选中 / 双击折叠
 *
 * 数据字段（与 /api/graph 一致）：
 *   nodes: {id, kind:'group'|'tag', name, zh, category, count, x, y, collapsed, parents[]}
 *   edges: {from, to, relation:'is_a'|'sub_of'|'parallel'}
 */
(function (global) {
  "use strict";

  // ===== 1. PC 端常量（app/ui/taxonomy.py 原值，别改比例）=====
  const SIZES = { group: 118.0, midTag: 92.0, tag: 68.0 };   // 分类大圆 / 作品级中圆 / 普通标签小圆
  const FS = { top: 7.0, nameGroup: 9.4, nameTag: 8.2, count: 7.4 };  // pt
  const COLOR = {
    groupFill: "#2b3d52", groupBorder: "#4a7fc1",
    tagFill: "#2a2c33", tagBorder: "#4a4e58",
    tagFillUsed: "#31343d", tagBorderUsed: "#5f6572",
    selBorder: "#ffb347", edge: "#5a6270",
    edgeSubOf: "#3fc1c9", edgeParallel: "#b57cff",
    textTopGroup: "#aab6c8", textTopTag: "#9fb0c6",
    textNameOn: "#eef2f8", textNameOff: "#b9bcc5",
    textCount: "#ffd479", legend: "#8f96a3"
  };
  const FAMILY_COLORS = ["#6f8cff", "#22d3ee", "#4ade80", "#facc15", "#fb923c", "#e879f9",
                         "#f472b6", "#a3e635", "#2dd4bf", "#f87171", "#818cf8", "#fbbf24",
                         "#34d399", "#38bdf8"];
  const HEAT_STOPS = [[0, "#3a3f47"], [1, "#2f6fb0"], [5, "#3fa8c9"], [20, "#5fd07a"],
                      [100, "#d8c05a"], [400, "#e08a4a"], [1000, "#d05050"]];
  const ZOOM = { min: 0.1, max: 5.0, step: 1.15, textAt: 0.55, edgeAt: 0.25,
                 tinyAt: 0.22, blockAt: 0.08 };
  const SPRING = { limit: 240.0, damp: 0.35, animMs: 340 };
  const EDGE_W = { is_a: 1.6, sub_of: 2.0, parallel: 1.8 };
  const ARROW = { size: 10.0, spread: 0.42 };
  const LAYOUT = { tagR0: 150.0, tagRingStep: 110.0, tagSpacing: 62.0,
                   groupFallbackR: 900.0, ringRadius: 900.0 };

  // ===== 2. 颜色工具（按 PC 的 darker/lighter 语义实现）=====
  function hex2rgb(h) {
    h = h.replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function rgb(c) { return `rgb(${c[0]|0},${c[1]|0},${c[2]|0})`; }
  function rgba(c, a) { return `rgba(${c[0]|0},${c[1]|0},${c[2]|0},${a})`; }
  /** Qt 的 darker(f)：分量乘以 100/f；f 越大越暗 */
  function darker(c, f) { return c.map(v => Math.max(0, Math.min(255, v * 100 / f))); }
  /** Qt 的 lighter(f)：分量往 255 靠，比例 (f-100)/100 */
  function lighter(c, f) {
    const t = Math.max(0, (f - 100) / 100);
    return c.map(v => v + (255 - v) * t);
  }
  /** 层级配色：fill=base.darker(max(135,330-55*depth))，border=base 或 lighter(100+10*depth) */
  function familyColor(idx, level) {
    const base = hex2rgb(FAMILY_COLORS[Math.abs(idx) % FAMILY_COLORS.length]);
    const depth = Math.max(0, (level | 0) - 1);
    const fill = darker(base, Math.max(135, 330 - 55 * depth));
    const border = depth === 0 ? base : lighter(base, 100 + 10 * depth);
    return { fill, border, base };
  }
  /** 热度配色：对数插值（0 → #3a3f47 … 1000+ → #d05050） */
  function heatColor(count) {
    const c = Math.max(0, count | 0);
    const pts = HEAT_STOPS.map(([k, v]) => [k ? Math.log10(Math.max(1, k)) : 0, hex2rgb(v)]);
    const x = c > 0 ? Math.log10(c) : 0;
    if (x <= pts[0][0]) return pts[0][1];
    for (let i = 0; i + 1 < pts.length; i++) {
      const [x0, c0] = pts[i], [x1, c1] = pts[i + 1];
      if (x <= x1) {
        const t = (x - x0) / Math.max(1e-6, x1 - x0);
        return [0, 1, 2].map(k => c0[k] + (c1[k] - c0[k]) * t);
      }
    }
    return pts[pts.length - 1][1];
  }

  // ===== 3. 径向布局（与 app/graph_layout.py 一致；离线档可用）=====
  function placeGraph(groups, tags) {
    const pos = new Map();
    const missing = [];
    groups.forEach(g => {
      if (g.x === null || g.x === undefined || g.y === null || g.y === undefined) missing.push(g);
      else pos.set(g.id, [g.x, g.y]);
    });
    missing.forEach((g, i) => {
      const a = -Math.PI / 2 + 2 * Math.PI * i / Math.max(1, missing.length);
      pos.set(g.id, [LAYOUT.groupFallbackR * Math.cos(a), LAYOUT.groupFallbackR * Math.sin(a)]);
    });
    const buckets = new Map(), orphans = [];
    tags.forEach(t => {
      const p = (t.parents || []).find(x => pos.has(x));
      if (p) { if (!buckets.has(p)) buckets.set(p, []); buckets.get(p).push(t); }
      else orphans.push(t);
    });
    const ringPlace = (items, cx, cy, r0, step) => {
      let i = 0, ring = 0;
      while (i < items.length) {
        const r = r0 + ring * step;
        const cap = Math.max(6, Math.floor(2 * Math.PI * r / LAYOUT.tagSpacing));
        const chunk = items.slice(i, i + cap);
        chunk.forEach((it, k) => {
          const a = -Math.PI / 2 + 2 * Math.PI * k / Math.max(1, chunk.length);
          pos.set(it.id, [cx + r * Math.cos(a), cy + r * Math.sin(a)]);
        });
        i += cap; ring++;
      }
    };
    const byCount = (a, b) => (b.count || 0) - (a.count || 0) || String(a.id).localeCompare(String(b.id));
    buckets.forEach((items, gid) => {
      const [cx, cy] = pos.get(gid);
      ringPlace(items.slice().sort(byCount), cx, cy, LAYOUT.tagR0, LAYOUT.tagRingStep);
    });
    if (orphans.length) ringPlace(orphans.slice().sort(byCount), 0, 0,
                                  LAYOUT.groupFallbackR + 700, 120);
    return pos;
  }

  // ===== 4. 画布 =====
  class PcGraph {
    constructor(canvas, opts) {
      this.cv = canvas;
      this.ctx = canvas.getContext("2d");
      this.nodes = []; this.edges = [];
      this.byId = new Map();
      this.zoom = 1; this.pan = [0, 0];
      this.heat = false;                       // 热度视图开关
      this.selected = null;
      this.hover = null;
      this.dpr = (opts && opts.dpr) || (global.devicePixelRatio || 1);
      this.spring = new Map();                 // id -> {home:[x,y], pos:[x,y], drag:bool}
      this.onSelect = (opts && opts.onSelect) || null;
      this._bind();
    }

    // ---- 数据 ----
    setData(data) {
      this.nodes = (data.nodes || []).map(n => ({
        ...n,
        level: this._levelOf(n),
        family: this._familyOf(n),
        size: n.kind === "group" ? SIZES.group : ((n.parents || []).some(p => this._hasKids(p)) || (n.children || []).length
          ? SIZES.midTag : SIZES.tag)
      }));
      this.byId = new Map(this.nodes.map(n => [n.id, n]));
      this.edges = (data.edges || []).filter(e => this.byId.has(e.from) && this.byId.has(e.to));
      // 坐标：优先用服务端下发的，缺了才本地算
      const need = this.nodes.some(n => n.x === null || n.x === undefined);
      if (need) {
        const pos = placeGraph(this.nodes.filter(n => n.kind === "group"),
                               this.nodes.filter(n => n.kind === "tag"));
        this.nodes.forEach(n => { const p = pos.get(n.id); if (p) { n.x = p[0]; n.y = p[1]; } });
      }
      this.nodes.forEach(n => {
        n.x = n.x || 0; n.y = n.y || 0;
        this.spring.set(n.id, { home: [n.x, n.y], pos: [n.x, n.y], drag: false });
      });
      this.draw();
    }

    _hasKids(id) { return this.nodes.some(n => (n.parents || []).includes(id)); }
    _levelOf(n) {
      // 层数 = 沿 parents 往上数（无父节点=1）
      let lvl = 1, cur = n, guard = 0;
      while (guard++ < 32) {
        const p = (cur.parents || []).map(p => this.byId.get(p)).find(Boolean);
        if (!p) break;
        lvl++; cur = p;
      }
      return lvl;
    }
    _familyOf(n) {
      // 色系按"最高层分类"取：往上找到最顶的那个分类，用它在分类列表里的序号
      let top = n, guard = 0;
      while (guard++ < 32) {
        const p = (top.parents || []).map(p => this.byId.get(p)).find(x => x && x.kind === "group");
        if (!p) break;
        top = p;
      }
      const idx = this.nodes.filter(x => x.kind === "group").findIndex(x => x.id === top.id);
      return idx < 0 ? 0 : idx;
    }

    // ---- 交互 ----
    _bind() {
      const cv = this.cv;
      cv.addEventListener("wheel", e => {
        e.preventDefault();
        const k = e.deltaY < 0 ? ZOOM.step : 1 / ZOOM.step;
        const nz = Math.min(ZOOM.max, Math.max(ZOOM.min, this.zoom * k));
        // 以鼠标位置为中心缩放
        const r = cv.getBoundingClientRect();
        const mx = e.clientX - r.left, my = e.clientY - r.top;
        this.pan[0] = mx - (mx - this.pan[0]) * (nz / this.zoom);
        this.pan[1] = my - (my - this.pan[1]) * (nz / this.zoom);
        this.zoom = nz; this.draw();
      }, { passive: false });
      let dragging = null, last = null;
      cv.addEventListener("pointerdown", e => {
        const n = this._pick(e);
        last = [e.clientX, e.clientY];
        if (n) {                              // 拖节点 → 弹簧（PC: 越拖越沉、松手弹回）
          dragging = { node: n, moved: false };
          const sp = this.spring.get(n.id); sp.drag = true; sp.pos = [n.x, n.y];
          cv.setPointerCapture(e.pointerId);
        }
      });
      cv.addEventListener("pointermove", e => {
        const r = cv.getBoundingClientRect();
        const p = this._toWorld(e.clientX - r.left, e.clientY - r.top);
        this.hover = this._pick(e);
        if (dragging) {
          const sp = this.spring.get(dragging.node.id);
          const dx = p[0] - sp.home[0], dy = p[1] - sp.home[1];
          const dist = Math.hypot(dx, dy), lim = SPRING.limit;
          let nx, ny;
          if (dist > lim) { const k = lim / dist; nx = sp.home[0] + dx * k; ny = sp.home[1] + dy * k; }
          else { const k = 1 - SPRING.damp * (dist / lim); nx = sp.home[0] + dx * k; ny = sp.home[1] + dy * k; }
          sp.pos = [nx, ny]; dragging.node.x = nx; dragging.node.y = ny;
          dragging.moved = true; this.draw();
        } else if (last && (e.buttons & 1)) {   // 空白拖动 = 平移
          this.pan[0] += e.clientX - last[0]; this.pan[1] += e.clientY - last[1];
          last = [e.clientX, e.clientY]; this.draw();
        }
        last = [e.clientX, e.clientY];
      });
      cv.addEventListener("pointerup", e => {
        if (dragging) {
          const n = dragging.node, sp = this.spring.get(n.id);
          if (dragging.moved) this._springBack(n, sp);   // 松手弹回原位（340ms）
          else { this.selected = n; this.onSelect && this.onSelect(n); this.draw(); }
          sp.drag = false;
        }
        dragging = null; last = null;
      });
      cv.addEventListener("dblclick", e => {              // 双击折叠/展开
        const n = this._pick(e);
        if (n) { n.collapsed = n.collapsed ? 0 : 1; this.draw(); }
      });
    }
    _toWorld(x, y) { return [(x - this.pan[0]) / this.zoom, (y - this.pan[1]) / this.zoom]; }
    _pick(e) {
      const r = this.cv.getBoundingClientRect();
      const [wx, wy] = this._toWorld(e.clientX - r.left, e.clientY - r.top);
      for (let i = this.nodes.length - 1; i >= 0; i--) {
        const n = this.nodes[i];
        if (n.hidden) continue;
        const rr = n.size / 2;
        if (Math.hypot(wx - (n.x + rr), wy - (n.y + rr)) <= rr) return n;
      }
      return null;
    }
    _springBack(node, sp) {
      const t0 = performance.now(), p0 = [node.x, node.y], home = sp.home;
      const step = now => {
        const t = Math.min(1, (now - t0) / SPRING.animMs);
        const e = 1 - Math.pow(1 - t, 3);          // ease-out
        node.x = p0[0] + (home[0] - p0[0]) * e;
        node.y = p0[1] + (home[1] - p0[1]) * e;
        this.draw();
        if (t < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    // ---- 视图 ----
    fit(pad) {
      if (!this.nodes.length) return;
      const xs = this.nodes.map(n => n.x), ys = this.nodes.map(n => n.y);
      const w = Math.max(1, Math.max(...xs) - Math.min(...xs) + SIZES.group);
      const h = Math.max(1, Math.max(...ys) - Math.min(...ys) + SIZES.group);
      const r = this.cv.getBoundingClientRect();
      const k = Math.min((r.width - (pad || 40)) / w, (r.height - (pad || 40)) / h);
      this.zoom = Math.min(ZOOM.max, Math.max(ZOOM.min, k));
      this.pan = [r.width / 2 - (Math.min(...xs) + w / 2) * this.zoom,
                  r.height / 2 - (Math.min(...ys) + h / 2) * this.zoom];
      this.draw();
    }
    /** 搜索定位：无加速 → 最高速 → 减速（用户要求的曲线） */
    centerOn(id, ms) {
      const n = this.byId.get(id); if (!n) return;
      const r = this.cv.getBoundingClientRect();
      const rr = n.size / 2;
      const target = [r.width / 2 - (n.x + rr) * this.zoom, r.height / 2 - (n.y + rr) * this.zoom];
      const t0 = performance.now(), from = [this.pan[0], this.pan[1]], T = ms || 420;
      const step = now => {
        const t = Math.min(1, (now - t0) / T);
        const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;   // 先快后慢的 easeInOutQuad
        this.pan = [from[0] + (target[0] - from[0]) * e, from[1] + (target[1] - from[1]) * e];
        this.draw();
        if (t < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
    setHeat(on) { this.heat = !!on; this.draw(); }

    // ---- 画 ----
    draw() {
      const cv = this.cv, ctx = this.ctx, dpr = this.dpr;
      const r = cv.getBoundingClientRect();
      if (cv.width !== Math.floor(r.width * dpr)) {
        cv.width = Math.floor(r.width * dpr); cv.height = Math.floor(r.height * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, r.width, r.height);
      ctx.fillStyle = "#1b1d22"; ctx.fillRect(0, 0, r.width, r.height);
      ctx.save();
      ctx.translate(this.pan[0], this.pan[1]); ctx.scale(this.zoom, this.zoom);
      const showText = this.zoom >= ZOOM.textAt;
      const showEdges = this.zoom >= ZOOM.edgeAt;
      const tiny = this.zoom < ZOOM.tinyAt;
      // 边（先画，压在节点下面）
      if (showEdges) {
        this.edges.forEach(e => {
          const a = this.byId.get(e.from), b = this.byId.get(e.to);
          if (!a || !b) return;
          const rel = e.relation || "is_a";
          const col = rel === "sub_of" ? COLOR.edgeSubOf : (rel === "parallel" ? COLOR.edgeParallel : COLOR.edge);
          ctx.strokeStyle = col; ctx.lineWidth = EDGE_W[rel] || 1.6;
          ctx.setLineDash(rel === "parallel" ? [6, 5] : []);
          const ax = a.x + a.size / 2, ay = a.y + a.size / 2;
          const bx = b.x + b.size / 2, by = b.y + b.size / 2;
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
          ctx.setLineDash([]);
          // 箭头（指向子节点，边长 10、张角 ±0.42）
          const ang = Math.atan2(by - ay, bx - ax), s = ARROW.size;
          ctx.fillStyle = col; ctx.beginPath(); ctx.moveTo(bx, by);
          ctx.lineTo(bx - s * Math.cos(ang - ARROW.spread), by - s * Math.sin(ang - ARROW.spread));
          ctx.lineTo(bx - s * Math.cos(ang + ARROW.spread), by - s * Math.sin(ang + ARROW.spread));
          ctx.closePath(); ctx.fill();
        });
      }
      // 节点
      this.nodes.forEach(n => {
        if (n.hidden) return;
        const d = n.size, cx = n.x + d / 2, cy = n.y + d / 2, rr = d / 2;
        const isGroup = n.kind === "group";
        ctx.save();
        if (isGroup) {
          const { fill, border } = familyColor(n.family, 1);
          ctx.fillStyle = rgb(darker(fill, 115));
          ctx.strokeStyle = this.selected === n ? COLOR.selBorder : rgb(border);
          ctx.lineWidth = 4.0;
        } else if (this.heat) {
          const hc = heatColor(n.count);
          ctx.fillStyle = rgb(hc);
          ctx.strokeStyle = this.selected === n ? COLOR.selBorder : rgb(darker(hc, 150));
          ctx.lineWidth = 2.0;
        } else {
          const { fill, border } = familyColor(n.family, n.level);
          ctx.fillStyle = rgb(fill);
          ctx.strokeStyle = this.selected === n ? COLOR.selBorder : rgb(border);
          ctx.lineWidth = 2.0;
          ctx.setLineDash(n.count ? [] : [4, 4]);      // 没图的标签虚线
        }
        ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2);
        if (tiny) { ctx.lineWidth = 0; ctx.fillStyle = rgb(lighter(hex2rgb(FAMILY_COLORS[n.family % 14]), 125)); }
        ctx.fill();
        if (!tiny) ctx.stroke();
        ctx.setLineDash([]);
        // 圈内三行
        if (showText) {
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          const top = isGroup ? "分类" : (n.category || "");
          if (top) {
            ctx.font = `${FS.top * 1.333}px "Microsoft YaHei",sans-serif`;
            ctx.fillStyle = isGroup ? COLOR.textTopGroup : COLOR.textTopTag;
            ctx.fillText(top, cx, n.y + d * 0.06 + d * 0.11);
          }
          ctx.font = `${(isGroup ? FS.nameGroup : FS.nameTag) * 1.333}px "Microsoft YaHei",sans-serif`;
          ctx.fillStyle = (isGroup || n.count) ? COLOR.textNameOn : COLOR.textNameOff;
          const label = n.zh && n.zh.length ? n.zh : n.name;
          const per = Math.max(2, Math.floor((d - 14) / (ctx.measureText("汉").width || 9)));
          const lines = [];
          for (let i = 0; i < label.length && lines.length < 2; i += per) lines.push(label.slice(i, i + per));
          if (label.length > per * 2) lines[lines.length - 1] = lines[lines.length - 1].slice(0, -1) + "…";
          const midY = n.y + d * 0.30 + d * (this.heat ? 0.20 : 0.26);
          lines.forEach((ln, i) => ctx.fillText(ln, cx, midY + i * (isGroup ? 13 : 11)));
          if (this.heat && !isGroup) {
            ctx.font = `${FS.count * 1.333}px "Microsoft YaHei",sans-serif`;
            ctx.fillStyle = COLOR.textCount;
            ctx.fillText(`${n.count} 张`, cx, n.y + d * 0.72 + d * 0.11);
          }
        }
        ctx.restore();
      });
      ctx.restore();
      if (this.heat) this._legend(r);
    }

    _legend(r) {
      const ctx = this.ctx, x = 12, y = 12, w = 190;
      ctx.save();
      ctx.fillStyle = "rgba(20,22,26,0.86)";
      ctx.strokeStyle = "rgba(255,255,255,0.10)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.roundRect(x, y, w, 96, 8); ctx.fill(); ctx.stroke();
      ctx.font = '12px "Microsoft YaHei",sans-serif';
      ctx.fillStyle = "#cdd6e4"; ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillText("热度视图：按图片数", x + 12, y + 10);
      const bar = { x: x + 12, y: y + 34, w: w - 24, h: 14 };
      for (let i = 0; i < 60; i++) {              // 渐变条：对数 0→1000
        const cnt = i ? Math.round(Math.pow(10, 3 * i / 60)) : 0;
        ctx.fillStyle = rgb(heatColor(cnt));
        ctx.fillRect(bar.x + bar.w * i / 60, bar.y, bar.w / 60 + 1, bar.h);
      }
      ctx.fillStyle = COLOR.legend;
      [["0", 0], ["10", 0.33], ["100", 0.66], ["1000+", 1]].forEach(([t, f]) => {
        ctx.fillText(t, bar.x + bar.w * f - (f === 1 ? 30 : 8), y + 52);
      });
      ctx.fillText("越红 = 图越多；灰 = 还没有图", x + 12, y + 74);
      ctx.restore();
    }
  }

  global.PcGraph = PcGraph;
  global.PcGraphConst = { SIZES, FS, COLOR, FAMILY_COLORS, HEAT_STOPS, ZOOM, SPRING,
                          EDGE_W, ARROW, LAYOUT, familyColor, heatColor, placeGraph };
})(window);
