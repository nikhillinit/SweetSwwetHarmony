"""
Starwatcher Adapter — Discovery Engine data → Starwatcher contract schema.

Converts signals, company files, and suppression entries into the
CompanyNode / Connection / ConstellationProps structures defined in
the Starwatcher v9.1.6 integration contract.
"""

import hashlib
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.db_path_helper import resolve_db_path_env

# ─── Status Mapping ──────────────────────────────────────────────────────────

# Notion label → stable internal ID (PipelineStatusId)
STATUS_ID_MAP: Dict[str, str] = {
    "Source": "source",
    "Initial Meeting / Call": "initial_meeting",
    "Dilligence": "diligence",       # Notion typo (double-l)
    "Diligence": "diligence",        # Contract spelling
    "Tracking": "tracking",
    "Committed": "committed",
    "Funded": "funded",
    "Passed": "passed",
    "Lost": "lost",
}

# Stable ID → canonical display label
STATUS_LABEL_MAP: Dict[str, str] = {
    "source": "Source",
    "initial_meeting": "Initial Meeting / Call",
    "diligence": "Diligence",
    "tracking": "Tracking",
    "committed": "Committed",
    "funded": "Funded",
    "passed": "Passed",
    "lost": "Lost",
}

VALID_STATUS_IDS = frozenset(STATUS_LABEL_MAP.keys())

# Status → shape (from starwatcher-status-map.ts)
STATUS_SHAPE_MAP: Dict[str, str] = {
    "source": "circle",
    "initial_meeting": "square",
    "diligence": "triangle",
    "tracking": "star",
    "committed": "diamond",
    "funded": "pentagon",
    "passed": "hexagon",
    "lost": "octagon",
}

# Status → color (cosmic_dark theme)
STATUS_COLOR_MAP: Dict[str, str] = {
    "source": "#9BA2AA",
    "initial_meeting": "#6BAEFF",
    "diligence": "#F7B84D",
    "tracking": "#B9A3FB",
    "committed": "#2DB299",
    "funded": "#34D399",
    "passed": "#F87171",
    "lost": "#FB923C",
}

# Status → dark background color (cosmic_dark theme)
STATUS_DARK_BG_MAP: Dict[str, str] = {
    "source": "#2D3035",
    "initial_meeting": "#1A2E4A",
    "diligence": "#3D2E10",
    "tracking": "#2A2240",
    "committed": "#0D2E27",
    "funded": "#0D3024",
    "passed": "#3B1515",
    "lost": "#3B2510",
}

# Sector angles for polar layout (8 statuses × 45° = 360°, with gaps)
SECTOR_ANGLES: Dict[str, float] = {
    "source": 0.0,
    "initial_meeting": 45.0,
    "diligence": 90.0,
    "tracking": 135.0,
    "committed": 180.0,
    "funded": 225.0,
    "passed": 270.0,
    "lost": 315.0,
}

SECTOR_WIDTH = 40.0  # degrees per sector (5° gap between sectors)


def to_status_id(label: str) -> str:
    """Convert a Notion status label to a stable internal ID."""
    return STATUS_ID_MAP.get(label, "source")


def to_status_label(status_id: str) -> str:
    """Convert a stable ID back to the display label."""
    return STATUS_LABEL_MAP.get(status_id, "Source")


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class SignalEntry:
    """A single signal attached to a company."""
    timestamp: str
    text: str
    source: str


@dataclass
class CompanyNode:
    """A single company node in the constellation."""
    id: str
    name: str
    posX: float
    posY: float
    thesisScore: float
    status: str
    thesisRationale: str
    signals: List[SignalEntry] = field(default_factory=list)
    notionUrl: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class Connection:
    """A connection (edge) between two companies."""
    source: str
    target: str
    strength: float
    reason: Optional[str] = None


@dataclass
class ConstellationProps:
    """Complete props for the constellation viewer."""
    nodes: List[CompanyNode]
    edges: List[Connection]
    selectedNodeIds: List[str] = field(default_factory=list)
    isolatedNodeId: Optional[str] = None
    theme: str = "cosmic_dark"
    loadingState: str = "idle"
    error: Optional[Dict[str, Any]] = None
    fatalError: Optional[Dict[str, Any]] = None
    emptyState: Optional[Dict[str, Any]] = None
    canUndo: bool = False
    canRedo: bool = False
    toasts: List[Dict[str, Any]] = field(default_factory=list)
    reducedMotion: bool = False
    filters: Dict[str, Any] = field(default_factory=lambda: {
        "applied": [],
        "draft": None,
        "draftSource": None,
    })


