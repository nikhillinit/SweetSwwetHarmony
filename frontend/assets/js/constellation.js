/**
 * Constellation Canvas 2D visualization.
 * Ported from dashboard/views/starwatcher.py + dashboard/adapters/starwatcher_adapter.py
 *
 * Two modes:
 *   - Ambient: star field + faint dots (dashboard/login background)
 *   - Full: interactive shapes, hover, pan/zoom (constellation view)
 */
import * as api from './api.js';
import { navigate } from './router.js';

// --- Status → visual mapping ---
const STATUS_COLORS = {
  source:          '#9BA2AA',
  initial_meeting: '#6BAEFF',
  diligence:       '#F7B84D',
  tracking:        '#B9A3FB',
  committed:       '#2DB299',
  funded:          '#34D399',
  passed:          '#F87171',
  lost:            '#FB923C',
};

const STATUS_SHAPES = {
  source:          'circle',
  initial_meeting: 'square',
  diligence:       'triangle',
  tracking:        'star',
  committed:       'diamond',
  funded:          'pentagon',
  passed:          'hexagon',
  lost:            'octagon',
};

// Inbox API status → Starwatcher ID
const INBOX_TO_STATUS = {
  inbox:              'source',
  tracking:           'tracking',
  passed:             'passed',
  pipeline_requested: 'diligence',
  funded:             'funded',
};

const SECTOR_ANGLES = {
  source: 0, initial_meeting: 45, diligence: 90, tracking: 135,
  committed: 180, funded: 225, passed: 270, lost: 315,
};
const SECTOR_WIDTH = 40;

// --- Deterministic hash for jitter ---
function deterministicHash(key) {
  // Simple string hash → [0, 1)
  let h = 0;
  for (let i = 0; i < key.length; i++) {
    h = ((h << 5) - h + key.charCodeAt(i)) | 0;
  }
  return Math.abs(h) / 2147483647;
}

// --- Polar layout ---
function computePolarPosition(statusId, thesisScore, canonicalKey, w, h) {
  const cx = w / 2;
  const cy = h / 2;
  const maxRadius = Math.min(cx, cy) * 0.85;

  const baseAngle = SECTOR_ANGLES[statusId] || 0;
  const jitter = deterministicHash(canonicalKey || 'unknown');
  const angleDeg = baseAngle + jitter * SECTOR_WIDTH;
  const angleRad = angleDeg * Math.PI / 180;

  const score = Math.max(0, Math.min(1, thesisScore || 0));
  const r = (1 - score) * maxRadius * 0.85 + maxRadius * 0.15;

  return {
    x: cx + r * Math.cos(angleRad),
    y: cy + r * Math.sin(angleRad),
  };
}

// --- Shape drawing ---
function drawShape(ctx, shape, x, y, size, color) {
  ctx.fillStyle = color;
  ctx.beginPath();

  switch (shape) {
    case 'circle':
      ctx.arc(x, y, size, 0, Math.PI * 2);
      break;
    case 'square':
      ctx.rect(x - size, y - size, size * 2, size * 2);
      break;
    case 'triangle':
      ctx.moveTo(x, y - size);
      ctx.lineTo(x + size, y + size * 0.7);
      ctx.lineTo(x - size, y + size * 0.7);
      ctx.closePath();
      break;
    case 'star':
      drawStar(ctx, x, y, 5, size, size * 0.5);
      break;
    case 'diamond':
      ctx.moveTo(x, y - size);
      ctx.lineTo(x + size * 0.7, y);
      ctx.lineTo(x, y + size);
      ctx.lineTo(x - size * 0.7, y);
      ctx.closePath();
      break;
    case 'pentagon':
      drawPolygon(ctx, x, y, size, 5);
      break;
    case 'hexagon':
      drawPolygon(ctx, x, y, size, 6);
      break;
    case 'octagon':
      drawPolygon(ctx, x, y, size, 8);
      break;
    default:
      ctx.arc(x, y, size, 0, Math.PI * 2);
  }
  ctx.fill();
}

