"""
Claim Store for Discovery Engine Knowledge Graph

Provides storage and retrieval for the claim ledger (KG-lite):
- Claim extractions (raw assertions from sources)
- Claims (canonicalized, confidence-weighted statements)
- Claim evidence (many-to-many linking)
- Predicates (controlled vocabulary)

Usage:
    store = ClaimStore(signal_store)

    # Save an extraction
    extraction_id = await store.save_extraction(
        entity_key="domain:acme.ai",
        extractor_name="website_profiler",
        predicate_hint="target_customer",
        raw_text="Enterprise SaaS companies",
        source_snippet="We serve enterprise SaaS companies...",
        source_url="https://acme.ai/about",
        source_signal_id=123,
    )

    # Save a claim with evidence
    claim_id = await store.save_claim(
        entity_key="domain:acme.ai",
        predicate="target_customer",
        value="Enterprise SaaS companies",
        confidence=0.85,
        extraction_ids=[extraction_id],
    )

    # Query claims
    claims = await store.get_claims_for_entity("domain:acme.ai")
    evidence = await store.get_evidence_for_claim(claim_id)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Predicate:
    """A predicate in the controlled vocabulary."""
    name: str
    display_name: str
    data_type: str = "text"  # text, numeric, enum, json
    units: Optional[str] = None
    decay_rate_days: Optional[int] = None
    source_priority_weights: Optional[Dict[str, float]] = None
    description: Optional[str] = None


@dataclass
class ClaimExtraction:
    """A raw extraction from a source."""
    id: int
    entity_key: str
    extractor_name: str
    raw_text: str
    predicate_hint: Optional[str] = None
    extractor_version: Optional[str] = None
    source_snippet: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    source_url: Optional[str] = None
    source_signal_id: Optional[int] = None
    extracted_at: Optional[datetime] = None


@dataclass
class Claim:
    """A canonicalized claim about an entity."""
    id: int
    entity_key: str
    predicate: str
    value: str
    confidence: float = 0.5
    status: str = "active"  # active, stale, conflicting, retracted
    value_type: str = "text"
    value_num: Optional[float] = None
    value_json: Optional[Dict[str, Any]] = None
    status_updated_at: Optional[datetime] = None
    status_reason: Optional[str] = None
    last_supported_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # Enriched fields from current_claims view
    competing_claims: int = 0
    evidence_count: int = 0


@dataclass
class ClaimWithEvidence:
    """A claim with its supporting evidence."""
    claim: Claim
    extractions: List[ClaimExtraction] = field(default_factory=list)

    def explain(self) -> str:
        """Generate a human-readable explanation of why we believe this claim."""
        lines = [
            f"Claim: {self.claim.predicate} = {self.claim.value}",
            f"Confidence: {self.claim.confidence:.0%}",
            f"Status: {self.claim.status}",
            f"Evidence sources: {len(self.extractions)}",
        ]

        for i, ext in enumerate(self.extractions[:3], 1):
            snippet = ext.source_snippet or ext.raw_text[:100]
            lines.append(f"  [{i}] \"{snippet}\"")
            if ext.source_url:
                lines.append(f"      Source: {ext.source_url}")
            if ext.extracted_at:
                lines.append(f"      Retrieved: {ext.extracted_at.isoformat()}")

        if len(self.extractions) > 3:
            lines.append(f"  ... and {len(self.extractions) - 3} more sources")

        return "\n".join(lines)


# =============================================================================
# CLAIM STORE
# =============================================================================

class ClaimStore:
    """
    Storage layer for the claim ledger (knowledge graph backbone).

    Wraps the SignalStore's database connection to provide
    claim-specific operations.
    """

    def __init__(self, signal_store: "SignalStore"):
        """
        Initialize claim store.

        Args:
            signal_store: SignalStore instance (provides DB connection)
        """
        self._store = signal_store

    @property
    def _db(self):
        """Get database connection from signal store."""
        return self._store._db

    # =========================================================================
    # PREDICATES
    # =========================================================================

    async def get_predicate(self, name: str) -> Optional[Predicate]:
        """Get a predicate by name."""
        cursor = await self._db.execute(
            "SELECT * FROM predicates WHERE name = ?",
            (name,)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_predicate(row, cursor.description)

    async def get_all_predicates(self) -> List[Predicate]:
        """Get all predicates."""
        cursor = await self._db.execute("SELECT * FROM predicates ORDER BY name")
        rows = await cursor.fetchall()
        return [self._row_to_predicate(row, cursor.description) for row in rows]

    async def add_predicate(
        self,
        name: str,
        display_name: str,
        data_type: str = "text",
        units: Optional[str] = None,
        decay_rate_days: Optional[int] = None,
        source_priority_weights: Optional[Dict[str, float]] = None,
        description: Optional[str] = None,
    ) -> None:
        """Add a new predicate to the vocabulary."""
        await self._db.execute(
            """
            INSERT OR IGNORE INTO predicates
            (name, display_name, data_type, units, decay_rate_days,
             source_priority_weights, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                display_name,
                data_type,
                units,
                decay_rate_days,
                json.dumps(source_priority_weights) if source_priority_weights else None,
                description,
            )
        )
        await self._db.commit()

    def _row_to_predicate(self, row, description) -> Predicate:
        """Convert a database row to a Predicate."""
        cols = [d[0] for d in description]
        data = dict(zip(cols, row))

        weights = None
        if data.get("source_priority_weights"):
            try:
                weights = json.loads(data["source_priority_weights"])
            except json.JSONDecodeError:
                pass

        return Predicate(
            name=data["name"],
            display_name=data["display_name"],
            data_type=data.get("data_type", "text"),
            units=data.get("units"),
            decay_rate_days=data.get("decay_rate_days"),
            source_priority_weights=weights,
            description=data.get("description"),
        )

    # =========================================================================
    # EXTRACTIONS
    # =========================================================================

    async def save_extraction(
        self,
        entity_key: str,
        extractor_name: str,
        raw_text: str,
        predicate_hint: Optional[str] = None,
        extractor_version: Optional[str] = None,
        source_snippet: Optional[str] = None,
        start_offset: Optional[int] = None,
        end_offset: Optional[int] = None,
        source_url: Optional[str] = None,
        source_signal_id: Optional[int] = None,
    ) -> int:
        """
        Save a raw extraction.

        Args:
            entity_key: Canonical key for the entity
            extractor_name: Name of the extractor (e.g., 'website_profiler')
            raw_text: The extracted text
            predicate_hint: Which predicate this might map to
            extractor_version: Version of the extractor
            source_snippet: Verbatim quote from source
            start_offset: Character offset in source
            end_offset: End character offset
            source_url: URL of the source
            source_signal_id: ID of the source signal

        Returns:
            ID of the created extraction
        """
        cursor = await self._db.execute(
            """
            INSERT INTO claim_extractions
            (entity_key, extractor_name, extractor_version, predicate_hint,
             raw_text, source_snippet, start_offset, end_offset,
             source_url, source_signal_id, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_key,
                extractor_name,
                extractor_version,
                predicate_hint,
                raw_text,
                source_snippet,
                start_offset,
                end_offset,
                source_url,
                source_signal_id,
                datetime.now(timezone.utc).isoformat(),
            )
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_extraction(self, extraction_id: int) -> Optional[ClaimExtraction]:
        """Get an extraction by ID."""
        cursor = await self._db.execute(
            "SELECT * FROM claim_extractions WHERE id = ?",
            (extraction_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_extraction(row, cursor.description)

    async def get_extractions_for_entity(
        self,
        entity_key: str,
        predicate: Optional[str] = None,
    ) -> List[ClaimExtraction]:
        """Get all extractions for an entity."""
        if predicate:
            cursor = await self._db.execute(
                """
                SELECT * FROM claim_extractions
                WHERE entity_key = ? AND predicate_hint = ?
                ORDER BY extracted_at DESC
                """,
                (entity_key, predicate)
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT * FROM claim_extractions
                WHERE entity_key = ?
                ORDER BY extracted_at DESC
                """,
                (entity_key,)
            )

        rows = await cursor.fetchall()
        return [self._row_to_extraction(row, cursor.description) for row in rows]

    def _row_to_extraction(self, row, description) -> ClaimExtraction:
        """Convert a database row to a ClaimExtraction."""
        cols = [d[0] for d in description]
        data = dict(zip(cols, row))

        extracted_at = None
        if data.get("extracted_at"):
            try:
                extracted_at = datetime.fromisoformat(data["extracted_at"])
            except ValueError:
                pass

        return ClaimExtraction(
            id=data["id"],
            entity_key=data["entity_key"],
            extractor_name=data["extractor_name"],
            raw_text=data["raw_text"],
            predicate_hint=data.get("predicate_hint"),
            extractor_version=data.get("extractor_version"),
            source_snippet=data.get("source_snippet"),
            start_offset=data.get("start_offset"),
            end_offset=data.get("end_offset"),
            source_url=data.get("source_url"),
            source_signal_id=data.get("source_signal_id"),
            extracted_at=extracted_at,
        )

    # =========================================================================
    # CLAIMS
    # =========================================================================

    async def save_claim(
        self,
        entity_key: str,
        predicate: str,
        value: str,
        confidence: float = 0.5,
        value_type: str = "text",
        value_num: Optional[float] = None,
        value_json: Optional[Dict[str, Any]] = None,
        extraction_ids: Optional[List[int]] = None,
    ) -> int:
        """
        Save a claim, optionally linking to extractions.

        If a claim with the same entity_key/predicate/value exists,
        updates the confidence and last_supported_at.

        Args:
            entity_key: Canonical key for the entity
            predicate: Predicate name (must exist in predicates table)
            value: The claim value
            confidence: Confidence score (0-1)
            value_type: Type of value (text, numeric, json)
            value_num: Numeric value for numeric predicates
            value_json: JSON value for complex predicates
            extraction_ids: IDs of extractions that support this claim

        Returns:
            ID of the created/updated claim
        """
        now = datetime.now(timezone.utc).isoformat()

        # Try to insert, or update if exists
        cursor = await self._db.execute(
            """
            INSERT INTO claims
            (entity_key, predicate, value, confidence, value_type,
             value_num, value_json, last_supported_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_key, predicate, value) DO UPDATE SET
                confidence = MAX(confidence, excluded.confidence),
                last_supported_at = excluded.last_supported_at
            """,
            (
                entity_key,
                predicate,
                value,
                confidence,
                value_type,
                value_num,
                json.dumps(value_json) if value_json else None,
                now,
                now,
            )
        )
        await self._db.commit()

        # Get the claim ID (either newly inserted or existing)
        cursor = await self._db.execute(
            """
            SELECT id FROM claims
            WHERE entity_key = ? AND predicate = ? AND value = ?
            """,
            (entity_key, predicate, value)
        )
        row = await cursor.fetchone()
        claim_id = row[0]

        # Link extractions as evidence
        if extraction_ids:
            for ext_id in extraction_ids:
                await self._db.execute(
                    """
                    INSERT OR IGNORE INTO claim_evidence
                    (claim_id, extraction_id, evidence_weight)
                    VALUES (?, ?, 1.0)
                    """,
                    (claim_id, ext_id)
                )
            await self._db.commit()

        return claim_id

    async def get_claim(self, claim_id: int) -> Optional[Claim]:
        """Get a claim by ID."""
        cursor = await self._db.execute(
            "SELECT * FROM claims WHERE id = ?",
            (claim_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_claim(row, cursor.description)

    async def get_claims_for_entity(
        self,
        entity_key: str,
        predicate: Optional[str] = None,
        status: str = "active",
    ) -> List[Claim]:
        """Get all claims for an entity."""
        if predicate:
            cursor = await self._db.execute(
                """
                SELECT * FROM claims
                WHERE entity_key = ? AND predicate = ? AND status = ?
                ORDER BY confidence DESC
                """,
                (entity_key, predicate, status)
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT * FROM claims
                WHERE entity_key = ? AND status = ?
                ORDER BY predicate, confidence DESC
                """,
                (entity_key, status)
            )

        rows = await cursor.fetchall()
        return [self._row_to_claim(row, cursor.description) for row in rows]

    async def get_current_claims(
        self,
        entity_key: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 100,
    ) -> List[Claim]:
        """
        Get current (winning) claims from the current_claims view.

        Returns only the highest-confidence claim per entity/predicate.
        """
        query = "SELECT * FROM current_claims WHERE 1=1"
        params = []

        if entity_key:
            query += " AND entity_key = ?"
            params.append(entity_key)

        if predicate:
            query += " AND predicate = ?"
            params.append(predicate)

        query += " ORDER BY entity_key, predicate LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_claim(row, cursor.description) for row in rows]

    async def get_evidence_for_claim(self, claim_id: int) -> List[ClaimExtraction]:
        """Get all extractions that support a claim."""
        cursor = await self._db.execute(
            """
            SELECT e.*, ce.evidence_weight
            FROM claim_extractions e
            JOIN claim_evidence ce ON e.id = ce.extraction_id
            WHERE ce.claim_id = ?
            ORDER BY ce.evidence_weight DESC, e.extracted_at DESC
            """,
            (claim_id,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_extraction(row, cursor.description) for row in rows]

    async def get_claim_with_evidence(self, claim_id: int) -> Optional[ClaimWithEvidence]:
        """Get a claim with its supporting evidence."""
        claim = await self.get_claim(claim_id)
        if not claim:
            return None

        extractions = await self.get_evidence_for_claim(claim_id)
        return ClaimWithEvidence(claim=claim, extractions=extractions)

    async def explain_claim(
        self,
        entity_key: str,
        predicate: str,
    ) -> Optional[str]:
        """
        Answer: "Why do we think {predicate} = X for {entity_key}?"

        Returns a human-readable explanation with evidence.
        """
        claims = await self.get_claims_for_entity(entity_key, predicate)
        if not claims:
            return None

        # Get the winning claim
        claim = claims[0]
        claim_with_evidence = await self.get_claim_with_evidence(claim.id)

        if claim_with_evidence:
            return claim_with_evidence.explain()

        return f"Claim: {predicate} = {claim.value} (confidence: {claim.confidence:.0%})"

    def _row_to_claim(self, row, description) -> Claim:
        """Convert a database row to a Claim."""
        cols = [d[0] for d in description]
        data = dict(zip(cols, row))

        # Parse datetime fields
        def parse_dt(val):
            if val:
                try:
                    return datetime.fromisoformat(val)
                except ValueError:
                    pass
            return None

        # Parse JSON fields
        value_json = None
        if data.get("value_json"):
            try:
                value_json = json.loads(data["value_json"])
            except json.JSONDecodeError:
                pass

        return Claim(
            id=data["id"],
            entity_key=data["entity_key"],
            predicate=data["predicate"],
            value=data["value"],
            confidence=data.get("confidence", 0.5),
            status=data.get("status", "active"),
            value_type=data.get("value_type", "text"),
            value_num=data.get("value_num"),
            value_json=value_json,
            status_updated_at=parse_dt(data.get("status_updated_at")),
            status_reason=data.get("status_reason"),
            last_supported_at=parse_dt(data.get("last_supported_at")),
            created_at=parse_dt(data.get("created_at")),
            competing_claims=data.get("competing_claims", 0),
            evidence_count=data.get("evidence_count", 0),
        )

    # =========================================================================
    # CONFLICT DETECTION
    # =========================================================================

    async def get_conflicts(
        self,
        entity_key: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Find claims that are in conflict (multiple active claims for same predicate).

        Returns list of {entity_key, predicate, claims: [...]}
        """
        query = """
            SELECT entity_key, predicate, COUNT(*) as claim_count
            FROM claims
            WHERE status = 'active'
        """
        params = []

        if entity_key:
            query += " AND entity_key = ?"
            params.append(entity_key)

        query += """
            GROUP BY entity_key, predicate
            HAVING COUNT(*) > 1
            ORDER BY claim_count DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        conflicts = []
        for row in rows:
            ek, pred, count = row
            claims = await self.get_claims_for_entity(ek, pred)
            conflicts.append({
                "entity_key": ek,
                "predicate": pred,
                "claim_count": count,
                "claims": claims,
            })

        return conflicts

    async def mark_conflict(
        self,
        claim_id: int,
        reason: str = "Multiple conflicting values",
    ) -> None:
        """Mark a claim as conflicting."""
        await self._db.execute(
            """
            UPDATE claims
            SET status = 'conflicting',
                status_updated_at = ?,
                status_reason = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), reason, claim_id)
        )
        await self._db.commit()

    async def retract_claim(
        self,
        claim_id: int,
        reason: str,
    ) -> None:
        """Retract a claim (mark as no longer valid)."""
        await self._db.execute(
            """
            UPDATE claims
            SET status = 'retracted',
                status_updated_at = ?,
                status_reason = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), reason, claim_id)
        )
        await self._db.commit()

    # =========================================================================
    # STATS
    # =========================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """Get claim ledger statistics."""
        stats = {}

        # Total claims by status
        cursor = await self._db.execute(
            "SELECT status, COUNT(*) FROM claims GROUP BY status"
        )
        rows = await cursor.fetchall()
        stats["claims_by_status"] = {row[0]: row[1] for row in rows}

        # Total extractions
        cursor = await self._db.execute("SELECT COUNT(*) FROM claim_extractions")
        stats["total_extractions"] = (await cursor.fetchone())[0]

        # Unique entities
        cursor = await self._db.execute(
            "SELECT COUNT(DISTINCT entity_key) FROM claims"
        )
        stats["unique_entities"] = (await cursor.fetchone())[0]

        # Claims by predicate
        cursor = await self._db.execute(
            """
            SELECT predicate, COUNT(*)
            FROM claims
            WHERE status = 'active'
            GROUP BY predicate
            ORDER BY COUNT(*) DESC
            """
        )
        rows = await cursor.fetchall()
        stats["claims_by_predicate"] = {row[0]: row[1] for row in rows}

        # Conflicts count
        cursor = await self._db.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT entity_key, predicate
                FROM claims
                WHERE status = 'active'
                GROUP BY entity_key, predicate
                HAVING COUNT(*) > 1
            )
            """
        )
        stats["conflict_count"] = (await cursor.fetchone())[0]

        return stats
