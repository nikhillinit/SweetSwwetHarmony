"""KGQueryEngine -- composable graph queries using KGStore primitives.

All graph traversal uses get_neighbors() + traverse().
Every query accepts a limit parameter to prevent unbounded scans.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from storage.kg_store import KGNode, KGStore

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class EvidenceChain:
    company_id: str
    company_label: Optional[str]
    signals: List[Dict[str, Any]] = field(default_factory=list)
    evidence_families: List[str] = field(default_factory=list)
    sectors: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    source_count: int = 0
    weighted_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company_id": self.company_id,
            "company_label": self.company_label,
            "signals": self.signals,
            "evidence_families": self.evidence_families,
            "sectors": self.sectors,
            "locations": self.locations,
            "source_count": self.source_count,
            "weighted_score": self.weighted_score,
        }


@dataclass
class ConflictRecord:
    company_id: str
    company_label: Optional[str]
    field_name: str
    values: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company_id": self.company_id,
            "company_label": self.company_label,
            "field": self.field_name,
            "values": self.values,
        }


class KGQueryEngine:
    """Composable graph queries on KGStore primitives."""

    def __init__(self, store: KGStore, db: "aiosqlite.Connection"):
        self._kg = store
        self._db = db

    # -- company listing (uses kg_nodes table) ---------------------------------

    async def _list_company_nodes(self, *, limit: int = 1000) -> List[KGNode]:
        """List live company nodes from signal_etl layer."""
        cursor = await self._db.execute(
            "SELECT id, node_type, label, properties, source_table, source_id, "
            "is_tombstone, created_at, updated_at, last_run_id "
            "FROM kg_nodes WHERE node_type = 'company' AND source_table = 'signal_etl' "
            "AND is_tombstone = 0 LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            KGNode(
                id=r[0], node_type=r[1], label=r[2],
                properties=KGStore._parse_props(r[3]),
                source_table=r[4], source_id=r[5],
                is_tombstone=bool(r[6]),
                created_at=r[7], updated_at=r[8], last_run_id=r[9],
            )
            for r in rows
        ]

    async def _list_nodes_by_type(self, node_type: str, *, limit: int = 500) -> List[KGNode]:
        """List live nodes of a specific type."""
        cursor = await self._db.execute(
            "SELECT id, node_type, label, properties, source_table, source_id, "
            "is_tombstone, created_at, updated_at, last_run_id "
            "FROM kg_nodes WHERE node_type = ? AND is_tombstone = 0 LIMIT ?",
            (node_type, limit),
        )
        rows = await cursor.fetchall()
        return [
            KGNode(
                id=r[0], node_type=r[1], label=r[2],
                properties=KGStore._parse_props(r[3]),
                source_table=r[4], source_id=r[5],
                is_tombstone=bool(r[6]),
                created_at=r[7], updated_at=r[8], last_run_id=r[9],
            )
            for r in rows
        ]

    # -- queries ---------------------------------------------------------------

    async def company_evidence_chain(self, company_id: str) -> Optional[EvidenceChain]:
        """All signals, confidence per source, and weighted score for a company."""
        node = await self._kg.get_node(company_id)
        if not node or node.node_type != "company":
            return None

        chain = EvidenceChain(
            company_id=company_id,
            company_label=node.label,
        )

        # Signals via detected_by edges
        neighbors = await self._kg.get_neighbors(
            company_id, edge_type="detected_by", direction="outgoing"
        )
        sources: Set[str] = set()
        total_weight = 0.0
        for nb in neighbors:
            signal_node = await self._kg.get_node(nb["neighbor_id"])
            if signal_node and signal_node.properties:
                chain.signals.append({
                    "signal_id": nb["neighbor_id"],
                    "signal_type": signal_node.properties.get("signal_type"),
                    "source_api": signal_node.properties.get("source_api"),
                    "confidence": signal_node.properties.get("confidence", 0),
                    "detected_at": signal_node.properties.get("detected_at"),
                })
                sources.add(signal_node.properties.get("source_api", ""))
                total_weight += nb["weight"]

        chain.source_count = len(sources)
        diversity_bonus = max(0, (chain.source_count - 1) * 0.1)
        if chain.signals:
            chain.weighted_score = min(
                1.0, (total_weight / len(chain.signals)) + diversity_bonus
            )

        # Evidence families
        ef_neighbors = await self._kg.get_neighbors(
            company_id, edge_type="has_evidence", direction="outgoing"
        )
        chain.evidence_families = [
            nb["neighbor_label"] for nb in ef_neighbors if nb["neighbor_label"]
        ]

        # Sectors
        sector_neighbors = await self._kg.get_neighbors(
            company_id, edge_type="in_sector", direction="outgoing"
        )
        chain.sectors = [
            nb["neighbor_label"] for nb in sector_neighbors if nb["neighbor_label"]
        ]

        # Locations
        loc_neighbors = await self._kg.get_neighbors(
            company_id, edge_type="located_in", direction="outgoing"
        )
        chain.locations = [
            nb["neighbor_label"] for nb in loc_neighbors if nb["neighbor_label"]
        ]

        return chain

    async def detect_conflicts(self, *, limit: int = 100) -> List[ConflictRecord]:
        """Find companies where sources disagree on stage or sector."""
        conflicts: List[ConflictRecord] = []
        company_nodes = await self._list_company_nodes(limit=limit * 2)

        for node in company_nodes:
            if len(conflicts) >= limit:
                break

            props = node.properties or {}
            claims = props.get("claims", {})

            # Stage conflicts: check if different sources report different stages
            if claims:
                stages: Dict[str, str] = {}
                for source, source_claims in claims.items():
                    if isinstance(source_claims, dict):
                        stage = source_claims.get("stage")
                        if stage:
                            stages[source] = stage

                if len(set(stages.values())) > 1:
                    conflicts.append(ConflictRecord(
                        company_id=node.id,
                        company_label=node.label,
                        field_name="stage",
                        values=stages,
                    ))

            # Sector conflicts: multiple different sectors
            sector_neighbors = await self._kg.get_neighbors(
                node.id, edge_type="in_sector", direction="outgoing"
            )
            if len(sector_neighbors) > 1:
                sector_map: Dict[str, str] = {}
                for nb in sector_neighbors:
                    sector_map[nb["neighbor_id"]] = nb["neighbor_label"] or nb["neighbor_id"]
                if len(set(sector_map.values())) > 1:
                    conflicts.append(ConflictRecord(
                        company_id=node.id,
                        company_label=node.label,
                        field_name="sector",
                        values=sector_map,
                    ))

        return conflicts[:limit]

    async def find_data_gaps(
        self,
        *,
        min_evidence: int = 2,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find companies with fewer than min_evidence unique sources."""
        gaps: List[Dict[str, Any]] = []
        company_nodes = await self._list_company_nodes(limit=limit * 3)

        for node in company_nodes:
            if len(gaps) >= limit:
                break

            neighbors = await self._kg.get_neighbors(
                node.id, edge_type="detected_by", direction="outgoing"
            )

            sources = set()
            for nb in neighbors:
                sig_node = await self._kg.get_node(nb["neighbor_id"])
                if sig_node and sig_node.properties:
                    sources.add(sig_node.properties.get("source_api", ""))

            if len(sources) < min_evidence:
                gaps.append({
                    "company_id": node.id,
                    "company_label": node.label,
                    "source_count": len(sources),
                    "sources": sorted(sources),
                    "signal_count": len(neighbors),
                })

        return gaps[:limit]

    async def sector_cluster(
        self,
        sector_id: str,
        *,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """All companies in a sector with evidence strength."""
        sector_node = await self._kg.get_node(sector_id)
        if not sector_node:
            return []

        neighbors = await self._kg.get_neighbors(
            sector_id, edge_type="in_sector", direction="incoming"
        )

        results: List[Dict[str, Any]] = []
        for nb in neighbors[:limit]:
            evidence = await self._kg.get_neighbors(
                nb["neighbor_id"], edge_type="detected_by", direction="outgoing"
            )
            results.append({
                "company_id": nb["neighbor_id"],
                "company_label": nb["neighbor_label"],
                "signal_count": len(evidence),
                "weight": nb["weight"],
            })

        results.sort(key=lambda x: x["signal_count"], reverse=True)
        return results[:limit]

    async def find_duplicate_candidates(
        self, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Find company pairs sharing locations (founder/investor deferred)."""
        duplicates: List[Dict[str, Any]] = []
        location_nodes = await self._list_nodes_by_type("location", limit=500)

        for loc_node in location_nodes:
            if len(duplicates) >= limit:
                break

            neighbors = await self._kg.get_neighbors(
                loc_node.id, edge_type="located_in", direction="incoming"
            )

            if len(neighbors) < 2:
                continue

            company_ids = [nb["neighbor_id"] for nb in neighbors]
            for i in range(len(company_ids)):
                for j in range(i + 1, len(company_ids)):
                    if len(duplicates) >= limit:
                        break
                    shared = await self._find_shared_edges(
                        company_ids[i], company_ids[j]
                    )
                    if len(shared) >= 2:
                        duplicates.append({
                            "company_a": company_ids[i],
                            "company_b": company_ids[j],
                            "shared_connections": shared,
                            "location": loc_node.label,
                        })

        return duplicates[:limit]

    async def rank_by_evidence_strength(
        self,
        *,
        limit: int = 100,
        min_sources: int = 1,
    ) -> List[Dict[str, Any]]:
        """Rank companies by multi-source evidence strength."""
        company_nodes = await self._list_company_nodes(limit=limit * 3)

        rankings: List[Dict[str, Any]] = []
        for node in company_nodes:
            neighbors = await self._kg.get_neighbors(
                node.id, edge_type="detected_by", direction="outgoing"
            )

            sources: Set[str] = set()
            total_confidence = 0.0
            for nb in neighbors:
                sig_node = await self._kg.get_node(nb["neighbor_id"])
                if sig_node and sig_node.properties:
                    sources.add(sig_node.properties.get("source_api", ""))
                    total_confidence += sig_node.properties.get("confidence", 0)

            if len(sources) < min_sources:
                continue

            avg_confidence = total_confidence / len(neighbors) if neighbors else 0
            diversity_bonus = max(0, (len(sources) - 1) * 0.1)
            strength = min(1.0, avg_confidence + diversity_bonus)

            rankings.append({
                "company_id": node.id,
                "company_label": node.label,
                "source_count": len(sources),
                "signal_count": len(neighbors),
                "avg_confidence": round(avg_confidence, 3),
                "evidence_strength": round(strength, 3),
            })

        rankings.sort(key=lambda x: x["evidence_strength"], reverse=True)
        return rankings[:limit]

    async def ego_graph(
        self,
        node_id: str,
        *,
        depth: int = 2,
    ) -> Dict[str, Any]:
        """JSON-serializable subgraph around a node."""
        traversal = await self._kg.traverse(node_id, max_depth=depth)

        nodes: List[Dict[str, Any]] = []
        seen_nodes: Set[str] = set()
        edges: List[Dict[str, Any]] = []

        for entry in traversal:
            nid = entry["node_id"]
            if nid not in seen_nodes:
                seen_nodes.add(nid)
                node = await self._kg.get_node(nid)
                if node:
                    nodes.append({
                        "id": node.id,
                        "type": node.node_type,
                        "label": node.label,
                        "depth": entry["depth"],
                    })

        for nid in seen_nodes:
            neighbors = await self._kg.get_neighbors(nid, direction="outgoing")
            for nb in neighbors:
                if nb["neighbor_id"] in seen_nodes:
                    edges.append({
                        "source": nid,
                        "target": nb["neighbor_id"],
                        "type": nb["edge_type"],
                        "weight": nb["weight"],
                    })

        return {
            "center": node_id,
            "depth": depth,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    async def founder_network(
        self, founder_id: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """All companies linked to a founder (Phase 3+)."""
        neighbors = await self._kg.get_neighbors(
            founder_id, edge_type="founded_by", direction="incoming"
        )
        return [
            {
                "company_id": nb["neighbor_id"],
                "company_label": nb["neighbor_label"],
                "weight": nb["weight"],
            }
            for nb in neighbors[:limit]
        ]

    async def investor_portfolio(
        self, investor_id: str, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """All companies backed by an investor (Phase 3+)."""
        neighbors = await self._kg.get_neighbors(
            investor_id, edge_type="backed_by", direction="incoming"
        )
        return [
            {
                "company_id": nb["neighbor_id"],
                "company_label": nb["neighbor_label"],
                "weight": nb["weight"],
            }
            for nb in neighbors[:limit]
        ]

    # -- helpers ---------------------------------------------------------------

    async def _find_shared_edges(self, node_a: str, node_b: str) -> List[str]:
        """Find edge types that both nodes share with the same targets."""
        neighbors_a = await self._kg.get_neighbors(node_a, direction="outgoing")
        neighbors_b = await self._kg.get_neighbors(node_b, direction="outgoing")

        targets_a = {(nb["edge_type"], nb["neighbor_id"]) for nb in neighbors_a}
        targets_b = {(nb["edge_type"], nb["neighbor_id"]) for nb in neighbors_b}

        common = targets_a & targets_b
        return [f"{edge_type}:{target_id}" for edge_type, target_id in common]
