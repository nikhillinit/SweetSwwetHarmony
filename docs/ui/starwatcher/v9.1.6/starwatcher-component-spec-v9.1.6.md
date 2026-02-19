# Starwatcher Component Specification (v9.1.6)
_Revised: 2026-02-18_

This document is the **engineering handoff** for the Constellation Canvas React custom component embedded in Streamlit.

It must be read **together** with:
- `starwatcher-contract.ts` (props/events + integration rules)
- `starwatcher-status-map.ts` (status → shape/color + derived visuals)
- `starwatcher-tokens.json` (all visual values)

---

## What changed in v9.1.5 (perf + correctness)

1. **Cluster dominance correctness**
   - `getClusterVisuals()` now seeds dominance from actual cluster members (prevents “phantom source” clusters when all members are terminal statuses).

2. **Allocation-free Deck.gl color accessors**
   - Status-map helpers now support a reusable `target` array (`hexToRgbArray(hex, target)` / `hexToRgbaArray(hex, alpha01, target)`), matching Deck.gl’s “target array” performance pattern.
   - Use these in high-frequency accessors (`getFillColor`, `getLineColor`, etc.) to avoid GC spikes.

3. **Picking radius documentation fix**
   - `pickingRadius` is a **Deck/DeckGL** prop, not a per-layer prop.

4. **Optional camera snapshot on event payloads**
   - `ConstellationEventPayload` may include `cameraState` so the backend can stay in sync even if same-tick events are coalesced.

---

## What changed in v9.1.4 (patch polish)
1. **Safer deck.gl color helpers**
   - `hexToRgbArray()` now returns a fresh tuple on every call to prevent shared-cache mutation.
   - `withAlpha()` now expects **0–1** alpha only; use `withAlpha255()` if you have 0–255 values.

2. **Overlay stack de-risking**
   - Tokens renamed per-component `offset` to `_legacy_offset` for `stale_indicator` and `progressive_disclosure`.
   - Implementation SHOULD render both inside `tokens.overlay_stacks.top_right_pills` (no magic top offsets).

3. **Contract clarity**
   - Edge color encoding is now explicitly documented in the contract (not just implied by tokens).

4. **Version coherence guard**
   - At component init, compare `tokens._meta.version` to `STARWATCHER_VERSION` and log an error if they differ.

---

## What changed in v9.1.3 (cross-file drift + WebGL helpers)
These updates close the gap between “spec correctness” and “Day 1 implementation reality”:

1. **Deterministic same-tick event arbitration**
   - Contract now exports `EVENT_PRIORITY` (exhaustive over `ConstellationEvent['type']`).
   - Component uses it when multiple events occur in the same microtask tick.

2. **Deck.gl-ready color helpers**
   - Status-map exports `hexToRgbArray`, `withAlpha`, `hexToRgbaArray`, and `getStatusColorRgb` helpers.
   - Conversions are cached to avoid per-frame hex parsing.

3. **Overlay stacking primitives**
   - Tokens now include `overlay_stacks.top_right_pills` so stale indicator + progressive disclosure never overlap.
   - Component spec mandates a single top-right stack container (no magic “top: 52px”).

4. **Canonical version manifest**
   - Added `starwatcher-version.json` to declare the canonical current version + file set.

---

## What changed in v9.1.2 (final-mile fixes)
These are not “nice to haves” — they address real browser/Streamlit mechanics that can cause **dropped events**, **OAuth dead-ends**, or **washed-out rendering**:

1. **Streamlit single-event bridge rule**
   - Streamlit custom components effectively bridge **one value at a time**.
   - React MUST NOT emit two distinct events synchronously in the same tick (the earlier one may be dropped).
   - Filter legend toggles now emit **only** `apply_filters` with `provenance` (no separate `toggle_status` event).

2. **OAuth iframe escape**
   - Any login/OAuth flow must break out of the component iframe.
   - `EmptyState.action.target` supports `'_top'` and Connect CTAs should use it.

3. **Additive halo washout**
   - Any additive-blended halo/glow layer must render **under** solid node fill + labels, not above.

4. **Mobile long-press + OS callout prevention**
   - Long-press is reserved for **mobile selection mode**.
   - Disable iOS callouts and allow Deck.gl to capture gestures via required wrapper CSS.


5. **Event queue safety (no microtask overwrite)**
   - The reference `emitEvent()` implementation uses an array queue (not a single `pending` variable).
   - If multiple events are generated in the same tick, the queue selects a deterministic “winner” payload (state-changing events > `camera_idle`).

6. **IconLayer tinting correctness**
   - The SVG sprite atlas is treated as an **alpha mask** (`mask: true`) so node shapes can be tinted via `getColor`.
   - Atlas icons must be monochrome + transparent (no embedded colors).