# ─── Polar Layout ─────────────────────────────────────────────────────────────

def _deterministic_hash(key: str) -> float:
    """Return a deterministic float in [0, 1) from a string key."""
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def compute_polar_position(
    status_id: str,
    thesis_score: float,
    canonical_key: str,
    canvas_width: float = 900.0,
    canvas_height: float = 700.0,
) -> Tuple[float, float]:
    """Compute (posX, posY) using polar constellation layout.

    - Angle: each status occupies a ~40° sector
    - Radius: 1.0 - thesisScore (high-thesis near center)
    - Jitter: deterministic angular spread within sector
    """
    cx = canvas_width / 2.0
    cy = canvas_height / 2.0
    max_radius = min(cx, cy) * 0.85  # leave margin

    # Base angle for this status sector
    base_angle = SECTOR_ANGLES.get(status_id, 0.0)

    # Deterministic jitter within sector
    jitter = _deterministic_hash(canonical_key)
    angle_deg = base_angle + (jitter * SECTOR_WIDTH)
    angle_rad = math.radians(angle_deg)

    # Radius: high thesis = close to center
    score = max(0.0, min(1.0, thesis_score))
    r = (1.0 - score) * max_radius * 0.85 + max_radius * 0.15  # min 15% from center

    pos_x = cx + r * math.cos(angle_rad)
    pos_y = cy + r * math.sin(angle_rad)

    return (pos_x, pos_y)


# ─── Edge Derivation ─────────────────────────────────────────────────────────

def derive_edges(
    nodes: List[CompanyNode],
    signals_by_company: Dict[str, List[Dict[str, Any]]],
) -> List[Connection]:
    """Derive edges from shared source_api + temporal proximity.

    Two companies are connected if they share a signal source and their
    signals were detected within 24 hours of each other.
    """
    edges: List[Connection] = []
    seen_pairs: set = set()

    # Build index: source_api → list of (node_id, detected_at)
    source_index: Dict[str, List[Tuple[str, str]]] = {}
    for node in nodes:
        for sig in signals_by_company.get(node.id, []):
            api = sig.get("source_api", "")
            detected = sig.get("detected_at", "")
            if api and detected:
                source_index.setdefault(api, []).append((node.id, detected))

    for api, entries in source_index.items():
        for i, (id_a, dt_a) in enumerate(entries):
            for j in range(i + 1, len(entries)):
                id_b, dt_b = entries[j]
                if id_a == id_b:
                    continue
                pair_key = tuple(sorted([id_a, id_b]))
                if pair_key in seen_pairs:
                    continue

                # Check temporal proximity (24h)
                try:
                    t_a = datetime.fromisoformat(dt_a.replace("Z", "+00:00"))
                    t_b = datetime.fromisoformat(dt_b.replace("Z", "+00:00"))
                    delta_hours = abs((t_a - t_b).total_seconds()) / 3600
                    if delta_hours <= 24:
                        seen_pairs.add(pair_key)
                        # Strength = inverse of time delta, clamped
                        strength = max(0.1, min(1.0, 1.0 - (delta_hours / 24.0)))
                        edges.append(Connection(
                            source=pair_key[0],
                            target=pair_key[1],
                            strength=round(strength, 3),
                            reason=f"Shared source: {api}",
                        ))
                except (ValueError, TypeError):
                    continue

    return edges


# ─── Main Builder ─────────────────────────────────────────────────────────────

