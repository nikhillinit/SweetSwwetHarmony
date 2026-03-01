"""
Claim Fact Store for Phase G Sprint 2

Provides bi-temporal SCD-2 claim fact storage with authority-based logic.

Key Design Decisions:
- SCD-2 (Slowly Changing Dimension Type 2) for full history tracking
- Authority tiers (1-5) determine which facts supersede others
- Same value re-observed = merge evidence, bump last_observed_at
- Higher authority (lower tier number) supersedes lower authority
- Equal tiers: newer observed_at wins

Bi-Temporal Model:
- valid_from/valid_until: Business time (when fact was true)
- observed_at/last_observed_at: System time (when we learned it)

Usage:
    store = ClaimFactStore(signal_store)

    # Save a new fact (inside transaction_immediate)
    async with signal_store.transaction_immediate() as tx:
        fact_id = await store.save_fact(ClaimFact(
            entity_id="abc123",
            predicate="company_name",
            value_json='"Acme Inc"',
            source_tier=2,
            confidence=0.85,
            valid_from="2024-01-15T00:00:00Z",
            observed_at="2024-06-15T12:00:00Z"
        ), tx)

    # Get current active fact
    fact = await store.get_active_fact("abc123", "company_name")
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite
    from storage.signal_store import SignalStore

# Import SOURCE_AUTHORITY for tier mapping
from utils.merge_policy import SOURCE_AUTHORITY, DEFAULT_AUTHORITY

logger = logging.getLogger(__name__)


# =============================================================================
# TIER MAPPING
# =============================================================================

def authority_to_tier(authority: float) -> int:
    """
    Map authority score (0-1) to tier (1-5).

    Tier 1 = highest authority (>= 0.90)
    Tier 5 = lowest authority (< 0.50)

    Args:
        authority: Authority score from SOURCE_AUTHORITY (0-1)

    Returns:
        Tier number 1-5
    """
    if authority >= 0.90:
        return 1
    elif authority >= 0.80:
        return 2
    elif authority >= 0.65:
        return 3
    elif authority >= 0.50:
        return 4
    else:
        return 5


def source_to_tier(source_key: str) -> int:
    """
    Map source key to authority tier using SOURCE_AUTHORITY.

    Args:
        source_key: Source API key (e.g., "companies_house", "github")

    Returns:
        Tier number 1-5
    """
    authority = SOURCE_AUTHORITY.get(source_key, DEFAULT_AUTHORITY)
    return authority_to_tier(authority)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ClaimFact:
    """A bi-temporal claim fact."""
    entity_id: str
    predicate: str  # 'company_name', 'founding_date', etc.
    value_json: str  # JSON-encoded value

    source_tier: int  # 1 (highest) to 5 (lowest)
    confidence: float  # 0-1

    valid_from: str  # ISO 8601 - when fact became true
    observed_at: str  # ISO 8601 - when we observed it

    # Optional fields
    id: Optional[int] = None
    valid_until: Optional[str] = None  # NULL = currently valid
    last_observed_at: Optional[str] = None
    is_retracted: bool = False
    supporting_signal_ids: Optional[List[int]] = None
    source_canonical_key: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class FactSaveResult:
    """Result of save_fact operation."""
    fact_id: int
    action: str  # 'inserted', 'merged', 'superseded', 'ignored'
    superseded_fact_id: Optional[int] = None
    message: Optional[str] = None


# =============================================================================
# CLAIM FACT STORE
# =============================================================================

class ClaimFactStore:
    """
    Database access layer for bi-temporal claim facts.

    Implements SCD-2 with authority-based supersession logic.
    """

    def __init__(self, signal_store: SignalStore):
        """
        Initialize with a SignalStore instance.

        Args:
            signal_store: The SignalStore providing database access
        """
        self._store = signal_store

    # =========================================================================
    # HASH GENERATION
    # =========================================================================

    @staticmethod
    def claim_hash(
        entity_id: str,
        predicate: str,
        value_json: str,
        valid_from: str
    ) -> str:
        """
        Generate a deterministic hash for a claim fact.

        Used for deduplication and idempotent operations.

        Args:
            entity_id: Entity identifier
            predicate: Claim predicate
            value_json: JSON-encoded value
            valid_from: Valid from timestamp

        Returns:
            32-character hex hash
        """
        content = f"{entity_id}|{predicate}|{value_json}|{valid_from}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]

    # =========================================================================
    # SAVE FACT (SCD-2 LOGIC)
    # =========================================================================

    async def save_fact(
        self,
        fact: ClaimFact,
        tx: aiosqlite.Connection
    ) -> FactSaveResult:
        """
        Save a claim fact with SCD-2 logic.

        Logic:
        1. If same value exists and is active: merge evidence, bump last_observed_at
        2. If different value exists:
           - new_tier > existing_tier (lower authority): IGNORE new fact
           - new_tier < existing_tier (higher authority): SUPERSEDE old, insert new
           - equal tiers: newer observed_at wins
        3. If no existing fact: INSERT new fact

        Args:
            fact: The claim fact to save
            tx: Database connection (inside transaction_immediate)

        Returns:
            FactSaveResult with action taken
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # Look for existing active fact
        cursor = await tx.execute(
            """
            SELECT id, value_json, source_tier, observed_at, last_observed_at,
                   supporting_signal_ids
            FROM claim_facts
            WHERE entity_id = ? AND predicate = ?
              AND valid_until IS NULL AND is_retracted = 0
            ORDER BY source_tier ASC, observed_at DESC
            LIMIT 1
            """,
            (fact.entity_id, fact.predicate)
        )
        existing = await cursor.fetchone()

        if existing:
            existing_id = existing[0]
            existing_value = existing[1]
            existing_tier = existing[2]
            existing_observed = existing[3]
            existing_last_observed = existing[4]
            existing_signals_json = existing[5]

            # Parse existing signals
            existing_signals: List[int] = []
            if existing_signals_json:
                try:
                    existing_signals = json.loads(existing_signals_json)
                except json.JSONDecodeError:
                    existing_signals = []

            # Case 1: Same value - merge evidence
            if self._values_match(existing_value, fact.value_json):
                # Merge supporting signal IDs
                merged_signals = self._merge_signal_ids(
                    existing_signals,
                    fact.supporting_signal_ids or []
                )

                await tx.execute(
                    """
                    UPDATE claim_facts
                    SET last_observed_at = ?,
                        supporting_signal_ids = ?
                    WHERE id = ?
                    """,
                    (now_iso, json.dumps(merged_signals), existing_id)
                )

                return FactSaveResult(
                    fact_id=existing_id,
                    action="merged",
                    message=f"Evidence merged with existing fact {existing_id}"
                )

            # Case 2: Different value - authority comparison
            if fact.source_tier > existing_tier:
                # New fact has lower authority - IGNORE
                return FactSaveResult(
                    fact_id=existing_id,
                    action="ignored",
                    message=f"Ignored: tier {fact.source_tier} < tier {existing_tier}"
                )

            elif fact.source_tier < existing_tier:
                # New fact has higher authority - SUPERSEDE
                return await self._supersede_and_insert(
                    fact, existing_id, tx, now_iso,
                    reason=f"Higher authority (tier {fact.source_tier} > {existing_tier})"
                )

            else:
                # Equal tiers - newer observed_at wins
                if fact.observed_at > existing_observed:
                    return await self._supersede_and_insert(
                        fact, existing_id, tx, now_iso,
                        reason=f"Same tier, newer observation ({fact.observed_at})"
                    )
                else:
                    return FactSaveResult(
                        fact_id=existing_id,
                        action="ignored",
                        message=f"Ignored: older observation at tier {fact.source_tier}"
                    )

        # Case 3: No existing fact - INSERT
        return await self._insert_fact(fact, tx, now_iso)

    async def _supersede_and_insert(
        self,
        fact: ClaimFact,
        existing_id: int,
        tx: aiosqlite.Connection,
        now_iso: str,
        reason: str
    ) -> FactSaveResult:
        """Supersede existing fact and insert new one."""
        # Close existing fact
        await tx.execute(
            """
            UPDATE claim_facts
            SET valid_until = ?
            WHERE id = ?
            """,
            (fact.valid_from, existing_id)
        )

        # Insert new fact
        result = await self._insert_fact(fact, tx, now_iso)
        result.action = "superseded"
        result.superseded_fact_id = existing_id
        result.message = reason

        return result

    async def _insert_fact(
        self,
        fact: ClaimFact,
        tx: aiosqlite.Connection,
        now_iso: str
    ) -> FactSaveResult:
        """Insert a new fact."""
        signals_json = json.dumps(fact.supporting_signal_ids or [])

        cursor = await tx.execute(
            """
            INSERT INTO claim_facts
            (entity_id, predicate, value_json, source_tier, confidence,
             valid_from, valid_until, observed_at, last_observed_at,
             is_retracted, supporting_signal_ids, source_canonical_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.entity_id,
                fact.predicate,
                fact.value_json,
                fact.source_tier,
                fact.confidence,
                fact.valid_from,
                fact.valid_until,
                fact.observed_at,
                fact.last_observed_at or now_iso,
                1 if fact.is_retracted else 0,
                signals_json,
                fact.source_canonical_key,
                now_iso
            )
        )

        fact_id = cursor.lastrowid

        return FactSaveResult(
            fact_id=fact_id,
            action="inserted",
            message=f"Inserted new fact with tier {fact.source_tier}"
        )

    # =========================================================================
    # GET ACTIVE FACT
    # =========================================================================

    async def get_active_fact(
        self,
        entity_id: str,
        predicate: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the current active fact for an entity/predicate.

        Returns the fact with:
        - valid_until IS NULL
        - is_retracted = 0
        - Lowest tier (highest authority)
        - Most recent observed_at (for tie-breaking)

        Args:
            entity_id: Entity identifier
            predicate: Claim predicate

        Returns:
            Fact dict or None if not found
        """
        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        cursor = await db.execute(
            """
            SELECT id, entity_id, predicate, value_json, source_tier, confidence,
                   valid_from, valid_until, observed_at, last_observed_at,
                   is_retracted, supporting_signal_ids, source_canonical_key, created_at
            FROM claim_facts
            WHERE entity_id = ? AND predicate = ?
              AND valid_until IS NULL AND is_retracted = 0
            ORDER BY source_tier ASC, observed_at DESC
            LIMIT 1
            """,
            (entity_id, predicate)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # Parse supporting_signal_ids
        signal_ids: List[int] = []
        if row[11]:
            try:
                signal_ids = json.loads(row[11])
            except json.JSONDecodeError:
                signal_ids = []

        return {
            "id": row[0],
            "entity_id": row[1],
            "predicate": row[2],
            "value_json": row[3],
            "value": json.loads(row[3]) if row[3] else None,
            "source_tier": row[4],
            "confidence": row[5],
            "valid_from": row[6],
            "valid_until": row[7],
            "observed_at": row[8],
            "last_observed_at": row[9],
            "is_retracted": bool(row[10]),
            "supporting_signal_ids": signal_ids,
            "source_canonical_key": row[12],
            "created_at": row[13]
        }

    # =========================================================================
    # HISTORY QUERIES
    # =========================================================================

    async def get_fact_history(
        self,
        entity_id: str,
        predicate: str,
        include_retracted: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get full history of facts for an entity/predicate.

        Args:
            entity_id: Entity identifier
            predicate: Claim predicate
            include_retracted: Whether to include retracted facts

        Returns:
            List of fact dicts, ordered by valid_from DESC
        """
        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        if include_retracted:
            where_clause = "entity_id = ? AND predicate = ?"
            params = [entity_id, predicate]
        else:
            where_clause = "entity_id = ? AND predicate = ? AND is_retracted = 0"
            params = [entity_id, predicate]

        cursor = await db.execute(
            f"""
            SELECT id, entity_id, predicate, value_json, source_tier, confidence,
                   valid_from, valid_until, observed_at, last_observed_at,
                   is_retracted, supporting_signal_ids, source_canonical_key, created_at
            FROM claim_facts
            WHERE {where_clause}
            ORDER BY valid_from DESC
            """,
            params
        )
        rows = await cursor.fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            signal_ids: List[int] = []
            if row[11]:
                try:
                    signal_ids = json.loads(row[11])
                except json.JSONDecodeError:
                    signal_ids = []

            result.append({
                "id": row[0],
                "entity_id": row[1],
                "predicate": row[2],
                "value_json": row[3],
                "value": json.loads(row[3]) if row[3] else None,
                "source_tier": row[4],
                "confidence": row[5],
                "valid_from": row[6],
                "valid_until": row[7],
                "observed_at": row[8],
                "last_observed_at": row[9],
                "is_retracted": bool(row[10]),
                "supporting_signal_ids": signal_ids,
                "source_canonical_key": row[12],
                "created_at": row[13]
            })

        return result

    async def get_fact_at_time(
        self,
        entity_id: str,
        predicate: str,
        point_in_time: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the fact that was valid at a specific point in time.

        Args:
            entity_id: Entity identifier
            predicate: Claim predicate
            point_in_time: ISO 8601 timestamp

        Returns:
            Fact dict or None if no fact was valid at that time
        """
        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        cursor = await db.execute(
            """
            SELECT id, entity_id, predicate, value_json, source_tier, confidence,
                   valid_from, valid_until, observed_at, last_observed_at,
                   is_retracted, supporting_signal_ids, source_canonical_key, created_at
            FROM claim_facts
            WHERE entity_id = ? AND predicate = ?
              AND valid_from <= ?
              AND (valid_until IS NULL OR valid_until > ?)
              AND is_retracted = 0
            ORDER BY source_tier ASC, observed_at DESC
            LIMIT 1
            """,
            (entity_id, predicate, point_in_time, point_in_time)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        signal_ids: List[int] = []
        if row[11]:
            try:
                signal_ids = json.loads(row[11])
            except json.JSONDecodeError:
                signal_ids = []

        return {
            "id": row[0],
            "entity_id": row[1],
            "predicate": row[2],
            "value_json": row[3],
            "value": json.loads(row[3]) if row[3] else None,
            "source_tier": row[4],
            "confidence": row[5],
            "valid_from": row[6],
            "valid_until": row[7],
            "observed_at": row[8],
            "last_observed_at": row[9],
            "is_retracted": bool(row[10]),
            "supporting_signal_ids": signal_ids,
            "source_canonical_key": row[12],
            "created_at": row[13]
        }

    # =========================================================================
    # RETRACTION
    # =========================================================================

    async def retract_fact(
        self,
        fact_id: int,
        tx: aiosqlite.Connection
    ) -> bool:
        """
        Retract a fact (mark as no longer valid).

        Retracted facts are preserved for audit but excluded from queries.

        Args:
            fact_id: ID of fact to retract
            tx: Database connection (inside transaction_immediate)

        Returns:
            True if fact was retracted, False if not found
        """
        cursor = await tx.execute(
            """
            UPDATE claim_facts
            SET is_retracted = 1
            WHERE id = ?
            """,
            (fact_id,)
        )
        return cursor.rowcount > 0

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _values_match(self, value1: str, value2: str) -> bool:
        """
        Compare two JSON values for semantic equality.

        Handles JSON encoding differences (whitespace, key ordering).
        """
        try:
            parsed1 = json.loads(value1)
            parsed2 = json.loads(value2)
            return parsed1 == parsed2
        except json.JSONDecodeError:
            # Fall back to string comparison
            return value1 == value2

    def _merge_signal_ids(
        self,
        existing: List[int],
        new: List[int]
    ) -> List[int]:
        """Merge two lists of signal IDs, preserving order, removing duplicates."""
        seen = set(existing)
        result = list(existing)
        for sig_id in new:
            if sig_id not in seen:
                seen.add(sig_id)
                result.append(sig_id)
        return result


# =============================================================================
# SYNC HELPERS (for monitoring / daily_aggregator)
# =============================================================================

def count_claim_facts_in_range_sync(conn, start_ts: str, end_ts: str) -> int:
    """Count claim facts observed in [start_ts, end_ts) range.

    Sync API for use by daily_aggregator and other monitoring code.
    Keeps all claim_facts SQL inside this allowlisted module.

    Args:
        conn: sqlite3.Connection (synchronous)
        start_ts: ISO timestamp lower bound (inclusive)
        end_ts: ISO timestamp upper bound (exclusive)

    Returns:
        Count of matching rows, or 0 if table doesn't exist.
    """
    exists = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='claim_facts'"
    ).fetchone()
    if not exists:
        return 0

    row = conn.execute(
        "SELECT COUNT(*) FROM claim_facts "
        "WHERE observed_at >= ? AND observed_at < ?",
        (start_ts, end_ts),
    ).fetchone()
    return row[0] if row else 0
