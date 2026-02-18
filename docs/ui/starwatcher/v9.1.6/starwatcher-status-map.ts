/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║  STARWATCHER STATUS → SHAPE MAPPING — FINAL INTEGRATED (v9.1.6)    ║
 * ║  Version: 9.1.6                                                  ║
 * ║  Revised: 2026-02-18                                             ║
 * ║                                                                  ║
 * ║  This file ends the drift. Every status gets exactly one shape, ║
 * ║  one color, and one set of visual treatments.                    ║
 * ║                                                                  ║
 * ║  Companion files (must stay in sync):                           ║
 * ║    starwatcher-contract.ts — integration contract               ║
 * ║    starwatcher-tokens.json — design tokens                      ║
 * ╚══════════════════════════════════════════════════════════════════╝
 *
 * CHANGELOG v9:
 *   - Added PipelineStatusId for stable internal keys (decoupled from Notion labels)
 *   - Made STATUS_COLOR_MAP theme-aware (explicit light + dark values)
 *   - Added getStatusColor(status, theme) accessor — the only way to get a color
 *   - Changed cluster dominant logic to highest pipeline stage (VC priority)
 *   - Changed cluster radius to sqrt scaling (visual area ∝ count)
 *   - Added ARIA label map for screen reader accessibility
 *   - Added getHoverColor / getSelectedColor interaction functions
 *   - Removed duplicated MAX_COMPARE_NODES (import from contract)
 *   - Clarified isolation halo uses dynamic status color, not fixed teal
 *
 * CHANGELOG v9.1:
 *   - Moved STATUS_ID_MAP/STATUS_LABEL_MAP + PipelineStatusId to contract (single source of truth)
 *   - Switched STATUS_SHAPE_MAP / STATUS_COLOR_MAP / STATUS_BG_MAP to PipelineStatusId keys
 *   - NODE_VISUAL_STATES now uses typed function refs (no string indirection)
 *   - Isolated border now uses dynamic status variant (consistent with isolation halo)
 *
 * CHANGELOG v9.1.1:
 *   - Cluster dominance now uses DOMINANCE_WEIGHTS (success > failure)
 *   - Cluster radius scaling adjusted (sqrt multiplier tuned for 500+ clusters)
 *   - NODE_VISUAL_STATES.border normalized to getColor() for strict typing
 *   - Added getEdgeStyleForLod() to enforce solid edges at reduced/minimal LOD
 *

 * CHANGELOG v9.1.2:
 *   - Fixed missing DOMINANCE_WEIGHTS constant (compiler crash in v9.1.1)
 *   - Implemented getEdgeStyleForLod() helper referenced by component spec
 *   - Added IconLayer mask support in SHAPE_ICON_MAPPING for theme-tinted SVG atlas icons
 *   - Made hovered border theme-aware for contrast on bright fills in dark mode
 *
 * CHANGELOG v9.1.3:
 *   - Added hex→RGB helpers (hexToRgbArray/withAlpha) with caching for deck.gl
 *   - Added getStatusColorRgb/getHoverColorRgb/getSelectedColorRgb exports
 *
 * WHY SHAPES?
 * Color alone can't indicate status — ~8% of men are colorblind.
 * Each pipeline stage gets a unique shape so the constellation
 * is readable without color.
 *
 * SHAPE MEANINGS (designed to be intuitive):
 *   Circle    ● = Source         — a dot, the starting point
 *   Square    ■ = Meeting        — structured, formal, a meeting room
 *   Triangle  ▲ = Diligence      — pointing up, investigating, alert
 *   Star      ★ = Tracking       — a star you're watching (fits the metaphor)
 *   Diamond   ◆ = Committed      — a gem, something valuable and decided
 *   Pentagon  ⬠ = Funded         — like a badge/seal, official and complete
 *   Hexagon   ⬢ = Passed         — closed, finite
 *   Octagon   ⯃ = Lost           — like a stop sign, it's over
 
 *
 * v9.1.5:
 *   - Fix cluster dominance baseline (prevents “phantom source” clusters when all members are terminal failures)
 *   - Add target-aware color helpers to avoid per-object array allocations in Deck.gl accessors
