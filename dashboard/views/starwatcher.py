"""
Starwatcher Constellation Visualization

Renders a cosmic observatory-themed Canvas visualization of the Discovery
Engine's company pipeline.  Each company is a node positioned in polar
coordinates by status sector (angle) and thesis score (radius).  Connections
are drawn between companies that share signal sources within 24 hours.

The entire Canvas scene is self-contained HTML5/JS embedded via
``st.components.v1.html``.  Sidebar controls let the user filter by status,
thesis score range, and signal source.
"""

import json
from dataclasses import asdict
from typing import Any, Dict, List

import streamlit as st
import streamlit.components.v1 as components

from dashboard.adapters.starwatcher_adapter import (
    CompanyNode,
    Connection,
    ConstellationProps,
    STATUS_COLOR_MAP,
    STATUS_LABEL_MAP,
    STATUS_SHAPE_MAP,
    VALID_STATUS_IDS,
    build_constellation_props,
    to_status_label,
)
from utils.db_path_helper import resolve_db_path_env

# =============================================================================
# CONSTANTS
# =============================================================================

CANVAS_WIDTH = 900
CANVAS_HEIGHT = 700
HTML_HEIGHT = 760  # extra for legend bar

ALL_STATUSES = list(STATUS_LABEL_MAP.keys())

# =============================================================================
# HELPERS
# =============================================================================


def _collect_sources(nodes: List[CompanyNode]) -> List[str]:
    """Return sorted unique signal sources across all nodes."""
    sources: set = set()
    for node in nodes:
        for tag in node.tags:
            sources.add(tag)
    return sorted(sources)


def _filter_nodes(
    nodes: List[CompanyNode],
    statuses: List[str],
    score_range: tuple,
    sources: List[str],
) -> List[CompanyNode]:
    """Filter nodes by sidebar selections."""
    filtered = []
    for n in nodes:
        if n.status not in statuses:
            continue
        if not (score_range[0] <= n.thesisScore <= score_range[1]):
            continue
        if sources and not any(t in sources for t in n.tags):
            continue
        filtered.append(n)
    return filtered


def _filter_edges(
    edges: List[Connection],
    node_ids: set,
) -> List[Connection]:
    """Keep only edges whose endpoints are both visible."""
    return [e for e in edges if e.source in node_ids and e.target in node_ids]


def _serialize_for_js(
    nodes: List[CompanyNode],
    edges: List[Connection],
) -> str:
    """Serialize nodes and edges to a JSON string safe for embedding."""
    node_dicts = []
    for n in nodes:
        d = asdict(n)
        d["shape"] = STATUS_SHAPE_MAP.get(n.status, "circle")
        d["color"] = STATUS_COLOR_MAP.get(n.status, "#9BA2AA")
        d["statusLabel"] = to_status_label(n.status)
        node_dicts.append(d)

    edge_dicts = [asdict(e) for e in edges]

    return json.dumps({"nodes": node_dicts, "edges": edge_dicts}, default=str)


# =============================================================================
# CANVAS HTML BUILDER
# =============================================================================