---

## Recommended File Structure
```
src/
  ConstellationCanvas/
    index.ts
    ConstellationCanvas.tsx
    ConstellationCanvas.types.ts
    state/
      ConstellationContext.tsx
      useSelection.ts
      useHover.ts
      useKeyboardShortcuts.ts
      useCamera.ts
      useLod.ts
    layers/
      buildEdgeLayer.ts
      buildHaloLayer.ts
      buildNodeLayer.ts
      buildIconLayer.ts
      buildTextLayer.ts
      buildFocusRingLayer.ts
    ui/
      Toolbar.tsx
      Legend.tsx
      ProgressiveDisclosurePill.tsx
      InspectPanel.tsx
      SelectionTray.tsx
      ToastStack.tsx
      StaleIndicatorPill.tsx
      EmptyStateOverlay.tsx
      ErrorBanner.tsx
      FatalFallbackTable.tsx
      HelpOverlay.tsx
      MiniMap.tsx
      LoadingOverlays.tsx
    util/
      color.ts
      perf.ts
      webglRecovery.ts
      events.ts
```

---

## Critical CSS Isolation (required)
Put this on the canvas wrapper element (the parent of the DeckGL canvas):

```css
.constellation-wrapper {
  touch-action: none;           /* Hand gesture control to Deck.gl */
  -webkit-touch-callout: none;  /* Disable iOS “Save Image” / callout */
  user-select: none;
  -webkit-user-select: none;
  -webkit-tap-highlight-color: transparent;
}
```

Notes:
- This is essential for **long-press selection mode** on mobile.
- Without it, iOS Safari/Chrome can hijack the gesture before your handlers run.

---

## Rendering Architecture

### Persistent Canvas Rule
**DeckGL MUST stay mounted** across loading/empty/error UI.
All non-canvas states must render as **DOM overlays** (absolute positioned) above the canvas.

Do **not** unmount/remount DeckGL during:
- `initial_load`, `refreshing`, `transitioning`
- empty states
- recoverable errors

This prevents:
- WebGL context churn inside an iframe
- “white flash” during Streamlit reruns
- GPU reinitialization costs

### Deck.gl Layer Stack

Use the RGB helpers from `starwatcher-status-map.ts` in WebGL accessors:
- `getStatusColorRgb(statusId, theme)`
- `getHoverColorRgb(statusId, theme)`
- `getSelectedColorRgb(statusId, theme)`
- `withAlpha(rgb, alpha)` / `hexToRgbaArray(hex, alpha)`

These helpers cache hex→RGB conversions, avoiding per-frame parsing in Deck.gl.
Bottom → top:

1. **Edges**: `LineLayer` / `PathLayer`
2. **Halos / glows**: `ScatterplotLayer` (additive blend)
3. **Node fill**: `ScatterplotLayer` (solid)
4. **Shape icons**: `IconLayer` (sprite atlas)
5. **Labels**: `TextLayer` (collision-filtered; highest priority layer)

Why halos must be below:
- Additive blending increases brightness by summing pixel values.
- If halos render on top of icons/labels, high-score nodes become illegible “white blobs”.

### Focus Ring Layer (keyboard accessibility)
Canvas-rendered nodes cannot use CSS focus rings.
Add a `buildFocusRingLayer.ts` (simple scatter/rect ring) rendered **above node fill** and **below labels**:
- 2px ring using `tokens.focus.ring_color_*`
- 2px transparent gap between node and ring
- Visible only for the current keyboard-focused node

Focus restoration rule (keyboard users):
- If the focused node becomes invisible (filtered out, removed, or below progressive-disclosure threshold), restore focus in order:
  1) first selected node (if any)
  2) isolated node (if set)
  3) Canvas mode (no node focus)

---

## Label Collision Strategy (contract-aligned)
Use Deck.gl’s **CollisionFilterExtension** on `TextLayer`.

- Pass thesisScore to `getCollisionPriority`.
- Selected / hovered / isolated nodes must bypass collision (always labeled).
- Clamp total visible labels to `LABEL_CONFIG.MAX_VISIBLE`.

Performance note: pre-filter label candidates **before** GPU collision work.
- Build `labelCandidates` as:
  1) always-labeled nodes (selected / hovered / isolated)
  2) then remaining visible nodes sorted by `thesisScore` (desc)
- Cap candidates to a small multiple of `MAX_VISIBLE` (e.g., `MAX_VISIBLE * 4`, with a hard ceiling like 200) before passing into `TextLayer`.
  This preserves label quality while avoiding collision work on hundreds of low-priority labels.

Pragmatic fallback:
- If CollisionFilterExtension is unavailable (Deck.gl version mismatch), fall back to a deterministic heuristic.
- But the default implementation path must match the contract.

---