*/

import type { PipelineStatus, PipelineStatusId, Theme } from './starwatcher-contract';
import { STATUS_ID_MAP, STATUS_LABEL_MAP, CLUSTER_CONFIG } from './starwatcher-contract';

// Convenience re-exports (avoid drift; keep older import paths working)
export type { PipelineStatusId };
export { STATUS_ID_MAP, STATUS_LABEL_MAP };

/** Convert a Notion status label to a stable ID. */
export function toStatusId(label: PipelineStatus): PipelineStatusId {
  return STATUS_ID_MAP[label];
}

/** Convert a stable ID back to the canonical label string. */
export function toStatusLabel(id: PipelineStatusId): PipelineStatus {
  return STATUS_LABEL_MAP[id];
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// PIPELINE STATUS ID (STABLE INTERNAL KEYS)
// Defined in the integration contract as PipelineStatusId.
// Backend maps Notion label → ID via STATUS_ID_MAP.
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// THE MAPPING — one status, one shape, no ambiguity
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export type NodeShape =
  | 'circle'
  | 'square'
  | 'triangle'
  | 'star'
  | 'diamond'
  | 'pentagon'
  | 'hexagon'
  | 'octagon';

export const STATUS_SHAPE_MAP: Record<PipelineStatusId, NodeShape> = {
  source:          'circle',
  initial_meeting: 'square',
  diligence:       'triangle',
  tracking:        'star',
  committed:       'diamond',
  funded:          'pentagon',
  passed:          'hexagon',
  lost:            'octagon',
} as const;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// STATUS → COLOR (THEME-AWARE)
// Must match color.status values in starwatcher-tokens.json
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Theme-aware status colors. No more ad-hoc desaturation rules —
 * dark mode values are explicitly defined and WCAG-verified.
 *
 * RULE: Never access this directly. Use getStatusColor() below.
 */
export const STATUS_COLOR_MAP: Record<Theme, Record<PipelineStatusId, string>> = {
  press_on_light: {
    source:          '#868E96',
    initial_meeting: '#4C9AFF',
    diligence:       '#F5A623',
    tracking:        '#A78BFA',
    committed:       '#0FA68A',
    funded:          '#10B981',
    passed:          '#EF4444',
    lost:            '#F97316',
  },
  cosmic_dark: {
    source:          '#9BA2AA',
    initial_meeting: '#6BAEFF',
    diligence:       '#F7B84D',
    tracking:        '#B9A3FB',
    committed:       '#2DB299',
    funded:          '#34D399',
    passed:          '#F87171',
    lost:            '#FB923C',
  },
} as const;

/** Light background for status chips and cards */
export const STATUS_BG_MAP: Record<Theme, Record<PipelineStatusId, string>> = {
  press_on_light: {
    source:          '#F1F3F5',
    initial_meeting: '#E8F2FF',
    diligence:       '#FFF5E0',
    tracking:        '#F3EEFF',
    committed:       '#E6FAF6',
    funded:          '#ECFDF5',
    passed:          '#FEF2F2',
    lost:            '#FFF7ED',
  },
  cosmic_dark: {
    source:          '#2D3035',
    initial_meeting: '#1A2E4A',
    diligence:       '#3D2E10',
    tracking:        '#2A2240',
    committed:       '#0D2E27',
    funded:          '#0D3024',
    passed:          '#3B1515',
    lost:            '#3B2510',
  },
} as const;

/**
 * The canonical accessor for status colors.
 * ALL rendering code must go through this function.
 */
export function getStatusColor(statusId: PipelineStatusId, theme: Theme): string {
  return STATUS_COLOR_MAP[theme][statusId];
}

/** Get the status chip/card background for the current theme. */
export function getStatusBg(statusId: PipelineStatusId, theme: Theme): string {
  return STATUS_BG_MAP[theme][statusId];
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// INTERACTION COLOR FUNCTIONS
// How node fill color changes on hover and selection
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Adjust a hex color's lightness for hover/selection visual feedback.
 * Lightens in dark mode, darkens in light mode.
 */
function adjustColorLightness(hex: string, amount: number): string {
  // Parse hex → RGB
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);

  // Simple lightness adjustment (clamp 0–255)
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v + amount)));
  const rr = clamp(r).toString(16).padStart(2, '0');
  const gg = clamp(g).toString(16).padStart(2, '0');
  const bb = clamp(b).toString(16).padStart(2, '0');

  return `#${rr}${gg}${bb}`;
}

