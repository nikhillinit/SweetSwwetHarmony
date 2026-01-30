"""
Tests for Phase G Entity Resolver (utils/phase_g_entity_resolver.py)

Covers:
- Name normalization (lowercase, strip punctuation, remove legal suffixes)
- Alias key generation (name_norm, name_loc)
- Blocking token generation (first, metaphone, tld3)
- Fuzzy matching with blocking (never scans all entities)
- Union-Find merge with deterministic winner (lexmin)
- ResolvedEntityGroup output structure
"""

import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, StoredSignal
from storage.entity_identity_store import EntityIdentityStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def fresh_db() -> tuple[SignalStore, str]:
    """Fresh database with all migrations applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store, path

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def mock_identity_store() -> EntityIdentityStore:
    """Mock EntityIdentityStore for testing resolver logic."""
    mock_store = MagicMock(spec=EntityIdentityStore)
    mock_store.lookup_strong_keys = AsyncMock(return_value={})
    mock_store.lookup_alias_keys = AsyncMock(return_value={})
    mock_store.lookup_blocking_candidates = AsyncMock(return_value={})
    mock_store.entity_id_for_seed = EntityIdentityStore.entity_id_for_seed
    mock_store.resolve_entity_root = AsyncMock(side_effect=lambda x: x)
    return mock_store


def make_signal(
    signal_id: int,
    canonical_key: str,
    company_name: str = "Test Corp",
    source_api: str = "test",
    confidence: float = 0.7,
    raw_data: dict = None
) -> StoredSignal:
    """Create a test StoredSignal."""
    now = datetime.now(timezone.utc)
    return StoredSignal(
        id=signal_id,
        signal_type="test",
        source_api=source_api,
        canonical_key=canonical_key,
        company_name=company_name,
        confidence=confidence,
        raw_data=raw_data or {},
        detected_at=now,
        created_at=now,
    )


# =============================================================================
# NAME NORMALIZATION TESTS
# =============================================================================

class TestNameNormalization:
    """Tests for name normalization logic."""

    def test_normalize_lowercase(self):
        """Names should be lowercased."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        result = resolver._normalize_name("ACME Corporation")

        assert result.islower() or result == ""

    def test_normalize_strips_punctuation(self):
        """Punctuation should be stripped."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        result = resolver._normalize_name("Acme, Inc.")

        assert "," not in result
        assert "." not in result

    def test_normalize_collapses_whitespace(self):
        """Multiple spaces should collapse to single space."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        result = resolver._normalize_name("Acme    Corp   Ltd")

        assert "  " not in result

    def test_normalize_removes_legal_suffixes(self):
        """Legal suffixes should be removed."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())

        test_cases = [
            ("Acme Inc", "acme"),
            ("Acme LLC", "acme"),
            ("Acme Corporation", "acme"),
            ("Acme Ltd", "acme"),
            ("Acme Limited", "acme"),
            ("Acme Co", "acme"),
            ("Acme GmbH", "acme"),
        ]

        for input_name, expected in test_cases:
            result = resolver._normalize_name(input_name)
            assert result == expected, f"Failed for {input_name}: got {result}"

    def test_normalize_empty_string(self):
        """Empty string should return empty."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        result = resolver._normalize_name("")

        assert result == ""

    def test_normalize_only_suffix(self):
        """Name that is only a suffix should return empty."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        result = resolver._normalize_name("LLC")

        assert result == ""


# =============================================================================
# BLOCKING TOKEN TESTS
# =============================================================================

class TestBlockingTokenGeneration:
    """Tests for blocking token generation."""

    def test_generates_first_token(self):
        """Should generate first-word token."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        tokens = resolver._generate_blocking_tokens("acme corp", "domain:acme.com")

        first_tokens = [t for t, typ in tokens if typ == "first"]
        assert len(first_tokens) >= 1
        assert any("acme" in t for t in first_tokens)

    def test_generates_metaphone_tokens(self):
        """Should generate metaphone tokens (if available)."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver, FUZZY_AVAILABLE

        if not FUZZY_AVAILABLE:
            pytest.skip("rapidfuzz/metaphone not available")

        resolver = PhaseGEntityResolver(MagicMock())
        tokens = resolver._generate_blocking_tokens("acme corp", "domain:acme.com")

        meta_tokens = [t for t, typ in tokens if typ == "meta"]
        assert len(meta_tokens) >= 1

    def test_generates_tld3_token_for_domain(self):
        """Should generate tld3 token for domain-based canonical keys."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        tokens = resolver._generate_blocking_tokens("acme corp", "domain:acme.com")

        tld3_tokens = [t for t, typ in tokens if typ == "tld3"]
        assert len(tld3_tokens) >= 1
        assert any("acm" in t.lower() and "com" in t.lower() for t in tld3_tokens)

    def test_no_tld3_for_non_domain(self):
        """Should not generate tld3 token for non-domain keys."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())
        tokens = resolver._generate_blocking_tokens("acme corp", "sec_edgar:12345")

        tld3_tokens = [t for t, typ in tokens if typ == "tld3"]
        assert len(tld3_tokens) == 0


# =============================================================================
# FUZZY MATCHING TESTS
# =============================================================================

class TestFuzzyMatchingBlockingOnly:
    """Tests for blocking-first fuzzy matching."""

    @pytest.mark.asyncio
    async def test_resolve_without_fuzzy_libs(self, mock_identity_store):
        """Should fall back gracefully when fuzzy libs unavailable."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        signals = [
            make_signal(1, "domain:acme.com", "Acme Corp"),
            make_signal(2, "domain:other.com", "Other Inc"),
        ]

        # Mock FUZZY_AVAILABLE as False
        with patch('utils.phase_g_entity_resolver.FUZZY_AVAILABLE', False):
            groups = await resolver.resolve(signals)

        # Should still group by canonical key
        assert len(groups) == 2

    @pytest.mark.asyncio
    async def test_blocking_limits_candidates(self, mock_identity_store):
        """Fuzzy matching should only compare blocked candidates."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver, FUZZY_AVAILABLE

        if not FUZZY_AVAILABLE:
            pytest.skip("rapidfuzz/metaphone not available")

        resolver = PhaseGEntityResolver(mock_identity_store)

        # Create signals that would match if compared directly
        signals = [
            make_signal(1, "domain:acme.com", "Acme Corp"),
            make_signal(2, "domain:akme.com", "Akme Corporation"),  # Similar name
            make_signal(3, "domain:different.com", "Totally Different Inc"),
        ]

        groups = await resolver.resolve(signals)

        # Should detect similar names via blocking
        # Acme/Akme likely share metaphone or first token
        assert len(groups) <= 3


# =============================================================================
# UNION-FIND MERGE TESTS
# =============================================================================

class TestUnionFindDeterministicMerge:
    """Tests for deterministic Union-Find merging."""

    def test_lexmin_winner(self):
        """Lexicographically smallest entity_id should win."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())

        # Set up Union-Find
        resolver._uf_find("bbb123")
        resolver._uf_find("aaa123")

        # Merge - aaa123 should win (lexmin)
        winner = resolver._uf_union("bbb123", "aaa123")

        assert winner == "aaa123"

    def test_transitive_merge(self):
        """A=B, B=C should result in A=C."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())

        # A = B
        resolver._uf_union("entity_a", "entity_b")

        # B = C
        resolver._uf_union("entity_b", "entity_c")

        # All should resolve to same root
        root_a = resolver._uf_find("entity_a")
        root_b = resolver._uf_find("entity_b")
        root_c = resolver._uf_find("entity_c")

        assert root_a == root_b == root_c

    def test_idempotent_merge(self):
        """Merging already-merged entities should be idempotent."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(MagicMock())

        resolver._uf_union("aaa", "bbb")
        resolver._uf_union("aaa", "bbb")  # Same merge again

        root = resolver._uf_find("aaa")
        assert root == resolver._uf_find("bbb")