def _build_canvas_html(data_json: str) -> str:
    """Return a fully self-contained HTML document for the constellation."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:100%;height:100%;overflow:hidden;background:#16191D;font-family:'DM Sans',sans-serif;color:#F1F3F5}}
canvas{{display:block;cursor:crosshair}}
#tooltip{{
  position:absolute;pointer-events:none;opacity:0;
  transition:opacity .15s ease;
  background:rgba(33,37,41,.92);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border:1px solid #495057;border-radius:10px;padding:14px 18px;
  min-width:220px;max-width:320px;
  font-family:'DM Sans',sans-serif;font-size:12px;color:#F1F3F5;
  box-shadow:0 8px 32px rgba(0,0,0,.45);z-index:100;
}}
#tooltip .tt-name{{font-family:'Instrument Serif',serif;font-size:17px;margin-bottom:6px;color:#F1F3F5}}
#tooltip .tt-status{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-bottom:6px}}
#tooltip .tt-score{{font-family:'JetBrains Mono',monospace;font-size:12px;color:#2DB299;margin-bottom:4px}}
#tooltip .tt-rationale{{font-size:11px;color:#CED4DA;line-height:1.45}}
#tooltip .tt-signals{{font-size:10px;color:#868E96;margin-top:6px}}
#legend{{
  position:absolute;bottom:12px;left:16px;display:flex;gap:8px;flex-wrap:wrap;z-index:50;
}}
.legend-chip{{
  display:flex;align-items:center;gap:5px;
  padding:4px 10px;border-radius:6px;
  font-size:10px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;
  background:rgba(33,37,41,.75);border:1px solid #495057;
  cursor:pointer;transition:border-color .2s,background .2s;user-select:none;
}}
.legend-chip:hover{{border-color:#868E96;background:rgba(33,37,41,.95)}}
.legend-chip.active{{border-color:#2DB299}}
.legend-dot{{width:8px;height:8px;border-radius:50%}}
#watermark{{
  position:absolute;top:14px;right:18px;
  font-family:'Instrument Serif',serif;font-size:13px;color:rgba(206,212,218,.35);
  letter-spacing:.06em;pointer-events:none;z-index:50;
}}
</style>
</head>
<body>
<canvas id="cv"></canvas>
<div id="tooltip"></div>
<div id="legend"></div>
<div id="watermark">STARWATCHER</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────
const DATA = {data_json};
const NODES = DATA.nodes;
const EDGES = DATA.edges;

// ── Theme constants ───────────────────────────────────────────────────────
const BG          = '#16191D';
const ACCENT      = '#2DB299';
const TEXT_PRI     = '#F1F3F5';
const TEXT_SEC     = '#CED4DA';
const TEXT_MUTE    = '#868E96';
const BORDER       = '#495057';

const STATUS_COLORS = {{
  source:'#9BA2AA', initial_meeting:'#6BAEFF', diligence:'#F7B84D',
  tracking:'#B9A3FB', committed:'#2DB299', funded:'#34D399',
  passed:'#F87171', lost:'#FB923C'
}};

const STATUS_LABELS = {{
  source:'Source', initial_meeting:'Initial Meeting', diligence:'Diligence',
  tracking:'Tracking', committed:'Committed', funded:'Funded',
  passed:'Passed', lost:'Lost'
}};

const SECTOR_ANGLES = {{
  source:0, initial_meeting:45, diligence:90, tracking:135,
  committed:180, funded:225, passed:270, lost:315
}};

// ── Canvas setup ──────────────────────────────────────────────────────────
const cv  = document.getElementById('cv');
const ctx = cv.getContext('2d');
let W = {CANVAS_WIDTH}, H = {CANVAS_HEIGHT};
const DPR = window.devicePixelRatio || 1;

function resize() {{
  W = window.innerWidth || {CANVAS_WIDTH};
  H = window.innerHeight || {CANVAS_HEIGHT};
  cv.width  = W * DPR;
  cv.height = H * DPR;
  cv.style.width  = W + 'px';
  cv.style.height = H + 'px';
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}}
resize();
window.addEventListener('resize', () => {{ resize(); draw(); }});

// ── Pan / Zoom state ──────────────────────────────────────────────────────
let camX = 0, camY = 0, camZ = 1;
let dragging = false, dragX = 0, dragY = 0;

cv.addEventListener('wheel', e => {{
  e.preventDefault();
  const zf = e.deltaY < 0 ? 1.08 : 0.92;
  const mx = e.offsetX, my = e.offsetY;
  camX = mx - (mx - camX) * zf;
  camY = my - (my - camY) * zf;
  camZ *= zf;
  camZ = Math.max(0.3, Math.min(5, camZ));
  draw();
}}, {{passive:false}});

cv.addEventListener('mousedown', e => {{
  if (e.button === 0) {{ dragging = true; dragX = e.offsetX - camX; dragY = e.offsetY - camY; }}
}});
cv.addEventListener('mousemove', e => {{
  if (dragging) {{ camX = e.offsetX - dragX; camY = e.offsetY - dragY; draw(); }}
  handleHover(e.offsetX, e.offsetY);
}});
cv.addEventListener('mouseup', () => {{ dragging = false; }});
cv.addEventListener('mouseleave', () => {{ dragging = false; hoveredNode = null; draw(); hideTooltip(); }});

// ── Click ─────────────────────────────────────────────────────────────────
cv.addEventListener('click', e => {{
  if (dragging) return;
  const n = findNearest(e.offsetX, e.offsetY, 24);
  if (n) {{
    window.parent.postMessage({{
      type: 'starwatcher:node-click',
      nodeId: n.id,
      name: n.name,
      status: n.status,
      thesisScore: n.thesisScore,
      statusLabel: n.statusLabel,
      thesisRationale: n.thesisRationale,
      notionUrl: n.notionUrl,
      signals: n.signals,
      tags: n.tags,
    }}, '*');
  }}
}});

// ── Seeded PRNG (xoshiro128**) ────────────────────────────────────────────
function splitmix32(a) {{
  return function() {{
    a |= 0; a = a + 0x9e3779b9 | 0;
    let t = a ^ a >>> 16; t = Math.imul(t, 0x21f0aaad);
    t = t ^ t >>> 15; t = Math.imul(t, 0x735a2d97);
    return ((t = t ^ t >>> 15) >>> 0) / 4294967296;
  }};
}}
const seeded = splitmix32(42);

// ── Star field (pre-computed) ─────────────────────────────────────────────
const STARS = [];
for (let i = 0; i < 220; i++) {{
  STARS.push({{
    x: seeded(),
    y: seeded(),
    r: 0.5 + seeded() * 1.5,
    a: 0.04 + seeded() * 0.12,
    twinkleOffset: seeded() * Math.PI * 2,
    twinkleSpeed: 0.3 + seeded() * 0.7,
  }});
}}

// ── World transform helpers ───────────────────────────────────────────────
function toScreen(px, py) {{
  return [px * camZ + camX, py * camZ + camY];
}}
function toWorld(sx, sy) {{
  return [(sx - camX) / camZ, (sy - camY) / camZ];
}}

// ── Hover ─────────────────────────────────────────────────────────────────
let hoveredNode = null;
function findNearest(sx, sy, maxDist) {{
  const [wx, wy] = toWorld(sx, sy);
  let best = null, bestD = maxDist / camZ;
  for (const n of NODES) {{
    const dx = n.posX - wx, dy = n.posY - wy;
    const d = Math.sqrt(dx*dx + dy*dy);
    if (d < bestD) {{ bestD = d; best = n; }}
  }}
  return best;
}}

function handleHover(sx, sy) {{
  const n = findNearest(sx, sy, 28);
  if (n !== hoveredNode) {{
    hoveredNode = n;
    draw();
    if (n) showTooltip(n, sx, sy); else hideTooltip();
  }}
}}

// ── Tooltip ───────────────────────────────────────────────────────────────
const ttEl = document.getElementById('tooltip');
function showTooltip(n, sx, sy) {{
  const sc = STATUS_COLORS[n.status] || '#9BA2AA';
  let html = '<div class="tt-name">' + esc(n.name) + '</div>';
  html += '<span class="tt-status" style="background:' + sc + '22;color:' + sc + '">' + esc(n.statusLabel) + '</span>';
  html += '<div class="tt-score">Thesis Score: ' + (n.thesisScore * 100).toFixed(1) + '%</div>';
  html += '<div class="tt-rationale">' + esc(n.thesisRationale) + '</div>';
  if (n.signals && n.signals.length) {{
    html += '<div class="tt-signals">' + n.signals.length + ' signal' + (n.signals.length > 1 ? 's' : '') + ' detected</div>';
  }}
  ttEl.innerHTML = html;
  // Position
  let tx = sx + 16, ty = sy - 10;
  if (tx + 260 > W) tx = sx - 270;
  if (ty + 160 > H) ty = sy - 160;
  if (ty < 8) ty = 8;
  ttEl.style.left = tx + 'px';
  ttEl.style.top  = ty + 'px';
  ttEl.style.opacity = '1';
}}
function hideTooltip() {{ ttEl.style.opacity = '0'; }}
function esc(s) {{ const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }}

// ── Shape drawing ─────────────────────────────────────────────────────────
function drawShape(cx, cy, r, shape, fill, stroke) {{
  ctx.beginPath();
  switch (shape) {{
    case 'circle':
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      break;
    case 'square':
      ctx.rect(cx - r * 0.82, cy - r * 0.82, r * 1.64, r * 1.64);
      break;
    case 'triangle':
      for (let i = 0; i < 3; i++) {{
        const a = -Math.PI / 2 + (i * 2 * Math.PI / 3);
        const method = i === 0 ? 'moveTo' : 'lineTo';
        ctx[method](cx + r * 1.1 * Math.cos(a), cy + r * 1.1 * Math.sin(a));
      }}
      ctx.closePath();
      break;
    case 'star':
      for (let i = 0; i < 10; i++) {{
        const a = -Math.PI / 2 + (i * Math.PI / 5);
        const sr = i % 2 === 0 ? r * 1.1 : r * 0.5;
        const method = i === 0 ? 'moveTo' : 'lineTo';
        ctx[method](cx + sr * Math.cos(a), cy + sr * Math.sin(a));
      }}
      ctx.closePath();
      break;
    case 'diamond':
      ctx.moveTo(cx, cy - r * 1.15);
      ctx.lineTo(cx + r * 0.8, cy);
      ctx.lineTo(cx, cy + r * 1.15);
      ctx.lineTo(cx - r * 0.8, cy);
      ctx.closePath();
      break;
    case 'pentagon':
      for (let i = 0; i < 5; i++) {{
        const a = -Math.PI / 2 + (i * 2 * Math.PI / 5);
        const method = i === 0 ? 'moveTo' : 'lineTo';
        ctx[method](cx + r * Math.cos(a), cy + r * Math.sin(a));
      }}
      ctx.closePath();
      break;
    case 'hexagon':
      for (let i = 0; i < 6; i++) {{
        const a = (i * Math.PI / 3);
        const method = i === 0 ? 'moveTo' : 'lineTo';
        ctx[method](cx + r * Math.cos(a), cy + r * Math.sin(a));
      }}
      ctx.closePath();
      break;
    case 'octagon':
      for (let i = 0; i < 8; i++) {{
        const a = (i * Math.PI / 4) + Math.PI / 8;
        const method = i === 0 ? 'moveTo' : 'lineTo';
        ctx[method](cx + r * Math.cos(a), cy + r * Math.sin(a));
      }}
      ctx.closePath();
      break;
    default:
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
  }}
  if (fill)   {{ ctx.fillStyle   = fill;   ctx.fill();   }}
  if (stroke) {{ ctx.strokeStyle = stroke; ctx.lineWidth = 1.2; ctx.stroke(); }}
}}

// ── Animation ─────────────────────────────────────────────────────────────
let animT = 0;
function animate(ts) {{
  animT = ts * 0.001;
  draw();
  requestAnimationFrame(animate);
}}

// ── Main draw ─────────────────────────────────────────────────────────────
function draw() {{
  ctx.save();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

  // ── Layer 0: Background ──
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, W, H);

  // Radial gradient overlay
  const bgGrad = ctx.createRadialGradient(W/2, H/2, 0, W/2, H/2, Math.max(W, H) * 0.7);
  bgGrad.addColorStop(0, 'rgba(45,178,153,0.03)');
  bgGrad.addColorStop(0.5, 'rgba(45,178,153,0.012)');
  bgGrad.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, W, H);

  // Subtle vignette
  const vig = ctx.createRadialGradient(W/2, H/2, W*0.25, W/2, H/2, W*0.75);
  vig.addColorStop(0, 'rgba(0,0,0,0)');
  vig.addColorStop(1, 'rgba(0,0,0,0.35)');
  ctx.fillStyle = vig;
  ctx.fillRect(0, 0, W, H);

  // ── Layer 1: Star field ──
  for (const s of STARS) {{
    const twinkle = 0.5 + 0.5 * Math.sin(animT * s.twinkleSpeed + s.twinkleOffset);
    const alpha = s.a * (0.6 + 0.4 * twinkle);
    ctx.fillStyle = 'rgba(255,255,255,' + alpha.toFixed(3) + ')';
    ctx.beginPath();
    ctx.arc(s.x * W, s.y * H, s.r, 0, Math.PI * 2);
    ctx.fill();
  }}

  // Apply camera transform for world-space layers
  ctx.save();
  ctx.translate(camX, camY);
  ctx.scale(camZ, camZ);

  // ── Layer 2: Sector guides ──
  const ccx = {CANVAS_WIDTH} / 2, ccy = {CANVAS_HEIGHT} / 2;
  const maxR = Math.min(ccx, ccy) * 0.88;
  ctx.strokeStyle = 'rgba(73,80,87,0.08)';
  ctx.lineWidth = 0.5 / camZ;
  for (const [status, angle] of Object.entries(SECTOR_ANGLES)) {{
    const a = angle * Math.PI / 180;
    ctx.beginPath();
    ctx.moveTo(ccx, ccy);
    ctx.lineTo(ccx + maxR * 1.05 * Math.cos(a), ccy + maxR * 1.05 * Math.sin(a));
    ctx.stroke();

    // Sector label at outer edge
    const lx = ccx + (maxR * 1.12) * Math.cos(a);
    const ly = ccy + (maxR * 1.12) * Math.sin(a);
    ctx.save();
    ctx.font = '500 ' + (9 / camZ) + 'px "DM Sans"';
    ctx.fillStyle = 'rgba(134,142,150,0.55)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.letterSpacing = '0.08em';
    const label = (STATUS_LABELS[status] || status).toUpperCase();
    ctx.fillText(label, lx, ly);
    ctx.restore();
  }}

  // Orbit rings
  ctx.strokeStyle = 'rgba(73,80,87,0.04)';
  ctx.lineWidth = 0.5 / camZ;
  for (let ring = 0.25; ring <= 1.0; ring += 0.25) {{
    ctx.beginPath();
    ctx.arc(ccx, ccy, maxR * ring, 0, Math.PI * 2);
    ctx.stroke();
  }}

  // ── Layer 3: Constellation edges ──
  for (const e of EDGES) {{
    const src = NODES.find(n => n.id === e.source);
    const tgt = NODES.find(n => n.id === e.target);
    if (!src || !tgt) continue;
    const [sx, sy] = [src.posX, src.posY];
    const [tx, ty] = [tgt.posX, tgt.posY];
    const op = 0.06 + e.strength * 0.18;
    ctx.strokeStyle = 'rgba(45,178,153,' + op.toFixed(3) + ')';
    ctx.lineWidth = (0.5 + e.strength * 1.5) / camZ;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    // Slight curve via midpoint offset
    const mx = (sx + tx) / 2 + (sy - ty) * 0.08;
    const my = (sy + ty) / 2 + (tx - sx) * 0.08;
    ctx.quadraticCurveTo(mx, my, tx, ty);
    ctx.stroke();
  }}

  // ── Layer 4: Node glows ──
  for (const n of NODES) {{
    if (n.thesisScore < 0.5) continue;
    const r = 12 + Math.sqrt(n.thesisScore) * 12;
    const intensity = n.thesisScore >= 0.75
      ? 0.25 + 0.10 * Math.sin(animT * 2.1 + n.posX * 0.01)
      : 0.12 + n.thesisScore * 0.08;
    const glowR = r * (2.2 + (n === hoveredNode ? 1.0 : 0));
    const grad = ctx.createRadialGradient(n.posX, n.posY, r * 0.3, n.posX, n.posY, glowR);
    const col = n.color || '#9BA2AA';
    grad.addColorStop(0, col.slice(0,7) + hexAlpha(intensity));
    grad.addColorStop(0.5, col.slice(0,7) + hexAlpha(intensity * 0.4));
    grad.addColorStop(1, col.slice(0,7) + '00');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(n.posX, n.posY, glowR, 0, Math.PI * 2);
    ctx.fill();
  }}

  // ── Layer 5: Nodes ──
  // Sort: hovered last (on top)
  const sortedNodes = [...NODES].sort((a, b) => {{
    if (a === hoveredNode) return 1;
    if (b === hoveredNode) return -1;
    return a.thesisScore - b.thesisScore;
  }});

  for (const n of sortedNodes) {{
    const r = 12 + Math.sqrt(n.thesisScore) * 12;
    const col = n.color || '#9BA2AA';
    const isHovered = n === hoveredNode;

    // Breathing for high-score nodes
    let breathScale = 1;
    if (n.thesisScore >= 0.75) {{
      breathScale = 1 + 0.06 * Math.sin(animT * 2.1 + n.posX * 0.01);
    }}
    const dr = r * breathScale * (isHovered ? 1.25 : 1);

    // Shadow for depth
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.4)';
    ctx.shadowBlur = 8 / camZ;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 2 / camZ;
    drawShape(n.posX, n.posY, dr, n.shape, col, null);
    ctx.restore();

    // Inner highlight
    const iGrad = ctx.createRadialGradient(
      n.posX - dr * 0.25, n.posY - dr * 0.3, dr * 0.1,
      n.posX, n.posY, dr
    );
    iGrad.addColorStop(0, 'rgba(255,255,255,0.25)');
    iGrad.addColorStop(1, 'rgba(255,255,255,0)');
    drawShape(n.posX, n.posY, dr, n.shape, iGrad, null);

    // Stroke on hover
    if (isHovered) {{
      drawShape(n.posX, n.posY, dr + 2 / camZ, n.shape, null, 'rgba(255,255,255,0.7)');
    }}
  }}

  // ── Layer 6: Labels ──
  const showAll = NODES.length < 30;
  const topTen = [...NODES].sort((a, b) => b.thesisScore - a.thesisScore).slice(0, 10);
  const topTenIds = new Set(topTen.map(n => n.id));

  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';

  for (const n of NODES) {{
    const show = showAll || n === hoveredNode || topTenIds.has(n.id);
    if (!show) continue;
    const r = 12 + Math.sqrt(n.thesisScore) * 12;
    const fs = Math.max(9, 11) / camZ;
    ctx.font = '500 ' + fs + 'px "DM Sans"';
    ctx.fillStyle = n === hoveredNode ? 'rgba(241,243,245,0.95)' : 'rgba(241,243,245,0.65)';

    // Text shadow for readability
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.7)';
    ctx.shadowBlur = 3 / camZ;
    ctx.fillText(truncate(n.name, 22), n.posX, n.posY + r + 6 / camZ);
    ctx.restore();
  }}

  ctx.restore(); // camera transform
  ctx.restore(); // DPR transform
}}

function hexAlpha(a) {{
  return Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0');
}}

function truncate(s, max) {{
  return s && s.length > max ? s.slice(0, max - 1) + '\u2026' : (s || '');
}}

// ── Legend ─────────────────────────────────────────────────────────────────
(function buildLegend() {{
  const el = document.getElementById('legend');
  const statuses = ['source','initial_meeting','diligence','tracking','committed','funded','passed','lost'];
  for (const s of statuses) {{
    const chip = document.createElement('div');
    chip.className = 'legend-chip';
    chip.innerHTML = '<span class="legend-dot" style="background:' + STATUS_COLORS[s] + '"></span>' + (STATUS_LABELS[s] || s);
    chip.addEventListener('click', () => {{
      chip.classList.toggle('active');
      window.parent.postMessage({{ type: 'starwatcher:legend-click', status: s }}, '*');
    }});
    el.appendChild(chip);
  }}
}})();

// ── Start ─────────────────────────────────────────────────────────────────
requestAnimationFrame(animate);
</script>
</body>
</html>"""