function drawStar(ctx, cx, cy, spikes, outer, inner) {
  let rot = -Math.PI / 2;
  const step = Math.PI / spikes;
  ctx.moveTo(cx + Math.cos(rot) * outer, cy + Math.sin(rot) * outer);
  for (let i = 0; i < spikes; i++) {
    ctx.lineTo(cx + Math.cos(rot) * outer, cy + Math.sin(rot) * outer);
    rot += step;
    ctx.lineTo(cx + Math.cos(rot) * inner, cy + Math.sin(rot) * inner);
    rot += step;
  }
  ctx.closePath();
}

function drawPolygon(ctx, cx, cy, r, sides) {
  const angleStep = (Math.PI * 2) / sides;
  ctx.moveTo(cx + r * Math.cos(-Math.PI / 2), cy + r * Math.sin(-Math.PI / 2));
  for (let i = 1; i <= sides; i++) {
    const angle = angleStep * i - Math.PI / 2;
    ctx.lineTo(cx + r * Math.cos(angle), cy + r * Math.sin(angle));
  }
  ctx.closePath();
}

// --- Star field ---
function generateStars(count, w, h, seed = 42) {
  const stars = [];
  // Simple seeded random
  let s = seed;
  function rand() { s = (s * 16807 + 0) % 2147483647; return s / 2147483647; }

  for (let i = 0; i < count; i++) {
    stars.push({
      x: rand() * w,
      y: rand() * h,
      size: rand() * 1.5 + 0.5,
      brightness: rand() * 0.5 + 0.3,
      twinkleOffset: rand() * Math.PI * 2,
      twinkleSpeed: rand() * 0.5 + 0.3,
    });
  }
  return stars;
}

function drawStarField(ctx, stars, time, reducedMotion) {
  stars.forEach(star => {
    const alpha = reducedMotion
      ? star.brightness
      : star.brightness * (0.7 + 0.3 * Math.sin(time * star.twinkleSpeed + star.twinkleOffset));
    ctx.fillStyle = `rgba(241, 243, 245, ${alpha})`;
    ctx.beginPath();
    ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
    ctx.fill();
  });
}

// =============================================================================
// Ambient mode (background)
// =============================================================================
let ambientRaf = null;
let ambientCanvas = null;

export function initAmbient() {
  ambientCanvas = document.getElementById('ambient-bg');
  if (!ambientCanvas) return;

  const ctx = ambientCanvas.getContext('2d');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let stars = [];
  let mouseX = 0, mouseY = 0;

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    ambientCanvas.width = window.innerWidth * dpr;
    ambientCanvas.height = window.innerHeight * dpr;
    ambientCanvas.style.width = window.innerWidth + 'px';
    ambientCanvas.style.height = window.innerHeight + 'px';
    ctx.scale(dpr, dpr);
    stars = generateStars(200, window.innerWidth, window.innerHeight);
  }

  resize();
  window.addEventListener('resize', resize);
  window.addEventListener('mousemove', (e) => {
    mouseX = e.clientX;
    mouseY = e.clientY;
  });

  function frame(time) {
    const w = window.innerWidth;
    const h = window.innerHeight;

    ctx.clearRect(0, 0, ambientCanvas.width, ambientCanvas.height);

    // Background
    ctx.fillStyle = '#16191D';
    ctx.fillRect(0, 0, w, h);

    // Radial gradient center glow
    const gradient = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w * 0.6);
    gradient.addColorStop(0, 'rgba(45, 178, 153, 0.04)');
    gradient.addColorStop(1, 'transparent');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, w, h);

    // Parallax offset
    const parallaxX = (mouseX - w / 2) * 0.005;
    const parallaxY = (mouseY - h / 2) * 0.005;

    ctx.save();
    ctx.translate(parallaxX, parallaxY);
    drawStarField(ctx, stars, time / 1000, reducedMotion);
    ctx.restore();

    if (!reducedMotion) {
      ambientRaf = requestAnimationFrame(frame);
    }
  }

  if (reducedMotion) {
    // Render single static frame
    frame(0);
  } else {
    ambientRaf = requestAnimationFrame(frame);
  }
}