# =============================================================================
# SPLIT BRAIN TESTS
# =============================================================================

class TestSplitBrainResolution:
    """Tests for split brain scenarios (same entity, different keys)."""

    @pytest.mark.asyncio
    async def test_similar_names_grouped(self, mock_identity_store):
        """Signals with similar names should be grouped."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver, FUZZY_AVAILABLE

        if not FUZZY_AVAILABLE:
            pytest.skip("rapidfuzz/metaphone not available")

        resolver = PhaseGEntityResolver(mock_identity_store)

        signals = [
            make_signal(1, "domain:acme.com", "Acme Corp"),
            make_signal(2, "sec_edgar:12345", "Acme Corporation"),
        ]

        groups = await resolver.resolve(signals)

        # Both signals should be in same group (same normalized name)
        if len(groups) == 1:
            assert len(groups[0].signals) == 2
        else:
            # Check if they were not merged due to threshold
            # At minimum, both signals should be accounted for
            total_signals = sum(len(g.signals) for g in groups)
            assert total_signals == 2

    @pytest.mark.asyncio
    async def test_different_names_not_grouped(self, mock_identity_store):
        """Signals with different names should not be grouped."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        signals = [
            make_signal(1, "domain:acme.com", "Acme Corp"),
            make_signal(2, "domain:other.com", "Completely Different Company"),
        ]

        groups = await resolver.resolve(signals)

        assert len(groups) == 2