# =============================================================================
# INSPECT PANEL (native Streamlit below canvas)
# =============================================================================


def _render_inspect_panel(node_data: Dict[str, Any]) -> None:
    """Render an inspect panel below the canvas for a selected node."""

    color = STATUS_COLOR_MAP.get(node_data.get("status", ""), "#9BA2AA")
    status_label = to_status_label(node_data.get("status", "source"))

    st.markdown(
        f"""
        <div style="
            background: #212529;
            border: 1px solid #495057;
            border-radius: 12px;
            padding: 20px 24px;
            margin-top: 8px;
            font-family: 'DM Sans', sans-serif;
            color: #F1F3F5;
        ">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
                <span style="
                    width:12px;height:12px;border-radius:50%;
                    background:{color};display:inline-block;
                "></span>
                <span style="
                    font-family:'Instrument Serif',serif;
                    font-size:22px;color:#F1F3F5;
                ">{node_data.get('name', 'Unknown')}</span>
                <span style="
                    padding:3px 10px;border-radius:5px;font-size:11px;
                    font-weight:600;letter-spacing:0.04em;text-transform:uppercase;
                    background:{color}22;color:{color};
                ">{status_label}</span>
            </div>
            <div style="
                font-family:'JetBrains Mono',monospace;font-size:13px;
                color:#2DB299;margin-bottom:8px;
            ">
                Thesis Score: {node_data.get('thesisScore', 0):.1%}
            </div>
            <div style="font-size:13px;color:#CED4DA;line-height:1.5;margin-bottom:10px;">
                {node_data.get('thesisRationale', '')}
            </div>
            <div style="font-size:11px;color:#868E96;">
                Sources: {', '.join(node_data.get('tags', []))}
                &nbsp;&middot;&nbsp;
                Signals: {len(node_data.get('signals', []))}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Notion link
    notion_url = node_data.get("notionUrl")
    if notion_url:
        st.markdown(
            f'<a href="{notion_url}" target="_blank" style="'
            f"color:#6BAEFF;font-size:12px;text-decoration:none;"
            f'margin-top:6px;display:inline-block;"'
            f">Open in Notion &rarr;</a>",
            unsafe_allow_html=True,
        )


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


def render_starwatcher_page() -> None:
    """Render the Starwatcher constellation visualization page."""

    # Page config (may already be set by app.py; guard against duplicate call)
    try:
        st.set_page_config(
            page_title="Starwatcher - Discovery Engine",
            page_icon="",
            layout="wide",
        )
    except st.errors.StreamlitAPIException:
        pass

    # ── Load data ──────────────────────────────────────────────────────────
    db_path = resolve_db_path_env()
    props = build_constellation_props(
        db_path=db_path,
        canvas_width=float(CANVAS_WIDTH),
        canvas_height=float(CANVAS_HEIGHT),
    )

    all_nodes = props.nodes
    all_edges = props.edges

    # ── Sidebar ────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(
            '<p style="font-family:\'Instrument Serif\',serif;font-size:24px;'
            'color:#F1F3F5;margin-bottom:4px;">Starwatcher</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="font-size:12px;color:#868E96;margin-bottom:20px;">'
            "Constellation Observatory</p>",
            unsafe_allow_html=True,
        )

        st.markdown("---")

        # Status filters
        st.markdown(
            '<p style="font-size:12px;font-weight:600;color:#CED4DA;'
            'letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px;">'
            "Pipeline Status</p>",
            unsafe_allow_html=True,
        )

        selected_statuses: List[str] = []
        for sid in ALL_STATUSES:
            label = to_status_label(sid)
            color = STATUS_COLOR_MAP.get(sid, "#9BA2AA")
            if st.checkbox(
                label,
                value=True,
                key=f"sw_status_{sid}",
                help=f"Show {label} companies",
            ):
                selected_statuses.append(sid)

        st.markdown("---")

        # Thesis score slider
        st.markdown(
            '<p style="font-size:12px;font-weight:600;color:#CED4DA;'
            'letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px;">'
            "Thesis Score Range</p>",
            unsafe_allow_html=True,
        )
        score_range = st.slider(
            "Score range",
            min_value=0.0,
            max_value=1.0,
            value=(0.0, 1.0),
            step=0.05,
            format="%.2f",
            key="sw_score_range",
            label_visibility="collapsed",
        )

        st.markdown("---")

        # Source multiselect
        available_sources = _collect_sources(all_nodes)
        st.markdown(
            '<p style="font-size:12px;font-weight:600;color:#CED4DA;'
            'letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px;">'
            "Signal Sources</p>",
            unsafe_allow_html=True,
        )
        selected_sources = st.multiselect(
            "Sources",
            options=available_sources,
            default=[],
            key="sw_sources",
            label_visibility="collapsed",
            placeholder="All sources",
        )

        st.markdown("---")

        # Stats summary
        st.markdown(
            '<p style="font-size:12px;font-weight:600;color:#CED4DA;'
            'letter-spacing:0.06em;text-transform:uppercase;margin-bottom:12px;">'
            "Observatory Stats</p>",
            unsafe_allow_html=True,
        )

        total_signals = sum(len(n.signals) for n in all_nodes)
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Companies", len(all_nodes))
            st.metric("Signals", total_signals)
        with col2:
            st.metric("Connections", len(all_edges))
            avg_score = (
                sum(n.thesisScore for n in all_nodes) / len(all_nodes)
                if all_nodes
                else 0
            )
            st.metric("Avg Score", f"{avg_score:.0%}")

    # ── Apply filters ──────────────────────────────────────────────────────
    if not selected_statuses:
        selected_statuses = ALL_STATUSES

    visible_nodes = _filter_nodes(
        all_nodes, selected_statuses, score_range, selected_sources
    )
    visible_ids = {n.id for n in visible_nodes}
    visible_edges = _filter_edges(all_edges, visible_ids)

    # ── Canvas ─────────────────────────────────────────────────────────────
    data_json = _serialize_for_js(visible_nodes, visible_edges)
    html_doc = _build_canvas_html(data_json)

    components.html(html_doc, height=HTML_HEIGHT, scrolling=False)

    # ── Empty state ────────────────────────────────────────────────────────
    if not visible_nodes:
        st.info(
            "No companies match the current filters. "
            "Adjust the sidebar controls to reveal nodes."
        )

    # ── Inspect panel (shown when a node is selected via session state) ───
    if "starwatcher_selected" in st.session_state:
        node_data = st.session_state["starwatcher_selected"]
        _render_inspect_panel(node_data)
        if st.button("Clear selection", key="sw_clear_select"):
            del st.session_state["starwatcher_selected"]
            st.rerun()