## LOD + Edge Style Enforcement
The status-map exposes `getEdgeStyle(strength)` for *Full* fidelity.
However the contract LOD matrix requires:

- **Reduced / Minimal LOD:** render **solid edges only** (no dash/dot)
- **Minimal + overview zoom:** no edges

Implementation detail:
- Prefer calling `getEdgeStyleForLod(strength, lodLevel)` from `starwatcher-status-map.ts`.
- If you must inline it: `style = lodLevel === 'full' ? getEdgeStyle(strength) : 'solid'`.

---

## Interaction + Picking

### Touch target padding (accessibility)
Visual node size can be smaller than the minimum tap target.
Use Deck.gl picking expansion instead of visual padding:

- Set `pickingRadius` on the **Deck/DeckGL canvas** to ensure a ~44px minimum hit area (it’s a Deck-level prop, not a layer prop).
- This increases hit area without increasing rendered size.

---

## Streamlit Event Emission Pattern (do not violate)
Streamlit’s component bridge is effectively **single-value**.

Rule:
- **One user action = one ConstellationEvent.**
- Never call `Streamlit.setComponentValue(...)` twice synchronously.

Recommended implementation (`events.ts`):
- Buffer events into a queue.
- Flush at most one payload per animation frame / microtask.

Example (conceptual):
```ts
import { EVENT_PRIORITY, type CameraState, type ConstellationEvent, type ConstellationEventPayload } from './starwatcher-contract';

let eventQueue: ConstellationEvent[] = [];
let flushScheduled = false;

function makePayload(
  event: ConstellationEvent,
  cameraState?: CameraState
): ConstellationEventPayload {
  return {
    eventId: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    event,
    cameraState,
  };
}

export function emitEvent(event: ConstellationEvent, cameraState?: CameraState) {
  eventQueue.push(event);

  // Only schedule one flush per tick.
  if (flushScheduled) return;
  flushScheduled = true;

  queueMicrotask(() => {
    flushScheduled = false;
    if (eventQueue.length === 0) return;

    // Streamlit bridge is single-value — choose ONE deterministic payload.
    // Higher priority events win; ties go to the most recent.
    const eventToSend = eventQueue.reduce((best, cur) => {
      const bestP = EVENT_PRIORITY[best.type];
      const curP = EVENT_PRIORITY[cur.type];
      if (curP > bestP) return cur;
      if (curP === bestP) return cur; // last wins on ties
      return best;
    });

    Streamlit.setComponentValue(makePayload(eventToSend, cameraState));
    eventQueue = [];
  });
}
```

Filter legend toggle:
- Compute the new `filters.applied` array in React.
- Emit **only**:
  - `apply_filters` with `{ filters, provenance: { action:'legend_toggle', status, active } }`

---

## Loading State UI (token-aligned)
Loading is visual, not structural; keep canvas mounted.

- `initial_load`:
  - Render placeholder nodes (count = `tokens.skeleton.initial_load.node_count`)
  - Shimmer unless `reducedMotion === true` (then static placeholders)

- `refreshing`:
  - Keep current nodes visible
  - Show subtle spinner in toolbar (do not block interactions)

- `transitioning`:
  - Crossfade duration = `tokens.skeleton.transitioning.crossfade_duration_ms`
  - Show top-edge indeterminate bar (`bar_height`)

---

## Filter Diff Preview (draft vs applied)
When `filters.draft` is non-null:

- Nodes that **would be filtered out**: opacity = `tokens.filter_diff.dimmed_opacity`
- Nodes that **would newly appear**: ghost outline opacity = `tokens.filter_diff.ghost_outline_opacity`
- Optional pulse on “newly visible” nodes using `tokens.filter_diff.preview_pulse_border_webgl` (canvas-safe; avoid CSS vars on WebGL)

Implementation approach:
- Maintain two derived sets: `appliedVisibleIds` and `draftVisibleIds`
- In node accessors, compute:
  - `dimmed = appliedVisible && !draftVisible`
  - `ghost = !appliedVisible && draftVisible`

---

## Top-Right Overlay Stack (no hardcoded offsets)
Two separate “pill” overlays live in the same corner and must never overlap:
- **Stale indicator** (data freshness)
- **Progressive disclosure** (hidden-by-thesis threshold)

Do **not** position these with magic numbers like “top: 52px”. Instead:

- Render **one** wrapper container anchored by `tokens.overlay_stacks.top_right_pills`.
- Stack pills vertically inside it (CSS `flex-direction: column` + `gap`).