// =============================================================================
// Full constellation renderer
// =============================================================================
export class ConstellationRenderer {
  constructor(canvas, tooltipEl, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.tooltip = tooltipEl;
    this.mode = opts.mode || 'full';
    this.nodes = [];
    this.stars = [];
    this.running = false;
    this.raf = null;
    this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Pan/zoom state
    this.offsetX = 0;
    this.offsetY = 0;
    this.zoom = 1;
    this.dragging = false;
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.hoveredNode = null;

    // Click detection
    this._pointerDownPos = null;

    // Perf degradation tracking
    this._lastFrameTime = 0;
    this._slowFrameCount = 0;
    this._fastFrameCount = 0;

    // Pinch zoom state
    this.pointers = new Map();

    this._onResize = this._onResize.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onVisibility = this._onVisibility.bind(this);
  }

  async start() {
    this.running = true;
    this._onResize();
    window.addEventListener('resize', this._onResize);
    this.canvas.addEventListener('wheel', this._onWheel, { passive: false });
    this.canvas.addEventListener('pointerdown', this._onPointerDown);
    this.canvas.addEventListener('pointermove', this._onPointerMove);
    this.canvas.addEventListener('pointerup', this._onPointerUp);
    this.canvas.addEventListener('pointercancel', this._onPointerUp);
    document.addEventListener('visibilitychange', this._onVisibility);

    // Fetch data
    await this._fetchData();

    this.stars = generateStars(300, this.canvas.width, this.canvas.height);
    this._loop(0);
  }

  stop() {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    window.removeEventListener('resize', this._onResize);
    this.canvas.removeEventListener('wheel', this._onWheel);
    this.canvas.removeEventListener('pointerdown', this._onPointerDown);
    this.canvas.removeEventListener('pointermove', this._onPointerMove);
    this.canvas.removeEventListener('pointerup', this._onPointerUp);
    this.canvas.removeEventListener('pointercancel', this._onPointerUp);
    document.removeEventListener('visibilitychange', this._onVisibility);
  }

  async _fetchData() {
    try {
      const res = await api.get('/api/v1/companies/inbox?page=1&page_size=200');
      if (!res.ok) return;

      const companies = res.data?.items || res.data || [];
      const w = this.canvas.width / (window.devicePixelRatio || 1);
      const h = this.canvas.height / (window.devicePixelRatio || 1);

      this.nodes = companies.map(c => {
        const statusId = INBOX_TO_STATUS[c.status] || 'source';
        const pos = computePolarPosition(statusId, c.confidence_score || 0, c.canonical_key || c.company_name || '', w, h);
        return {
          x: pos.x,
          y: pos.y,
          name: c.company_name || c.name || c.canonical_key || 'Unknown',
          status: statusId,
          score: c.confidence_score || 0,
          canonicalKey: c.canonical_key || '',
          size: 12 + Math.sqrt(c.confidence_score || 0) * 12,
        };
      });
    } catch (err) {
      console.error('Constellation fetch error:', err);
    }
  }

