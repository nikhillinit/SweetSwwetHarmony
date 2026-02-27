"""
PR9: Canonical foundation + no-regress tests

Tests cover:
- select_strongest_candidate() — picks best key from a list using strength scoring
- _extract_canonical_key() — updated to use strength-based selection
- DOMAIN_SOURCE_PRIORITY — ordering invariant for DNS Phase 2 foundation
- Priority invariant: promoted > article > dns_probe domain sources
"""

import pytest
from unittest.mock import MagicMock


# =============================================================================
# TEST: select_strongest_candidate
# =============================================================================

class TestSelectStrongestCandidate:
    """Tests for select_strongest_candidate function."""

    def test_picks_domain_over_name_loc(self):
        """Domain key should win over name_loc."""
        from utils.canonical_keys import select_strongest_candidate
        candidates = ["name_loc:acme", "domain:acme.ai"]
        assert select_strongest_candidate(candidates) == "domain:acme.ai"

    def test_picks_domain_over_github(self):
        """Domain key should win over github_org."""
        from utils.canonical_keys import select_strongest_candidate
        candidates = ["github_org:acme", "domain:acme.ai"]
        assert select_strongest_candidate(candidates) == "domain:acme.ai"

    def test_picks_companies_house_over_github(self):
        """Companies house should win over github_org."""
        from utils.canonical_keys import select_strongest_candidate
        candidates = ["github_org:acme", "companies_house:12345678"]
        assert select_strongest_candidate(candidates) == "companies_house:12345678"

    def test_picks_domain_over_everything(self):
        """Domain should win over all other key types."""
        from utils.canonical_keys import select_strongest_candidate
        candidates = [
            "name_loc:acme",
            "github_repo:acme/product",
            "github_org:acme",
            "crunchbase:acme-cb",
            "companies_house:12345678",
            "domain:acme.ai",
        ]
        assert select_strongest_candidate(candidates) == "domain:acme.ai"

    def test_preserves_order_on_tie(self):
        """When scores tie, first candidate wins (caller's priority order)."""
        from utils.canonical_keys import select_strongest_candidate
        # Two unknown prefixes both score 0 — tiebreaker is input order
        candidates = ["custom_a:acme", "custom_b:beta"]
        assert select_strongest_candidate(candidates) == "custom_a:acme"
        # Reversed order — custom_b should win
        candidates_rev = ["custom_b:beta", "custom_a:acme"]
        assert select_strongest_candidate(candidates_rev) == "custom_b:beta"

    def test_single_candidate_returned(self):
        """Single candidate should be returned as-is."""
        from utils.canonical_keys import select_strongest_candidate
        assert select_strongest_candidate(["name_loc:acme"]) == "name_loc:acme"

    def test_empty_list_returns_empty(self):
        """Empty candidate list should return empty string."""
        from utils.canonical_keys import select_strongest_candidate
        assert select_strongest_candidate([]) == ""

    def test_unknown_prefix_loses_to_known(self):
        """Unknown prefix (score 0) should lose to any known prefix."""
        from utils.canonical_keys import select_strongest_candidate
        candidates = ["unknown:xyz", "name_loc:acme"]
        assert select_strongest_candidate(candidates) == "name_loc:acme"

    def test_all_unknown_returns_first(self):
        """If all candidates have unknown prefixes, first wins."""
        from utils.canonical_keys import select_strongest_candidate
        candidates = ["foo:bar", "baz:qux"]
        assert select_strongest_candidate(candidates) == "foo:bar"

    def test_full_priority_chain(self):
        """Verify the full priority chain: domain > ch > cb > pb > org > repo > name_loc."""
        from utils.canonical_keys import select_strongest_candidate, get_key_strength_score
        ordered = [
            "domain:x",
            "companies_house:x",
            "crunchbase:x",
            "pitchbook:x",
            "github_org:x",
            "github_repo:x",
            "name_loc:x",
        ]
        # Each key should beat everything below it
        for i, stronger in enumerate(ordered):
            for weaker in ordered[i + 1:]:
                result = select_strongest_candidate([weaker, stronger])
                assert result == stronger, (
                    f"Expected {stronger} to beat {weaker}, got {result}"
                )


# =============================================================================
# TEST: DOMAIN_SOURCE_PRIORITY invariant
# =============================================================================