/**
 * Returns the fill color for a hovered node.
 * In light mode: slightly darken (-20). In dark mode: slightly lighten (+20).
 */
export function getHoverColor(statusId: PipelineStatusId, theme: Theme): string {
  const base = getStatusColor(statusId, theme);
  return adjustColorLightness(base, theme === 'cosmic_dark' ? 20 : -20);
}

/**
 * Returns the fill color for a selected node.
 * In light mode: darken more (-35). In dark mode: lighten more (+35).
 */
export function getSelectedColor(statusId: PipelineStatusId, theme: Theme): string {
  const base = getStatusColor(statusId, theme);
  return adjustColorLightness(base, theme === 'cosmic_dark' ? 35 : -35);
}




// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// DECK.GL COLOR HELPERS
// Convert hex token colors to [r,g,b] / [r,g,b,a] arrays for WebGL.
// Caches conversions to avoid per-frame parsing in Deck.gl accessors.
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export type RgbColor = [number, number, number];
export type RgbaColor = [number, number, number, number];

/**
 * Write-targets for allocation-free Deck.gl accessors.
 * Deck.gl will pass a reusable `target` array into accessors; these types accept both
 * 3- and 4-component arrays (and typed arrays) as long as the indices exist.
 */
export type RgbWriteTarget = { 0: number; 1: number; 2: number };
export type RgbaWriteTarget = { 0: number; 1: number; 2: number; 3: number };

const HEX_RGB_CACHE = new Map<string, RgbColor>();

function normalizeHex(hex: string): string {
  let h = hex.trim();
  if (h.startsWith('0x')) h = '#' + h.slice(2);
  if (!h.startsWith('#')) h = '#' + h;

  // Expand shorthand form (#ABC → #AABBCC)
  if (h.length === 4) {
    h = '#' + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
  }

  return h.toUpperCase();
}

/**
 * Converts a hex color (#RRGGBB or #RGB) to a deck.gl RGB tuple.
 *
 * NOTE: This function returns a **fresh array** on every call (including cache hits).
 * This prevents subtle shared-cache corruption if any downstream code mutates a returned
 * color tuple (intentional or accidental) inside high-frequency render paths.
 */
export function hexToRgbArray(hex: string, target?: RgbWriteTarget): RgbColor {
  const key = normalizeHex(hex);
  const cached = HEX_RGB_CACHE.get(key);

  if (cached) {
    if (target) {
      target[0] = cached[0];
      target[1] = cached[1];
      target[2] = cached[2];
      return target as unknown as RgbColor;
    }
    // Return a fresh copy to prevent shared-cache mutation (Deck.gl may normalize in-place).
    return [cached[0], cached[1], cached[2]];
  }

  if (!/^#[0-9A-F]{6}$/.test(key)) {
    // Safe fallback to avoid runtime crashes inside render loops.
    const fallback: RgbColor = [128, 128, 128];
    HEX_RGB_CACHE.set(key, fallback);
    if (target) {
      target[0] = fallback[0];
      target[1] = fallback[1];
      target[2] = fallback[2];
      return target as unknown as RgbColor;
    }
    return [fallback[0], fallback[1], fallback[2]];
  }

  const r = parseInt(key.slice(1, 3), 16);
  const g = parseInt(key.slice(3, 5), 16);
  const b = parseInt(key.slice(5, 7), 16);

  const rgb: RgbColor = [r, g, b];
  HEX_RGB_CACHE.set(key, rgb);

  if (target) {
    target[0] = r;
    target[1] = g;
    target[2] = b;
    return target as unknown as RgbColor;
  }

  // Return a fresh copy to prevent shared-cache mutation.
  return [r, g, b];
}


