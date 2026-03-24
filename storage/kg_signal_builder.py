"""Signal ETL builder for the v50 knowledge graph.

Reads signals + company_files tables and materializes company, signal,
sector, location, and evidence_family nodes with appropriate edges.

Follows the same BuildSourcePayload + build() lifecycle as kg_builder.py.

Two modes:
  - full:  batch backfill, tombstones stale nodes
  - incremental:  watermark-based, additive only

All nodes/edges tagged source_table="signal_etl" to distinguish from
the architecture layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from storage.kg_store import KGEdge, KGNode, KGStore, kg_node_id
from storage.kg_signal_extractors import (
    SOURCE_PRIORITY,
    extract_signal,
    merge_attrs,
)
from verification.evidence_families import get_family

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

SOURCE_TABLE = "signal_etl"


def _etl_edge_id(edge_type: str, source_id: str, target_id: str) -> str:
    """Deterministic edge ID from natural key so rebuilds are idempotent."""
    seed = f"signal_etl|{edge_type}|{source_id}|{target_id}".encode("utf-8")
    return hashlib.sha1(seed).hexdigest()[:16]


@dataclass
class SignalETLPayload:
    """Payload of nodes and edges to write."""
    nodes: List[KGNode] = field(default_factory=list)
    edges: List[KGEdge] = field(default_factory=list)
    evidence: List[tuple] = field(default_factory=list)


@dataclass
class SignalETLReport:
    """Report from a signal ETL run."""
    run_id: str
    mode: str
    status: str
    company_nodes: int = 0
    signal_nodes: int = 0
    location_nodes: int = 0
    detected_by_edges: int = 0
    in_sector_edges: int = 0
    located_in_edges: int = 0
    has_evidence_edges: int = 0
    nodes_tombstoned: int = 0
    edges_expired: int = 0
    signals_scanned: int = 0
    companies_scanned: int = 0
    warnings: List[str] = field(default_factory=list)
    duration_ms: float = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "status": self.status,
            "company_nodes": self.company_nodes,
            "signal_nodes": self.signal_nodes,
            "location_nodes": self.location_nodes,
            "detected_by_edges": self.detected_by_edges,
            "in_sector_edges": self.in_sector_edges,
            "located_in_edges": self.located_in_edges,
            "has_evidence_edges": self.has_evidence_edges,
            "nodes_tombstoned": self.nodes_tombstoned,
            "edges_expired": self.edges_expired,
            "signals_scanned": self.signals_scanned,
            "companies_scanned": self.companies_scanned,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }


class KGSignalBuilder:
    """ETL builder that populates the KG from signals and company_files."""

    def __init__(self, db: "aiosqlite.Connection"):
        self._db = db
        self._kg = KGStore(db)

    async def build(
        self,
        *,
        mode: str = "full",
        dry_run: bool = False,
    ) -> SignalETLReport:
        """Run the signal ETL process.

        Args:
            mode: 'full' (batch backfill, tombstones stale) or 'incremental'
            dry_run: If True, compute counts but don't write
        """
        start = time.monotonic()
        await self._kg.recover_stale_runs()

        if not dry_run:
            run = await self._kg.create_run(mode=mode)
            run_id = run.run_id
        else:
            run_id = "dry_run"

        report = SignalETLReport(run_id=run_id, mode=mode, status="running")

        try:
            # Phase 1: Scan data
            companies = await self._scan_companies()
            signals = await self._scan_signals(mode=mode)
            report.companies_scanned = len(companies)
            report.signals_scanned = len(signals)

            # Phase 2: Build payload
            payload = self._build_payload(companies, signals)
            report.company_nodes = sum(1 for n in payload.nodes if n.node_type == "company")
            report.signal_nodes = sum(1 for n in payload.nodes if n.node_type == "signal")
            report.location_nodes = sum(1 for n in payload.nodes if n.node_type == "location")
            report.detected_by_edges = sum(1 for e in payload.edges if e.edge_type == "detected_by")
            report.in_sector_edges = sum(1 for e in payload.edges if e.edge_type == "in_sector")
            report.located_in_edges = sum(1 for e in payload.edges if e.edge_type == "located_in")
            report.has_evidence_edges = sum(1 for e in payload.edges if e.edge_type == "has_evidence")

            if dry_run:
                report.status = "dry_run"
                report.duration_ms = (time.monotonic() - start) * 1000
                return report

            # Phase 3: Write to KG
            current_node_ids, current_edge_ids = await self._write_payload(
                payload, run_id
            )

            # Phase 4: Tombstone stale (full mode only)
            if mode == "full":
                report.nodes_tombstoned = await self._tombstone_stale(
                    run_id, current_node_ids
                )
                report.edges_expired = await self._expire_stale(
                    run_id, current_edge_ids
                )

            total_nodes = report.company_nodes + report.signal_nodes + report.location_nodes
            total_edges = (
                report.detected_by_edges + report.in_sector_edges
                + report.located_in_edges + report.has_evidence_edges
            )

            await self._kg.complete_run(
                run_id,
                status="completed",
                nodes_upserted=total_nodes,
                edges_upserted=total_edges,
                nodes_tombstoned=report.nodes_tombstoned,
                edges_expired=report.edges_expired,
            )
            report.status = "completed"

        except Exception as exc:
            logger.exception("Signal ETL failed")
            if not dry_run:
                await self._kg.complete_run(run_id, status="failed")
            report.status = "failed"
            report.warnings.append(str(exc))
            raise
        finally:
            report.duration_ms = (time.monotonic() - start) * 1000

        logger.info(
            "Signal ETL complete: run_id=%s companies=%d signals=%d edges=%d",
            run_id, report.company_nodes, report.signal_nodes,
            report.detected_by_edges + report.in_sector_edges
            + report.located_in_edges + report.has_evidence_edges,
        )
        return report

    # -- data scanning ---------------------------------------------------------

    async def _scan_companies(self) -> List[Dict[str, Any]]:
        """Scan company_files table for non-archived companies."""
        cursor = await self._db.execute(
            "SELECT company_id, company_name, canonical_key, status, "
            "source_apis, first_seen_at, last_seen_at, metadata "
            "FROM company_files WHERE status != 'archived'"
        )
        rows = await cursor.fetchall()
        return [
            {
                "company_id": r[0],
                "company_name": r[1],
                "canonical_key": r[2],
                "status": r[3],
                "source_apis": json.loads(r[4]) if r[4] else [],
                "first_seen_at": r[5],
                "last_seen_at": r[6],
                "metadata": json.loads(r[7]) if r[7] else {},
            }
            for r in rows
        ]

    async def _scan_signals(self, *, mode: str = "full") -> List[Dict[str, Any]]:
        """Scan signals table. In incremental mode, only scan after watermark."""
        if mode == "incremental":
            cursor = await self._db.execute(
                "SELECT MAX(source_watermark) FROM kg_runs "
                "WHERE mode = 'incremental' AND status = 'completed'"
            )
            row = await cursor.fetchone()
            watermark = row[0] if row and row[0] else None

            if watermark:
                cursor = await self._db.execute(
                    "SELECT id, signal_type, source_api, canonical_key, "
                    "company_name, confidence, raw_data, detected_at, created_at "
                    "FROM signals WHERE created_at > ? ORDER BY id",
                    (watermark,),
                )
            else:
                cursor = await self._db.execute(
                    "SELECT id, signal_type, source_api, canonical_key, "
                    "company_name, confidence, raw_data, detected_at, created_at "
                    "FROM signals ORDER BY id"
                )
        else:
            cursor = await self._db.execute(
                "SELECT id, signal_type, source_api, canonical_key, "
                "company_name, confidence, raw_data, detected_at, created_at "
                "FROM signals ORDER BY id"
            )

        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "signal_type": r[1],
                "source_api": r[2],
                "canonical_key": r[3],
                "company_name": r[4],
                "confidence": r[5],
                "raw_data": r[6],
                "detected_at": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]

    # -- payload building ------------------------------------------------------

    def _build_payload(
        self,
        companies: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
    ) -> SignalETLPayload:
        """Build the complete node/edge payload."""
        payload = SignalETLPayload()

        # Index companies by canonical_key for signal linkage
        company_by_key: Dict[str, Dict[str, Any]] = {}
        for co in companies:
            company_by_key[co["canonical_key"]] = co

        # Group signals by canonical_key for attribute merging
        signals_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for sig in signals:
            signals_by_key.setdefault(sig["canonical_key"], []).append(sig)

        # Track location nodes to avoid duplicates
        location_nodes: Dict[str, KGNode] = {}

        # --- Company nodes (from company_files) ---
        for co in companies:
            co_signals = signals_by_key.get(co["canonical_key"], [])

            # Extract and merge attributes from all signals
            attrs_list = []
            for sig in co_signals:
                attrs = extract_signal(sig["source_api"], sig["raw_data"])
                attrs_list.append(attrs)

            merged = merge_attrs(attrs_list) if attrs_list else {}

            # Build company node properties
            props: Dict[str, Any] = {
                "canonical_key": co["canonical_key"],
                "source_apis": co["source_apis"],
                "first_seen_at": co["first_seen_at"],
                "last_seen_at": co["last_seen_at"],
                "layer": "signal_etl",
            }
            for key in ("domain", "description", "stage", "employees", "founded_year", "claims"):
                val = merged.get(key)
                if val:
                    props[key] = val

            company_node = KGNode(
                id=co["company_id"],
                node_type="company",
                label=merged.get("company_name") or co["company_name"],
                properties=props,
                source_table=SOURCE_TABLE,
                source_id=co["company_id"],
            )
            payload.nodes.append(company_node)

            # Sector edges
            for sector in merged.get("sectors", []):
                sector_node_id = f"sector:{sector}"
                edge = KGEdge(
                    id=_etl_edge_id("in_sector", co["company_id"], sector_node_id),
                    edge_type="in_sector",
                    source_node_id=co["company_id"],
                    target_node_id=sector_node_id,
                    weight=1.0,
                    properties={"layer": "signal_etl"},
                )
                payload.edges.append(edge)

            # Location edges
            for loc in merged.get("locations", []):
                loc_node_id = kg_node_id("location", loc)
                if loc_node_id not in location_nodes:
                    location_nodes[loc_node_id] = KGNode(
                        id=loc_node_id,
                        node_type="location",
                        label=loc,
                        properties={"layer": "signal_etl"},
                        source_table=SOURCE_TABLE,
                        source_id=loc_node_id,
                    )
                edge = KGEdge(
                    id=_etl_edge_id("located_in", co["company_id"], loc_node_id),
                    edge_type="located_in",
                    source_node_id=co["company_id"],
                    target_node_id=loc_node_id,
                    weight=1.0,
                    properties={"layer": "signal_etl"},
                )
                payload.edges.append(edge)

            # Evidence family edges
            families_seen: Set[str] = set()
            for sig in co_signals:
                family = get_family(sig["signal_type"], sig["source_api"])
                if family not in families_seen:
                    families_seen.add(family)
                    ef_node_id = f"ef:{family}"
                    edge = KGEdge(
                        id=_etl_edge_id("has_evidence", co["company_id"], ef_node_id),
                        edge_type="has_evidence",
                        source_node_id=co["company_id"],
                        target_node_id=ef_node_id,
                        weight=1.0,
                        properties={"layer": "signal_etl", "family": family},
                    )
                    payload.edges.append(edge)

        # --- Signal nodes + detected_by edges ---
        for sig in signals:
            signal_node_id = kg_node_id("signal", str(sig["id"]))

            signal_node = KGNode(
                id=signal_node_id,
                node_type="signal",
                label=f"{sig['signal_type']} ({sig['source_api']})",
                properties={
                    "signal_type": sig["signal_type"],
                    "source_api": sig["source_api"],
                    "confidence": sig["confidence"],
                    "detected_at": sig["detected_at"],
                    "canonical_key": sig["canonical_key"],
                    "layer": "signal_etl",
                },
                source_table=SOURCE_TABLE,
                source_id=f"signal:{sig['id']}",
            )
            payload.nodes.append(signal_node)

            # detected_by edge: company -> signal
            co = company_by_key.get(sig["canonical_key"])
            if co:
                edge = KGEdge(
                    id=_etl_edge_id("detected_by", co["company_id"], signal_node_id),
                    edge_type="detected_by",
                    source_node_id=co["company_id"],
                    target_node_id=signal_node_id,
                    weight=sig["confidence"],
                    properties={
                        "layer": "signal_etl",
                        "signal_type": sig["signal_type"],
                        "source_api": sig["source_api"],
                    },
                )
                payload.edges.append(edge)

        # --- Location nodes ---
        payload.nodes.extend(location_nodes.values())

        return payload

    # -- writing ---------------------------------------------------------------

    async def _write_payload(
        self,
        payload: SignalETLPayload,
        run_id: str,
    ) -> tuple[Set[str], Set[str]]:
        """Write payload to KG. Returns (node_ids, edge_ids) written."""
        node_ids: Set[str] = set()
        edge_ids: Set[str] = set()

        source_id = await self._kg.create_run_source(
            run_id, SOURCE_TABLE, refresh_strategy="full_recompute"
        )

        start = time.monotonic()
        try:
            await self._db.execute("BEGIN")
            try:
                for node in payload.nodes:
                    await self._kg.upsert_node(node, run_id=run_id, conn=self._db)
                    node_ids.add(node.id)

                for edge in payload.edges:
                    await self._kg.upsert_edge(edge, run_id=run_id, conn=self._db)
                    edge_ids.add(edge.id)

                for edge_id, source, detail in payload.evidence:
                    await self._kg.add_edge_evidence(
                        edge_id, source, detail=detail, conn=self._db
                    )

                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise

            duration_ms = (time.monotonic() - start) * 1000
            await self._kg.complete_run_source(
                source_id,
                status="completed",
                rows_scanned=len(payload.nodes) + len(payload.edges),
                rows_written=len(node_ids) + len(edge_ids),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            await self._kg.complete_run_source(
                source_id,
                status="failed",
                rows_scanned=len(payload.nodes) + len(payload.edges),
                error_text=str(exc),
                duration_ms=duration_ms,
            )
            raise

        return node_ids, edge_ids

    async def _tombstone_stale(
        self, run_id: str, current_ids: Set[str]
    ) -> int:
        """Tombstone signal_etl nodes not in current set."""
        existing = await self._kg.list_live_node_ids_by_source(SOURCE_TABLE)
        stale = existing - current_ids
        count = 0
        for node_id in stale:
            if await self._kg.tombstone_node(node_id, run_id=run_id):
                count += 1
        return count

    async def _expire_stale(
        self, run_id: str, current_ids: Set[str]
    ) -> int:
        """Expire signal_etl edges not in current set."""
        cursor = await self._db.execute(
            "SELECT id FROM kg_edges "
            "WHERE valid_until IS NULL "
            "AND json_extract(properties, '$.layer') = ?",
            ("signal_etl",),
        )
        existing = {row[0] for row in await cursor.fetchall()}
        stale = existing - current_ids
        count = 0
        for edge_id in stale:
            if await self._kg.expire_edge(edge_id, run_id=run_id):
                count += 1
        return count

    async def get_etl_status(self) -> Dict[str, Any]:
        """Get current ETL status: counts, last run, source table sizes."""
        cursor = await self._db.execute(
            "SELECT node_type, COUNT(*) FROM kg_nodes "
            "WHERE source_table = ? AND is_tombstone = 0 "
            "GROUP BY node_type",
            (SOURCE_TABLE,),
        )
        node_counts = dict(await cursor.fetchall())

        cursor = await self._db.execute(
            "SELECT edge_type, COUNT(*) FROM kg_edges "
            "WHERE valid_until IS NULL "
            "AND json_extract(properties, '$.layer') = ? "
            "GROUP BY edge_type",
            ("signal_etl",),
        )
        edge_counts = dict(await cursor.fetchall())

        cursor = await self._db.execute(
            "SELECT run_id, mode, started_at, completed_at, status, "
            "nodes_upserted, edges_upserted "
            "FROM kg_runs WHERE run_id IN ("
            "  SELECT run_id FROM kg_run_sources WHERE source_name = ?"
            ") ORDER BY started_at DESC LIMIT 1",
            (SOURCE_TABLE,),
        )
        last_run_row = await cursor.fetchone()
        last_run = None
        if last_run_row:
            last_run = {
                "run_id": last_run_row[0],
                "mode": last_run_row[1],
                "started_at": last_run_row[2],
                "completed_at": last_run_row[3],
                "status": last_run_row[4],
                "nodes_upserted": last_run_row[5],
                "edges_upserted": last_run_row[6],
            }

        cursor = await self._db.execute("SELECT COUNT(*) FROM signals")
        row = await cursor.fetchone()
        total_signals = row[0] if row else 0

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM company_files WHERE status != 'archived'"
        )
        row = await cursor.fetchone()
        total_companies = row[0] if row else 0

        return {
            "node_counts": node_counts,
            "edge_counts": edge_counts,
            "last_run": last_run,
            "source_tables": {
                "signals": total_signals,
                "company_files": total_companies,
            },
        }