class TestDomainSourcePriority:
    """Tests for DOMAIN_SOURCE_PRIORITY constant — DNS Phase 2 foundation.

    Establishes the invariant: promoted_domain > article_domain > dns_probe_domain.
    """

    def test_constant_exists(self):
        """DOMAIN_SOURCE_PRIORITY should be defined."""
        from utils.canonical_keys import DOMAIN_SOURCE_PRIORITY
        assert isinstance(DOMAIN_SOURCE_PRIORITY, dict)

    def test_contains_all_three_sources(self):
        """Should contain promoted, article, and dns_probe entries."""
        from utils.canonical_keys import DOMAIN_SOURCE_PRIORITY
        assert "promoted_domain" in DOMAIN_SOURCE_PRIORITY
        assert "article_domain" in DOMAIN_SOURCE_PRIORITY
        assert "dns_probe_domain" in DOMAIN_SOURCE_PRIORITY

    def test_promoted_beats_article(self):
        """promoted_domain should have higher priority than article_domain."""
        from utils.canonical_keys import DOMAIN_SOURCE_PRIORITY
        assert DOMAIN_SOURCE_PRIORITY["promoted_domain"] > DOMAIN_SOURCE_PRIORITY["article_domain"]

    def test_article_beats_dns_probe(self):
        """article_domain should have higher priority than dns_probe_domain."""
        from utils.canonical_keys import DOMAIN_SOURCE_PRIORITY
        assert DOMAIN_SOURCE_PRIORITY["article_domain"] > DOMAIN_SOURCE_PRIORITY["dns_probe_domain"]

    def test_promoted_beats_dns_probe(self):
        """promoted_domain should have higher priority than dns_probe_domain (transitive)."""
        from utils.canonical_keys import DOMAIN_SOURCE_PRIORITY
        assert DOMAIN_SOURCE_PRIORITY["promoted_domain"] > DOMAIN_SOURCE_PRIORITY["dns_probe_domain"]

    def test_all_values_positive(self):
        """All priority values should be positive integers."""
        from utils.canonical_keys import DOMAIN_SOURCE_PRIORITY
        for source, priority in DOMAIN_SOURCE_PRIORITY.items():
            assert isinstance(priority, int), f"{source} priority is not int"
            assert priority > 0, f"{source} priority must be positive"


# =============================================================================
# TEST: _extract_canonical_key strength-based selection
# =============================================================================

