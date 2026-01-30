"""
Entity Identity Store for Phase G Sprint 2

Provides database access for stable entity identity state:
- Strong key lookups (domain, registry IDs)
- Weak alias lookups (name variants, fuzzy-derived)
- Blocking index for fuzzy candidate retrieval
- Entity migrations for merge tracking

Key Design Decisions:
- Deterministic entity IDs: sha256[:16] of seed key
- Deterministic merge winner: lexicographically smallest entity_id
- ACTIVE alias = archived_at IS NULL AND (expires_at IS NULL OR expires_at > now)
- All writes use BEGIN IMMEDIATE transactions

Usage:
    store = EntityIdentityStore(signal_store)
    await store.initialize()

    # Lookup existing
    strong_map = await store.lookup_strong_keys(["domain:acme.ai"])
    alias_map = await store.lookup_alias_keys(["name_norm:acme"])

    # Blocking candidates for fuzzy matching
    candidates = await store.lookup_blocking_candidates([
        ("tok:first:acme", "first"),
        ("tok:meta:AKM", "meta")
    ])

    # Persistence (inside transaction_immediate)
    async with signal_store.transaction_immediate() as tx:
        await store.upsert_strong_key_bindings(bindings, tx)
        await store.upsert_alias_bindings(aliases, tx)
        await store.upsert_blocking_tokens(tokens, tx)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BlockingCandidate:
    """A candidate entity found via blocking index lookup."""
    entity_id: str
    alias_key: str
    blocking_token: str
    token_type: str


@dataclass
class StrongKeyBinding:
    """Binding of a strong key to an entity."""
    strong_key: str
    entity_id: str
    source_signal_id: Optional[int] = None
    source_key: Optional[str] = None


@dataclass
class AliasKeyBinding:
    """Binding of a weak/alias key to an entity."""
    alias_key: str
    entity_id: str
    alias_type: str  # 'name_norm', 'name_loc', 'fuzzy_derived'
    confidence: float = 0.8
    source: Optional[str] = None
    expires_at: Optional[datetime] = None  # Fuzzy aliases should expire


@dataclass
class BlockingToken:
    """A blocking token for fuzzy candidate retrieval."""
    blocking_token: str
    token_type: str  # 'first', 'meta', 'tld3', 'trigram'
    entity_id: str
    alias_key: str


# =============================================================================
# ENTITY IDENTITY STORE
# =============================================================================

class EntityIdentityStore:
    """
    Database access layer for entity identity resolution.

    Manages stable entity IDs, strong key bindings, weak alias bindings,
    and blocking index for fuzzy matching.
    """

    def __init__(self, signal_store: SignalStore):
        """
        Initialize with a SignalStore instance.

        Args:
            signal_store: The SignalStore providing database access
        """
        self._store = signal_store

    # =========================================================================
    # DETERMINISTIC ID GENERATION
    # =========================================================================

    @staticmethod
    def entity_id_for_seed(seed_key: str) -> str:
        """
        Generate a deterministic entity ID from a seed key.

        Uses SHA256[:16] for a compact but collision-resistant ID.

        Args:
            seed_key: The seed key (e.g., "domain:acme.ai")

        Returns:
            16-character hex string entity ID
        """
        return hashlib.sha256(seed_key.encode('utf-8')).hexdigest()[:16]

    # =========================================================================
    # ENTITY ROOT RESOLUTION
    # =========================================================================

    async def resolve_entity_root(
        self,
        entity_id: str,
        max_hops: int = 10
    ) -> str:
        """
        Follow entity migrations to find the root entity ID.

        Handles transitive merges: A->B, B->C => A resolves to C.

        Args:
            entity_id: Starting entity ID
            max_hops: Maximum migration chain length (prevents infinite loops)

        Returns:
            The root entity ID after following all migrations
        """
        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        current_id = entity_id
        visited: Set[str] = set()

        for _ in range(max_hops):
            if current_id in visited:
                # Cycle detected - shouldn't happen with proper merging
                logger.warning(f"Migration cycle detected at entity {current_id}")
                break
            visited.add(current_id)

            cursor = await db.execute(
                """
                SELECT to_entity_id
                FROM entity_migrations
                WHERE from_entity_id = ?
                ORDER BY merged_at DESC
                LIMIT 1
                """,
                (current_id,)
            )
            row = await cursor.fetchone()

            if not row:
                # No more migrations - this is the root
                break

            current_id = row[0]

        return current_id

    # =========================================================================
    # LOOKUPS (READ-ONLY)
    # =========================================================================

    async def lookup_strong_keys(
        self,
        strong_keys: List[str]
    ) -> Dict[str, str]:
        """
        Look up strong keys to find their root entity IDs.

        Strong keys are authoritative identifiers like domains or registry IDs.
        Uses entity_aliases table from migration 19.

        Args:
            strong_keys: List of strong keys to look up

        Returns:
            Dict mapping strong_key -> root_entity_id (for found keys)
        """
        if not strong_keys:
            return {}

        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        # Batch lookup
        placeholders = ",".join("?" * len(strong_keys))
        cursor = await db.execute(
            f"""
            SELECT strong_key, entity_id
            FROM entity_aliases
            WHERE strong_key IN ({placeholders})
            """,
            strong_keys
        )
        rows = await cursor.fetchall()

        # Resolve each to root
        result: Dict[str, str] = {}
        for strong_key, entity_id in rows:
            root_id = await self.resolve_entity_root(entity_id)
            result[strong_key] = root_id

        return result

    async def lookup_alias_keys(
        self,
        alias_keys: List[str]
    ) -> Dict[str, str]:
        """
        Look up weak/alias keys to find their root entity IDs.

        Only returns ACTIVE aliases:
        - archived_at IS NULL
        - expires_at IS NULL OR expires_at > now

        Args:
            alias_keys: List of alias keys to look up

        Returns:
            Dict mapping alias_key -> root_entity_id (for found ACTIVE keys)
        """
        if not alias_keys:
            return {}

        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        now_iso = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(alias_keys))

        cursor = await db.execute(
            f"""
            SELECT alias_key, entity_id
            FROM entity_key_aliases
            WHERE alias_key IN ({placeholders})
              AND archived_at IS NULL
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            [*alias_keys, now_iso]
        )
        rows = await cursor.fetchall()

        # Resolve each to root
        result: Dict[str, str] = {}
        for alias_key, entity_id in rows:
            root_id = await self.resolve_entity_root(entity_id)
            result[alias_key] = root_id

        return result

    async def lookup_blocking_candidates(
        self,
        tokens: List[Tuple[str, str]],
        limit: int = 200
    ) -> Dict[Tuple[str, str], List[BlockingCandidate]]:
        """
        Look up blocking candidates for a set of tokens.

        Used to constrain fuzzy matching to only entities that share
        blocking tokens.

        Args:
            tokens: List of (blocking_token, token_type) tuples
            limit: Maximum candidates per token (default 200)

        Returns:
            Dict mapping (token, type) -> List[BlockingCandidate]
        """
        if not tokens:
            return {}

        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        result: Dict[Tuple[str, str], List[BlockingCandidate]] = {}

        for blocking_token, token_type in tokens:
            cursor = await db.execute(
                """
                SELECT entity_id, alias_key, blocking_token, token_type
                FROM entity_blocking_index
                WHERE blocking_token = ? AND token_type = ?
                LIMIT ?
                """,
                (blocking_token, token_type, limit)
            )
            rows = await cursor.fetchall()

            candidates = [
                BlockingCandidate(
                    entity_id=row[0],
                    alias_key=row[1],
                    blocking_token=row[2],
                    token_type=row[3]
                )
                for row in rows
            ]
            result[(blocking_token, token_type)] = candidates

        return result

    # =========================================================================
    # WRITES (INSIDE TRANSACTION_IMMEDIATE)
    # =========================================================================

    async def upsert_strong_key_bindings(
        self,
        bindings: List[StrongKeyBinding],
        tx: aiosqlite.Connection
    ) -> List[Tuple[str, str]]:
        """
        Upsert strong key bindings, handling collisions via entity merge.

        If a strong key is already bound to a different entity, we merge
        the entities using deterministic winner (lexmin entity_id).

        Args:
            bindings: List of StrongKeyBinding to upsert
            tx: Database connection (inside transaction_immediate)

        Returns:
            List of (from_entity_id, to_entity_id) merges that occurred
        """
        merges: List[Tuple[str, str]] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for binding in bindings:
            # Check if key already exists
            cursor = await tx.execute(
                "SELECT entity_id FROM entity_aliases WHERE strong_key = ?",
                (binding.strong_key,)
            )
            existing = await cursor.fetchone()

            if existing:
                existing_id = existing[0]
                existing_root = await self.resolve_entity_root(existing_id)
                new_root = await self.resolve_entity_root(binding.entity_id)

                if existing_root != new_root:
                    # Collision! Merge entities
                    await self._merge_entities_internal(
                        existing_root, new_root,
                        f"strong_key_collision:{binding.strong_key}",
                        tx
                    )
                    # Determine winner
                    winner = min(existing_root, new_root)
                    loser = max(existing_root, new_root)
                    merges.append((loser, winner))
                # Key already bound - no need to update
            else:
                # New binding
                await tx.execute(
                    """
                    INSERT INTO entity_aliases
                    (strong_key, entity_id, created_at, source_signal_id, source_key)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        binding.strong_key,
                        binding.entity_id,
                        now_iso,
                        binding.source_signal_id,
                        binding.source_key
                    )
                )

        return merges

    async def upsert_alias_bindings(
        self,
        aliases: List[AliasKeyBinding],
        tx: aiosqlite.Connection
    ) -> List[Tuple[str, str]]:
        """
        Upsert alias key bindings, handling collisions via entity merge.

        If an alias key is already bound to a different entity, we merge
        the entities using deterministic winner (lexmin entity_id).

        Args:
            aliases: List of AliasKeyBinding to upsert
            tx: Database connection (inside transaction_immediate)

        Returns:
            List of (from_entity_id, to_entity_id) merges that occurred
        """
        merges: List[Tuple[str, str]] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for alias in aliases:
            # Check if alias already exists and is active
            cursor = await tx.execute(
                """
                SELECT entity_id
                FROM entity_key_aliases
                WHERE alias_key = ?
                  AND archived_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (alias.alias_key, now_iso)
            )
            existing = await cursor.fetchone()

            if existing:
                existing_id = existing[0]
                existing_root = await self.resolve_entity_root(existing_id)
                new_root = await self.resolve_entity_root(alias.entity_id)

                if existing_root != new_root:
                    # Collision! Merge entities
                    await self._merge_entities_internal(
                        existing_root, new_root,
                        f"alias_key_collision:{alias.alias_key}",
                        tx
                    )
                    winner = min(existing_root, new_root)
                    loser = max(existing_root, new_root)
                    merges.append((loser, winner))
                # Alias already bound - no need to update
            else:
                # Archive any existing (expired or different entity)
                await tx.execute(
                    """
                    UPDATE entity_key_aliases
                    SET archived_at = ?
                    WHERE alias_key = ? AND archived_at IS NULL
                    """,
                    (now_iso, alias.alias_key)
                )

                # Insert new alias
                expires_iso = alias.expires_at.isoformat() if alias.expires_at else None
                await tx.execute(
                    """
                    INSERT INTO entity_key_aliases
                    (alias_key, entity_id, alias_type, confidence, source, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alias.alias_key,
                        alias.entity_id,
                        alias.alias_type,
                        alias.confidence,
                        alias.source,
                        expires_iso,
                        now_iso
                    )
                )

        return merges

    async def upsert_blocking_tokens(
        self,
        tokens: List[BlockingToken],
        tx: aiosqlite.Connection
    ) -> None:
        """
        Upsert blocking tokens for entity.

        Uses INSERT OR REPLACE since primary key includes all fields.

        Args:
            tokens: List of BlockingToken to upsert
            tx: Database connection (inside transaction_immediate)
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        for token in tokens:
            await tx.execute(
                """
                INSERT OR REPLACE INTO entity_blocking_index
                (blocking_token, token_type, entity_id, alias_key, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    token.blocking_token,
                    token.token_type,
                    token.entity_id,
                    token.alias_key,
                    now_iso
                )
            )

    async def merge_entities(
        self,
        from_entity_id: str,
        to_entity_id: str,
        reason: str,
        tx: aiosqlite.Connection
    ) -> str:
        """
        Merge two entities using deterministic winner.

        The lexicographically smallest entity_id always wins.

        Args:
            from_entity_id: One entity in the merge
            to_entity_id: Other entity in the merge
            reason: Why the merge is happening
            tx: Database connection (inside transaction_immediate)

        Returns:
            The winning entity_id
        """
        return await self._merge_entities_internal(
            from_entity_id, to_entity_id, reason, tx
        )

    async def _merge_entities_internal(
        self,
        entity_a: str,
        entity_b: str,
        reason: str,
        tx: aiosqlite.Connection
    ) -> str:
        """
        Internal merge implementation with deterministic winner.

        Always merges larger ID into smaller ID (lexmin wins).

        Args:
            entity_a: First entity
            entity_b: Second entity
            reason: Merge reason
            tx: Database connection

        Returns:
            The winning entity_id
        """
        # Resolve to roots first
        root_a = await self.resolve_entity_root(entity_a)
        root_b = await self.resolve_entity_root(entity_b)

        if root_a == root_b:
            # Already same entity
            return root_a

        # Deterministic winner: lexmin
        winner = min(root_a, root_b)
        loser = max(root_a, root_b)

        now_iso = datetime.now(timezone.utc).isoformat()

        # Record migration
        await tx.execute(
            """
            INSERT OR IGNORE INTO entity_migrations
            (from_entity_id, to_entity_id, merged_at, merge_reason)
            VALUES (?, ?, ?, ?)
            """,
            (loser, winner, now_iso, reason)
        )

        logger.info(f"Entity merge: {loser} -> {winner} (reason: {reason})")

        return winner

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def get_entity_aliases(
        self,
        entity_id: str,
        include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get all aliases (strong and weak) for an entity.

        Args:
            entity_id: Entity to look up
            include_archived: Whether to include archived aliases

        Returns:
            List of alias info dicts
        """
        db = self._store._db
        if not db:
            raise RuntimeError("Database not initialized")

        root_id = await self.resolve_entity_root(entity_id)
        result: List[Dict[str, Any]] = []

        # Strong keys from entity_aliases
        cursor = await db.execute(
            """
            SELECT strong_key, created_at, source_signal_id, source_key
            FROM entity_aliases
            WHERE entity_id = ?
            """,
            (root_id,)
        )
        for row in await cursor.fetchall():
            result.append({
                "key": row[0],
                "type": "strong",
                "created_at": row[1],
                "source_signal_id": row[2],
                "source_key": row[3]
            })

        # Weak aliases from entity_key_aliases
        if include_archived:
            where_clause = "entity_id = ?"
            params = [root_id]
        else:
            now_iso = datetime.now(timezone.utc).isoformat()
            where_clause = """
                entity_id = ?
                AND archived_at IS NULL
                AND (expires_at IS NULL OR expires_at > ?)
            """
            params = [root_id, now_iso]

        cursor = await db.execute(
            f"""
            SELECT alias_key, alias_type, confidence, source, expires_at, archived_at
            FROM entity_key_aliases
            WHERE {where_clause}
            """,
            params
        )
        for row in await cursor.fetchall():
            result.append({
                "key": row[0],
                "type": row[1],
                "confidence": row[2],
                "source": row[3],
                "expires_at": row[4],
                "archived_at": row[5]
            })

        return result
