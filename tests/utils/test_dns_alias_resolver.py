"""
Tests for utils/dns_alias_resolver.py — DNS promotion alias resolution.

TDD RED phase: tests define the alias resolution contract.
Uses raw sqlite3 connections (not aiosqlite) for synchronous test simplicity.
"""

from __future__ import annotations

import sqlite3

import pytest

from utils.dns_alias_resolver import (
    resolve_alias,
    resolve_aliases_batch,
    record_alias,
    rollback_aliases,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def conn():
    """In-memory SQLite with dns_promotion_aliases table."""
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE dns_promotion_aliases (
            alias_key TEXT PRIMARY KEY,
            target_key TEXT NOT NULL,
            alias_type TEXT NOT NULL DEFAULT 'dns_promotion'
                CHECK(alias_type IN ('dns_promotion')),
            source TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_dpa_target ON dns_promotion_aliases(target_key)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_dpa_enabled ON dns_promotion_aliases(enabled) WHERE enabled = 1")
    db.commit()
    yield db
    db.close()


@pytest.fixture
def conn_no_table():
    """In-memory SQLite WITHOUT dns_promotion_aliases table (pre-v44)."""
    db = sqlite3.connect(":memory:")
    yield db
    db.close()


# =============================================================================
# resolve_alias()
# =============================================================================


class TestResolveAlias:
    """Single-hop alias lookup."""

    def test_returns_target_when_alias_exists(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        assert resolve_alias("name_loc:acme", conn) == "domain:acme.ai"

    def test_returns_original_when_no_alias(self, conn):
        assert resolve_alias("name_loc:unknown", conn) == "name_loc:unknown"

    def test_returns_original_when_disabled(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        conn.execute("UPDATE dns_promotion_aliases SET enabled = 0 WHERE alias_key = 'name_loc:acme'")
        conn.commit()
        assert resolve_alias("name_loc:acme", conn) == "name_loc:acme"

    def test_graceful_when_table_missing(self, conn_no_table):
        """Pre-v44 databases should not crash."""
        assert resolve_alias("name_loc:acme", conn_no_table) == "name_loc:acme"


# =============================================================================
# resolve_aliases_batch()
# =============================================================================


class TestResolveAliasesBatch:
    """Batch alias resolution."""

    def test_resolves_multiple(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        record_alias("name_loc:beta", "domain:beta.co", "news_api", conn)
        result = resolve_aliases_batch(
            ["name_loc:acme", "name_loc:beta", "name_loc:gamma"], conn
        )
        assert result == {
            "name_loc:acme": "domain:acme.ai",
            "name_loc:beta": "domain:beta.co",
            "name_loc:gamma": "name_loc:gamma",
        }

    def test_empty_input(self, conn):
        assert resolve_aliases_batch([], conn) == {}

    def test_graceful_when_table_missing(self, conn_no_table):
        result = resolve_aliases_batch(["name_loc:acme"], conn_no_table)
        assert result == {"name_loc:acme": "name_loc:acme"}


# =============================================================================
# record_alias()
# =============================================================================


class TestRecordAlias:
    """UPSERT semantics for alias recording."""

    def test_inserts_new_alias(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        row = conn.execute(
            "SELECT alias_key, target_key, source, enabled FROM dns_promotion_aliases"
        ).fetchone()
        assert row == ("name_loc:acme", "domain:acme.ai", "rss_feeds", 1)

    def test_upsert_updates_existing(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        record_alias("name_loc:acme", "domain:acme.com", "news_api", conn)
        row = conn.execute(
            "SELECT target_key, source FROM dns_promotion_aliases WHERE alias_key = 'name_loc:acme'"
        ).fetchone()
        assert row == ("domain:acme.com", "news_api")

    def test_upsert_re_enables_disabled(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        conn.execute("UPDATE dns_promotion_aliases SET enabled = 0 WHERE alias_key = 'name_loc:acme'")
        conn.commit()
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        row = conn.execute(
            "SELECT enabled FROM dns_promotion_aliases WHERE alias_key = 'name_loc:acme'"
        ).fetchone()
        assert row[0] == 1


# =============================================================================
# rollback_aliases()
# =============================================================================


class TestRollbackAliases:
    """Disable all aliases (reversible soft-delete)."""

    def test_disables_all(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        record_alias("name_loc:beta", "domain:beta.co", "news_api", conn)
        count = rollback_aliases(conn)
        assert count == 2
        enabled = conn.execute(
            "SELECT COUNT(*) FROM dns_promotion_aliases WHERE enabled = 1"
        ).fetchone()[0]
        assert enabled == 0

    def test_rollback_restores_equivalence(self, conn):
        """Apply aliases -> rollback -> all keys resolve to original."""
        record_alias("name_loc:foo", "domain:foo.com", "rss_feeds", conn)
        assert resolve_alias("name_loc:foo", conn) == "domain:foo.com"
        rollback_aliases(conn)
        assert resolve_alias("name_loc:foo", conn) == "name_loc:foo"

    def test_rollback_idempotent(self, conn):
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        rollback_aliases(conn)
        count = rollback_aliases(conn)
        assert count == 0

    def test_rollback_graceful_when_table_missing(self, conn_no_table):
        """Pre-v44 databases should not crash."""
        count = rollback_aliases(conn_no_table)
        assert count == 0


# =============================================================================
# Fragmentation proof: name_loc + domain converge
# =============================================================================


class TestFragmentationProof:
    """Verify that aliases correctly unify fragmented keys."""

    def test_name_loc_and_domain_converge(self, conn):
        """Two signals with name_loc:acme and domain:acme.ai should
        resolve to the same key after alias is recorded."""
        record_alias("name_loc:acme", "domain:acme.ai", "rss_feeds", conn)
        key_a = resolve_alias("name_loc:acme", conn)
        key_b = resolve_alias("domain:acme.ai", conn)  # already canonical
        assert key_a == key_b == "domain:acme.ai"

    def test_multiple_name_loc_converge_to_same_domain(self, conn):
        """Two different name_loc keys can alias to the same domain."""
        record_alias("name_loc:acme_inc", "domain:acme.ai", "rss_feeds", conn)
        record_alias("name_loc:acme", "domain:acme.ai", "news_api", conn)
        result = resolve_aliases_batch(
            ["name_loc:acme_inc", "name_loc:acme", "domain:acme.ai"], conn
        )
        assert result["name_loc:acme_inc"] == "domain:acme.ai"
        assert result["name_loc:acme"] == "domain:acme.ai"
        assert result["domain:acme.ai"] == "domain:acme.ai"