Conceptual CSS (pseudo):
```css
.top-right-pill-stack {
  position: absolute;
  top:    tokens.overlay_stacks.top_right_pills.offset.top;
  right:  calc(tokens.overlay_stacks.top_right_pills.offset.right + var(--starwatcher-right-inset, 0px));
  /* If a right-docked InspectPanel is open, set --starwatcher-right-inset to its width. */
  z-index: tokens.overlay_stacks.top_right_pills.zIndex;

  display: flex;
  flex-direction: column;
  gap: tokens.overlay_stacks.top_right_pills.gap;
  max-width: tokens.overlay_stacks.top_right_pills.max_width;
}
```

Recommended render order:
1) `StaleIndicatorPill` (if `staleIndicator?.stale === true`)
2) `ProgressiveDisclosurePill` (if hiddenCount > 0)

---

## Progressive Disclosure Indicator
When thesis threshold hides nodes, show `ProgressiveDisclosurePill`:

Text:
- “Showing X of Y companies — Z hidden (zoom in or search)”

Optional actions:
- **Show all** (toggles the threshold off for this session)
- If total > 500, show a lightweight perf warning

Tokens:
- Uses `tokens.progressive_disclosure` (v9.1.1 addition) for styling.

---

## Empty State CTA + OAuth
When EmptyState provides `action.href` for OAuth / login:
- Use `target: '_top'` to break out of the iframe.
- If using `_blank`, require `rel: 'noopener noreferrer'`.

---

## Mini-Map (optional feature)
When `miniMap.enabled = true`:

Rendering:
- Simplified dots only (no icons, no labels)
- Viewport rectangle:
  - border uses `tokens.mini_map.viewport_rect.border_*`

Interaction:
- Click/tap recenters camera (programmatic camera movement)
- Double-click (or double-tap) optionally zooms one step in

Placement:
- z-index from `tokens.elevation.mini_map`

---

## Fatal Fallback Table (accessibility)
If `fatalError.fallback === 'table'`, render `FatalFallbackTable` (React-only).

Minimum accessibility:
- Keyboard navigable rows (roving tab index or native table controls)
- Sort controls are buttons with aria-labels (e.g., “Sort by thesis score descending”)
- Row activation:
  - Enter selects / navigates to node
  - Esc returns focus to canvas
- Keep search available (command palette still works)

---

## Keyboard Shortcuts (context gating)
Shortcuts must only be active when:
- focus is on canvas (not inside an input)
- no modal is open (palette/share/help)
- selection mode overlays aren’t capturing focus

Add `data-keyboard-shortcuts="enabled"` to the wrapper when active for QA.

---

## Typography loading
Prefer hosting fonts locally in the app bundle.
If using a remote font source, ensure `font-display: swap` and provide system fallbacks so labels never disappear.

---

## QA / Edge Cases
- WebGL context loss: simulate by forcing `webglcontextlost`; verify retry + table fallback
- OAuth CTA: verify opens in top-level browser context (not in iframe)
- Reduced motion: shimmer disabled, no pulsing glows, no camera interpolation
- Large dataset: verify solid-only edges at reduced/minimal LOD, stable FPS


---


## Python Backend Reference Implementation Notes (Streamlit)

These points are **contract-critical** for a smooth Day‑1 build and are easy to miss when porting the reference backend pattern.

### REQUIRED ConstellationProps must always be passed
Even when there is nothing to show, the backend MUST send these props (use `null` where applicable; do not omit keys):
- `loadingState`
- `error`
- `fatalError`
- `emptyState`

If `nodes.length === 0` and the backend does not provide an `emptyState`, the user will see a blank canvas instead of the intended CTA (“Connect Notion” / “No results”).

### Avoid rerun cascades on high-frequency events
To prevent UI stutter from repeatedly serializing large `nodes/edges` arrays:
- Handle `camera_idle` with **no Streamlit rerun** (store state silently).
- Handle `dismiss_toast` with **no Streamlit rerun** (React should dismiss optimistically; backend sync is best-effort).

### One-shot `initialCameraState`
`initialCameraState` is **mount-only**. The backend should pass it exactly once (e.g., `pop()` it from session_state) to avoid camera snap-backs when Streamlit remounts the iframe.

### Backend hardening (recommended)
- Cap undo stack depth (e.g., 50 entries) to avoid unbounded session_state growth.
- Prune old toasts server-side (TTL + max length) so invisible toasts don’t reappear on a later rerun.
- Prefer narrow exception handling for fragment reruns (`StreamlitAPIException`, `TypeError`) rather than a blanket `except Exception`.

## Canonical Files & Versioning
To prevent “parallel file set” drift:

- Treat the **unsuffixed** files as canonical import targets:
  - `starwatcher-contract.ts`
  - `starwatcher-status-map.ts`
  - `starwatcher-tokens.json`
  - `starwatcher-component-spec.md`
- Keep any `*-vX.Y.Z.*` files as **archived release artifacts only** (do not import them).
- `starwatcher-version.json` declares the current canonical version and revision date.