class TestExtractCanonicalKeyStrengthBased:
    """Tests for updated _extract_canonical_key in BaseCollector.

    Verifies that the method now uses strength-based selection instead of
    blindly taking raw_data['canonical_key'] or candidates[0].
    """

    def _make_signal(self, raw_data=None, signal_id="test-signal-123"):
        """Create a mock Signal object."""
        signal = MagicMock()
        signal.raw_data = raw_data or {}
        signal.id = signal_id
        return signal

    def _make_collector(self):
        """Create a minimal BaseCollector instance for testing."""
        from collectors.base import BaseCollector
        # BaseCollector is abstract; we need a concrete subclass
        class _TestCollector(BaseCollector):
            async def _collect_signals(self):
                return []
            async def _convert_to_signals(self, raw_signals):
                return []
        # Bypass __init__ by creating with __new__ and setting required attrs
        collector = _TestCollector.__new__(_TestCollector)
        return collector

    def test_prefers_stronger_candidate_over_weak_raw_key(self):
        """If raw_data canonical_key is weak but candidates contain a strong key,
        the strong candidate should win."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key": "name_loc:acme",
            "canonical_key_candidates": ["name_loc:acme", "domain:acme.ai"],
        })
        result = collector._extract_canonical_key(signal)
        assert result == "domain:acme.ai"

    def test_raw_key_wins_when_strongest(self):
        """If raw_data canonical_key is already the strongest, it should be returned."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key": "domain:acme.ai",
            "canonical_key_candidates": ["domain:acme.ai", "name_loc:acme"],
        })
        result = collector._extract_canonical_key(signal)
        assert result == "domain:acme.ai"

    def test_candidates_only_no_raw_key(self):
        """With no raw_data canonical_key, should pick strongest from candidates."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key_candidates": ["github_org:acme", "domain:acme.ai"],
        })
        result = collector._extract_canonical_key(signal)
        assert result == "domain:acme.ai"

    def test_raw_key_only_no_candidates(self):
        """With only raw_data canonical_key and no candidates, use raw key."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key": "name_loc:acme",
        })
        result = collector._extract_canonical_key(signal)
        assert result == "name_loc:acme"

    def test_fallback_to_signal_id(self):
        """With no raw_data canonical_key or candidates, fall back to signal.id."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={}, signal_id="fallback-id-456")
        result = collector._extract_canonical_key(signal)
        assert result == "fallback-id-456"

    def test_empty_raw_data(self):
        """None raw_data should fall back to signal.id."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data=None, signal_id="fallback-id")
        result = collector._extract_canonical_key(signal)
        assert result == "fallback-id"

    def test_empty_candidates_list(self):
        """Empty candidates list should not crash."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key_candidates": [],
        }, signal_id="fallback-id")
        result = collector._extract_canonical_key(signal)
        assert result == "fallback-id"

    def test_non_list_candidates_ignored(self):
        """Non-list candidates should be ignored gracefully."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key": "name_loc:acme",
            "canonical_key_candidates": "not-a-list",
        })
        result = collector._extract_canonical_key(signal)
        assert result == "name_loc:acme"

    def test_non_string_candidates_filtered(self):
        """Non-string items in candidates list should be filtered out."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key_candidates": [None, 123, "domain:acme.ai", "", "name_loc:acme"],
        })
        result = collector._extract_canonical_key(signal)
        assert result == "domain:acme.ai"

    def test_deduplication_in_collection(self):
        """Duplicate keys in raw_key + candidates should not cause issues."""
        collector = self._make_collector()
        signal = self._make_signal(raw_data={
            "canonical_key": "domain:acme.ai",
            "canonical_key_candidates": ["domain:acme.ai", "domain:acme.ai"],
        })
        result = collector._extract_canonical_key(signal)
        assert result == "domain:acme.ai"


# =============================================================================
# TEST: No-regress — prefix ordering is strict
# =============================================================================

class TestPrefixOrderingNoRegress:
    """No-regress tests that lock down the canonical key prefix ordering.

    These tests ensure future changes don't accidentally break the priority chain.
    """

    def test_canonical_prefix_order_tuple(self):
        """_CANONICAL_PREFIX_ORDER should maintain exact ordering."""
        from utils.canonical_keys import _CANONICAL_PREFIX_ORDER
        expected = (
            "domain",
            "companies_house",
            "crunchbase",
            "pitchbook",
            "github_org",
            "github_repo",
            "name_loc",
        )
        assert _CANONICAL_PREFIX_ORDER == expected

    def test_strength_scores_monotonically_decrease(self):
        """Strength scores should decrease following prefix order."""
        from utils.canonical_keys import _CANONICAL_PREFIX_ORDER, get_key_strength_score
        scores = [get_key_strength_score(f"{prefix}:x") for prefix in _CANONICAL_PREFIX_ORDER]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Score for {_CANONICAL_PREFIX_ORDER[i]} ({scores[i]}) "
                f"should be >= score for {_CANONICAL_PREFIX_ORDER[i+1]} ({scores[i+1]})"
            )

    def test_strong_weak_partition_complete(self):
        """Every prefix in _CANONICAL_PREFIX_ORDER should be in either STRONG or WEAK."""
        from utils.canonical_keys import (
            _CANONICAL_PREFIX_ORDER, STRONG_KEY_PREFIXES, WEAK_KEY_PREFIXES
        )
        all_prefixes = STRONG_KEY_PREFIXES | WEAK_KEY_PREFIXES
        for prefix in _CANONICAL_PREFIX_ORDER:
            assert prefix in all_prefixes, f"Prefix {prefix} not in STRONG or WEAK"

    def test_build_candidates_respects_prefix_order(self):
        """build_canonical_key_candidates output should follow _CANONICAL_PREFIX_ORDER."""
        from utils.canonical_keys import build_canonical_key_candidates, _CANONICAL_PREFIX_ORDER
        candidates = build_canonical_key_candidates(
            domain_or_website="acme.ai",
            companies_house_number="12345678",
            crunchbase_id="acme-cb",
            pitchbook_id="acme-pb",
            github_org="acme",
            github_repo="acme/product",
            fallback_company_name="Acme Inc",
            fallback_region="US",
        )
        # Extract prefixes from candidates
        prefixes = [c.split(":")[0] for c in candidates]
        # They should be in the same relative order as _CANONICAL_PREFIX_ORDER
        prefix_indices = [_CANONICAL_PREFIX_ORDER.index(p) for p in prefixes]
        assert prefix_indices == sorted(prefix_indices), (
            f"Candidate prefixes {prefixes} not in priority order"
        )
