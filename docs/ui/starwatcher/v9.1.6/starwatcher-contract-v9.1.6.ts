/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║  STARWATCHER INTEGRATION CONTRACT — FINAL INTEGRATED (v9.1.6)      ║
 * ║  Version: 9.1.6                                                  ║
 * ║  Revised: 2026-02-18                                             ║
 * ║                                                                  ║
 * ║  This file is the single source of truth for all data flowing   ║
 * ║  between the Streamlit (Python) backend and the React           ║
 * ║  constellation-viewer frontend.                                  ║
 * ║                                                                  ║
 * ║  Companion files (must stay in sync):                           ║
 * ║    starwatcher-tokens.json   — every visual value                ║
 * ║    starwatcher-status-map.ts — status→shape→color mapping        ║
 * ║                                                                  ║
 * ║  RULES:                                                          ║
 * ║  • If it's not in this file, it's not in the contract.          ║
 * ║  • Changes here require sign-off from both frontend & backend.  ║
 * ║  • No other document may contradict this one.                    ║
 * ║                                                                  ║
 * ║  CHANGELOG v9:                                                   ║
 * ║  • Added Theme type (referenced by status-map)                   ║
 * ║  • Simplified selection events: set_selection + clear_selection   ║
 * ║  • Unified status filtering: removed activeStatuses from props   ║
 * ║  • EmptyState CTA supports href in addition to event             ║
 * ║  • Added open_settings event type                                ║
 * ║  • Toast duration is now optional (with enforced defaults)       ║
 * ║  • Split camera: initialCameraState + cameraState                ║
 * ║  • Added reducedMotion prop                                      ║
 * ║  • Added fatalError prop for React runtime errors                ║
 * ║  • Added ESC priority order, ?, LOD×Zoom matrix                  ║
 * ║  • Added mobile interaction specification                        ║
 * ║  • Added tooltip hover-intent and panning suppression            ║
 * ║  • Added progressive disclosure UI indicator                     ║
 * ║  • Label collision: use CollisionFilterExtension (GPU)            ║
 * ║  • Added command palette interaction detail                      ║
 * ║  • Added selection tray overflow behavior                        ║
 * ║                                                                  ║
 * ║  CHANGELOG v9.1:                                                 ║
 * ║  • CompanyNode.status now uses PipelineStatusId (stable IDs)      ║
 * ║  • Added STATUS_ID_MAP/STATUS_LABEL_MAP + backend mapping rule    ║
 * ║  • Expanded FatalError with WebGL recovery config + table fallback║
 * ║  • camera_idle now includes source + trigger for provenance       ║
 * ║  • EmptyState.action supports target/rel for safe OAuth nav        ║
 * ║  • Added compare_selected event (selection tray CTA)              ║
 * ║  • Added staleIndicator, keyboardShortcuts, and miniMap props     ║
 * ║                                                                  ║
 * ║  CHANGELOG v9.1.1:                                               ║
 * ║  • Removed toggle_status event (Streamlit single-event bridge)   ║
 * ║  • apply_filters now includes optional provenance metadata        ║
 * ║  • EmptyState.action.target supports '_top' for OAuth escape      ║
 * ║  • Clarified tags filter operators (contains_any/all/excludes)    ║
 * ║  • Removed mobile long-press context menu trigger                 ║
 * ║  • Toast defaults: warning=6000ms, error=persistent (0ms)         ║
 * ║                                                                  ║
 * ║  CHANGELOG v9.1.2:                                               ║
 * ║  • EmptyState.action is now a mutually exclusive union (event XOR href)║
 * ║  • Documented cluster radius sqrt multiplier derivation           ║
 * ║  • Minor clarity fixes for engineering handoff                    ║
 * ║                                                                  ║
 * ║  CHANGELOG v9.1.3:                                               ║
 * ║  • Added STARWATCHER_VERSION constant (single source of truth)    ║
 * ║  • Added EVENT_PRIORITY map for same-tick Streamlit arbitration   ║
 * ║  • Clarified CollisionFilterExtension fallback (compat mode)     ║
 * ║                                                                  ║
 * ║  CHANGELOG v9.1.4:                                               ║
 * ║  • Clarified edge colors in contract (no more token-only truth)     ║
 * ║  • Changelog wording fix: “discriminated” → “mutually exclusive”    ║
 * ║                                                                  ║
* ║  CHANGELOG v9.1.5:                                               ║
* ║  • ConstellationEventPayload now supports optional cameraState     ║
* ║  • Perf: status-map adds allocation-free color helpers for Deck.gl ║
* ║                                                                  ║
 *  * ║                                                                  ║
 * ║  CHANGELOG v9.1.6:                                               ║
 * ║  • Added Streamlit backend reference implementation (v9.1.6)     ║
 * ║    - Always send required props (loadingState/error/fatalError/  ║
 * ║      emptyState), even when null                                ║
 * ║    - No-rerun handling for camera_idle + dismiss_toast          ║
 * ║    - One-shot initialCameraState (pop) to prevent snap-backs    ║
 * ║    - Undo stack cap + toast pruning recommendations              ║
 * ║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
 */

export const STARWATCHER_VERSION = '9.1.6' as const;



// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 1: PIPELINE STATUSES & THEME
// Notion label strings must match exactly for mapping (PipelineStatus).
// React receives PipelineStatusId (stable IDs) only.
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Every company in the pipeline is in one of these stages.
 *
 *  Source              → We found them, haven't talked yet
 *  Initial Meeting     → First call or meeting happened
 *  Diligence           → Actively evaluating (deep research)
 *  Tracking            → Interesting but not ready to pursue now
 *  Committed           → We've decided to invest, deal in progress
 *  Funded              → Money wired, deal closed
 *  Passed              → We evaluated and said no
 *  Lost                → We wanted to invest but lost the deal
 *
 * NOTE: Notion uses human-readable label strings (PipelineStatus).
 * The Starwatcher system uses stable internal IDs (PipelineStatusId)
 * for rendering, token lookup, filtering, and persistence.
 *
 * BACKEND RULE (v9.1): Backend MUST convert Notion labels → PipelineStatusId
 * via STATUS_ID_MAP before sending nodes to React.
 */
export type PipelineStatus =
  | 'Source'
  | 'Initial Meeting / Call'
  | 'Diligence'
  | 'Tracking'
  | 'Committed'
  | 'Funded'
  | 'Passed'
  | 'Lost';


/**
 * Stable internal identifiers for pipeline stages.
 *
 * These IDs are used everywhere in code and in the React component.
 * They are decoupled from Notion label strings so a label rename
 * doesn't break rendering or filter persistence.
 */
export type PipelineStatusId =
  | 'source'
  | 'initial_meeting'
  | 'diligence'
  | 'tracking'
  | 'committed'
  | 'funded'
  | 'passed'
  | 'lost';

/** Map Notion label → stable ID. Used at the data ingestion boundary. */
export const STATUS_ID_MAP: Record<PipelineStatus, PipelineStatusId> = {
  'Source':                 'source',
  'Initial Meeting / Call': 'initial_meeting',
  'Diligence':              'diligence',
  'Tracking':               'tracking',
  'Committed':              'committed',
  'Funded':                 'funded',
  'Passed':                 'passed',
  'Lost':                   'lost',
} as const;

