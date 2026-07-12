"""Emit a single self-contained HTML file from a laid-out ``GraphData``.

Everything (data, CSS, JS) is inlined so the file opens offline with no server
and no CDN. The only network traffic the page ever makes is the optional
"Describe with AI" call, which the user points at their own local model
(Ollama or any OpenAI-compatible endpoint) from the settings panel.
"""

from __future__ import annotations

import json

from .extract import GraphData

# Optional color overrides per layer label.  Any layer not listed here gets a
# color auto-assigned from _PALETTE (deterministic, keyed by label name).
# Add entries here when you want a specific colour for a specific layer:
#
#   _LAYER_COLORS = {
#       "api":      "#4f9dff",
#       "services": "#ff8f4f",
#   }
_LAYER_COLORS: dict[str, str] = {}

# Visually distinct hues for auto-assignment.  Order is intentional: adjacent
# colours are perceptually far apart so neighbouring layers in the legend
# don't look similar.  36 entries means collision is impossible below 37 layers;
# the interleaved hue order keeps consecutive palette entries perceptually far
# apart so alphabetically-adjacent layers in the legend don't blur together.
_PALETTE = [
    # row 1: blue   orange  green     purple   yellow   pink
    "#4f9dff",
    "#ff8f4f",
    "#41d0a5",
    "#c78bff",
    "#ffd23f",
    "#ff6b9d",
    # row 2: cyan   red     lime      violet   gold     magenta
    "#36d7e0",
    "#ff4757",
    "#6bcb77",
    "#9c88ff",
    "#f9ca24",
    "#fd79a8",
    # row 3: sky    coral   teal      lavender chartreuse salmon
    "#74b9ff",
    "#e17055",
    "#55efc4",
    "#a29bfe",
    "#badc58",
    "#ee5a24",
    # row 4: ice    rust    mint      orchid   lemon    rose
    "#6bd6ff",
    "#fd9644",
    "#00b894",
    "#d980fa",
    "#ffdd59",
    "#ff6b81",
    # row 5: steel  amber   seafoam   periwinkle cream  blush
    "#89b4fa",
    "#fa8231",
    "#43e97b",
    "#786fa6",
    "#fdcb6e",
    "#f8a5c2",
    # row 6: slate  bronze  sage      plum     straw  mauve
    "#8a94a6",
    "#b07d51",
    "#7bed9f",
    "#6c5ce7",
    "#e2d96e",
    "#b0bec5",
]