  _onResize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.parentElement?.getBoundingClientRect() || { width: 800, height: 600 };
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.canvas.style.width = rect.width + 'px';
    this.canvas.style.height = rect.height + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.stars = generateStars(300, rect.width, rect.height);
  }

  _onWheel(e) {
    e.preventDefault();
    const zoomFactor = e.deltaY > 0 ? 0.92 : 1.08;
    this.zoom = Math.max(0.3, Math.min(5, this.zoom * zoomFactor));
  }

  _onPointerDown(e) {
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    this._pointerDownPos = { x: e.clientX, y: e.clientY };
    if (this.pointers.size === 1) {
      this.dragging = true;
      this.dragStartX = e.clientX - this.offsetX;
      this.dragStartY = e.clientY - this.offsetY;
      this.canvas.setPointerCapture(e.pointerId);
    }
  }

  _onPointerMove(e) {
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    // Pinch zoom
    if (this.pointers.size === 2) {
      const pts = [...this.pointers.values()];
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      if (this._lastPinchDist) {
        const delta = dist / this._lastPinchDist;
        this.zoom = Math.max(0.3, Math.min(5, this.zoom * delta));
      }
      this._lastPinchDist = dist;
      return;
    }

    if (this.dragging) {
      this.offsetX = e.clientX - this.dragStartX;
      this.offsetY = e.clientY - this.dragStartY;
    }

    // Hover detection
    this._updateHover(e);
  }

  _onPointerUp(e) {
    // Click detection: if pointer barely moved, treat as click
    if (this._pointerDownPos && this.pointers.size <= 1) {
      const dx = e.clientX - this._pointerDownPos.x;
      const dy = e.clientY - this._pointerDownPos.y;
      if (Math.hypot(dx, dy) < 5) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left - this.offsetX) / this.zoom;
        const my = (e.clientY - rect.top - this.offsetY) / this.zoom;
        for (const node of this.nodes) {
          if (Math.hypot(mx - node.x, my - node.y) < Math.max(node.size, 22)) {
            navigate('#/companies?highlight=' + encodeURIComponent(node.canonicalKey));
            break;
          }
        }
      }
    }
    this._pointerDownPos = null;
    this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this._lastPinchDist = null;
    if (this.pointers.size === 0) this.dragging = false;
  }

  _onVisibility() {
    if (document.hidden) {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = null;
    } else if (this.running) {
      this._loop(0);
    }
  }

  _updateHover(e) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - this.offsetX) / this.zoom;
    const my = (e.clientY - rect.top - this.offsetY) / this.zoom;

    let found = null;
    for (const node of this.nodes) {
      const dist = Math.hypot(mx - node.x, my - node.y);
      if (dist < Math.max(node.size, 22)) {
        found = node;
        break;
      }
    }

    if (found !== this.hoveredNode) {
      this.hoveredNode = found;
      if (found && this.tooltip) {
        this.tooltip.style.display = 'block';
        this.tooltip.style.left = (e.clientX - this.canvas.getBoundingClientRect().left + 16) + 'px';
        this.tooltip.style.top = (e.clientY - this.canvas.getBoundingClientRect().top - 10) + 'px';
        this.tooltip.innerHTML = '';
        const nameEl = document.createElement('div');
        nameEl.className = 'constellation-tooltip-name';
        nameEl.textContent = found.name;
        const scoreEl = document.createElement('div');
        scoreEl.className = 'constellation-tooltip-score';
        scoreEl.textContent = `Score: ${found.score.toFixed(2)}`;
        const statusEl = document.createElement('div');
        statusEl.style.cssText = 'font-size:var(--text-xs);color:var(--color-text-muted);margin-top:4px;text-transform:capitalize;';
        statusEl.textContent = found.status.replace('_', ' ');
        this.tooltip.appendChild(nameEl);
        this.tooltip.appendChild(scoreEl);
        this.tooltip.appendChild(statusEl);
      } else if (this.tooltip) {
        this.tooltip.style.display = 'none';
      }
    }
  }

  _loop(time) {
    if (!this.running || document.hidden) return;

    // Perf degradation tracking
    if (this._lastFrameTime > 0) {
      const delta = time - this._lastFrameTime;
      if (delta > 20) {
        this._slowFrameCount++;
        this._fastFrameCount = 0;
      } else {
        this._fastFrameCount++;
        this._slowFrameCount = 0;
      }
      const parent = this.canvas.parentElement;
      if (parent) {
        if (this._slowFrameCount >= 3) parent.classList.add('perf-reduced');
        else if (this._fastFrameCount >= 3) parent.classList.remove('perf-reduced');
      }
    }
    this._lastFrameTime = time;

    const w = this.canvas.width / (window.devicePixelRatio || 1);
    const h = this.canvas.height / (window.devicePixelRatio || 1);
    const ctx = this.ctx;

    // Clear
    ctx.clearRect(0, 0, w, h);

    // Layer 1: Background
    ctx.fillStyle = '#16191D';
    ctx.fillRect(0, 0, w, h);

    // Radial gradient
    const grad = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w * 0.5);
    grad.addColorStop(0, 'rgba(45, 178, 153, 0.06)');
    grad.addColorStop(1, 'transparent');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);

    // Vignette
    const vignette = ctx.createRadialGradient(w / 2, h / 2, w * 0.3, w / 2, h / 2, w * 0.7);
    vignette.addColorStop(0, 'transparent');
    vignette.addColorStop(1, 'rgba(0, 0, 0, 0.4)');
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, w, h);

    // Layer 2: Star field
    drawStarField(ctx, this.stars, time / 1000, this.reducedMotion);

    // Apply transform for pan/zoom
    ctx.save();
    ctx.translate(this.offsetX, this.offsetY);
    ctx.scale(this.zoom, this.zoom);

    // Layer 3: Sector guides
    this._drawSectorGuides(ctx, w, h);

    // Layer 4: Orbit rings
    this._drawOrbitRings(ctx, w, h);

    // Layer 5: Node glows (for high-thesis nodes)
    for (const node of this.nodes) {
      if (node.score >= 0.5) {
        const glowAlpha = node.score >= 0.75 ? 0.35 : 0.08;
        ctx.fillStyle = `rgba(45, 178, 153, ${glowAlpha})`;
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.size * 2, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Layer 6: Nodes
    for (const node of this.nodes) {
      const color = STATUS_COLORS[node.status] || '#9BA2AA';
      const shape = STATUS_SHAPES[node.status] || 'circle';

      // Hover highlight
      if (node === this.hoveredNode) {
        ctx.fillStyle = 'rgba(45, 178, 153, 0.2)';
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.size * 1.5, 0, Math.PI * 2);
        ctx.fill();
      }

      drawShape(ctx, shape, node.x, node.y, node.size, color);
    }

    // Layer 7: Labels (top 10 by score, or all if < 30)
    const labelNodes = this.nodes.length < 30
      ? this.nodes
      : [...this.nodes].sort((a, b) => b.score - a.score).slice(0, 10);

    ctx.font = '500 11px "DM Sans", sans-serif';
    ctx.textAlign = 'center';
    for (const node of labelNodes) {
      ctx.fillStyle = 'rgba(241, 243, 245, 0.8)';
      ctx.fillText(
        node.name.length > 16 ? node.name.slice(0, 14) + '...' : node.name,
        node.x,
        node.y + node.size + 14
      );
    }

    ctx.restore();

    this.raf = requestAnimationFrame((t) => this._loop(t));
  }

  _drawSectorGuides(ctx, w, h) {
    const cx = w / 2;
    const cy = h / 2;
    const maxR = Math.min(cx, cy) * 0.85;

    ctx.strokeStyle = 'rgba(73, 80, 87, 0.15)';
    ctx.lineWidth = 1;

    for (const [statusId, angle] of Object.entries(SECTOR_ANGLES)) {
      const rad = angle * Math.PI / 180;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + maxR * Math.cos(rad), cy + maxR * Math.sin(rad));
      ctx.stroke();

      // Sector label
      const labelR = maxR + 20;
      const lx = cx + labelR * Math.cos(rad);
      const ly = cy + labelR * Math.sin(rad);
      ctx.font = '600 9px "DM Sans", sans-serif';
      ctx.fillStyle = 'rgba(134, 142, 150, 0.5)';
      ctx.textAlign = 'center';
      ctx.fillText(statusId.replace('_', ' '), lx, ly);
    }
  }

  _drawOrbitRings(ctx, w, h) {
    const cx = w / 2;
    const cy = h / 2;
    const maxR = Math.min(cx, cy) * 0.85;

    ctx.strokeStyle = 'rgba(73, 80, 87, 0.08)';
    ctx.lineWidth = 1;

    for (let i = 1; i <= 4; i++) {
      ctx.beginPath();
      ctx.arc(cx, cy, maxR * (i / 4), 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}