/** Map stable ID → Notion label. Used for display rendering when needed. */
export const STATUS_LABEL_MAP: Record<PipelineStatusId, PipelineStatus> = {
  source:          'Source',
  initial_meeting: 'Initial Meeting / Call',
  diligence:       'Diligence',
  tracking:        'Tracking',
  committed:       'Committed',
  funded:          'Funded',
  passed:          'Passed',
  lost:            'Lost',
} as const;

/** The two supported visual themes. Used by status-map for theme-aware colors. */
export type Theme = 'press_on_light' | 'cosmic_dark';


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 2: DATA STRUCTURES
// What a "company" and a "connection" look like in the system
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * A single company node in the constellation.
 *
 * Think of this as one dot on the map — it represents a company
 * your fund is tracking, with its current stage, position on
 * screen, and how well it matches your investment thesis.
 */
export interface CompanyNode {
  /** Unique identifier — must match Notion's page ID */
  id: string;

  /** Company name as displayed to the user */
  name: string;

  /** Horizontal position on the constellation canvas (pixels) */
  posX: number;

  /** Vertical position on the constellation canvas (pixels) */
  posY: number;

  /**
   * How well this company matches the fund's investment thesis.
   * 0.0 = no match, 1.0 = perfect match.
   * Used to control visual prominence (size, glow, visibility).
   */
  thesisScore: number;

  /** Current stage in the deal pipeline (stable ID) */
  status: PipelineStatusId;

  /**
   * Short explanation of why the thesis score is what it is.
   * Displayed in the Inspect Panel under "Why it matches."
   * Generated by the backend's LLM analysis.
   * Example: "Strong AI/ML team, Series A timing, B2B SaaS focus"
   */
  thesisRationale: string;

  /**
   * Key signals / recent activity for this company.
   * Displayed in the Inspect Panel under "Signals."
   * Each entry is a short human-readable note with a timestamp.
   */
  signals: SignalEntry[];

  /**
   * URL to the company's Notion page for "Open in Notion" actions.
   * Optional — if missing, the action button is hidden.
   */
  notionUrl?: string;

  /**
   * Sector/vertical tags for filtering.
   * Examples: ["AI/ML", "B2B SaaS", "HealthTech"]
   */
  tags?: string[];
}

/**
 * A single signal / activity note attached to a company.
 * These appear in the Inspect Panel's "Signals" tab.
 */
export interface SignalEntry {
  /** ISO 8601 timestamp — when this signal was captured */
  timestamp: string;

  /** Short human-readable description */
  text: string;

  /** Where this signal came from */
  source: 'notion' | 'email' | 'llm_extraction' | 'manual';
}

/**
 * A connection (edge) between two companies.
 *
 * This represents a relationship — shared investors, same sector,
 * co-mentioned in research, etc. The visualization draws a line
 * between the two nodes.
 */
export interface Connection {
  /** ID of the first company (must exist in the nodes array) */
  source: string;

  /** ID of the second company (must exist in the nodes array) */
  target: string;

  /**
   * How strong the connection is.
   * 0.0 = barely related, 1.0 = strongly linked.
   * Controls the line style: solid (>0.75), dashed (0.25–0.75), dotted (<0.25).
   */
  strength: number;

  /**
   * Why these two companies are connected.
   * Optional — shown on hover/inspect.
   * Example: "Shared lead investor (a16z), both Series A"
   */
  reason?: string;
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 3: CAMERA STATE & ZOOM LEVELS
// Where the user is "looking" on the constellation, and how the
// component adapts its rendering at different zoom levels
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * The camera's current position and zoom level.
 * Used for permalinks ("Share View") and restoring saved views.
 */
export interface CameraState {
  /** Horizontal position the camera is centered on */
  x: number;

  /** Vertical position the camera is centered on */
  y: number;

