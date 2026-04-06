"""Tests for Phase G alias binding deduplication.

Verifies that _build_groups() deduplicates alias bindings when multiple
signals in the same batch share the same normalized company name, preventing
UNIQUE constraint violations in entity_key_aliases.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.phase_g_entity_resolver import PhaseGEntityResolver


# =============================================================================
# MINIMAL STORED SIGNAL STUB
# =============================================================================

@dataclass
class FakeStoredSignal:
    """Minimal stub matching the fields _build_groups reads."""
    id: int
    canonical_key: str
    source_api: str
    company_name: Optional[str] = None
    signal_type: str = "test"
    confidence: float = 0.5
    raw_data: Dict[str, Any] = None  # type: ignore[assignment]
    detected_at: datetime = None  # type: ignore[assignment]
    created_at: datetime = None  # type: ignore[assignment]
    company_id: Optional[str] = None
    content_hash: str = ""
    title: Optional[str] = None
    url: Optional[str] = None
    source_context: Optional[str] = None
    raw_metadata: Optional[Dict[str, Any]] = None
    status: str = "pending"
    filter_result: Optional[str] = None
    filter_stage: Optional[str] = None
    extracted_company_name: Optional[str] = None
    notion_page_id: Optional[str] = None
    first_seen_at: datetime = None  # type: ignore[assignment]
    last_seen_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    processing_status: Optional[str] = None
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        now = datetime.now(timezone.utc)
        if self.raw_data is None:
            self.raw_data = {}
        for field_name in ("detected_at", "created_at", "first_seen_at", "last_seen_at", "updated_at"):
            if getattr(self, field_name) is None:
                object.__setattr__(self, field_name, now)


# =============================================================================
# TESTS
# =============================================================================

class TestBuildGroupsAliasDedup:
    """Tests that _build_groups deduplicates alias bindings within a batch."""

    def _make_resolver(self) -> PhaseGEntityResolver:
        """Create a resolver with a mocked identity store."""
        mock_store = MagicMock()
        resolver = PhaseGEntityResolver(mock_store)
        return resolver

    def _register_entity(self, resolver: PhaseGEntityResolver, entity_id: str):
        """Register an entity in the Union-Find so _uf_find returns it."""
        resolver._uf_find(entity_id)

    def test_duplicate_alias_keys_deduped_same_entity(self):
        """Three signals with same company → only one alias binding per alias key."""
        resolver = self._make_resolver()
        entity_id = "ent_acme_001"
        self._register_entity(resolver, entity_id)

        # Three signals from different sources, all mapping to the same entity
        signals = [
            FakeStoredSignal(id=1, canonical_key="domain:acme.com", source_api="news_api"),
            FakeStoredSignal(id=2, canonical_key="domain:acme.com", source_api="rss_feeds"),
            FakeStoredSignal(id=3, canonical_key="domain:acme.com", source_api="hacker_news"),
        ]

        # All three signals have the same canonical key → same entity
        strong_key_map = {
            "domain:acme.com": entity_id,
        }

        # All three signals produce the same alias keys (same company name)
        signal_aliases = {
            1: {
                "alias_keys": ["name_norm:acme-inc", "name_loc:acme-inc|us"],
                "blocking_tokens": [("acme", "word"), ("inc", "word")],
            },
            2: {
                "alias_keys": ["name_norm:acme-inc", "name_loc:acme-inc|us"],
                "blocking_tokens": [("acme", "word"), ("inc", "word")],
            },
            3: {
                "alias_keys": ["name_norm:acme-inc", "name_loc:acme-inc|us"],
                "blocking_tokens": [("acme", "word"), ("inc", "word")],
            },
        }

        groups = resolver._build_groups(signals, strong_key_map, signal_aliases)

        assert len(groups) == 1
        group = groups[0]

        # Extract alias keys from the bindings
        alias_keys_in_bindings = [binding[0] for binding in group.alias_keys_to_bind]

        # Should have exactly 2 unique alias keys (name_norm + name_loc), not 6
        assert len(alias_keys_in_bindings) == 2
        assert "name_norm:acme-inc" in alias_keys_in_bindings
        assert "name_loc:acme-inc|us" in alias_keys_in_bindings

    def test_different_alias_keys_not_deduped(self):
        """Signals with different alias keys all produce separate bindings."""
        resolver = self._make_resolver()
        entity_id = "ent_multi_001"
        self._register_entity(resolver, entity_id)

        signals = [
            FakeStoredSignal(id=1, canonical_key="domain:foo.com", source_api="news_api"),
            FakeStoredSignal(id=2, canonical_key="domain:foo.com", source_api="rss_feeds"),
        ]

        strong_key_map = {"domain:foo.com": entity_id}

        # Different alias keys per signal (different company name variants)
        signal_aliases = {
            1: {
                "alias_keys": ["name_norm:foo-bar"],
                "blocking_tokens": [("foo", "word")],
            },
            2: {
                "alias_keys": ["name_norm:foo-baz"],
                "blocking_tokens": [("foo", "word")],
            },
        }

        groups = resolver._build_groups(signals, strong_key_map, signal_aliases)

        assert len(groups) == 1
        alias_keys = [b[0] for b in groups[0].alias_keys_to_bind]
        assert len(alias_keys) == 2
        assert "name_norm:foo-bar" in alias_keys
        assert "name_norm:foo-baz" in alias_keys

    def test_cross_entity_same_alias_not_deduped(self):
        """Same alias key across different entities should NOT be deduped."""
        resolver = self._make_resolver()
        entity_a = "ent_aaa"
        entity_b = "ent_bbb"
        self._register_entity(resolver, entity_a)
        self._register_entity(resolver, entity_b)

        signals = [
            FakeStoredSignal(id=1, canonical_key="domain:alpha.com", source_api="news_api"),
            FakeStoredSignal(id=2, canonical_key="domain:beta.com", source_api="news_api"),
        ]

        strong_key_map = {
            "domain:alpha.com": entity_a,
            "domain:beta.com": entity_b,
        }

        # Both entities happen to have the same alias key (rare but possible)
        signal_aliases = {
            1: {
                "alias_keys": ["name_norm:shared-name"],
                "blocking_tokens": [("shared", "word")],
            },
            2: {
                "alias_keys": ["name_norm:shared-name"],
                "blocking_tokens": [("shared", "word")],
            },
        }

        groups = resolver._build_groups(signals, strong_key_map, signal_aliases)

        # Two separate groups (different entities)
        assert len(groups) == 2

        # Each group should have exactly 1 alias binding
        for group in groups:
            assert len(group.alias_keys_to_bind) == 1
            assert group.alias_keys_to_bind[0][0] == "name_norm:shared-name"

    def test_first_signal_source_preserved(self):
        """The source from the first signal encountering an alias key is kept."""
        resolver = self._make_resolver()
        entity_id = "ent_src_001"
        self._register_entity(resolver, entity_id)

        signals = [
            FakeStoredSignal(id=1, canonical_key="domain:test.com", source_api="news_api"),
            FakeStoredSignal(id=2, canonical_key="domain:test.com", source_api="rss_feeds"),
        ]

        strong_key_map = {"domain:test.com": entity_id}

        signal_aliases = {
            1: {
                "alias_keys": ["name_norm:test-co"],
                "blocking_tokens": [],
            },
            2: {
                "alias_keys": ["name_norm:test-co"],
                "blocking_tokens": [],
            },
        }

        groups = resolver._build_groups(signals, strong_key_map, signal_aliases)
        assert len(groups) == 1

        # Only 1 binding, and it should have the first signal's source
        bindings = groups[0].alias_keys_to_bind
        assert len(bindings) == 1
        # binding tuple: (alias_key, entity_id, alias_type, confidence, source, expiry)
        assert bindings[0][4] == "news_api"

    def test_no_aliases_no_crash(self):
        """Signals without alias info should not cause issues."""
        resolver = self._make_resolver()
        entity_id = "ent_none_001"
        self._register_entity(resolver, entity_id)

        signals = [
            FakeStoredSignal(id=1, canonical_key="domain:empty.com", source_api="github"),
        ]

        strong_key_map = {"domain:empty.com": entity_id}
        signal_aliases = {}  # No alias info for signal 1

        groups = resolver._build_groups(signals, strong_key_map, signal_aliases)
        assert len(groups) == 1
        assert len(groups[0].alias_keys_to_bind) == 0