def build_constellation_props(
    db_path: Optional[str] = None,
    canvas_width: float = 900.0,
    canvas_height: float = 700.0,
) -> ConstellationProps:
    """Build complete ConstellationProps from the SQLite database.

    Reads signals + company_files to construct nodes and edges.
    """
    if db_path is None:
        db_path = resolve_db_path_env()

    if not Path(db_path).exists():
        return ConstellationProps(
            nodes=[],
            edges=[],
            loadingState="idle",
            emptyState={
                "type": "initial",
                "title": "No database found",
                "message": f"Expected database at {db_path}",
            },
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return _build_from_db(conn, canvas_width, canvas_height)
    finally:
        conn.close()


def _build_from_db(
    conn: sqlite3.Connection,
    canvas_width: float,
    canvas_height: float,
) -> ConstellationProps:
    """Internal: build props from an open DB connection."""
    # Fetch signals grouped by canonical_key
    rows = conn.execute("""
        SELECT s.id, s.signal_type, s.source_api, s.canonical_key,
               s.company_name, s.confidence, s.detected_at, s.created_at,
               sp.notion_page_id, sp.processing_status
        FROM signals s
        LEFT JOIN signal_processing sp ON s.id = sp.signal_id
        ORDER BY s.detected_at DESC
    """).fetchall()

    if not rows:
        return ConstellationProps(
            nodes=[],
            edges=[],
            loadingState="idle",
            emptyState={
                "type": "initial",
                "title": "No signals yet",
                "message": "Run the pipeline to discover companies.",
            },
        )

    # Group signals by canonical_key
    companies: Dict[str, Dict[str, Any]] = {}
    signals_by_id: Dict[str, List[Dict[str, Any]]] = {}

    for row in rows:
        key = row["canonical_key"]
        if key not in companies:
            companies[key] = {
                "canonical_key": key,
                "company_name": row["company_name"] or key,
                "max_confidence": row["confidence"],
                "signals": [],
                "source_apis": set(),
                "notion_page_id": None,
                "status": "Source",  # default
            }

        company = companies[key]
        company["max_confidence"] = max(
            company["max_confidence"], row["confidence"] or 0
        )
        company["source_apis"].add(row["source_api"])

        if row["notion_page_id"]:
            company["notion_page_id"] = row["notion_page_id"]

        sig_entry = {
            "signal_id": row["id"],
            "source_api": row["source_api"],
            "signal_type": row["signal_type"],
            "detected_at": row["detected_at"],
        }
        company["signals"].append(sig_entry)

    # Try to get status from company_files if available
    try:
        cf_rows = conn.execute(
            "SELECT canonical_key, status FROM company_files"
        ).fetchall()
        cf_status: Dict[str, str] = {}
        for cf in cf_rows:
            cf_status[cf["canonical_key"]] = cf["status"]
    except sqlite3.OperationalError:
        cf_status = {}

    # Try to get suppression statuses
    try:
        sup_rows = conn.execute(
            "SELECT canonical_key, status FROM suppression_cache"
        ).fetchall()
        for sr in sup_rows:
            if sr["canonical_key"] in companies:
                companies[sr["canonical_key"]]["status"] = sr["status"]
    except sqlite3.OperationalError:
        pass

    # Build nodes
    nodes: List[CompanyNode] = []
    node_ids: set = set()

    for key, company in companies.items():
        node_id = company.get("notion_page_id") or f"sig-{key}"
        if node_id in node_ids:
            continue
        node_ids.add(node_id)

        thesis_score = max(0.0, min(1.0, company["max_confidence"]))
        status_label = company.get("status", "Source")
        status_id = to_status_id(status_label)

        pos_x, pos_y = compute_polar_position(
            status_id, thesis_score, key, canvas_width, canvas_height,
        )

        # Build signal entries
        sig_entries = []
        for sig in company["signals"][:5]:  # limit to 5 most recent
            sig_entries.append(SignalEntry(
                timestamp=sig.get("detected_at", ""),
                text=f"{sig.get('signal_type', 'signal')} via {sig.get('source_api', 'unknown')}",
                source=sig.get("source_api", "unknown"),
            ))

        # Build rationale
        sources = sorted(company["source_apis"])
        rationale = f"Detected via {', '.join(sources)}. Confidence: {thesis_score:.0%}"

        # Notion URL
        notion_url = None
        if company.get("notion_page_id"):
            notion_url = f"https://notion.so/{company['notion_page_id'].replace('-', '')}"

        nodes.append(CompanyNode(
            id=node_id,
            name=company["company_name"],
            posX=round(pos_x, 2),
            posY=round(pos_y, 2),
            thesisScore=round(thesis_score, 4),
            status=status_id,
            thesisRationale=rationale,
            signals=sig_entries,
            notionUrl=notion_url,
            tags=list(sources),
        ))

        # Store signals for edge derivation
        signals_by_id[node_id] = company["signals"]

    # Derive edges
    edges = derive_edges(nodes, signals_by_id)

    # Build empty state
    empty_state = None
    if not nodes:
        empty_state = {
            "type": "initial",
            "title": "No companies yet",
            "message": "Run the pipeline to discover companies.",
        }

    return ConstellationProps(
        nodes=nodes,
        edges=edges,
        loadingState="idle",
        error=None,
        fatalError=None,
        emptyState=empty_state,
    )