  /** Zoom level — 1.0 is default, 2.0 is zoomed in 2x, 0.5 is zoomed out */
  zoom: number;
}

/**
 * ZOOM LEVEL SEMANTICS
 *
 * The component adapts its rendering based on the current zoom level.
 * These thresholds are derived from the camera's zoom value.
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │  Zoom Range     │  Semantic Name  │  What's visible              │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  < 0.5          │  overview       │  Clusters only. No           │
 *  │                 │                 │  individual labels.          │
 *  │                 │                 │  Edges hidden.               │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  0.5 – 1.5      │  exploration    │  Individual nodes with       │
 *  │                 │                 │  shapes. Labels on           │
 *  │                 │                 │  hover + selected.           │
 *  │                 │                 │  Strong edges visible.       │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  > 1.5          │  inspection     │  All labels visible          │
 *  │                 │                 │  (collision-detected).       │
 *  │                 │                 │  All edges visible.          │
 *  │                 │                 │  Full node detail.           │
 *  └──────────────────────────────────────────────────────────────────┘
 *
 * MOBILE MULTI-SELECT PATTERN (v9.1)
 * - Tap: Select a single node (replaces selection).
 * - Long-press (500ms): Enter "selection mode".
 *     Visual indicator: canvas border highlights + a floating pill shows
 *     "{count} selected" + [Done].
 * - In selection mode: subsequent taps toggle nodes in/out of selection.
 * - Exit selection mode: tap [Done], press the OS back button, or tap outside.
 *
 * CONTEXT ACTIONS ON MOBILE:
 * - Avoid relying on right-click context menus.
 * - Expose actions (Isolate, Add to Compare, Share) in the inspect bottom sheet
 *   and/or the selection pill overflow menu.

 *
 *  These thresholds are NOT in the props — they are client-side
 *  rendering decisions. They are documented here so both frontend
 *  and backend understand what the user sees at each zoom level.
 */
export const ZOOM_LEVELS = {
  /** Below this zoom, switch to cluster view */
  OVERVIEW_THRESHOLD: 0.5,
  /** Above this zoom, show all labels and edges */
  INSPECTION_THRESHOLD: 1.5,
} as const;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 4: LEVEL OF DETAIL (LOD) & CLUSTERING
// How the component handles datasets of varying sizes
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * LOD SYSTEM
 *
 * The component automatically reduces visual complexity based on
 * how many nodes are on screen. This prevents WebGL performance
 * degradation on large datasets.
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │  Node Count    │  LOD Level  │  Rendering Changes               │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  1 – 200       │  full       │  All features enabled.           │
 *  │                │             │  Shapes, labels, glows,          │
 *  │                │             │  halos, edge patterns.           │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  201 – 500     │  reduced    │  Labels only on hover +          │
 *  │                │             │  selected. Glows disabled.       │
 *  │                │             │  Edge patterns simplified        │
 *  │                │             │  to solid-only.                  │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  501+          │  minimal    │  Clustering enabled (see         │
 *  │                │             │  below). Individual nodes        │
 *  │                │             │  only shown when zoomed in.      │
 *  │                │             │  No edge rendering at            │
 *  │                │             │  overview zoom level.            │
 *  └──────────────────────────────────────────────────────────────────┘
 *
 *  LOD is a client-side decision — it is NOT part of the props.
 *  The backend sends all nodes; the client decides how to render.
 */
export const LOD_THRESHOLDS = {
  /** Above this count, switch to "reduced" rendering */
  REDUCED: 200,
  /** Above this count, enable clustering */
  MINIMAL: 500,
} as const;

/**
 * LOD × ZOOM FEATURE MATRIX (v9 addition)
 *
 * Defines exactly what is rendered for each combination of LOD level
 * and zoom semantic. This eliminates interpretation drift during
 * implementation.
 *
 *  ┌──────────────┬─────────────────────┬─────────────────────┬─────────────────────┐
 *  │              │  Full (1–200)        │  Reduced (201–500)  │  Minimal (501+)     │
 *  ├──────────────┼─────────────────────┼─────────────────────┼─────────────────────┤
 *  │  Overview    │  Nodes w/ shapes    │  Nodes w/ shapes    │  Clusters only      │
 *  │  (< 0.5)    │  No labels          │  No labels          │  Cluster labels     │
 *  │              │  No edges           │  No edges           │  No edges           │
 *  │              │  Thesis glows ON    │  No glows           │  No glows           │
 *  ├──────────────┼─────────────────────┼─────────────────────┼─────────────────────┤
 *  │  Exploration │  Shapes + hover/    │  Shapes + hover/    │  Clusters dissolve  │
 *  │  (0.5–1.5)  │  selected labels    │  selected labels    │  into nodes         │
 *  │              │  Strong edges       │  Solid edges only   │  Hover labels only  │
 *  │              │  (all patterns)     │  (no dash/dot)      │  No edges           │
 *  │              │  Thesis glows ON    │  No glows           │  No glows           │
 *  ├──────────────┼─────────────────────┼─────────────────────┼─────────────────────┤
 *  │  Inspection  │  All labels         │  All labels         │  Individual nodes   │
 *  │  (> 1.5)    │  (collision-detect)  │  (collision-detect)  │  Collision labels   │
 *  │              │  All edges          │  Solid edges only   │  Strong edges only  │
 *  │              │  Full glows + halos │  No glows           │  No glows           │
 *  └──────────────┴─────────────────────┴─────────────────────┴─────────────────────┘
 *
 *  KEY RULES:
 *  - Selected/hovered/isolated nodes ALWAYS show labels, regardless of LOD.
 *  - Glows are only enabled at Full LOD (too expensive for 200+ nodes).
 *  - Edge dash/dot patterns are only at Full LOD; reduced/minimal use solid.
 *  - Edge color uses the theme primary accent (teal) with opacity scaled by strength:
 *      strong: 0.45, medium: 0.22, weak: 0.12 (see tokens.color.dataviz.edge_*).
 */

/**
 * CLUSTERING SPECIFICATION
 *
 * When node count exceeds LOD_THRESHOLDS.MINIMAL (500), the
 * component groups nearby nodes into clusters at overview zoom.
 *
 * ALGORITHM: Grid-based spatial clustering.
 *   1. Divide the canvas into a grid (cell size = 120px at zoom 1.0).
 *   2. All nodes whose (posX, posY) falls in the same cell become
 *      a single cluster node.
 *   3. The cluster's position is the centroid of its members.
 *   4. Grid cell size scales inversely with zoom: as the user zooms
 *      in, cells get smaller and clusters break apart into
 *      individual nodes.
 *
 * WHY GRID-BASED (not force-directed or DBSCAN):
 *   - Deterministic: same data always produces same clusters.
 *   - Fast: O(n) — no iterative convergence needed.
 *   - Zoom-responsive: cell size changes with zoom level naturally.
 *   - Simple to implement in Deck.gl's data pipeline.
 *
 * VISUAL REPRESENTATION:
 *   - Shape: Circle (regardless of member statuses).
 *   - Size: Square root scaling (visual area ∝ member count).
 *     Formula: min(64, 24 + sqrt(memberCount) × 2) pixels radius.
 *     (Reaches max radius closer to ~400 nodes for better density perception.)
 *   - Color: A *dominant* status derived by VC-first dominance weights.
 *     Funded/Committed should visually dominate Passed/Lost so a single
 *     dead deal does not mask a successful cluster. Frequency breakdown
 *     is still shown in the hover tooltip.
 *   - Label: Shows member count. Example: "12 companies"
 *   - Border: 2px ring in the dominant status color at 40% opacity.
 *
 * INTERACTION:
 *   - Hover: Tooltip shows status breakdown.
 *     Example: "12 companies: 5 Source, 4 Diligence, 3 Tracking"
 *   - Click: Zooms the camera to fit all cluster members on screen
 *     (animated, 800ms, interruptible). Does NOT select any nodes.
 *   - The cluster dissolves into individual nodes as the zoom crosses
 *     the threshold where the grid cell is large enough to contain
 *     only 1 node per cell.
 *
 * EDGES IN CLUSTERED VIEW:
 *   - Edges between nodes in the same cluster are hidden.
 *   - Edges between nodes in different clusters are aggregated
 *     into a single cluster-to-cluster edge.
 *   - Aggregated edge strength = max(member edge strengths).
 *   - Aggregated edge width scales with the count of member edges:
 *     1 edge = 1px, 5+ edges = 3px (clamped).
 */
export const CLUSTER_CONFIG = {
  /** Base grid cell size in pixels at zoom 1.0 */
  GRID_CELL_SIZE: 120,
  /** Minimum cluster node radius in pixels */
  MIN_CLUSTER_RADIUS: 24,
  /** Maximum cluster node radius in pixels */
  MAX_CLUSTER_RADIUS: 64,
  /**
   * sqrt(memberCount) multiplier for cluster radius scaling.
   * With MIN_CLUSTER_RADIUS = 24 and MAX_CLUSTER_RADIUS = 64,
   * a multiplier of 2 reaches the max radius at sqrt(400)*2 = 40,
   * i.e., 24 + 40 = 64. This makes clusters with ~400 members
   * appear at full size, improving density perception.
   */
  RADIUS_SQRT_MULTIPLIER: 2,
  /** Cluster zoom animation duration in ms */
  ZOOM_DURATION: 800,
} as const;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 5: LOADING, ERROR, & EMPTY STATES
// How the app communicates what's happening to the user
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Granular loading states so the UI can show the right indicator.
 *
 *  idle            → Nothing is loading, all good
 *  initial_load    → First time opening — show skeleton placeholder
 *  refreshing      → Data is updating in background — keep old data, show spinner
 *  transitioning   → Switching between filtered views — crossfade animation
 */
export type LoadingState = 'idle' | 'initial_load' | 'refreshing' | 'transitioning';

/**
 * When something goes wrong, this tells the UI what to show.
 */
export interface ErrorInfo {
  /** Short title for the error banner — e.g., "Connection Lost" */
  title: string;

  /** Longer explanation — e.g., "We couldn't reach Notion. Your data may be stale." */
  message: string;

  /** Can the user do something about it? */
  recoverable: boolean;

  /** Label for the retry button — e.g., "Try Again", "Reconnect" */
  retryLabel?: string;
}

/**
 * EMPTY STATE MATRIX
 *
 * When there's nothing to show, the component renders one of these
 * states instead of a blank canvas. The backend determines which
 * empty state to show by setting `emptyState` in props.
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │  Type          │  When it appears        │  What's shown        │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  initial       │  First-ever load,       │  Welcome message     │
 *  │                │  no data connected yet  │  + "Connect          │
 *  │                │                         │  Notion" CTA         │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  no_results    │  Active filters match   │  "No companies       │
 *  │                │  zero companies         │  match" + "Clear     │
 *  │                │                         │  Filters" CTA        │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  search_empty  │  Command palette search │  "No results for     │
 *  │                │  found nothing          │  '[query]'" +        │
 *  │                │                         │  suggestions         │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  error         │  Data load failed       │  Error message       │
 *  │                │  completely             │  + retry CTA         │
 *  └──────────────────────────────────────────────────────────────────┘
 */
export interface EmptyState {
  type: 'initial' | 'no_results' | 'search_empty' | 'error';