def render_html(graph: GraphData, out_path: str) -> None:
    """Serialise ``graph`` into the HTML template and write ``out_path``."""
    # Build a color map covering only the layers that actually appear, so the
    # legend contains no phantom entries from hardcoded tables.
    # Layers with an explicit override in _LAYER_COLORS keep their colour;
    # the rest are assigned palette colours in sorted order — deterministic
    # and guaranteed collision-free within a single build.
    actual_layers = sorted({n.layer for n in graph.nodes})
    palette_iter = iter(_PALETTE)
    color_map: dict[str, str] = {}
    for layer in actual_layers:
        color_map[layer] = _LAYER_COLORS.get(layer) or next(palette_iter, "#9e9e9e")
    payload = {
        "nodes": [n.__dict__ for n in graph.nodes],
        "edges": [e.__dict__ for e in graph.edges],
        "colors": color_map,
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = _TEMPLATE.replace("/*__DATA__*/", data_json)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ArchMap — Python Code Graph</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden;
    font: 13px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
    background: #0d1117; color: #e6edf3; }
  #app { display: flex; height: 100%; }
  #stage { position: relative; flex: 1; min-width: 0; }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas.dragging { cursor: grabbing; }

  #topbar { position: absolute; top: 12px; left: 12px; right: 12px; display: flex;
    gap: 8px; align-items: center; flex-wrap: wrap; pointer-events: none; }
  #topbar > * { pointer-events: auto; }
  #search { width: 240px; padding: 7px 10px; border-radius: 7px;
    border: 1px solid #30363d; background: #161b22; color: #e6edf3; }
  .pill { padding: 6px 10px; border-radius: 7px; border: 1px solid #30363d;
    background: #161b22; color: #c9d1d9; cursor: pointer; user-select: none; }
  .pill:hover { border-color: #58a6ff; }
  #hint { color: #8b949e; font-size: 12px; }
  #search-count { color: #ffd23f; font-size: 12px; font-weight: 600; min-width: 60px; }

  #legend { position: absolute; bottom: 12px; left: 12px; background: #161b22cc;
    border: 1px solid #30363d; border-radius: 8px; padding: 8px 10px;
    backdrop-filter: blur(6px); max-width: 190px; }
  #legend .row { display: flex; align-items: center; gap: 7px; padding: 2px 0;
    cursor: pointer; opacity: 1; }
  #legend .row.off { opacity: .35; }
  #legend .dot { width: 11px; height: 11px; border-radius: 50%; flex: none; }
  #legend .cnt { margin-left: auto; color: #8b949e; font-size: 11px; }

  #divider { width: 6px; flex: none; cursor: col-resize; background: #21262d;
    display: none; }
  #divider:hover, #divider.active { background: #1f6feb; }
  #app.panel-open #divider { display: block; }
  #panel { width: 430px; min-width: 300px; flex: none; border-left: 1px solid #30363d;
    background: #0f141a; display: flex; flex-direction: column; }
  #panel.hidden { display: none; }
  #panel header { padding: 14px 16px; border-bottom: 1px solid #21262d; }
  #panel .kind { font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
    color: #8b949e; }
  #panel h2 { margin: 4px 0 2px; font-size: 17px; word-break: break-word; }
  #panel .qual { font-size: 11px; color: #6e7681; word-break: break-all; }
  #panel .body { padding: 14px 16px; overflow-y: auto; flex: 1; }
  #panel .sig { background: #161b22; border: 1px solid #21262d; border-radius: 6px;
    padding: 8px 10px; font-family: ui-monospace, Consolas, monospace; font-size: 12px;
    white-space: pre-wrap; word-break: break-word; margin-bottom: 12px; color: #79c0ff; }
  .sectlabel { font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
    color: #8b949e; margin: 14px 0 6px; }
  .desc { color: #c9d1d9; }
  pre.src { background: #161b22; border: 1px solid #21262d; border-radius: 6px;
    padding: 10px; overflow: auto; max-height: 300px; font-family: ui-monospace,
    Consolas, monospace; font-size: 11.5px; line-height: 1.45; }
  .btnrow { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
  button.act { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
    border-radius: 6px; padding: 7px 11px; cursor: pointer; font-size: 12px; }
  button.act:hover { border-color: #58a6ff; }
  button.act.primary { background: #1f6feb; border-color: #1f6feb; }
  button.act.primary:hover { background: #388bfd; }
  a.act { text-decoration: none; }
  .rel { margin: 6px 0; }
  .rel a { color: #79c0ff; cursor: pointer; text-decoration: none; }
  .rel a:hover { text-decoration: underline; }
  .rel .muted { color: #6e7681; }
  #ai-thread { display: none; margin-top: 8px; max-height: 320px; overflow-y: auto;
    border: 1px solid #21262d; border-radius: 6px; padding: 8px; background: #0d1117; }
  .msg { margin: 6px 0; padding: 7px 9px; border-radius: 6px; white-space: pre-wrap;
    word-break: break-word; font-size: 12.5px; line-height: 1.5; }
  .msg .who { font-size: 10px; text-transform: uppercase; letter-spacing: .5px;
    color: #8b949e; margin-bottom: 3px; }
  .msg.user { background: #1f6feb22; border: 1px solid #1f6feb55; }
  .msg.assistant { background: #161b22; border: 1px solid #21262d; color: #c9d1d9; }
  .msg.err { background: #3d1a1a; border: 1px solid #6e2b2b; color: #ff9c9c; }
  .msg.notice { background: transparent; border: none; color: #8b949e;
    text-align: center; font-size: 11px; padding: 2px; }
  #ai-status { margin-top: 8px; min-height: 14px; }
  #ai-tools { display: none; gap: 6px; margin-top: 8px; }
  #ai-tools .act { font-size: 11px; padding: 4px 8px; }
  #ai-inputrow { display: none; gap: 6px; margin-top: 8px; }
  #ai-input { flex: 1; resize: vertical; min-height: 32px; max-height: 140px;
    padding: 6px 8px; background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    color: #e6edf3; font-family: inherit; font-size: 12.5px; }
  details.settings { margin-top: 10px; border: 1px solid #21262d; border-radius: 6px;
    padding: 6px 10px; }
  details.settings summary { cursor: pointer; color: #8b949e; }
  details.settings label { display: block; margin: 8px 0 2px; font-size: 11px;
    color: #8b949e; }
  details.settings input, details.settings select { width: 100%; padding: 5px 7px;
    background: #161b22; border: 1px solid #30363d; border-radius: 5px; color: #e6edf3; }
  .note { font-size: 11px; color: #6e7681; margin-top: 6px; }
</style>
</head>
<body>
<div id="app">
  <div id="stage">
    <canvas id="cv"></canvas>
    <div id="topbar">
      <input id="search" placeholder="Search functions, classes, files...">
      <span id="search-count"></span>
      <span class="pill" id="fit">Fit</span>
      <span class="pill" id="toggle-contains">contains: on</span>
      <span class="pill" id="toggle-calls">calls: on</span>
      <span class="pill" id="toggle-imports">imports: off</span>
      <span id="hint">drag to pan &middot; scroll to zoom &middot; click a node &middot; search + &crarr; to frame matches</span>
    </div>
    <div id="legend"></div>
  </div>
  <div id="divider" title="Drag to resize"></div>
  <aside id="panel" class="hidden">
    <header>
      <div class="kind" id="p-kind"></div>
      <h2 id="p-name"></h2>
      <div class="qual" id="p-qual"></div>
    </header>
    <div class="body" id="p-body"></div>
  </aside>
</div>

<script id="graph-data" type="application/json">/*__DATA__*/</script>
<script>
const DATA = JSON.parse(document.getElementById('graph-data').textContent);
const COLORS = DATA.colors;
const nodes = DATA.nodes;
const edges = DATA.edges;
const byId = new Map(nodes.map(n => [n.id, n]));

// adjacency for the side panel's relationship lists
const outCalls = new Map(), inCalls = new Map(), children = new Map();
for (const e of edges) {
  if (e.relation === 'calls') {
    (outCalls.get(e.source) || outCalls.set(e.source, []).get(e.source)).push(e.target);
    (inCalls.get(e.target) || inCalls.set(e.target, []).get(e.target)).push(e.source);
  }
  if (e.relation === 'contains') {
    (children.get(e.source) || children.set(e.source, []).get(e.source)).push(e.target);
  }
}

const RADIUS = { module: 9, class: 6.5, function: 4.2, method: 3.8 };
const relOn = { contains: true, calls: true, imports: false };
const layerOff = new Set();

const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
let DPR = window.devicePixelRatio || 1;
let view = { x: 0, y: 0, scale: 1 };   // world->screen: s = world*scale + offset
let selected = null, hovered = null, searchHits = new Set();

function resize() {
  DPR = window.devicePixelRatio || 1;
  cv.width = cv.clientWidth * DPR;
  cv.height = cv.clientHeight * DPR;
  draw();
}
window.addEventListener('resize', resize);

function worldToScreen(x, y) {
  return [x * view.scale + view.x, y * view.scale + view.y];
}
function screenToWorld(sx, sy) {
  return [(sx - view.x) / view.scale, (sy - view.y) / view.scale];
}

function fit() {
  const vis = nodes.filter(n => !layerOff.has(n.layer));
  if (!vis.length) return;
  const w = cv.clientWidth, h = cv.clientHeight, pad = 60;
  if (w <= pad || h <= pad) return;  // canvas not laid out yet — a 0-height
  // stage here would make (h - pad) negative and mirror the whole graph.
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of vis) {
    minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
  }
  const scale = Math.min((w - pad) / (maxX - minX || 1), (h - pad) / (maxY - minY || 1));
  view.scale = Math.max(0.02, Math.min(scale, 2.5));
  view.x = w / 2 - (minX + maxX) / 2 * view.scale;
  view.y = h / 2 - (minY + maxY) / 2 * view.scale;
  draw();
}

function nodeVisible(n) { return !layerOff.has(n.layer); }

function draw() {
  ctx.save();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, cv.clientWidth, cv.clientHeight);

  const searching = searchHits.size > 0;

  // edges
  const focus = selected || hovered;
  const focusSet = focus ? relatedSet(focus.id) : null;
  ctx.lineWidth = 1;
  for (const e of edges) {
    if (!relOn[e.relation]) continue;
    const a = byId.get(e.source), b = byId.get(e.target);
    if (!a || !b || !nodeVisible(a) || !nodeVisible(b)) continue;
    const [ax, ay] = worldToScreen(a.x, a.y);
    const [bx, by] = worldToScreen(b.x, b.y);
    let alpha = 0.10, col = '#5b6572';
    if (e.relation === 'calls') { col = '#3fb0ff'; alpha = 0.16; }
    if (e.relation === 'imports') { col = '#c78bff'; alpha = 0.14; }
    if (focusSet && (e.source === focus.id || e.target === focus.id)) {
      alpha = 0.9; ctx.lineWidth = 1.6;
    } else if (focusSet) { alpha *= 0.25; ctx.lineWidth = 1; }
    else if (searching) {
      // an edge touching a match stays lit; the rest of the field recedes
      if (searchHits.has(e.source) || searchHits.has(e.target)) { alpha = 0.55; ctx.lineWidth = 1.4; }
      else { alpha *= 0.12; ctx.lineWidth = 1; }
    }
    else { ctx.lineWidth = 1; }
    ctx.strokeStyle = col; ctx.globalAlpha = alpha;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // nodes
  for (const n of nodes) {
    if (!nodeVisible(n)) continue;
    const [sx, sy] = worldToScreen(n.x, n.y);
    if (sx < -40 || sy < -40 || sx > cv.clientWidth + 40 || sy > cv.clientHeight + 40) continue;
    const hit = searching && searchHits.has(n.id);
    let r = (RADIUS[n.kind] || 4) * Math.max(0.6, Math.min(view.scale, 1.6));
    if (hit) r *= 1.7;  // matches grow so they read at any zoom
    // dim: out-of-focus while inspecting, or a non-match while searching
    const dim = (focusSet && !focusSet.has(n.id)) || (searching && !hit);

    // search halo: a soft gold glow behind each match so it pops off the field
    if (hit) {
      ctx.globalAlpha = 1;
      ctx.fillStyle = 'rgba(255,210,63,0.20)';
      ctx.beginPath(); ctx.arc(sx, sy, r + 8, 0, 6.2832); ctx.fill();
      ctx.fillStyle = 'rgba(255,210,63,0.38)';
      ctx.beginPath(); ctx.arc(sx, sy, r + 3.5, 0, 6.2832); ctx.fill();
    }

    ctx.globalAlpha = dim ? (searching && !focusSet ? 0.08 : 0.18) : 1;
    ctx.fillStyle = COLORS[n.layer] || '#9e9e9e';
    ctx.beginPath(); ctx.arc(sx, sy, r, 0, 6.2832); ctx.fill();
    if (n === selected) { ctx.lineWidth = 2.5; ctx.strokeStyle = '#fff'; ctx.stroke(); }
    else if (hit) { ctx.lineWidth = 2.5; ctx.strokeStyle = '#ffd23f'; ctx.stroke(); }
    // labels: modules always; others when zoomed in or focused/hit
    const showLabel = n.kind === 'module' ? view.scale > 0.5
      : (view.scale > 1.15 || n === selected || n === hovered || hit);
    if (showLabel && !dim) {
      ctx.globalAlpha = 1;
      ctx.fillStyle = hit ? '#ffe08a' : (n.kind === 'module' ? '#e6edf3' : '#9aa4b2');
      ctx.font = (n.kind === 'module' || hit ? '600 ' : '') + (n.kind === 'module' ? 12 : (hit ? 11 : 10)) + 'px sans-serif';
      ctx.fillText(n.name, sx + r + 3, sy + 3);
    }
  }
  ctx.globalAlpha = 1;
  ctx.restore();
}

function relatedSet(id) {
  const s = new Set([id]);
  for (const t of (outCalls.get(id) || [])) s.add(t);
  for (const t of (inCalls.get(id) || [])) s.add(t);
  for (const t of (children.get(id) || [])) s.add(t);
  return s;
}

// --- interaction ----------------------------------------------------------
let dragging = false, moved = false, last = null, resizingPanel = false;
cv.addEventListener('mousedown', e => { dragging = true; moved = false; last = [e.clientX, e.clientY]; cv.classList.add('dragging'); });
window.addEventListener('mouseup', () => { dragging = false; cv.classList.remove('dragging'); });
window.addEventListener('mousemove', e => {
  if (resizingPanel) return;
  if (dragging) {
    const dx = e.clientX - last[0], dy = e.clientY - last[1];
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    view.x += dx; view.y += dy; last = [e.clientX, e.clientY]; draw();
  } else {
    const r = cv.getBoundingClientRect();
    const h = pick(e.clientX - r.left, e.clientY - r.top);
    if (h !== hovered) { hovered = h; cv.style.cursor = h ? 'pointer' : 'grab'; draw(); }
  }
});
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const [wx, wy] = screenToWorld(mx, my);
  const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  view.scale = Math.max(0.08, Math.min(view.scale * f, 6));
  view.x = mx - wx * view.scale; view.y = my - wy * view.scale;
  draw();
}, { passive: false });
cv.addEventListener('click', e => {
  if (moved) return;
  const r = cv.getBoundingClientRect();
  const h = pick(e.clientX - r.left, e.clientY - r.top);
  if (h) selectNode(h); else {
    selected = null;
    document.getElementById('panel').classList.add('hidden');
    document.getElementById('app').classList.remove('panel-open');
    resize();  // stage width changed — resize the canvas backing store, then draw
  }
});

function pick(sx, sy) {
  let best = null, bestD = 14 * 14;
  for (const n of nodes) {
    if (!nodeVisible(n)) continue;
    const [x, y] = worldToScreen(n.x, n.y);
    const d = (x - sx) ** 2 + (y - sy) ** 2;
    if (d < bestD) { bestD = d; best = n; }
  }
  return best;
}

// --- side panel -----------------------------------------------------------
function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function vscodeLink(n) { return 'vscode://file/' + n.abs_file + ':' + n.line; }
// A plain <a href="vscode://..."> navigates the *current* document even with
// target="_blank" inside VS Code's built-in Simple Browser / Live Preview
// (which don't hand off custom schemes to the OS) — that blanks out the whole
// graph. Attempt the protocol via a disposable hidden iframe instead, so a
// failed handoff never destroys the visible page, and always copy path:line
// to the clipboard first as a guaranteed Ctrl+P fallback either way.
function openInVSCode(n) {
  // Quick Open (Ctrl+P) fuzzy-matches on the filename — pasting the full
  // repo-relative path returns no results, so copy just `name.py:line` here
  // (the plain "Copy path" button below still copies the full path).
  navigator.clipboard.writeText(n.file.split('/').pop() + ':' + n.line).catch(() => {});
  const f = document.createElement('iframe');
  f.style.display = 'none';
  f.src = vscodeLink(n);
  document.body.appendChild(f);
  setTimeout(() => f.remove(), 1000);
}

function relList(ids, label) {
  if (!ids || !ids.length) return '';
  const items = ids.map(id => {
    const t = byId.get(id); if (!t) return '';
    return `<div class="rel">&bull; <a data-goto="${t.id}">${esc(t.name)}</a> `
      + `<span class="muted">${esc(t.file.split('/').pop())}</span></div>`;
  }).join('');
  return `<div class="sectlabel">${label} (${ids.length})</div>${items}`;
}

function selectNode(n) {
  selected = n;
  document.getElementById('p-kind').innerHTML = esc(n.kind) + ' &middot; '
    + `<span style="color:${COLORS[n.layer]}">${esc(n.layer)}</span>`;
  document.getElementById('p-name').textContent = n.name;
  document.getElementById('p-qual').textContent = n.qualname;
  const desc = n.docstring ? esc(n.docstring)
    : '<span class="muted">No docstring. Use "Describe with AI" for a summary.</span>';
  const body = document.getElementById('p-body');
  body.innerHTML = `
    ${n.signature ? `<div class="sig">${esc(n.signature)}</div>` : ''}
    <div class="btnrow">
      <button class="act primary" id="btn-vscode" title="Also copies filename:line — if this doesn't jump directly (e.g. inside VS Code's built-in browser), press Ctrl+P and paste">Open in VS Code</button>
      <button class="act" id="btn-copy">Copy path</button>
      <button class="act" id="btn-ai">Describe with AI</button>
    </div>
    <div class="sectlabel">Docstring</div>
    <div class="desc" id="p-desc">${desc}</div>
    <div class="sectlabel">Ask AI</div>
    <div id="ai-thread"></div>
    <div id="ai-status" class="note"></div>
    <div id="ai-tools">
      <button class="act" id="ai-compact" title="Summarise the running context">/compact</button>
      <button class="act" id="ai-clear" title="Clear the conversation and start fresh">/clear</button>
    </div>
    <div id="ai-inputrow">
      <textarea id="ai-input" rows="1" placeholder="Ask, or type a /command… (Enter to send)"></textarea>
      <button class="act" id="ai-send">Send</button>
    </div>
    ${n.source ? `<div class="sectlabel">Source &mdash; ${esc(n.file)}:${n.line}</div>
      <pre class="src">${esc(n.source)}</pre>` : ''}
    ${relList(n.parent ? [n.parent] : null, 'Contained in')}
    ${relList(outCalls.get(n.id), 'Calls')}
    ${relList(inCalls.get(n.id), 'Called by')}
    ${relList(children.get(n.id), 'Contains')}
    ${aiSettingsHtml()}
  `;
  document.getElementById('panel').classList.remove('hidden');
  document.getElementById('app').classList.add('panel-open');
  const copy = document.getElementById('btn-copy');
  if (copy) copy.onclick = () => navigator.clipboard.writeText(n.file + ':' + n.line);
  const vsc = document.getElementById('btn-vscode');
  if (vsc) vsc.onclick = () => {
    openInVSCode(n);
    const prev = vsc.textContent;
    vsc.textContent = 'Opening… (path copied — Ctrl+P to jump if not)';
    setTimeout(() => { vsc.textContent = prev; }, 2200);
  };
  const ai = document.getElementById('btn-ai');
  if (ai) ai.onclick = () => describe(n);
  const send = document.getElementById('ai-send');
  const inp = document.getElementById('ai-input');
  if (send) send.onclick = () => sendFollowup();
  if (inp) inp.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendFollowup(); }
  });
  const bc = document.getElementById('ai-compact');
  if (bc) bc.onclick = () => { if (!chat.busy) aiTurn('/compact', '/compact'); };
  const br = document.getElementById('ai-clear');
  if (br) br.onclick = clearContext;
  renderThread();  // the conversation persists across node selection
  body.querySelectorAll('[data-goto]').forEach(a =>
    a.onclick = () => { const t = byId.get(a.dataset.goto); if (t) { centerOn(t); selectNode(t); } });
  bindSettings();
  resize();  // panel just opened/changed width — resize the canvas, then draw
}

function centerOn(n) {
  view.x = cv.clientWidth / 2 - n.x * view.scale;
  view.y = cv.clientHeight * 0.4 - n.y * view.scale;
}

// --- AI chat ---------------------------------------------------------------
// The key never lives in the browser: the page POSTs the message history to the
// archmap server's same-origin /chat proxy, which calls the provider with a key
// read from the environment / backend/.env (or the Claude Code subscription).
// Provider + model are chosen here; only the (non-secret) local base URL is sent.
const AI_DEFAULTS = {
  'claude-code': { url: '', model: '' },
  ollama: { url: 'http://localhost:11434', model: 'llama3.2' },
  openai: { url: 'http://localhost:1234', model: 'local-model' },
  claude: { url: '', model: 'claude-haiku-4-5-20251001' },
};
const NO_URL = new Set(['claude', 'claude-code']);
const servedByProxy = location.protocol !== 'file:';
function aiCfg() {
  const style = localStorage.getItem('am_style') || 'claude-code';
  const d = AI_DEFAULTS[style] || AI_DEFAULTS['claude-code'];
  return {
    style,
    url: localStorage.getItem('am_url_' + style) || d.url,
    model: localStorage.getItem('am_model_' + style) || d.model,
  };
}
function aiNote(style) {
  if (style === 'claude-code')
    return 'Keeps a live <code>claude</code> session (subscription, no API credits) so '
      + 'context is reused across turns and functions. Type <code>/compact</code>, '
      + '<code>/context</code>, etc. Leave model blank for your default, or set e.g. '
      + '<code>claude-haiku-4-5-20251001</code>.';
  if (style === 'claude')
    return 'Anthropic API. Uses <code>ANTHROPIC_API_KEY</code> from your environment '
      + '/ backend/.env (server-side). Billed as metered API credits, separate from Pro.';
  if (style === 'openai')
    return 'Local OpenAI-compatible server (LM Studio :1234, llama.cpp, vLLM). '
      + 'Key, if any, comes from <code>OPENAI_API_KEY</code> server-side.';
  return 'Local Ollama. The server calls it directly — no <code>OLLAMA_ORIGINS</code> '
    + 'needed since the browser is not the caller.';
}
function aiSettingsHtml() {
  const c = aiCfg();
  const opt = (v, t) => `<option value="${v}"${c.style === v ? ' selected' : ''}>${t}</option>`;
  const localUrl = !NO_URL.has(c.style);
  return `<details class="settings">
    <summary>AI settings</summary>
    <label>Provider</label>
    <select id="s-style">
      ${opt('claude-code', 'Claude Code (your subscription)')}
      ${opt('ollama', 'Ollama (local)')}
      ${opt('openai', 'OpenAI-compatible (local)')}
      ${opt('claude', 'Claude (Anthropic API — credits)')}
    </select>
    <label>Model${c.style === 'claude-code' ? ' (optional)' : ''}</label>
    <input id="s-model" value="${esc(c.model)}"
      placeholder="${c.style === 'claude-code' ? 'blank = your Claude Code default' : ''}">
    <div id="s-url-wrap" style="${localUrl ? '' : 'display:none'}">
      <label>Local endpoint base URL</label>
      <input id="s-url" value="${esc(c.url)}">
    </div>
    <div class="note" id="s-note">${aiNote(c.style)}</div>
    ${servedByProxy ? '' : '<div class="note" style="color:#e3b341">'
      + 'Opened as a file — AI needs the server. Run '
      + '<code>python -m tools.archmap.serve</code> and open the localhost URL.</div>'}
  </details>`;
}
function bindSettings() {
  const style = document.getElementById('s-style');
  const model = document.getElementById('s-model');
  const url = document.getElementById('s-url');
  if (!style) return;
  const cur = () => localStorage.getItem('am_style') || 'claude-code';
  model.onchange = () => localStorage.setItem('am_model_' + cur(), model.value);
  if (url) url.onchange = () => localStorage.setItem('am_url_' + cur(), url.value);
  style.onchange = () => {
    localStorage.setItem('am_style', style.value);
    const c = aiCfg();
    model.value = c.model;
    model.placeholder = c.style === 'claude-code' ? 'blank = your Claude Code default' : '';
    const wrap = document.getElementById('s-url-wrap');
    if (NO_URL.has(c.style)) { wrap.style.display = 'none'; }
    else { wrap.style.display = ''; document.getElementById('s-url').value = c.url; }
    document.getElementById('s-note').innerHTML = aiNote(c.style);
    updateTools();
  };
}

// One conversation for the whole tool. It persists across node selection so
// context accumulates as you explore; Claude Code keeps it in a live process
// (see /session), other providers replay this history to /chat each turn.
const chat = { messages: [], busy: false };

function thinkingLabel() {
  const s = aiCfg().style;
  return ({ 'claude-code': 'Thinking with Claude Code (subscription)…',
    claude: 'Thinking with Claude…' })[s] || 'Thinking with local model…';
}
function scrollThread() {
  const t = document.getElementById('ai-thread');
  if (t) t.scrollTop = t.scrollHeight;
}
function addBubble(role, text) {
  const thread = document.getElementById('ai-thread');
  if (!thread) return;
  thread.style.display = 'block';
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  if (role === 'notice') {
    d.textContent = text;
  } else {
    const who = role === 'user' ? 'You' : role === 'err' ? 'Error' : 'AI';
    d.innerHTML = '<div class="who">' + who + '</div>' + esc(text);
  }
  thread.appendChild(d);
  scrollThread();
}
function pushMsg(role, content, display) {
  chat.messages.push({ role, content, display: display == null ? content : display });
  addBubble(role, display == null ? content : display);
}
function renderThread() {
  const thread = document.getElementById('ai-thread');
  if (!thread) return;
  thread.innerHTML = '';
  for (const m of chat.messages) addBubble(m.role, m.display);
  updateTools();
}
function updateTools() {
  const cc = aiCfg().style === 'claude-code';
  const tools = document.getElementById('ai-tools');
  if (tools) tools.style.display = servedByProxy ? 'flex' : 'none';
  const compact = document.getElementById('ai-compact');
  if (compact) compact.style.display = cc ? '' : 'none';  // /compact is Claude Code only
  const rows = document.getElementById('ai-inputrow');
  if (rows) rows.style.display = servedByProxy ? 'flex' : 'none';
}
function setBusy(busy) {
  for (const id of ['ai-send', 'ai-compact', 'ai-clear']) {
    const b = document.getElementById(id); if (b) b.disabled = busy;
  }
  const s = document.getElementById('ai-status');
  if (s) s.textContent = busy ? thinkingLabel() : '';
}
function firstPrompt(n) {
  const head = 'You are a senior software engineer reading a Python codebase.\n'
    + `In 2-4 sentences, explain what this Python ${n.kind} does and its role in the system. `
    + 'Be concrete and specific; do not restate the signature.\n\n';
  if (n.source)
    return head + `File: ${n.file}\n\n\`\`\`python\n${n.source}\n\`\`\``;
  // Modules carry no source snippet — hand over the path, docstring and member
  // list. Claude Code can open the file itself; local models get the outline.
  const kids = (children.get(n.id) || []).map(id => byId.get(id))
    .filter(Boolean).map(t => t.name);
  const doc = n.docstring ? `\nModule docstring: ${n.docstring}` : '';
  const members = kids.length ? `\nTop-level members: ${kids.join(', ')}` : '';
  return head + `Module file: ${n.file}${doc}${members}\n\n`
    + '(You can open this file directly to read its full source.)';
}
async function postJSON(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

async function aiTurn(sendText, displayText) {
  if (chat.busy) return;
  if (!servedByProxy) { fileModeNote(); return; }
  const c = aiCfg();
  pushMsg('user', sendText, displayText);
  chat.busy = true; setBusy(true);
  try {
    let reply, notice;
    if (c.style === 'claude-code') {
      const j = await postJSON('/session', { action: 'send', text: sendText, model: c.model });
      reply = j.reply; notice = j.notice;
    } else {
      const hist = chat.messages.filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role, content: m.content }));
      const j = await postJSON('/chat', { provider: c.style, model: c.model, base: c.url, messages: hist });
      reply = j.reply;
    }
    pushMsg('assistant', (reply || '').trim() || '(empty response)');
    if (notice) pushMsg('notice', notice);
    const inp = document.getElementById('ai-input'); if (inp) inp.focus();
  } catch (err) {
    chat.messages.pop();       // drop the unanswered user turn
    renderThread();            // rebuild without it
    addBubble('err', 'AI call failed: ' + err.message);
  } finally {
    chat.busy = false; setBusy(false); scrollThread();
  }
}

function fileModeNote() {
  addBubble('err', 'AI runs through the archmap server so your key / subscription stays '
    + 'server-side. Start it with:  python -m archmap.serve  then open '
    + 'http://localhost:8777/archmap.html');
}

function describe(n) {
  if (!servedByProxy) { fileModeNote(); return; }
  aiTurn(firstPrompt(n), 'Describe ' + n.name + ' — ' + n.file.split('/').pop());
}

function sendFollowup() {
  const inp = document.getElementById('ai-input');
  if (!inp) return;
  const text = inp.value.trim();
  if (!text || chat.busy) return;
  if (text[0] === '/' && aiCfg().style !== 'claude-code') {
    addBubble('err', 'Slash commands like /compact only work with the Claude Code provider.');
    return;
  }
  inp.value = '';
  aiTurn(text, text);
}

// Clear the running conversation. For Claude Code this also resets the live
// server session (guaranteed context wipe); stateless providers just drop the
// local history, which is itself a fresh start.
async function clearContext() {
  if (chat.busy) return;
  try { await postJSON('/session', { action: 'reset' }); } catch (e) { /* local clear anyway */ }
  chat.messages = [];
  renderThread();
  addBubble('notice', 'Context cleared.');
}

// --- topbar / legend ------------------------------------------------------
document.getElementById('fit').onclick = fit;
function relBtn(id, key) {
  const el = document.getElementById(id);
  el.onclick = () => { relOn[key] = !relOn[key];
    el.textContent = key + ': ' + (relOn[key] ? 'on' : 'off'); draw(); };
}
relBtn('toggle-contains', 'contains');
relBtn('toggle-calls', 'calls');
relBtn('toggle-imports', 'imports');

const search = document.getElementById('search');
const searchCount = document.getElementById('search-count');
function runSearch() {
  const q = search.value.trim().toLowerCase();
  searchHits = new Set();
  if (q) {
    for (const n of nodes)
      if (n.name.toLowerCase().includes(q) || n.file.toLowerCase().includes(q)
          || (n.source && n.source.toLowerCase().includes(q))
          || (n.docstring && n.docstring.toLowerCase().includes(q))
          || (n.signature && n.signature.toLowerCase().includes(q)))
        searchHits.add(n.id);
    const directCount = searchHits.size;
    // bubble matches up to containing classes/modules so a hit deep inside is still visible
    for (const id of [...searchHits]) {
      let p = byId.get(id)?.parent;
      while (p && !searchHits.has(p)) { searchHits.add(p); p = byId.get(p)?.parent; }
    }
    searchCount.textContent = directCount + ' match' + (directCount === 1 ? '' : 'es');
  } else {
    searchCount.textContent = '';
  }
  draw();
}
search.oninput = runSearch;
// Enter frames the matches so off-screen hits come into view.
search.addEventListener('keydown', e => { if (e.key === 'Enter') fitToMatches(); });

function fitToMatches() {
  const vis = nodes.filter(n => searchHits.has(n.id) && !layerOff.has(n.layer));
  if (!vis.length) return;
  const w = cv.clientWidth, h = cv.clientHeight, pad = 120;
  if (w <= pad || h <= pad) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of vis) { minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y); }
  const scale = Math.min((w - pad) / (maxX - minX || 1), (h - pad) / (maxY - minY || 1));
  view.scale = Math.max(0.08, Math.min(scale, 2.2));
  view.x = w / 2 - (minX + maxX) / 2 * view.scale;
  view.y = h / 2 - (minY + maxY) / 2 * view.scale;
  draw();
}

const legend = document.getElementById('legend');
const counts = {};
for (const n of nodes) counts[n.layer] = (counts[n.layer] || 0) + 1;
for (const layer of Object.keys(COLORS)) {
  if (!counts[layer]) continue;
  const row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = `<span class="dot" style="background:${COLORS[layer]}"></span>`
    + `<span>${layer}</span><span class="cnt">${counts[layer]}</span>`;
  row.onclick = () => {
    if (layerOff.has(layer)) layerOff.delete(layer); else layerOff.add(layer);
    row.classList.toggle('off'); draw();
  };
  legend.appendChild(row);
}

// --- resizable side panel -------------------------------------------------
(function () {
  const divider = document.getElementById('divider');
  const panel = document.getElementById('panel');
  const saved = parseInt(localStorage.getItem('am_panelw'), 10);
  if (saved) panel.style.width = saved + 'px';
  divider.addEventListener('mousedown', e => {
    resizingPanel = true; divider.classList.add('active');
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!resizingPanel) return;
    const w = Math.min(Math.max(window.innerWidth - e.clientX, 300), window.innerWidth - 220);
    panel.style.width = w + 'px';
    resize();
  });
  window.addEventListener('mouseup', () => {
    if (!resizingPanel) return;
    resizingPanel = false; divider.classList.remove('active');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    localStorage.setItem('am_panelw', parseInt(panel.style.width, 10) || 430);
  });
})();

resize();
fit();
// Re-fit once layout has settled: on first paint the stage can report a
// 0-height (fit bails), so run again on the next frame when it has real size.
requestAnimationFrame(() => { resize(); fit(); });
</script>
</body>
</html>
"""