/**
 * Adds an alpha channel to an RGB tuple.
 *
 * Alpha MUST be in the range 0–1.
 * If you already have 0–255 alpha values (rare in this codebase), use withAlpha255().
 */
export function withAlpha(
  rgb: RgbWriteTarget,
  alpha01: number,
  target?: RgbaWriteTarget
): RgbaColor {
  const a = Math.round(Math.max(0, Math.min(1, alpha01)) * 255);

  if (target) {
    target[0] = rgb[0];
    target[1] = rgb[1];
    target[2] = rgb[2];
    target[3] = a;
    return target as unknown as RgbaColor;
  }

  return [rgb[0], rgb[1], rgb[2], a];
}

/** Adds an alpha channel to an RGB tuple (alpha is 0–255). */
export function withAlpha255(rgb: RgbColor, alpha255: number): RgbaColor {
  const a = Math.round(alpha255);
  const clamped = Math.max(0, Math.min(255, a));
  return [rgb[0], rgb[1], rgb[2], clamped];
}


/** Converts a hex color to an RGBA tuple with the given alpha. */
export function hexToRgbaArray(
  hex: string,
  alpha01: number,
  target?: RgbaWriteTarget
): RgbaColor {
  // Allocation-free path for Deck.gl accessors: fill the provided target.
  if (target) {
    hexToRgbArray(hex, target);
    target[3] = Math.round(Math.max(0, Math.min(1, alpha01)) * 255);
    return target as unknown as RgbaColor;
  }

  const rgb = hexToRgbArray(hex);
  return [rgb[0], rgb[1], rgb[2], Math.round(Math.max(0, Math.min(1, alpha01)) * 255)];
}

/** deck.gl helpers for status colors. Prefer these in WebGL layers. */
export function getStatusColorRgb(
  statusId: PipelineStatusId,
  theme: Theme,
  target?: RgbWriteTarget
): RgbColor {
  return hexToRgbArray(getStatusColor(statusId, theme), target);
}

export function getHoverColorRgb(
  statusId: PipelineStatusId,
  theme: Theme,
  target?: RgbWriteTarget
): RgbColor {
  return hexToRgbArray(getHoverColor(statusId, theme), target);
}

export function getSelectedColorRgb(
  statusId: PipelineStatusId,
  theme: Theme,
  target?: RgbWriteTarget
): RgbColor {
  return hexToRgbArray(getSelectedColor(statusId, theme), target);
}

/** Convenience: status color as RGBA (0–1 alpha), with an allocation-free `target` path for Deck.gl. */
export function getStatusColorRgba(
  statusId: PipelineStatusId,
  theme: Theme,
  alpha01: number = 1,
  target?: RgbaWriteTarget
): RgbaColor {
  return hexToRgbaArray(getStatusColor(statusId, theme), alpha01, target);
}

export function getHoverColorRgba(
  statusId: PipelineStatusId,
  theme: Theme,
  alpha01: number = 1,
  target?: RgbaWriteTarget
): RgbaColor {
  return hexToRgbaArray(getHoverColor(statusId, theme), alpha01, target);
}