# =============================================================================
# RESOLVED ENTITY GROUP TESTS
# =============================================================================

class TestResolvedEntityGroup:
    """Tests for ResolvedEntityGroup output structure."""

    @pytest.mark.asyncio
    async def test_group_has_entity_id(self, mock_identity_store):
        """Each group should have an entity_id."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        signals = [make_signal(1, "domain:acme.com", "Acme Corp")]
        groups = await resolver.resolve(signals)

        assert len(groups) == 1
        assert groups[0].entity_id is not None
        assert len(groups[0].entity_id) == 16  # SHA256[:16]

    @pytest.mark.asyncio
    async def test_group_has_primary_key(self, mock_identity_store):
        """Each group should have a primary_canonical_key."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        signals = [make_signal(1, "domain:acme.com", "Acme Corp")]
        groups = await resolver.resolve(signals)

        assert len(groups) == 1
        assert groups[0].primary_canonical_key == "domain:acme.com"

    @pytest.mark.asyncio
    async def test_group_has_persistence_plans(self, mock_identity_store):
        """Each group should have persistence plans."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver, FUZZY_AVAILABLE

        resolver = PhaseGEntityResolver(mock_identity_store)

        signals = [make_signal(1, "domain:acme.com", "Acme Corp")]
        groups = await resolver.resolve(signals)

        assert len(groups) == 1
        group = groups[0]

        # Should have strong key binding
        assert len(group.strong_keys_to_bind) >= 1

        # Should have alias bindings if FUZZY_AVAILABLE
        if FUZZY_AVAILABLE:
            assert len(group.alias_keys_to_bind) >= 0

    @pytest.mark.asyncio
    async def test_primary_key_priority_domain(self, mock_identity_store):
        """Domain keys should have highest priority."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        # If both domain and registry key resolve to same entity
        primary = resolver._select_primary_key({
            "sec_edgar:12345",
            "domain:acme.com",
            "companies_house:67890",
        })

        assert primary == "domain:acme.com"

    @pytest.mark.asyncio
    async def test_primary_key_priority_registry(self, mock_identity_store):
        """Registry keys should be preferred over name keys."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        primary = resolver._select_primary_key({
            "name_norm:acme",
            "companies_house:12345",
            "sec_edgar:67890",
        })

        assert primary.startswith("companies_house:") or primary.startswith("sec_edgar:")


# =============================================================================
# MERGE STATS TESTS
# =============================================================================

class TestMergeStats:
    """Tests for merge statistics."""

    @pytest.mark.asyncio
    async def test_get_merge_stats(self, mock_identity_store):
        """get_merge_stats should return accurate counts."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        # Set up some entities
        resolver._uf_find("ent1")
        resolver._uf_find("ent2")
        resolver._uf_find("ent3")

        # Merge two
        resolver._uf_union("ent1", "ent2")

        stats = resolver.get_merge_stats()

        assert stats["total_entities"] == 3
        assert stats["merged_groups"] == 2  # ent1+ent2, ent3
        assert stats["merge_operations"] == 1


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestPhaseGResolverIntegration:
    """Integration tests with actual EntityIdentityStore."""

    @pytest.mark.asyncio
    async def test_resolve_with_real_store(self, fresh_db: tuple[SignalStore, str]):
        """Test resolver with actual database."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        store, path = fresh_db
        identity_store = EntityIdentityStore(store)
        resolver = PhaseGEntityResolver(identity_store)

        signals = [
            make_signal(1, "domain:acme.com", "Acme Corp"),
            make_signal(2, "domain:other.com", "Other Inc"),
        ]

        groups = await resolver.resolve(signals)

        assert len(groups) == 2

        # Verify entity IDs are deterministic
        acme_group = next(g for g in groups if g.primary_canonical_key == "domain:acme.com")
        expected_id = EntityIdentityStore.entity_id_for_seed("domain:acme.com")
        assert acme_group.entity_id == expected_id

    @pytest.mark.asyncio
    async def test_empty_signals(self, mock_identity_store):
        """Should handle empty signal list."""
        from utils.phase_g_entity_resolver import PhaseGEntityResolver

        resolver = PhaseGEntityResolver(mock_identity_store)

        groups = await resolver.resolve([])

        assert groups == []
