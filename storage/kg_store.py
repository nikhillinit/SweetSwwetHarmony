"""Knowledge Graph store — CRUD, traversal, validation, and crash recovery.

All graph mutations go through this module.  Direct SQL on kg_* tables
from other modules is blocked by the governance ratchet (PR3).

Key design decisions:
  - Cycle-safe traversal uses JSON array visited set (no INSTR false positives)
  - Undirected edges stored with source_node_id < target_node_id
  - Bidirectional traversal uses kg_edges_bidirectional view
  - Crash recovery marks abandoned 'running' rows as 'failed' on init
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from storage.migrations.v50_knowledge_graph import (
    UNDIRECTED_EDGE_TYPES,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KGNode:
    id: str
    node_type: str
    label: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    source_table: Optional[str] = None
    source_id: Optional[str] = None
    is_tombstone: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_run_id: Optional[str] = None


@dataclass
class KGEdge:
    id: str
    edge_type: str
    source_node_id: str
    target_node_id: str
    weight: float = 1.0
    properties: Optional[Dict[str, Any]] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    is_directed: bool = True
    last_run_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class KGRunInfo:
    run_id: str
    mode: str
    started_at: str
    completed_at: Optional[str] = None
    nodes_upserted: int = 0
    edges_upserted: int = 0
    nodes_tombstoned: int = 0
    edges_expired: int = 0
    status: str = "running"


@dataclass
class KGRunSourceInfo:
    id: Optional[int] = None
    run_id: str = ""
    source_name: str = ""
    refresh_strategy: str = "full_recompute"
    status: str = "running"
    started_at: str = ""
    completed_at: Optional[str] = None
    watermark_before: Optional[str] = None
    watermark_after: Optional[str] = None
    rows_scanned: int = 0
    rows_written: int = 0
    duration_ms: Optional[float] = None
    lock_retries: int = 0
    error_text: Optional[str] = None


@dataclass
class KGStats:
    total_nodes: int = 0
    live_nodes: int = 0
    tombstoned_nodes: int = 0
    total_edges: int = 0
    live_edges: int = 0
    expired_edges: int = 0
    nodes_by_type: Dict[str, int] = field(default_factory=dict)
    edges_by_type: Dict[str, int] = field(default_factory=dict)
    total_runs: int = 0
    last_run: Optional[Dict[str, Any]] = None


@dataclass
class ValidationResult:
    check: str
    status: str  # "pass" or "fail"
    details: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"check": self.check, "status": self.status}
        if self.details:
            d["details"] = self.details
        return d


# ---------------------------------------------------------------------------
# ID generation helpers
# ---------------------------------------------------------------------------

def kg_node_id(prefix: str, seed: str) -> str:
    """Return a deterministic, non-hashed node ID for KG materialization.

    Company nodes reuse the Phase G entity_id directly. Other node types use a
    readable prefixed natural key so callers do not mint ad hoc hash IDs.
    """
    node_type = prefix.strip()
    stable_key = seed.strip()
    if not node_type or not stable_key:
        raise ValueError("kg_node_id requires non-empty prefix and seed")
    if node_type == "company":
        return stable_key
    return f"{node_type}:{stable_key}"


def kg_edge_id() -> str:
    """Random edge ID (UUID4 hex prefix)."""
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# KGStore
# ---------------------------------------------------------------------------

class KGStore:
    """CRUD, traversal, validation, and crash recovery for the knowledge graph."""

    def __init__(self, db: "aiosqlite.Connection"):
        self._db = db

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _props_json(props: Optional[Dict[str, Any]]) -> Optional[str]:
        return json.dumps(props) if props else None

    @staticmethod
    def _parse_props(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    # -- crash recovery -------------------------------------------------------

    async def recover_stale_runs(self) -> int:
        """Mark runs stuck in 'running' for >1 hour as 'failed'.

        Returns the number of runs recovered.
        """
        now = self._now()
        cursor = await self._db.execute("""
            UPDATE kg_runs SET status = 'failed', completed_at = :now
            WHERE status = 'running'
              AND started_at < datetime('now', '-1 hour')
        """, {"now": now})
        count = cursor.rowcount
        if count:
            logger.warning("Recovered %d stale KG runs", count)
        # Also recover stale source records
        await self._db.execute("""
            UPDATE kg_run_sources SET status = 'failed', completed_at = :now
            WHERE status = 'running'
              AND started_at < datetime('now', '-1 hour')
        """, {"now": now})
        await self._db.commit()
        return count

    # -- runs CRUD ------------------------------------------------------------

    async def create_run(self, mode: str = "full") -> KGRunInfo:
        """Create a new KG run record."""
        run = KGRunInfo(
            run_id=uuid.uuid4().hex[:16],
            mode=mode,
            started_at=self._now(),
        )
        await self._db.execute("""
            INSERT INTO kg_runs (run_id, mode, started_at, status)
            VALUES (:run_id, :mode, :started_at, :status)
        """, {
            "run_id": run.run_id,
            "mode": run.mode,
            "started_at": run.started_at,
            "status": run.status,
        })
        await self._db.commit()
        return run

    async def complete_run(
        self,
        run_id: str,
        *,
        status: str = "completed",
        nodes_upserted: int = 0,
        edges_upserted: int = 0,
        nodes_tombstoned: int = 0,
        edges_expired: int = 0,
    ) -> None:
        """Mark a run as completed or failed with aggregate counts."""
        await self._db.execute("""
            UPDATE kg_runs
            SET status = :status,
                completed_at = :now,
                nodes_upserted = :nu,
                edges_upserted = :eu,
                nodes_tombstoned = :nt,
                edges_expired = :ee
            WHERE run_id = :rid
        """, {
            "status": status,
            "now": self._now(),
            "nu": nodes_upserted,
            "eu": edges_upserted,
            "nt": nodes_tombstoned,
            "ee": edges_expired,
            "rid": run_id,
        })
        await self._db.commit()

    async def get_run(self, run_id: str) -> Optional[KGRunInfo]:
        """Fetch a single run by ID."""
        cursor = await self._db.execute(
            "SELECT run_id, mode, started_at, completed_at, nodes_upserted, "
            "edges_upserted, nodes_tombstoned, edges_expired, status "
            "FROM kg_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return KGRunInfo(
            run_id=row[0], mode=row[1], started_at=row[2],
            completed_at=row[3], nodes_upserted=row[4],
            edges_upserted=row[5], nodes_tombstoned=row[6],
            edges_expired=row[7], status=row[8],
        )

    async def list_runs(self, limit: int = 20) -> List[KGRunInfo]:
        """List recent runs ordered by started_at DESC."""
        cursor = await self._db.execute(
            "SELECT run_id, mode, started_at, completed_at, nodes_upserted, "
            "edges_upserted, nodes_tombstoned, edges_expired, status "
            "FROM kg_runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            KGRunInfo(
                run_id=r[0], mode=r[1], started_at=r[2],
                completed_at=r[3], nodes_upserted=r[4],
                edges_upserted=r[5], nodes_tombstoned=r[6],
                edges_expired=r[7], status=r[8],
            )
            for r in rows
        ]

    # -- run source lifecycle -------------------------------------------------

    async def create_run_source(
        self,
        run_id: str,
        source_name: str,
        refresh_strategy: str = "full_recompute",
        watermark_before: Optional[str] = None,
    ) -> int:
        """Create a run-source lifecycle record.  Returns the row ID."""
        now = self._now()
        cursor = await self._db.execute("""
            INSERT INTO kg_run_sources
                (run_id, source_name, refresh_strategy, status,
                 started_at, watermark_before)
            VALUES (:rid, :src, :strat, 'running', :now, :wb)
        """, {
            "rid": run_id,
            "src": source_name,
            "strat": refresh_strategy,
            "now": now,
            "wb": watermark_before,
        })
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def complete_run_source(
        self,
        source_id: int,
        *,
        status: str = "completed",
        rows_scanned: int = 0,
        rows_written: int = 0,
        duration_ms: Optional[float] = None,
        watermark_after: Optional[str] = None,
        lock_retries: int = 0,
        error_text: Optional[str] = None,
    ) -> None:
        """Update a run-source record on completion or failure."""
        await self._db.execute("""
            UPDATE kg_run_sources
            SET status = :st, completed_at = :now,
                rows_scanned = :rs, rows_written = :rw,
                duration_ms = :dm, watermark_after = :wa,
                lock_retries = :lr, error_text = :et
            WHERE id = :sid
        """, {
            "st": status, "now": self._now(),
            "rs": rows_scanned, "rw": rows_written,
            "dm": duration_ms, "wa": watermark_after,
            "lr": lock_retries, "et": error_text,
            "sid": source_id,
        })
        await self._db.commit()

    async def get_run_sources(self, run_id: str) -> List[KGRunSourceInfo]:
        """Fetch all source records for a run."""
        cursor = await self._db.execute(
            "SELECT id, run_id, source_name, refresh_strategy, status, "
            "started_at, completed_at, watermark_before, watermark_after, "
            "rows_scanned, rows_written, duration_ms, lock_retries, error_text "
            "FROM kg_run_sources WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        rows = await cursor.fetchall()
        return [
            KGRunSourceInfo(
                id=r[0], run_id=r[1], source_name=r[2],
                refresh_strategy=r[3], status=r[4], started_at=r[5],
                completed_at=r[6], watermark_before=r[7],
                watermark_after=r[8], rows_scanned=r[9],
                rows_written=r[10], duration_ms=r[11],
                lock_retries=r[12], error_text=r[13],
            )
            for r in rows
        ]

    # -- node CRUD ------------------------------------------------------------

    async def upsert_node(
        self,
        node: KGNode,
        *,
        run_id: Optional[str] = None,
        conn: Optional["aiosqlite.Connection"] = None,
    ) -> str:
        """Insert or update a node.  Returns the node ID.

        For undirected edge symmetry, callers must ensure that
        seed-based IDs are deterministic.
        """
        db = conn or self._db
        now = self._now()
        node.updated_at = now
        if not node.created_at:
            node.created_at = now

        await db.execute("""
            INSERT INTO kg_nodes
                (id, node_type, label, properties, source_table, source_id,
                 is_tombstone, created_at, updated_at, last_run_id)
            VALUES (:id, :nt, :label, :props, :st, :si,
                    :tomb, :ca, :ua, :lr)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                properties = excluded.properties,
                is_tombstone = excluded.is_tombstone,
                updated_at = excluded.updated_at,
                last_run_id = excluded.last_run_id
        """, {
            "id": node.id,
            "nt": node.node_type,
            "label": node.label,
            "props": self._props_json(node.properties),
            "st": node.source_table,
            "si": node.source_id,
            "tomb": 1 if node.is_tombstone else 0,
            "ca": node.created_at,
            "ua": node.updated_at,
            "lr": run_id or node.last_run_id,
        })
        if conn is None:
            await self._db.commit()
        return node.id

    async def get_node(self, node_id: str) -> Optional[KGNode]:
        """Fetch a single node by ID."""
        cursor = await self._db.execute(
            "SELECT id, node_type, label, properties, source_table, source_id, "
            "is_tombstone, created_at, updated_at, last_run_id "
            "FROM kg_nodes WHERE id = ?",
            (node_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return KGNode(
            id=row[0], node_type=row[1], label=row[2],
            properties=self._parse_props(row[3]),
            source_table=row[4], source_id=row[5],
            is_tombstone=bool(row[6]),
            created_at=row[7], updated_at=row[8],
            last_run_id=row[9],
        )

    async def tombstone_node(
        self,
        node_id: str,
        *,
        run_id: Optional[str] = None,
        conn: Optional["aiosqlite.Connection"] = None,
    ) -> bool:
        """Mark a node as tombstoned.  Returns True if the node existed."""
        db = conn or self._db
        now = self._now()
        cursor = await db.execute("""
            UPDATE kg_nodes SET is_tombstone = 1, updated_at = :now, last_run_id = :lr
            WHERE id = :nid AND is_tombstone = 0
        """, {"now": now, "lr": run_id, "nid": node_id})
        if conn is None:
            await self._db.commit()
        return cursor.rowcount > 0

    async def count_nodes(self, *, node_type: Optional[str] = None, live_only: bool = True) -> int:
        """Count nodes, optionally filtered by type."""
        where_parts = []
        params: Dict[str, Any] = {}
        if live_only:
            where_parts.append("is_tombstone = 0")
        if node_type:
            where_parts.append("node_type = :nt")
            params["nt"] = node_type
        where = " AND ".join(where_parts) if where_parts else "1=1"
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE {where}", params,
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # -- edge CRUD ------------------------------------------------------------

    async def upsert_edge(
        self,
        edge: KGEdge,
        *,
        run_id: Optional[str] = None,
        conn: Optional["aiosqlite.Connection"] = None,
    ) -> str:
        """Insert or update an edge.

        For undirected edges, enforces source_node_id < target_node_id.
        Returns the edge ID.
        """
        db = conn or self._db
        now = self._now()
        edge.updated_at = now
        if not edge.created_at:
            edge.created_at = now

        src, tgt = edge.source_node_id, edge.target_node_id
        is_directed = edge.is_directed
        if edge.edge_type in UNDIRECTED_EDGE_TYPES:
            is_directed = False
            if src > tgt:
                src, tgt = tgt, src

        await db.execute("""
            INSERT INTO kg_edges
                (id, edge_type, source_node_id, target_node_id, weight,
                 properties, valid_from, valid_until, is_directed,
                 last_run_id, created_at, updated_at)
            VALUES (:id, :et, :src, :tgt, :w,
                    :props, :vf, :vu, :dir,
                    :lr, :ca, :ua)
            ON CONFLICT(edge_type, source_node_id, target_node_id)
                WHERE valid_until IS NULL
            DO UPDATE SET
                weight = excluded.weight,
                properties = excluded.properties,
                last_run_id = excluded.last_run_id,
                updated_at = excluded.updated_at
        """, {
            "id": edge.id,
            "et": edge.edge_type,
            "src": src,
            "tgt": tgt,
            "w": edge.weight,
            "props": self._props_json(edge.properties),
            "vf": edge.valid_from,
            "vu": edge.valid_until,
            "dir": 1 if is_directed else 0,
            "lr": run_id or edge.last_run_id,
            "ca": edge.created_at,
            "ua": edge.updated_at,
        })
        if conn is None:
            await self._db.commit()
        return edge.id

    async def get_edge(self, edge_id: str) -> Optional[KGEdge]:
        """Fetch a single edge by ID."""
        cursor = await self._db.execute(
            "SELECT id, edge_type, source_node_id, target_node_id, weight, "
            "properties, valid_from, valid_until, is_directed, "
            "last_run_id, created_at, updated_at "
            "FROM kg_edges WHERE id = ?",
            (edge_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return KGEdge(
            id=row[0], edge_type=row[1],
            source_node_id=row[2], target_node_id=row[3],
            weight=row[4], properties=self._parse_props(row[5]),
            valid_from=row[6], valid_until=row[7],
            is_directed=bool(row[8]),
            last_run_id=row[9], created_at=row[10], updated_at=row[11],
        )

    async def expire_edge(
        self,
        edge_id: str,
        *,
        run_id: Optional[str] = None,
        conn: Optional["aiosqlite.Connection"] = None,
    ) -> bool:
        """Set valid_until on a live edge.  Returns True if the edge existed."""
        db = conn or self._db
        now = self._now()
        cursor = await db.execute("""
            UPDATE kg_edges SET valid_until = :now, updated_at = :now, last_run_id = :lr
            WHERE id = :eid AND valid_until IS NULL
        """, {"now": now, "lr": run_id, "eid": edge_id})
        if conn is None:
            await self._db.commit()
        return cursor.rowcount > 0

    async def count_edges(self, *, edge_type: Optional[str] = None, live_only: bool = True) -> int:
        """Count edges, optionally filtered by type."""
        where_parts = []
        params: Dict[str, Any] = {}
        if live_only:
            where_parts.append("valid_until IS NULL")
        if edge_type:
            where_parts.append("edge_type = :et")
            params["et"] = edge_type
        where = " AND ".join(where_parts) if where_parts else "1=1"
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM kg_edges WHERE {where}", params,
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # -- neighbors + traversal ------------------------------------------------

    async def get_neighbors(
        self,
        node_id: str,
        *,
        edge_type: Optional[str] = None,
        direction: str = "both",
        live_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get neighbor nodes via bidirectional view.

        Args:
            node_id: Starting node.
            edge_type: Filter by edge type (optional).
            direction: 'outgoing', 'incoming', or 'both'.
            live_only: Only live edges (valid_until IS NULL).

        Returns:
            List of dicts with edge_type, neighbor_id, neighbor_label, weight.
        """
        if direction == "both":
            # Use bidirectional view for undirected support
            base = "kg_edges_bidirectional" if live_only else "kg_edges"
            where_parts = ["e.source_node_id = :nid"]
        elif direction == "outgoing":
            base = "kg_edges"
            where_parts = ["e.source_node_id = :nid"]
            if live_only:
                where_parts.append("e.valid_until IS NULL")
        else:  # incoming
            base = "kg_edges"
            where_parts = ["e.target_node_id = :nid"]
            if live_only:
                where_parts.append("e.valid_until IS NULL")

        params: Dict[str, Any] = {"nid": node_id}
        if edge_type:
            where_parts.append("e.edge_type = :et")
            params["et"] = edge_type

        where = " AND ".join(where_parts)

        if direction == "incoming":
            neighbor_col = "e.source_node_id"
        else:
            neighbor_col = "e.target_node_id"

        cursor = await self._db.execute(f"""
            SELECT e.edge_type, {neighbor_col} AS neighbor_id,
                   n.label AS neighbor_label, e.weight
            FROM {base} e
            JOIN kg_nodes n ON n.id = {neighbor_col}
            WHERE {where}
              AND n.is_tombstone = 0
            ORDER BY e.weight DESC, n.label
        """, params)
        rows = await cursor.fetchall()
        return [
            {
                "edge_type": r[0],
                "neighbor_id": r[1],
                "neighbor_label": r[2],
                "weight": r[3],
            }
            for r in rows
        ]

    async def traverse(
        self,
        start_id: str,
        *,
        max_depth: int = 3,
        edge_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Cycle-safe BFS traversal using JSON array visited set.

        Uses kg_edges_bidirectional view for undirected edge support.
        Returns list of {node_id, depth, path} dicts.
        """
        params: Dict[str, Any] = {
            "start_id": start_id,
            "max_depth": max_depth,
        }
        edge_filter = ""
        if edge_type:
            edge_filter = "AND e.edge_type = :et"
            params["et"] = edge_type

        cursor = await self._db.execute(f"""
            WITH RECURSIVE traverse(node_id, depth, visited) AS (
                SELECT :start_id, 0, json_array(:start_id)
                UNION ALL
                SELECT e.target_node_id, t.depth + 1,
                       json_insert(t.visited, '$[#]', e.target_node_id)
                FROM kg_edges_bidirectional e
                JOIN traverse t ON e.source_node_id = t.node_id
                WHERE t.depth < :max_depth
                  {edge_filter}
                  AND NOT EXISTS (
                      SELECT 1 FROM json_each(t.visited) v
                      WHERE v.value = e.target_node_id
                  )
            )
            SELECT t.node_id, t.depth, t.visited, n.label, n.node_type
            FROM traverse t
            JOIN kg_nodes n ON n.id = t.node_id
            WHERE n.is_tombstone = 0
            ORDER BY t.depth, t.node_id
        """, params)
        rows = await cursor.fetchall()
        return [
            {
                "node_id": r[0],
                "depth": r[1],
                "path": json.loads(r[2]) if r[2] else [r[0]],
                "label": r[3],
                "node_type": r[4],
            }
            for r in rows
        ]

    # -- stats ----------------------------------------------------------------

    async def get_stats(self) -> KGStats:
        """Compute aggregate graph statistics."""
        stats = KGStats()

        # Node counts
        cursor = await self._db.execute("SELECT COUNT(*) FROM kg_nodes")
        row = await cursor.fetchone()
        stats.total_nodes = row[0] if row else 0

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE is_tombstone = 0"
        )
        row = await cursor.fetchone()
        stats.live_nodes = row[0] if row else 0
        stats.tombstoned_nodes = stats.total_nodes - stats.live_nodes

        # Edge counts
        cursor = await self._db.execute("SELECT COUNT(*) FROM kg_edges")
        row = await cursor.fetchone()
        stats.total_edges = row[0] if row else 0

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM kg_edges WHERE valid_until IS NULL"
        )
        row = await cursor.fetchone()
        stats.live_edges = row[0] if row else 0
        stats.expired_edges = stats.total_edges - stats.live_edges

        # By-type breakdowns (live only)
        cursor = await self._db.execute(
            "SELECT node_type, COUNT(*) FROM kg_nodes "
            "WHERE is_tombstone = 0 GROUP BY node_type ORDER BY node_type"
        )
        for r in await cursor.fetchall():
            stats.nodes_by_type[r[0]] = r[1]

        cursor = await self._db.execute(
            "SELECT edge_type, COUNT(*) FROM kg_edges "
            "WHERE valid_until IS NULL GROUP BY edge_type ORDER BY edge_type"
        )
        for r in await cursor.fetchall():
            stats.edges_by_type[r[0]] = r[1]

        # Run info
        cursor = await self._db.execute("SELECT COUNT(*) FROM kg_runs")
        row = await cursor.fetchone()
        stats.total_runs = row[0] if row else 0

        cursor = await self._db.execute(
            "SELECT run_id, mode, started_at, completed_at, status, "
            "nodes_upserted, edges_upserted "
            "FROM kg_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            stats.last_run = {
                "run_id": row[0], "mode": row[1], "started_at": row[2],
                "completed_at": row[3], "status": row[4],
                "nodes_upserted": row[5], "edges_upserted": row[6],
            }

        return stats

    # -- validation -----------------------------------------------------------

    async def validate(self, *, fail_fast: bool = False) -> List[ValidationResult]:
        """Run all named validation checks.

        Returns list of ValidationResult.  With fail_fast, stops on first failure.
        """
        checks = [
            self._check_orphan_edges,
            self._check_tombstone_edges,
            self._check_schema_conformance,
            self._check_referential_fk,
            self._check_symmetry,
            self._check_cycle_safety,
            self._check_view_liveness,
        ]
        results: List[ValidationResult] = []
        for check_fn in checks:
            result = await check_fn()
            results.append(result)
            if fail_fast and result.status == "fail":
                break
        return results

    async def _check_orphan_edges(self) -> ValidationResult:
        """Every edge endpoint must exist in kg_nodes."""
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges e
            WHERE NOT EXISTS (SELECT 1 FROM kg_nodes n WHERE n.id = e.source_node_id)
               OR NOT EXISTS (SELECT 1 FROM kg_nodes n WHERE n.id = e.target_node_id)
        """)
        row = await cursor.fetchone()
        count = row[0] if row else 0
        if count > 0:
            return ValidationResult("orphan_edges", "fail", f"{count} orphan edge(s)")
        return ValidationResult("orphan_edges", "pass")

    async def _check_tombstone_edges(self) -> ValidationResult:
        """No live edges should touch tombstoned nodes."""
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges e
            WHERE e.valid_until IS NULL
              AND (
                  EXISTS (SELECT 1 FROM kg_nodes n WHERE n.id = e.source_node_id AND n.is_tombstone = 1)
               OR EXISTS (SELECT 1 FROM kg_nodes n WHERE n.id = e.target_node_id AND n.is_tombstone = 1)
              )
        """)
        row = await cursor.fetchone()
        count = row[0] if row else 0
        if count > 0:
            return ValidationResult("tombstone_edges", "fail", f"{count} live edge(s) touch tombstoned nodes")
        return ValidationResult("tombstone_edges", "pass")

    async def _check_schema_conformance(self) -> ValidationResult:
        """All nodes have valid node_type, all edges have valid edge_type."""
        # CHECK constraints handle this at INSERT time, but verify existing data
        valid_nodes = set(VALID_NODE_TYPES)
        valid_edges = set(VALID_EDGE_TYPES)

        cursor = await self._db.execute(
            "SELECT DISTINCT node_type FROM kg_nodes"
        )
        bad_nodes = [r[0] for r in await cursor.fetchall() if r[0] not in valid_nodes]

        cursor = await self._db.execute(
            "SELECT DISTINCT edge_type FROM kg_edges"
        )
        bad_edges = [r[0] for r in await cursor.fetchall() if r[0] not in valid_edges]

        if bad_nodes or bad_edges:
            details = []
            if bad_nodes:
                details.append(f"invalid node_types: {bad_nodes}")
            if bad_edges:
                details.append(f"invalid edge_types: {bad_edges}")
            return ValidationResult("schema_conformance", "fail", "; ".join(details))
        return ValidationResult("schema_conformance", "pass")

    async def _check_referential_fk(self) -> ValidationResult:
        """All source_node_id/target_node_id resolve to existing nodes."""
        # Same as orphan_edges but named separately per plan contract
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges e
            WHERE NOT EXISTS (SELECT 1 FROM kg_nodes n WHERE n.id = e.source_node_id)
        """)
        row = await cursor.fetchone()
        bad_src = row[0] if row else 0

        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges e
            WHERE NOT EXISTS (SELECT 1 FROM kg_nodes n WHERE n.id = e.target_node_id)
        """)
        row = await cursor.fetchone()
        bad_tgt = row[0] if row else 0

        if bad_src or bad_tgt:
            return ValidationResult(
                "referential_fk", "fail",
                f"{bad_src} bad source_node_id, {bad_tgt} bad target_node_id",
            )
        return ValidationResult("referential_fk", "pass")

    async def _check_symmetry(self) -> ValidationResult:
        """Undirected edges must be stored with source_node_id < target_node_id."""
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges
            WHERE is_directed = 0 AND source_node_id > target_node_id
        """)
        row = await cursor.fetchone()
        count = row[0] if row else 0
        if count > 0:
            return ValidationResult("symmetry", "fail", f"{count} undirected edge(s) with source > target")
        return ValidationResult("symmetry", "pass")

    async def _check_cycle_safety(self) -> ValidationResult:
        """Verify that A->B->A traversal terminates (JSON array prevents cycles)."""
        # Pick up to 5 live nodes and verify traversal completes
        cursor = await self._db.execute(
            "SELECT id FROM kg_nodes WHERE is_tombstone = 0 LIMIT 5"
        )
        nodes = [r[0] for r in await cursor.fetchall()]
        for nid in nodes:
            try:
                result = await self.traverse(nid, max_depth=5)
                # Should always terminate — no infinite loop
                if len(result) > 10000:
                    return ValidationResult(
                        "cycle_safety", "fail",
                        f"Traversal from {nid} returned {len(result)} rows (possible cycle)",
                    )
            except Exception as e:
                return ValidationResult("cycle_safety", "fail", f"Traversal error from {nid}: {e}")
        return ValidationResult("cycle_safety", "pass")

    async def _check_view_liveness(self) -> ValidationResult:
        """Both views return only valid_until IS NULL edges."""
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges
            WHERE valid_until IS NOT NULL
        """)
        row = await cursor.fetchone()
        expired_count = row[0] if row else 0

        if expired_count == 0:
            # No expired edges to test against — vacuously true
            return ValidationResult("view_liveness", "pass", "no expired edges to test")

        # Check undirected view doesn't include expired
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges_undirected e
            JOIN kg_edges raw ON raw.id = e.id
            WHERE raw.valid_until IS NOT NULL
        """)
        row = await cursor.fetchone()
        bad_undirected = row[0] if row else 0

        # Check bidirectional view doesn't include expired
        cursor = await self._db.execute("""
            SELECT COUNT(*) FROM kg_edges_bidirectional e
            JOIN kg_edges raw ON raw.id = e.id
            WHERE raw.valid_until IS NOT NULL
        """)
        row = await cursor.fetchone()
        bad_bidir = row[0] if row else 0

        if bad_undirected or bad_bidir:
            return ValidationResult(
                "view_liveness", "fail",
                f"undirected_view={bad_undirected}, bidirectional_view={bad_bidir} expired edges leaked",
            )
        return ValidationResult("view_liveness", "pass")

    # -- evidence CRUD --------------------------------------------------------

    async def add_edge_evidence(
        self,
        edge_id: str,
        source: str,
        detail: Optional[Dict[str, Any]] = None,
        *,
        conn: Optional["aiosqlite.Connection"] = None,
    ) -> int:
        """Add a supporting evidence row for an edge.  Returns the row ID."""
        db = conn or self._db
        now = self._now()
        cursor = await db.execute("""
            INSERT INTO kg_edge_evidence (edge_id, source, detail, created_at)
            VALUES (:eid, :src, :det, :now)
        """, {
            "eid": edge_id,
            "src": source,
            "det": self._props_json(detail),
            "now": now,
        })
        if conn is None:
            await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # -- provenance -----------------------------------------------------------

    async def log_provenance(
        self,
        run_id: str,
        action: str,
        *,
        node_id: Optional[str] = None,
        edge_id: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        conn: Optional["aiosqlite.Connection"] = None,
    ) -> None:
        """Record a provenance entry for a graph mutation."""
        db = conn or self._db
        now = self._now()
        await db.execute("""
            INSERT INTO kg_provenance (run_id, node_id, edge_id, action, detail, created_at)
            VALUES (:rid, :nid, :eid, :act, :det, :now)
        """, {
            "rid": run_id,
            "nid": node_id,
            "eid": edge_id,
            "act": action,
            "det": self._props_json(detail),
            "now": now,
        })
        if conn is None:
            await self._db.commit()