  /** Primary message — e.g., "No companies match your filters" */
  title: string;

  /** Secondary explanation */
  message: string;

  /**
   * Optional CTA button.
   *
   * Use `event` for in-app actions (clear filters, retry).
   * Use `href` for external navigation (Connect Notion settings page / OAuth).
   *
   * IMPORTANT: `event` and `href` are mutually exclusive at the type level.
   * This prevents accidental OAuth failures where an engineer sets both and
   * the in-app event silently “wins” over navigation.
   */
  action?:
    | {
        label: string;
        /** Which event to emit when the user clicks the CTA */
        event: ConstellationEvent;
      }
    | {
        label: string;
        /** External URL to open (e.g., Notion OAuth page, settings) */
        href: string;
        /**
         * Where to open the href. Default: '_self'
         * IMPORTANT (Streamlit iframe): OAuth/login flows SHOULD use target='_top'
         * so navigation breaks out of the component iframe.
         * If target is '_blank', rel MUST be 'noopener noreferrer'.
         */
        target?: '_blank' | '_self' | '_top';
        /** Security requirement for target='_blank' */
        rel?: 'noopener noreferrer';
      };
}

/**
 * Fatal error state for React runtime failures (WebGL context loss,
 * component crash). Separate from ErrorInfo which handles data errors.
 */
export interface FatalError {
  /** Short title — e.g., "Visualization Error" */
  title: string;
  /** Explanation — e.g., "WebGL context was lost" */
  message: string;

  /**
   * Fallback strategy
   * - 'retry': React attempts WebGL restoration (context recovery).
   * - 'table': React renders an inline HTML table fallback (no Streamlit coordination).
   */
  fallback: 'table' | 'retry';

  /**
   * Recovery configuration for retry fallback.
   * If omitted, React uses safe defaults.
   */
  recovery?: {
    /** Maximum retry attempts before giving up. Default: 3 */
    maxAttempts: number;
    /**
     * Base backoff (ms). Backoff is exponential: backoffMs * 2^(attempt-1).
     * Default: 1000.
     */
    backoffMs: number;
    /** What to do when retries are exhausted. Default: 'table' */
    onExhausted: 'table' | 'error';
  };

  /**
   * Optional opaque details for debugging. If present, UI may expose
   * a "Copy error details" link for support.
   */
  details?: string;
}

/**
 * REACT RECOVERY NOTE (v9.1)
 * - When WebGL context is lost, React should attempt to restore the context and rehydrate layers.
 * - Use FatalError.recovery if provided; otherwise default to 3 attempts with 1000ms exponential backoff.
 * - If exhausted and onExhausted='table', render an inline HTML table fallback using `nodes` data.
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 6: TOAST NOTIFICATIONS
// Transient feedback messages for user actions
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * A transient notification shown at the bottom of the screen.
 * Used for action confirmations, warnings, and recoverable errors.
 *
 * Examples:
 *   { type: 'success', message: 'Link copied to clipboard' }
 *   { type: 'warning', message: 'Compare limit reached (5 max)',
 *     action: { label: 'Clear selection', event: { type: 'clear_selection', ... } } }
 *   { type: 'info', message: 'AI suggested new filters',
 *     action: { label: 'Review', event: { type: 'apply_filters', ... } } }
 */
export interface ToastNotification {
  /** Unique ID — used for dismissal and deduplication */
  id: string;

  /** Visual style — maps to semantic colors in the token system */
  type: 'success' | 'info' | 'warning' | 'error';

  /** The message shown to the user */
  message: string;

  /**
   * Optional action button in the toast.
   * Clicking it emits the specified event and dismisses the toast.
   */
  action?: {
    label: string;
    event: ConstellationEvent;
  };

  /**
   * How long the toast stays visible, in milliseconds.
   * 0 = persistent (user must dismiss manually).
   *
   * v9 CHANGE: Now optional with enforced defaults:
   *   success/info → 4000ms
   *   warning → 6000ms (longer, but not persistent)
   *   error   → 0 (persistent, requires manual dismiss)
   *
   * The React component applies these defaults when duration is undefined.
   *
   * Example defaulting logic:
   *   const duration = toast.duration ?? (
   *     toast.type === 'error' ? 0 : toast.type === 'warning' ? 6000 : 4000
   *   );
   */
  duration?: number;
}

/**
 * WHICH ACTIONS PRODUCE TOASTS
 *
 * The backend controls toasts by adding/removing items from the
 * `toasts` array in props. Here are the standard situations:
 *
 *  Action                          → Toast
 *  ─────────────────────────────────────────────────────────────
 *  User applies filters            → success: "Filters applied — showing X companies"
 *  User discards draft filters     → info: "Draft filters discarded"
 *  AI suggests new filters         → info: "AI suggested new filters" + Review CTA
 *  User copies share link          → success: "Link copied to clipboard"
 *  Compare limit reached (6th)     → warning: "Compare limit reached (5 max)"
 *  Undo performed                  → info: "Undone: [description]" + Redo CTA
 *  Redo performed                  → info: "Redone: [description]"
 *  Network error (recoverable)     → error: "Connection lost" + Retry CTA
 *  Data refresh complete           → success: "Data updated — X new companies"
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 7: FILTER STATE
// The "Draft vs. Applied" AI-assisted filtering system
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * A single filter criterion.
 *
 * NOTE (v9.1): Status filter values use PipelineStatusId (stable IDs), not labels.
 *
 * TAG OPERATORS (array-to-array semantics):
 *   - contains_any: company.tags ∩ value[] ≠ ∅
 *   - contains_all: value[] ⊆ company.tags
 *   - excludes:     company.tags ∩ value[] = ∅
 *
 * Examples:
 *   { field: 'status', operator: 'in', value: ['source', 'diligence'] }
 *   { field: 'thesisScore', operator: 'gte', value: 0.75 }
 *   { field: 'tags', operator: 'contains_any', value: ['ai', 'fintech'] }
 */
export type FilterCriterion =
  | { field: 'status'; operator: 'eq' | 'neq'; value: PipelineStatusId }
  | { field: 'status'; operator: 'in' | 'not_in'; value: PipelineStatusId[] }
  | { field: 'thesisScore'; operator: 'eq' | 'neq' | 'gte' | 'lte'; value: number }
  | { field: 'tags'; operator: 'eq' | 'neq'; value: string }
  | { field: 'tags'; operator: 'contains_any' | 'contains_all' | 'excludes'; value: string[] };

/**
 * Metadata describing why an apply_filters event occurred.
 *
 * Used by the backend for history labels (undo/redo), analytics, and
 * UX affordances (e.g., different toast copy).
 */
export type FilterProvenance =
  | { action: 'legend_toggle'; status: PipelineStatusId; active: boolean }
  | { action: 'apply_draft' }
  | { action: 'clear_filters' };


/**
 * The full filter state, supporting the "Draft vs. Applied" pattern.
 *
 * How it works:
 * 1. User (or AI) proposes filters → they go into `draft`
 * 2. UI shows a preview/diff of what would change (see filter_diff tokens)
 * 3. User clicks "Apply" → `draft` moves to `applied`, `draft` becomes null
 * 4. User clicks "Discard" → `draft` becomes null, nothing changes
 *
 * v9 CHANGE — UNIFIED STATUS FILTERING:
 * Legend status toggles now update the `applied` array (adding/removing
 * status criteria) rather than using a separate `activeStatuses` prop.
 * This eliminates the risk of contradictory filter states.
 *
 * When the user toggles "Source" off in the legend (statusId 'source'):
 *   → If no status filter exists: add { field:'status', operator:'not_in', value:['source'] }
 *   → If a status filter exists: update its value array
 * When all statuses are active: remove the status criterion entirely.
 *
 * FILTER DIFF VISUAL SPEC:
 * When `draft` is non-null, the UI shows a preview:
 *   - Nodes that WOULD be filtered out: dim to 15% opacity
 *   - Nodes that WOULD newly appear: ghost outline at 40% + subtle pulse
 *   - "Apply" and "Discard" buttons visible in the filter bar
 *   - Toast shows who proposed: "AI suggested" or "You filtered"
 */
export interface FilterState {
  /** Currently active filters (what the constellation is showing now) */
  applied: FilterCriterion[];

