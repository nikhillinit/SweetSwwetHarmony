"""
Phase G Entity Resolver for Sprint 2

Groups signals into resolved entity groups using a blocking-first approach
for efficient fuzzy matching.

Architecture:
1. Strong Key Resolution: Lookup entity_aliases, follow migrations
2. Weak Key Resolution: Generate blocking tokens, fuzzy match on candidates
3. Union-Find Merge: Deterministic grouping with lexmin entity_id

Key Design Decisions:
- Blocking tokens constrain fuzzy matching (never scan all entities)
- Cap of 200 candidates per token; if exceeded, require 2-token overlap
- RapidFuzz token_sort_ratio with >= 90% threshold
- Fuzzy aliases expire after 30 days to prevent contamination
- Primary canonical key priority: domain > registry > lexmin

Usage:
    resolver = PhaseGEntityResolver(identity_store)
    groups = await resolver.resolve(pending_signals)

    # Persist identity state
    async with store.transaction_immediate() as tx:
        for group in groups:
            await identity_store.upsert_strong_key_bindings(
                group.strong_keys_to_bind, tx
            )
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

# Import fuzzy matching libraries (Phase G Sprint 2 dependencies)
try:
    from rapidfuzz import fuzz
    from metaphone import doublemetaphone
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    fuzz = None  # type: ignore
    doublemetaphone = None  # type: ignore

if TYPE_CHECKING:
    from storage.signal_store import StoredSignal
    from storage.entity_identity_store import (
        EntityIdentityStore,
        StrongKeyBinding,
        AliasKeyBinding,
        BlockingToken,
        BlockingCandidate,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Legal suffixes to strip from company names
LEGAL_SUFFIXES = frozenset([
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "gmbh", "ag", "sa", "srl", "bv", "nv",
    "pty", "pvt", "private", "public", "holdings", "group"
])

# Fuzzy matching thresholds
FUZZY_THRESHOLD = 90  # Minimum token_sort_ratio score
BLOCKING_CANDIDATE_LIMIT = 200  # Max candidates per blocking token
FUZZY_ALIAS_CONFIDENCE = 0.85  # Confidence for fuzzy-derived aliases
FUZZY_ALIAS_EXPIRY_DAYS = 30  # Days until fuzzy alias expires

# Canonical key priority (higher = more authoritative)
CANONICAL_KEY_PRIORITY: Dict[str, int] = {
    "domain": 100,
    "companies_house": 90,
    "sec_edgar": 85,
    "opencorporates": 80,
    "crunchbase": 75,
    "linkedin": 70,
    "github": 60,
    "product_hunt": 55,
    "name_norm": 10,  # Lowest priority
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ResolvedEntityGroup:
    """
    Result of resolving signals into an entity group.

    Contains the grouped signals and persistence plans for identity state.
    """
    entity_id: str
    primary_canonical_key: str
    signals: List[StoredSignal]

    # Persistence plans (for upsert operations)
    strong_keys_to_bind: List[Tuple[str, str, Optional[int], Optional[str]]] = field(
        default_factory=list
    )  # (strong_key, entity_id, source_signal_id, source_key)

    alias_keys_to_bind: List[Tuple[str, str, str, float, Optional[str], Optional[datetime]]] = field(
        default_factory=list
    )  # (alias_key, entity_id, alias_type, confidence, source, expires_at)

    blocking_tokens_to_bind: List[Tuple[str, str, str, str]] = field(
        default_factory=list
    )  # (blocking_token, token_type, entity_id, alias_key)


@dataclass
class UnionFindNode:
    """Node for Union-Find (Disjoint Set Union) data structure."""
    parent: str
    rank: int = 0


# =============================================================================
# PHASE G ENTITY RESOLVER
# =============================================================================

class PhaseGEntityResolver:
    """
    Resolves signals into entity groups using blocking-first fuzzy matching.

    Stages:
    1. Strong key resolution (deterministic)
    2. Weak key resolution (blocking + fuzzy)
    3. Union-Find grouping (deterministic winner)
    """

    def __init__(self, identity_store: EntityIdentityStore):
        """
        Initialize with an EntityIdentityStore.

        Args:
            identity_store: Store for entity identity lookups and persistence
        """
        self._identity_store = identity_store
        self._uf_nodes: Dict[str, UnionFindNode] = {}

    # =========================================================================
    # MAIN RESOLUTION
    # =========================================================================

    async def resolve(
        self,
        signals: List[StoredSignal]
    ) -> List[ResolvedEntityGroup]:
        """
        Resolve signals into entity groups.

        Args:
            signals: List of StoredSignal to group

        Returns:
            List of ResolvedEntityGroup with persistence plans
        """
        if not signals:
            return []

        if not FUZZY_AVAILABLE:
            logger.warning(
                "rapidfuzz/metaphone not available - falling back to strong key only"
            )
            return await self._resolve_strong_keys_only(signals)

        # Reset Union-Find
        self._uf_nodes.clear()

        # Stage 1: Strong key resolution
        strong_key_map = await self._resolve_strong_keys(signals)

        # Stage 2: Generate blocking tokens for weak matching
        signal_aliases = self._generate_signal_aliases(signals)

        # Stage 3: Fuzzy match using blocking index
        await self._fuzzy_match_signals(signals, signal_aliases, strong_key_map)

        # Stage 4: Build groups from Union-Find
        groups = self._build_groups(signals, strong_key_map, signal_aliases)

        logger.info(
            f"Resolved {len(signals)} signals into {len(groups)} entity groups"
        )

        return groups

    # =========================================================================
    # STAGE 1: STRONG KEY RESOLUTION
    # =========================================================================

    async def _resolve_strong_keys(
        self,
        signals: List[StoredSignal]
    ) -> Dict[str, str]:
        """
        Look up strong keys and return mapping to entity IDs.

        Returns:
            Dict mapping canonical_key -> entity_id
        """
        # Collect all canonical keys
        canonical_keys = [s.canonical_key for s in signals]

        # Look up existing bindings
        strong_map = await self._identity_store.lookup_strong_keys(canonical_keys)

        # For keys without bindings, generate new entity IDs
        for key in canonical_keys:
            if key not in strong_map:
                entity_id = self._identity_store.entity_id_for_seed(key)
                strong_map[key] = entity_id
                # Initialize in Union-Find
                self._uf_find(entity_id)

        # Initialize Union-Find for all entities
        for entity_id in strong_map.values():
            self._uf_find(entity_id)

        return strong_map

    async def _resolve_strong_keys_only(
        self,
        signals: List[StoredSignal]
    ) -> List[ResolvedEntityGroup]:
        """
        Fallback: Group signals by canonical key only (no fuzzy matching).

        Used when rapidfuzz/metaphone are not available.
        """
        strong_map = await self._resolve_strong_keys(signals)

        # Group by canonical key
        by_key: Dict[str, List[StoredSignal]] = defaultdict(list)
        for signal in signals:
            by_key[signal.canonical_key].append(signal)

        groups: List[ResolvedEntityGroup] = []
        for canonical_key, key_signals in by_key.items():
            entity_id = strong_map[canonical_key]

            groups.append(ResolvedEntityGroup(
                entity_id=entity_id,
                primary_canonical_key=canonical_key,
                signals=key_signals,
                strong_keys_to_bind=[(
                    canonical_key,
                    entity_id,
                    key_signals[0].id if key_signals else None,
                    key_signals[0].source_api if key_signals else None
                )]
            ))

        return groups

    # =========================================================================
    # STAGE 2: ALIAS GENERATION
    # =========================================================================

    def _generate_signal_aliases(
        self,
        signals: List[StoredSignal]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Generate alias keys and blocking tokens for each signal.

        Returns:
            Dict mapping signal_id -> {
                "normalized_name": str,
                "alias_keys": List[str],
                "blocking_tokens": List[(token, type)]
            }
        """
        result: Dict[int, Dict[str, Any]] = {}

        for signal in signals:
            if not signal.company_name:
                continue

            # Normalize name
            normalized = self._normalize_name(signal.company_name)
            if not normalized:
                continue

            # Generate alias keys
            alias_keys: List[str] = [f"name_norm:{normalized}"]

            # Add location-based alias if available
            location = self._extract_location(signal)
            if location:
                location_slug = self._slugify(location)
                alias_keys.append(f"name_loc:{normalized}:{location_slug}")

            # Generate blocking tokens
            blocking_tokens = self._generate_blocking_tokens(
                normalized, signal.canonical_key
            )

            result[signal.id] = {
                "normalized_name": normalized,
                "alias_keys": alias_keys,
                "blocking_tokens": blocking_tokens
            }

        return result

    def _normalize_name(self, name: str) -> str:
        """
        Normalize a company name for matching.

        - Lowercase
        - Strip punctuation
        - Collapse whitespace
        - Remove legal suffixes
        """
        if not name:
            return ""

        # Lowercase
        normalized = name.lower()

        # Strip punctuation
        normalized = re.sub(r'[^\w\s]', ' ', normalized)

        # Collapse whitespace
        normalized = ' '.join(normalized.split())

        # Remove legal suffixes
        words = normalized.split()
        while words and words[-1] in LEGAL_SUFFIXES:
            words.pop()

        return ' '.join(words)

    def _slugify(self, text: str) -> str:
        """Convert text to a URL-safe slug."""
        slug = text.lower()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        return slug.strip('-')

    def _extract_location(self, signal: StoredSignal) -> Optional[str]:
        """Extract location from signal's raw_data."""
        raw = signal.raw_data or {}

        # Check common location fields
        for field in ["region", "location", "country", "state", "hq_country", "hq_city"]:
            if field in raw and raw[field]:
                return str(raw[field])

        return None

    def _generate_blocking_tokens(
        self,
        normalized_name: str,
        canonical_key: str
    ) -> List[Tuple[str, str]]:
        """
        Generate blocking tokens for a normalized name.

        Token types:
        - first: First word of name
        - meta: Double Metaphone primary
        - tld3: First 3 chars + TLD (for domain-based keys)
        """
        tokens: List[Tuple[str, str]] = []
        words = normalized_name.split()

        if not words:
            return tokens

        # First word token
        first_word = words[0]
        if len(first_word) >= 2:
            tokens.append((f"tok:first:{first_word}", "first"))

        # Metaphone tokens
        if doublemetaphone:
            try:
                primary, secondary = doublemetaphone(normalized_name)
                if primary:
                    tokens.append((f"tok:meta:{primary}", "meta"))
                if secondary and secondary != primary:
                    tokens.append((f"tok:meta:{secondary}", "meta"))
            except Exception as e:
                logger.debug(f"Metaphone failed for '{normalized_name}': {e}")

        # TLD-based token for domain keys
        if canonical_key.startswith("domain:"):
            domain = canonical_key[7:]  # Strip "domain:"
            parts = domain.split('.')
            if len(parts) >= 2:
                name_part = parts[0][:3] if parts[0] else ""
                tld = parts[-1]
                if name_part and tld:
                    tokens.append((f"tok:tld3:{name_part}-{tld}", "tld3"))

        return tokens

    # =========================================================================
    # STAGE 3: FUZZY MATCHING
    # =========================================================================

    async def _fuzzy_match_signals(
        self,
        signals: List[StoredSignal],
        signal_aliases: Dict[int, Dict[str, Any]],
        strong_key_map: Dict[str, str]
    ) -> None:
        """
        Perform fuzzy matching using blocking index.

        Modifies Union-Find structure to link matching entities.
        """
        # First, check existing alias bindings
        all_alias_keys: List[str] = []
        for alias_info in signal_aliases.values():
            all_alias_keys.extend(alias_info["alias_keys"])

        existing_aliases = await self._identity_store.lookup_alias_keys(all_alias_keys)

        # Link signals that share alias bindings
        for signal in signals:
            if signal.id not in signal_aliases:
                continue

            signal_entity = strong_key_map[signal.canonical_key]
            alias_info = signal_aliases[signal.id]

            for alias_key in alias_info["alias_keys"]:
                if alias_key in existing_aliases:
                    existing_entity = existing_aliases[alias_key]
                    self._uf_union(signal_entity, existing_entity)

        # Now use blocking tokens for fuzzy matching between signals
        await self._fuzzy_match_via_blocking(signals, signal_aliases, strong_key_map)

    async def _fuzzy_match_via_blocking(
        self,
        signals: List[StoredSignal],
        signal_aliases: Dict[int, Dict[str, Any]],
        strong_key_map: Dict[str, str]
    ) -> None:
        """
        Match signals using blocking tokens + fuzzy comparison.

        Only compares signals that share blocking tokens (never scans all).
        """
        # Build inverted index: blocking_token -> list of (signal_id, normalized_name)
        token_index: Dict[str, List[Tuple[int, str]]] = defaultdict(list)

        for signal_id, alias_info in signal_aliases.items():
            normalized = alias_info["normalized_name"]
            for token, _ in alias_info["blocking_tokens"]:
                token_index[token].append((signal_id, normalized))

        # Track which signal pairs we've already compared
        compared: Set[Tuple[int, int]] = set()

        # For each signal, find candidates via blocking and compare
        for signal_id, alias_info in signal_aliases.items():
            normalized = alias_info["normalized_name"]

            # Collect candidates from all blocking tokens
            candidates: Dict[int, int] = defaultdict(int)  # signal_id -> token overlap count

            for token, _ in alias_info["blocking_tokens"]:
                for other_id, other_name in token_index[token]:
                    if other_id != signal_id:
                        candidates[other_id] += 1

            # Check if any token has too many candidates
            max_candidates_per_token = max(
                (len(token_index[t]) for t, _ in alias_info["blocking_tokens"]),
                default=0
            )

            require_two_token_overlap = max_candidates_per_token > BLOCKING_CANDIDATE_LIMIT

            # Filter and compare candidates
            for other_id, overlap_count in candidates.items():
                # Skip if we've already compared this pair
                pair_key = (min(signal_id, other_id), max(signal_id, other_id))
                if pair_key in compared:
                    continue
                compared.add(pair_key)

                # If too many candidates, require 2-token overlap
                if require_two_token_overlap and overlap_count < 2:
                    continue

                # Get other signal's normalized name
                other_alias_info = signal_aliases.get(other_id)
                if not other_alias_info:
                    continue
                other_normalized = other_alias_info["normalized_name"]

                # Fuzzy compare
                score = fuzz.token_sort_ratio(normalized, other_normalized)

                if score >= FUZZY_THRESHOLD:
                    # Find corresponding signals
                    signal_a = next((s for s in signals if s.id == signal_id), None)
                    signal_b = next((s for s in signals if s.id == other_id), None)

                    if signal_a and signal_b:
                        entity_a = strong_key_map[signal_a.canonical_key]
                        entity_b = strong_key_map[signal_b.canonical_key]
                        self._uf_union(entity_a, entity_b)

                        logger.debug(
                            f"Fuzzy match: '{normalized}' <-> '{other_normalized}' "
                            f"(score={score:.1f})"
                        )

    # =========================================================================
    # STAGE 4: BUILD GROUPS
    # =========================================================================

    def _build_groups(
        self,
        signals: List[StoredSignal],
        strong_key_map: Dict[str, str],
        signal_aliases: Dict[int, Dict[str, Any]]
    ) -> List[ResolvedEntityGroup]:
        """
        Build ResolvedEntityGroup objects from Union-Find structure.
        """
        # Group signals by their Union-Find root
        groups_by_root: Dict[str, List[StoredSignal]] = defaultdict(list)
        root_keys: Dict[str, Set[str]] = defaultdict(set)  # root -> canonical keys

        for signal in signals:
            entity_id = strong_key_map[signal.canonical_key]
            root = self._uf_find(entity_id)
            groups_by_root[root].append(signal)
            root_keys[root].add(signal.canonical_key)

        # Build groups
        groups: List[ResolvedEntityGroup] = []
        expiry_time = datetime.now(timezone.utc) + timedelta(days=FUZZY_ALIAS_EXPIRY_DAYS)

        for root_entity_id, group_signals in groups_by_root.items():
            # Select primary canonical key (highest priority)
            canonical_keys = root_keys[root_entity_id]
            primary_key = self._select_primary_key(canonical_keys)

            # Build persistence plans
            strong_bindings: List[Tuple[str, str, Optional[int], Optional[str]]] = []
            alias_bindings: List[Tuple[str, str, str, float, Optional[str], Optional[datetime]]] = []
            blocking_bindings: List[Tuple[str, str, str, str]] = []

            for signal in group_signals:
                # Strong key binding
                strong_bindings.append((
                    signal.canonical_key,
                    root_entity_id,
                    signal.id,
                    signal.source_api
                ))

                # Alias bindings (if we have alias info)
                if signal.id in signal_aliases:
                    alias_info = signal_aliases[signal.id]

                    for alias_key in alias_info["alias_keys"]:
                        alias_type = "name_norm" if alias_key.startswith("name_norm:") else "name_loc"
                        alias_bindings.append((
                            alias_key,
                            root_entity_id,
                            alias_type,
                            FUZZY_ALIAS_CONFIDENCE,
                            signal.source_api,
                            expiry_time
                        ))

                    for token, token_type in alias_info["blocking_tokens"]:
                        # Use the first alias key for blocking index
                        alias_key = alias_info["alias_keys"][0] if alias_info["alias_keys"] else ""
                        blocking_bindings.append((
                            token,
                            token_type,
                            root_entity_id,
                            alias_key
                        ))

            groups.append(ResolvedEntityGroup(
                entity_id=root_entity_id,
                primary_canonical_key=primary_key,
                signals=group_signals,
                strong_keys_to_bind=strong_bindings,
                alias_keys_to_bind=alias_bindings,
                blocking_tokens_to_bind=blocking_bindings
            ))

        return groups

    def _select_primary_key(self, canonical_keys: Set[str]) -> str:
        """
        Select the primary canonical key from a set of keys.

        Priority:
        1. Domain keys (highest)
        2. Registry keys (companies_house, sec_edgar, etc.)
        3. Lexicographically smallest (tie-breaker)
        """
        def key_priority(key: str) -> Tuple[int, str]:
            prefix = key.split(":")[0] if ":" in key else key
            priority = CANONICAL_KEY_PRIORITY.get(prefix, 50)
            return (-priority, key)  # Negative for descending priority

        return min(canonical_keys, key=key_priority)

    # =========================================================================
    # UNION-FIND OPERATIONS
    # =========================================================================

    def _uf_find(self, entity_id: str) -> str:
        """Find the root of an entity (with path compression)."""
        if entity_id not in self._uf_nodes:
            self._uf_nodes[entity_id] = UnionFindNode(parent=entity_id)
            return entity_id

        node = self._uf_nodes[entity_id]
        if node.parent != entity_id:
            # Path compression
            node.parent = self._uf_find(node.parent)

        return node.parent

    def _uf_union(self, entity_a: str, entity_b: str) -> str:
        """
        Union two entities with deterministic winner (lexmin).

        Returns the winner entity_id.
        """
        root_a = self._uf_find(entity_a)
        root_b = self._uf_find(entity_b)

        if root_a == root_b:
            return root_a

        # Deterministic: lexmin wins
        winner = min(root_a, root_b)
        loser = max(root_a, root_b)

        # Union by rank
        node_winner = self._uf_nodes[winner]
        node_loser = self._uf_nodes[loser]

        node_loser.parent = winner
        if node_winner.rank == node_loser.rank:
            node_winner.rank += 1

        return winner

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_merge_stats(self) -> Dict[str, int]:
        """
        Get statistics about merges performed.

        Returns:
            Dict with counts of entities, groups, etc.
        """
        # Count unique roots
        roots: Set[str] = set()
        for entity_id in self._uf_nodes:
            roots.add(self._uf_find(entity_id))

        return {
            "total_entities": len(self._uf_nodes),
            "merged_groups": len(roots),
            "merge_operations": len(self._uf_nodes) - len(roots)
        }