export function getSelectedColorRgba(
  statusId: PipelineStatusId,
  theme: Theme,
  alpha01: number = 1,
  target?: RgbaWriteTarget
): RgbaColor {
  return hexToRgbaArray(getSelectedColor(statusId, theme), alpha01, target);
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// PIPELINE ORDER
// Used for cluster dominant status (highest stage wins) and sorting
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * The canonical order of pipeline stages.
 * Lower index = earlier in the pipeline.
 * Used for:
 *   - Legend ordering
 *   - Data table default sort
 *   - Secondary tie-breakers (when weights/frequencies are equal)
 */
export const PIPELINE_ORDER: PipelineStatusId[] = [
  'source',
  'initial_meeting',
  'diligence',
  'tracking',
  'committed',
  'funded',
  'passed',
  'lost',
] as const;

/** Utility: get the ordinal position of a status */
export function getPipelineOrdinal(statusId: PipelineStatusId): number {
  return PIPELINE_ORDER.indexOf(statusId);
}

/**
 * Weights for cluster dominance (VC-first).
 *
 * Intent:
 * - Terminal successes should visually dominate everything else (Funded > Committed).
 * - Active flow should dominate cold flow (Diligence/Meeting > Tracking/Source).
 * - Terminal failures should never mask successes (Passed/Lost are lowest).
 *
 * NOTE: These weights are intentionally NOT the same as PIPELINE_ORDER.
 * PIPELINE_ORDER is chronological; DOMINANCE_WEIGHTS is about visual emphasis.
 */
export const DOMINANCE_WEIGHTS: Record<PipelineStatusId, number> = {
  funded:          80,
  committed:       70,
  diligence:       60,
  initial_meeting: 50,
  tracking:        40,
  source:          30,
  passed:          20,
  lost:            10,
} as const;



// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// EDGE STRENGTH → LINE STYLE
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export type EdgeStyle = 'solid' | 'dashed' | 'dotted';

/**
 * Determines the line style for a connection edge based on its strength.
 * Strong connections are solid and obvious.
 * Weak connections are dotted and subtle.
 */
export function getEdgeStyle(strength: number): EdgeStyle {
  if (strength > 0.75) return 'solid';
  if (strength >= 0.25) return 'dashed';
  return 'dotted';
}

export type LodLevel = 'full' | 'reduced' | 'minimal';

/**
 * LOD-aware edge styling.
 * Contract rule: Reduced/Minimal LOD must render SOLID edges only (no dash/dot),
 * both for performance and to reduce visual noise.
 */
export function getEdgeStyleForLod(strength: number, lodLevel: LodLevel): EdgeStyle {
  if (lodLevel !== 'full') return 'solid';
  return getEdgeStyle(strength);
}



// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// NODE VISUAL STATES
// How a node looks under different interaction conditions
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * VISUAL STATE TABLE
 *
 * A node can be in multiple states simultaneously (e.g., selected + hovered).
 * Effects stack. The table shows individual state effects.
 *
 *  ┌──────────────────────────────────────────────────────────────────────────┐
 *  │  State     │  Fill              │  Border    │  Glow              │  Op │
 *  ├──────────────────────────────────────────────────────────────────────────┤
 *  │  Default   │  status color      │  none      │  thesis-based*     │ 1.0 │
 *  │  Hovered   │  getHoverColor()   │  2px white │  hover_halo (20%)  │ 1.0 │
 *  │  Selected  │  getSelectedColor()│  2px accent│  glow (30%)        │ 1.0 │
 *  │  Isolated  │  status color      │  3px status (selected variant) │  status @ 50%**    │ 1.0 │
 *  │  Dimmed    │  status color      │  none      │  none              │0.15 │
 *  │  Cluster   │  dominant color    │  2px @ 40% │  none              │ 1.0 │
 *  └──────────────────────────────────────────────────────────────────────────┘
 *
 *  * thesis-based glow: if thesisScore >= 0.75 → glow_high (35%),
 *    else if >= 0.5 → glow_low (8%), else → none.
 *
 *  ** isolation_halo: 50% opacity glow in the node's OWN status color
 *     (computed via getStatusColor(node.status, theme)).
 *     Halo radius = 1.5× node radius. A Source (circle, gray) gets a
 *     gray halo; a Committed (diamond, teal) gets a teal halo.
 *     There is NO fixed teal — it is always dynamic.
 *
 *  DIMMED: Applied to all nodes that are NOT the isolated node or
 *  its direct connections during isolation mode.
 */
// Theme-aware primary accent for selection borders (must match tokens.color.primary)
export const PRIMARY_ACCENT: Record<Theme, string> = {
  press_on_light: '#0FA68A',
  cosmic_dark: '#2DB299',
} as const;

export const NODE_VISUAL_STATES = {
  hovered: {
    border: { width: 2, getColor: (_: PipelineStatusId, theme: Theme) => (theme === 'cosmic_dark' ? '#16191D' : '#FFFFFF') },
    halo: { opacity: 0.20, radiusMultiplier: 1.3 },
    getFill: (statusId: PipelineStatusId, theme: Theme) => getHoverColor(statusId, theme),
  },
  selected: {
    border: { width: 2, getColor: (_: PipelineStatusId, theme: Theme) => PRIMARY_ACCENT[theme] },
    glow: { opacity: 0.30, radiusMultiplier: 1.4 },
    getFill: (statusId: PipelineStatusId, theme: Theme) => getSelectedColor(statusId, theme),
  },
  isolated: {
    border: { width: 3, getColor: (statusId: PipelineStatusId, theme: Theme) => getSelectedColor(statusId, theme) },
    halo: { opacity: 0.50, radiusMultiplier: 1.5, getColor: (statusId: PipelineStatusId, theme: Theme) => getStatusColor(statusId, theme) },
  },
  dimmed: {
    opacity: 0.15,
  },
} as const;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// CLUSTER VISUAL DERIVATION
// How to render a cluster node from its member nodes
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Derives the visual properties of a cluster node.
 *
 * @param memberStatusIds - Array of PipelineStatusId values for all
 *                         nodes in the cluster.
 * @param memberCount    - Number of nodes in the cluster.
 * @param theme          - Current visual theme.
 *
 * @returns Visual properties for rendering the cluster.
 *
 * RULES (v9 changes noted):
 *   - Shape: Always circle (clusters don't get pipeline shapes).
 *   - Color: A dominant status derived from DOMINANCE_WEIGHTS.
 *     For VC deal-flow, terminal successes (Funded/Committed) should visually
 *     dominate terminal failures (Passed/Lost) so one dead deal does not mask
 *     a strong cluster. Frequency breakdown is shown in the hover tooltip,
 *     so no information is lost.
 *   - Size: Scaled by SQUARE ROOT of member count.
 *     v9 CHANGE: Previously linear (24 + count*2). Linear scaling makes
 *     visual area grow quadratically, distorting density perception.
 *     sqrt(count) makes visual area proportional to count.
 *     Formula: clamp(MIN, MIN + sqrt(memberCount) × MULT, MAX)
 *     where MULT is tuned so MAX is reached closer to ~400 members.
 *   - Label: "{memberCount} companies"
 *   - Border: 2px ring in dominant color at 40% opacity.
 */
export function getClusterVisuals(
  memberStatusIds: PipelineStatusId[],
  memberCount: number,
  theme: Theme
): {
  color: string;
  radius: number;
  label: string;
  borderColor: string;
  dominantStatus: PipelineStatusId;
} {
  // Find dominant status by VC-first dominance weights.
  // NOTE: This intentionally does NOT follow PIPELINE_ORDER so Passed/Lost
  // cannot mask Funded/Committed clusters.
  let dominant: PipelineStatusId = memberStatusIds[0] ?? 'source';
  for (const status of memberStatusIds) {
    if (DOMINANCE_WEIGHTS[status] > DOMINANCE_WEIGHTS[dominant]) dominant = status;
  }

  const color = getStatusColor(dominant, theme);

  // Square root scaling: visual area is proportional to member count.
  // Multiplier is tuned so MAX radius is reached closer to ~400 members.
  const base = CLUSTER_CONFIG.MIN_CLUSTER_RADIUS;
  const mult = CLUSTER_CONFIG.RADIUS_SQRT_MULTIPLIER;
  const radius = Math.min(
    CLUSTER_CONFIG.MAX_CLUSTER_RADIUS,
    Math.max(base, base + Math.sqrt(memberCount) * mult)
  );

  const label = `${memberCount} companies`;

  return {
    color,
    radius,
    label,
    borderColor: color, // rendered at 40% opacity by the component
    dominantStatus: dominant,
  };
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SVG SPRITE SHEET (ICON ATLAS)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * The SVG sprite sheet for Deck.gl's IconLayer.
 * Each shape is a 64×64 icon in a horizontal strip.
 * The icon atlas is a single image file.
 *
 * File: /public/assets/node-shapes-atlas.svg
 *
 * ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
 * │  ●   │  ■   │  ▲   │  ★   │  ◆   │  ⬠   │  ⬢   │  ⯃   │
 * │circle│square│ tri  │ star │ dia  │penta │ hex  │ oct  │
 * │ 0,0  │64,0  │128,0 │192,0 │256,0 │320,0 │384,0 │448,0 │
 * └──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
 *
 * Each cell is 64×64 pixels.
 * Anchor point is always center (32, 32).
 *
 * IMPORTANT: Deck.gl's IconLayer requires exact camelCase property names.
 * anchorX and anchorY must use capital X/Y — lowercase will default to 0
 * and break hover hitboxes.
 *
 * MASK TINTING (critical): To allow IconLayer to tint the sprite with getColor(),
 * each icon mapping MUST set `mask: true`, and the atlas should be monochrome
 * (solid shape + transparent background). Avoid embedding colored fills in the atlas.
 */
export const SHAPE_ICON_MAPPING: Record<
  NodeShape,
  { x: number; y: number; width: number; height: number; anchorX: number; anchorY: number; mask: boolean }
> = {
  circle:   { x: 0,   y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  square:   { x: 64,  y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  triangle: { x: 128, y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  star:     { x: 192, y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  diamond:  { x: 256, y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  pentagon: { x: 320, y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  hexagon:  { x: 384, y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
  octagon:  { x: 448, y: 0, width: 64, height: 64, anchorX: 32, anchorY: 32, mask: true },
};


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// ACCESSIBILITY — ARIA LABELS FOR SCREEN READERS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Shape-aware ARIA labels for screen readers.
 * Shapes encode status for colorblind users, but screen readers
 * can't announce visual shapes — this bridges the gap.
 *
 * IMPLEMENTATION: React component constructs the full aria-label as:
 *   "{name}, {STATUS_ARIA_LABELS[status]}, thesis score {score}"
 *   Example: "Acme Corp, Diligence stage triangle shape, thesis score 0.87"
 */
export const STATUS_ARIA_LABELS: Record<PipelineStatusId, string> = {
  source:          'Source stage, circle shape',
  initial_meeting: 'Initial Meeting / Call stage, square shape',
  diligence:       'Diligence stage, triangle shape',
  tracking:        'Tracking stage, star shape',
  committed:       'Committed stage, diamond shape',
  funded:          'Funded stage, pentagon shape',
  passed:          'Passed stage, hexagon shape',
  lost:            'Lost stage, octagon shape',
} as const;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// CONSTANTS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Nodes with thesisScore at or above this value are shown by default
 * (Stage 1 of progressive disclosure). Nodes below this threshold
 * are hidden until the user zooms in or searches.
 *
 * IMPORTANT: When progressive disclosure is active, the UI MUST show
 * a visibility indicator: "Showing X of Y companies — Z hidden (zoom in or search)".
 * See contract Section 16 for the full specification.
 */
export const DEFAULT_THESIS_VISIBILITY_THRESHOLD = 0.75;

// NOTE: MAX_COMPARE_NODES is defined ONCE in starwatcher-contract.ts.
// Import it from there. Do not duplicate.
// import { MAX_COMPARE_NODES } from './starwatcher-contract';