  /**
   * Proposed but not-yet-applied filters (shown as a preview/diff).
   * Null when there's no pending suggestion.
   */
  draft: FilterCriterion[] | null;

  /** Who proposed the draft? Shown in the UI as "AI suggested" or "You filtered" */
  draftSource: 'user' | 'ai' | null;
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 8: TOOLTIP & CONTEXT MENU DATA
// What appears when users hover or right-click
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * TOOLTIP BEHAVIOR (CLIENT-SIDE — no props needed)
 *
 * Tooltips are rendered entirely from data already in CompanyNode.
 * No additional backend call is required.
 *
 *  ┌─────────────────────────────────────────┐
 *  │  Company Name                           │
 *  │  ● Status chip        Score: 0.87       │
 *  │  ─────────────────────────────────────  │
 *  │  "Strong AI/ML team, Series A..."       │  ← thesisRationale (truncated 80 chars)
 *  │  3 connections · 2 signals              │
 *  └─────────────────────────────────────────┘
 *
 *  Show delay: 300ms (to avoid flicker during mouse movement)
 *  Hide delay: 100ms (fast — don't block the next interaction)
 *  Position: Above the node, centered. Flip below if near top edge.
 *  Reduced motion: No entrance animation, instant show.
 *
 *  v9 ADDITIONS — TOOLTIP QUALITY-OF-LIFE:
 *  - SUPPRESS DURING PAN/DRAG: Tooltips are hidden and the show timer
 *    is cancelled while the user is panning or dragging. This prevents
 *    flicker as the cursor crosses nodes during a drag gesture.
 *  - HOVER INTENT: The cursor must slow down or stop over a node for
 *    at least 150ms before the 300ms show timer starts. This prevents
 *    tooltip spam during casual mouse movement across the canvas.
 *    Implementation: track cursor velocity; if > 200px/s, don't start timer.
 */

/**
 * CONTEXT MENU (CLIENT-SIDE — emits events)
 *
 * Triggered by right-click (desktop) only.
 *
 * MOBILE NOTE (v9.1.2+): Long-press is reserved for entering selection mode.
 * Context actions on mobile must be exposed via the Inspect bottom sheet and
 * Selection Tray overflow menus (no long-press context menu).
 * All items emit existing events — no new event types needed.
 *
 *  ┌────────────────────────────────────┐
 *  │  Inspect           Enter ↵        │  → emits set_selection event
 *  │  Isolate           I              │  → emits isolate_node event
 *  │  Add to Compare    ⌘+Click       │  → emits set_selection event (appended)
 *  │  ─────────────────────────────── │
 *  │  Open in Notion    ↗             │  → window.open(notionUrl)
 *  │  Copy Link         ⌘+L           │  → copies permalink, shows toast
 *  └────────────────────────────────────┘
 *
 *  "Open in Notion" is hidden if notionUrl is undefined.
 *  "Add to Compare" is DISABLED (not hidden) if MAX_COMPARE_NODES is
 *  reached, with a tooltip: "Compare limit reached (5 max)".
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 9: LABEL COLLISION MANAGEMENT
// How the component prevents overlapping text labels
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * LABEL RULES (CLIENT-SIDE — no props needed)
 *
 * Labels are only rendered at "exploration" and "inspection" zoom levels.
 * At "overview" zoom, no labels are shown (clusters show count only).
 *
 * PRIORITY ORDER (when labels collide, higher priority wins):
 *   1. Selected nodes       — always labeled
 *   2. Hovered node         — always labeled
 *   3. Isolated node        — always labeled
 *   4. High thesis score    — thesisScore >= 0.75
 *   5. Alphabetical         — tiebreaker
 *
 * COLLISION DETECTION:
 *   v9 CHANGE: Use Deck.gl's CollisionFilterExtension on TextLayer.
 *   Pass thesisScore to getCollisionPriority so the GPU handles
 *   collision detection natively. This replaces the previous
 *   "greedy rectangle packing in JS" approach which would lock up
 *   the main thread for 200+ labels at 60fps.

 *   If CollisionFilterExtension is unavailable (Deck.gl version mismatch),
 *   the component MAY fall back to a deterministic CPU heuristic — but must
 *   still respect PRIORITY ORDER and MAX_VISIBLE.
 *
 *   Selected, hovered, and isolated nodes bypass collision filtering
 *   (they are always labeled regardless of overlap).
 *
 * MAX LABELS: At most 50 labels rendered simultaneously to avoid
 * Deck.gl TextLayer performance degradation.
 *
 * LABEL POSITION: Centered below the node, offset by node radius + 4px.
 */
export const LABEL_CONFIG = {
  /** Maximum labels rendered at once */
  MAX_VISIBLE: 50,
  /** Padding around label bounding box for collision checks (px) */
  COLLISION_PADDING: 8,
  /** Gap between bottom of node and top of label (px) */
  NODE_LABEL_GAP: 4,
} as const;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 10: COMMAND PALETTE
// Quick-search overlay for finding companies by name
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * COMMAND PALETTE (Cmd/Ctrl+K)
 *
 * A quick-search overlay for finding and navigating to any company.
 * Entirely client-side — searches the in-memory nodes array.
 *
 * SEARCH BEHAVIOR:
 *   - Fuzzy matching against company name (case-insensitive).
 *   - Results limited to 10 items for performance.
 *   - Each result shows: company name, status shape+color chip,
 *     thesis score (mono font), and first tag (if any).
 *   - Empty state: "No results for '[query]'" + suggestion text.
 *
 * KEYBOARD NAVIGATION:
 *   - Arrow Up/Down: Move highlight through results.
 *   - Enter: Select highlighted result → emit navigate_to_node,
 *     center camera, and select the node.
 *   - Escape: Close palette, return focus to canvas.
 *   - Type to filter (no explicit "search" button).
 *
 * VISUAL:
 *   - Position: Top-center of canvas, floating modal.
 *   - Width: min(480px, 90vw)
 *   - Max visible results: 6 (scrollable if more).
 *   - Uses command_palette_bg, modals z-index and shadow from tokens.
 *   - Focus is trapped inside while open.
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 11: PROPS (Python → React)
// Everything the backend sends to the visualization component
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * The complete set of data and state passed from Streamlit to React.
 * This is the "downward" data flow — backend tells frontend what to show.
 */
export interface ConstellationProps {
  /** All companies to display */
  nodes: CompanyNode[];

  /** All connections between companies */
  edges: Connection[];

  /** Which nodes are currently selected (highlighted) */
  selectedNodeIds: string[];

  /**
   * If set, "isolate" this node — dim everything except this node
   * and its direct connections. Used for focused exploration.
   * The isolated node gets a halo glow at 50% opacity in its status color.
   */
  isolatedNodeId: string | null;

  /** Visual theme — matches the user's preference */
  theme: Theme;

  /** What's loading right now (if anything). REQUIRED — backend must always send a value (use 'idle' when nothing is loading). */
  loadingState: LoadingState;

  /** Current error (if any) — null means no error. REQUIRED — backend must send null when no error (do not omit). */
  error: ErrorInfo | null;


  /**
   * Data freshness indicator (trust signal).
   * When stale=true, render a subtle warning pill in the top-right.
   */
  staleIndicator?: {
    stale: boolean;
    /** ISO timestamp of last successful sync, if known */
    lastSync?: string;
    /** Human-readable message e.g. "Data may be outdated. Last sync: 2h ago" */
    message: string;
    /** Optional action (e.g., "Refresh") */
    action?: { label: string; event: ConstellationEvent };
  };

  /**
   * Fatal React runtime error (WebGL context loss, component crash).
   * When set, the component renders the fallback (data table or retry).
   * Null in normal operation.
   */
  fatalError: FatalError | null;  // REQUIRED — backend must send null when not in fatal state

  /**
   * If there are zero nodes to display, this tells the component
   * which empty state to render and what CTA to show.
   * Null when nodes.length > 0.
   */
  emptyState: EmptyState | null;  // REQUIRED — backend must send null when nodes.length > 0

  /**
   * Camera position for initial page load or permalink restore.
   * Applied INSTANTLY (no animation) on component mount.
   * Null or undefined for default camera position.
   *
   * v9 CHANGE: Split from cameraState. This prevents the "jarring jump"
   * when restoring a permalink — the default camera briefly renders
   * before animating to the target if both use the same prop.
   */
  initialCameraState?: CameraState;

  /**
   * Camera position for mid-session updates (e.g., programmatic
   * navigation after a backend action).
   * Animated over 800ms (interruptible). Ignored on first mount.
   */
  cameraState?: CameraState;

  /** Current filter state including any AI-suggested draft */
  filters: FilterState;

  // v9 REMOVED: activeStatuses
  // Legend visibility is now unified with FilterState.applied.
  // Legend toggles update the status criteria in filters.applied.
  // See FilterState documentation for the mechanism.

  /**
   * Whether undo is available (enables/disables the undo button).
   * The history stack lives in Python's session_state.
   */
  canUndo: boolean;

  /** Whether redo is available */
  canRedo: boolean;

  /**
   * Active toast notifications. The backend manages this array:
   * add items to show toasts, remove them to dismiss.
   * The component renders them in a stack at the bottom-right.
   */
  toasts: ToastNotification[];

  /**
   * Whether the user's system prefers reduced motion.
   * Detected by the Python backend via JS bridge or user preference.
   * When true: all decorative animations are disabled, camera
   * movements snap instead of interpolating, tooltips appear instantly.
   * See motion.reduced_motion in tokens for the full list.
   */
  reducedMotion: boolean;


  /**
   * Optional keyboard shortcut overrides for power users / enterprise environments.
   * If omitted, defaults described in Section 14 apply.
   */
  keyboardShortcuts?: {
    /** Default: ['i', 'I'] */
    isolate?: string[];
    /** Default: ['c', 'C'] */
    compare?: string[];
    /** Default: ['mod+k'] (Cmd/Ctrl+K) */
    commandPalette?: string[];
    /** Default: ['?'] */
    help?: string[];
  };

  /**
   * Optional mini-map configuration for large constellations (>200 nodes).
   * When enabled, render a simplified overview map with current viewport.
   */
  miniMap?: {
    enabled: boolean;
    size?: 'small' | 'medium'; // 100px vs 150px
    position?: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
    opacity?: number; // Default 0.8
  };
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 12: EVENTS (React → Python)
// Everything the visualization tells the backend about user actions
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Every event the React component sends is wrapped in this envelope.
 * The `eventId` prevents Streamlit from processing the same event
 * twice during its rerun cycle.
 *
 * STREAMLIT INTEGRATION RULE (v9.1.2+):
 * Streamlit Custom Components expose a **single-value** state bridge.
 * React MUST NOT attempt to emit two distinct events synchronously in
 * the same execution tick. If additional metadata is needed, it MUST be
 * included in the same payload (e.g., apply_filters.provenance).
 */
export interface ConstellationEventPayload {
  /**
   * Unique ID for this payload — prevents double-processing.
   * Use Date.now().toString() or a UUID.
   */
  eventId: string;

  /** The primary event data */
  event: ConstellationEvent;

  /**
   * Optional snapshot of the camera at the moment the payload was emitted.
   *
   * Why this exists:
   * - Streamlit components can only publish one value per microtask.
   * - The frontend may coalesce multiple low-level events into a single payload.
   *
   * When present, the backend SHOULD treat this as the authoritative latest camera state.
   */
  cameraState?: CameraState;
}

/**
 * EVENT TIMING
 *
 * Some events are rate-limited client-side to prevent flooding
 * the Streamlit rerun cycle. These values are enforced by the
 * React component and are not configurable via props.
 *
 *  Event              │  Strategy   │  Delay   │  Max Wait
 *  ───────────────────┼─────────────┼──────────┼──────────
 *  camera_idle        │  debounce   │  500ms   │  2000ms
 *  apply_filters      │  debounce   │  300ms   │  —
 *  (all others)       │  immediate  │  0ms     │  —
 *
 *  "debounce" = wait until the user stops doing the action.
 *  "throttle" = fire at most once per interval.
 *  "maxWait"  = fire anyway after this long, even if still active
 *               (prevents continuous panning from never saving state).
 */
export const EVENT_TIMING = {
  camera_idle:    { strategy: 'debounce' as const, delay: 500, maxWait: 2000 },
  apply_filters:  { strategy: 'debounce' as const, delay: 300 },
} as const;

/**
 * All possible events the constellation can emit.
 *
 * DESIGN RULE: Events only cross the bridge when Python needs to know.
 * Purely visual interactions (hover halo, zoom, pan, tooltip) stay in React.
 * Events cross when they change data, selection, or shared state.
 *
 * v9 CHANGE — SIMPLIFIED SELECTION MODEL:
 * The previous select + ctrlKey + multi_select trio is replaced by:
 *   - set_selection: replaces the entire selection with nodeIds[]
 *   - clear_selection: empties the selection
 *
 * The React component computes the resulting selection set client-side
 * (handling click, ctrl+click, tray chip removal, keyboard) and always
 * sends the full resulting array. The backend never needs toggle logic.
 */
export type ConstellationEvent =
  // —— Selection Events (v9: simplified) ——————————————————————————
  | {
      /**
       * Set the current selection to exactly these nodes.
       * The React component computes this from all input methods:
       *   - Click a node → set_selection([nodeId])
       *   - Ctrl+Click → set_selection([...current, nodeId]) or
       *     set_selection(current.filter(id => id !== nodeId))
       *   - Tray chip remove → set_selection(current.filter(...))
       *   - Keyboard Enter → set_selection([focusedNodeId])
       *   - Context "Add to Compare" → set_selection([...current, nodeId])
       */
      type: 'set_selection';
      /** The complete set of selected node IDs after the action */
      nodeIds: string[];
      /** Current camera position (for permalink accuracy) */
      cameraState: CameraState;
    }
  | {
      /** User clicked empty space or pressed Escape — deselect all */
      type: 'clear_selection';
      cameraState: CameraState;
    }
  | {
      /** User clicked "Compare" in the selection tray with 2+ nodes selected */
      type: 'compare_selected';
      /** Always 2–5 node IDs (enforced by MAX_COMPARE_NODES) */
      nodeIds: string[];
      /** Current camera position (for permalink accuracy) */
      cameraState: CameraState;
    }


  // —— Camera Events ————————————————————————————————————————————
  | {
      /**
       * User stopped panning/zooming (fires after debounce period).
       * Backend stores this for the "Share View" permalink feature.
       */
      type: 'camera_idle';
      cameraState: CameraState;
      /** Provenance: did the user move the camera, or did code? */
      source: 'user' | 'programmatic';
      /**
       * Optional granular trigger for analytics/history.
       * 'navigate' covers navigate_to_node + story jumps.
       */
      trigger?: 'pan' | 'zoom' | 'navigate' | 'cluster_click';
    }

  // —— Filter Events ————————————————————————————————————————————
  | {
      /**
       * Set the *applied* filters to exactly this array.
       *
       * IMPORTANT (Streamlit bridge): one user action MUST emit exactly
       * one event payload. Do not emit multiple filter events in the same
       * execution tick — Streamlit may drop earlier values.
       */
      type: 'apply_filters';
      filters: FilterCriterion[];
      /** Optional metadata describing why filters changed (undo/redo labeling, analytics). */
      provenance?: FilterProvenance;
    }
  | {
      /** User clicked "Discard" on a draft filter set */
      type: 'discard_draft';
    }
  
  // —— History Events ————————————————————————————————————————————
  | {
      /** User pressed Ctrl/Cmd+Z or clicked the undo button */
      type: 'undo';
    }
  | {
      /** User pressed Ctrl/Cmd+Shift+Z or clicked the redo button */
      type: 'redo';
    }

  // —— Navigation Events ————————————————————————————————————————
  | {
      /**
       * User searched for a company in the Command Palette (Cmd/Ctrl+K)
       * and selected a result. Backend should center camera and select it.
       */
      type: 'navigate_to_node';
      nodeId: string;
    }
  | {
      /** User clicked "Isolate" — focus on this node and its connections */
      type: 'isolate_node';
      nodeId: string;
    }
  | {
      /** User exited isolation mode — show everything again */
      type: 'exit_isolation';
    }

  // —— Sharing Events ————————————————————————————————————————————
  | {
      /**
       * User clicked "Share View" — backend generates a permalink
       * from the current state and returns it via a toast.
       */
      type: 'request_share_link';
      viewState: ShareableViewState;
    }

  // —— Toast Events ————————————————————————————————————————————
  | {
      /**
       * User dismissed a toast (clicked X or it auto-expired).
       * Backend should remove it from the toasts array.
       */
      type: 'dismiss_toast';
      toastId: string;
    }

  // —— Settings Events (v9 addition) ——————————————————————————
  | {
      /**
       * User clicked a CTA that requires opening settings/configuration.
       * Used by the "Connect Notion" empty state CTA.
       * Backend should navigate to the appropriate settings page.
       */
      type: 'open_settings';
      /** Which settings page to open */
      target: 'notion_connection' | 'general';
    }

  // —— Error Recovery ————————————————————————————————————————————
  | {
      /** User clicked "Retry" on an error banner or empty state CTA */
      type: 'retry';
    };




/**
 * EVENT PRIORITY (Streamlit bridge)
 *
 * Streamlit Custom Components expose a single-value state bridge.
 * Even with microtask queuing, multiple events may be produced in the
 * same execution tick. The component SHOULD emit only one payload per tick.
 *
 * When multiple events are queued, choose the highest priority one.
 * Higher number = more important (data/selection changes > camera/telemetry).
 *
 * This map MUST be exhaustive over ConstellationEvent['type'].
 */
export const EVENT_PRIORITY: Record<ConstellationEvent['type'], number> = {
  set_selection:     100,
  clear_selection:    95,
  compare_selected:   94,
  apply_filters:      90,
  discard_draft:      80,
  undo:               70,
  redo:               70,
  navigate_to_node:   60,
  isolate_node:       55,
  exit_isolation:     54,
  request_share_link: 50,
  open_settings:      45,
  retry:              40,
  dismiss_toast:      20,
  camera_idle:        10,
} as const;
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 13: SHARING & PERMALINKS
// What gets encoded in a "Share View" URL
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Everything needed to reconstruct a shared view.
 * This gets serialized into a URL parameter.
 *
 * Serialization: JSON → base64url (no padding).
 * URL format: https://app.example.com/constellation?view={base64url}
 *
 * COMPATIBILITY: If the encoded view references node IDs that no
 * longer exist (deleted companies), the backend should silently
 * remove them from selection/isolation and load the rest normally.
 */
export interface ShareableViewState {
  /** Camera position and zoom */
  camera: CameraState;

  /** Which nodes are selected */
  selectedNodeIds: string[];

  /** Which node is isolated (if any) */
  isolatedNodeId: string | null;

  /** Currently applied filters (v9: this is the single filter truth) */
  filters: FilterCriterion[];
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 14: ACCESSIBILITY CONTRACT
// Keyboard navigation modes and ARIA requirements
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * KEYBOARD NAVIGATION MODES
 *
 * The component supports two primary modes (v9: Table mode removed
 * from in-component keyboard cycling — see note below).
 *
 * SWITCHING MECHANISM:
 *   F6 cycles through modes: Canvas → Node → Canvas
 *   The mode indicator in the toolbar is also clickable.
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │  Mode    │  Keys              │  Behavior                       │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  Canvas  │  Arrow keys        │  Pan the canvas                 │
 *  │          │  +/-               │  Zoom in/out                    │
 *  │          │  Home              │  Reset to default view          │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  Node    │  Tab / Shift+Tab   │  Move focus between nodes       │
 *  │          │  Arrow keys        │  Move to connected nodes        │
 *  │          │  Enter             │  Select focused node            │
 *  │          │  Ctrl+Enter        │  Add to compare selection       │
 *  │          │  I                 │  Isolate focused node           │
 *  └──────────────────────────────────────────────────────────────────┘
 *
 * TABLE MODE — IMPLEMENTATION NOTE (v9):
 *   Table mode is removed from in-component keyboard cycling because
 *   the data table lives in Streamlit's DOM outside the React iframe.
 *   Cross-iframe focus transfer is unreliable and creates a "paper
 *   feature" that passes spec review but fails in production.
 *
 *   Instead: A "Show Data Table" button in the component toolbar
 *   emits a navigate event. The Streamlit backend renders the table
 *   in a separate section below the component. Users reach it by
 *   tabbing past the component or clicking the button.
 *
 * ESCAPE KEY PRIORITY ORDER (v9 addition):
 *   When Escape is pressed, the FIRST applicable action fires:
 *   1. Close modal (command palette, share modal, context menu)
 *   2. Exit isolation (if isolated)
 *   3. Clear selection (if any nodes selected)
 *   4. No-op (nothing to dismiss)
 *   This strict ordering prevents "why did it do that?" confusion.
 *
 * FOCUS TRAPPING:
 *   When a modal is open (context menu, share modal, command palette),
 *   focus is trapped inside the modal. Esc closes it and returns
 *   focus to the previously focused element.
 *
 * ARIA LIVE REGION:
 *   A single aria-live="polite" region announces:
 *   - Selection changes: "Selected [Company Name]"
 *   - Zoom level changes: "Zoomed to [level]%"
 *   - Loading state changes: "Loading data..." / "Data loaded, X companies"
 *   - Error messages: "[Error title]: [Error message]"
 *   - Mode changes: "Keyboard mode: [Canvas/Node]"
 *   Placement: A visually hidden div at the end of the component DOM.
 *
 * SCREEN READER INSTRUCTIONS:
 *   On first focus, announce: "Constellation viewer.
 *   Press F6 to switch keyboard modes. Press ? for help."
 *
 * HELP OVERLAY (? key) — v9 addition:
 *   Pressing ? opens a lightweight shortcut cheat sheet modal:
 *   - Lists all keyboard shortcuts grouped by mode
 *   - Includes mouse interaction reference (click, ctrl+click, scroll, drag)
 *   - Includes touch gesture reference (tap, long-press, pinch, swipe)
 *   - Escape closes it (priority 1 in ESC order above)
 *   - Accessible: focus-trapped, aria-labeled, dismissible by Esc or button
 */

/**
 * SHORTCUT CONTEXT RULES (v9.1.1 clarification)
 *
 * To avoid conflicts with text inputs (command palette search, filter chips,
 * note fields) and modal interactions, keyboard shortcuts MUST be active only
 * when ALL of the following are true:
 *   1) Focus is on the canvas container (or an element within it that is not a text input)
 *   2) No modal is open (command palette, share, help)
 *   3) The user is not currently typing in an <input>, <textarea>, or contenteditable element
 *
 * Implementation note: gate shortcuts behind a single boolean, and add a
 * `data-keyboard-shortcuts="enabled"` attribute on the canvas wrapper for QA.
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 15: SELECTION SEMANTICS
// How node selection works across all input methods
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * SELECTION RULES
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │  Action               │  Result                                 │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  Click a node         │  Replace selection with that node       │
 *  │  Ctrl/Cmd + Click     │  Toggle node in/out of selection        │
 *  │  Click empty space    │  Clear all selection                    │
 *  │  Press Escape         │  Clear selection (or close panel/modal) │
 *  │  Click a cluster      │  Zoom into cluster (no select)         │
 *  └──────────────────────────────────────────────────────────────────┘
 *
 * All selection actions go through the React component, which computes
 * the resulting selection set and emits a single set_selection event
 * with the complete nodeIds array. The backend never needs toggle logic.
 *
 * MAX COMPARE SIZE: 5 nodes.
 *   When the user tries to add a 6th node via Ctrl+Click:
 *   - The click is ignored (selection doesn't change).
 *   - A warning toast appears: "Compare limit reached (5 max)"
 *   - The toast has a "Clear selection" action CTA.
 *
 * SELECTION TRAY:
 *   When 1+ nodes are selected, a persistent tray appears at the
 *   bottom of the canvas showing:
 *   - Chips for each selected node (click chip to deselect)
 *   - A "Compare" CTA button (enabled when 2+ selected)
 *   - A "Clear" button
 *   - A count: "3 of 5 selected"
 *
 *   OVERFLOW BEHAVIOR (v9 addition):
 *   The tray uses horizontal scroll with gradient fade indicators
 *   (left/right gradient masks showing content continues) rather
 *   than wrapping to a second row. This preserves vertical canvas
 *   real estate.
 *
 *   MOBILE: On viewports below 640px, the tray becomes a floating
 *   pill at the bottom showing count + "Compare" button. Tap the
 *   pill to expand to full tray view.
 */
export const MAX_COMPARE_NODES = 5;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 16: PROGRESSIVE DISCLOSURE
// How the UI communicates hidden nodes
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * PROGRESSIVE DISCLOSURE INDICATOR (v9 addition)
 *
 * When the thesis visibility threshold is active (hiding low-scoring
 * nodes), the UI MUST show a persistent indicator so users don't
 * think the system is missing companies.
 *
 * PLACEMENT: Top-right of canvas, floating overlay (legend z-index).
 *
 * FORMAT: "Showing {visible} of {total} companies — {hidden} hidden (zoom in or search)"
 *
 * OPTIONAL CTA: "Show all" — disables the threshold for this session.
 * Include a performance warning: "(may reduce performance)" if total > 500.
 *
 * THRESHOLD: Defined in starwatcher-status-map.ts as
 * DEFAULT_THESIS_VISIBILITY_THRESHOLD (currently 0.75).
 *
 * INTERACTION:
 * - When all nodes are visible (total <= 200 or threshold disabled),
 *   the indicator is hidden.
 * - When the user zooms into inspection level, all nodes in the
 *   viewport become visible regardless of threshold.
 * - When the user searches via command palette, matching nodes
 *   bypass the threshold.
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 17: MOBILE INTERACTION SPECIFICATION
// Touch and responsive behavior
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * MOBILE INTERACTION (v9 addition)
 *
 * Breakpoints are defined in tokens. Here's how interaction adapts:
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │  Viewport        │  Inspect Panel      │  Selection             │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  Mobile (<640px) │  Bottom sheet        │  Tap: select single.   │
 *  │                  │  (50% height,        │  Long-press (500ms):   │
 *  │                  │   draggable to 90%)  │  enter selection mode. │
 *  │                  │                      │  Pinch-to-zoom.        │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  Tablet          │  Slide-over          │  Standard              │
 *  │  (640–1024px)    │  (40% width, right)  │  multi-select.         │
 *  │                  │                      │  Two-finger pan.       │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │  Desktop         │  Fixed right         │  Ctrl/Cmd+Click        │
 *  │  (>1024px)       │  sidebar             │  multi-select.         │
 *  │                  │                      │  Scroll-to-zoom.       │
 *  └──────────────────────────────────────────────────────────────────┘
 *
 * CRITICAL DETAIL: When the virtual keyboard opens (e.g., command
 * palette search on mobile), the canvas must maintain its aspect ratio.
 * Do not allow the keyboard to distort the graph layout. Reduce
 * the canvas viewport height instead.
 */


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SECTION 18: BACKEND VALIDATION GUARANTEES
// What the backend promises to be true about the data it sends
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * DATA QUALITY CONTRACT
 *
 * The React component assumes all incoming data is clean.
 * The Python backend MUST guarantee the following before sending:
 *
 *  1. All node IDs are unique (no duplicates in the nodes array).
 *  2. All edge source/target IDs exist in the nodes array.
 *  3. posX and posY are finite numbers (not NaN, not Infinity).
 *  4. thesisScore is between 0.0 and 1.0 inclusive.
 *  5. status is one of the 8 PipelineStatusId values exactly (IDs, not labels).
 *  5a. Backend MUST convert Notion PipelineStatus labels → PipelineStatusId via STATUS_ID_MAP before sending to React.
 *  6. strength on edges is between 0.0 and 1.0 inclusive.
 *  7. selectedNodeIds only contains IDs that exist in nodes.
 *  8. isolatedNodeId is either null or an ID that exists in nodes.
 *  9. toasts array has no duplicate IDs.
 * 10. emptyState is null when nodes.length > 0.
 * 11. signals array is sorted by timestamp descending (newest first).
 *
 * If any of these invariants are violated, behavior is undefined.
 * The backend should log a warning and fix or discard bad records.
 */