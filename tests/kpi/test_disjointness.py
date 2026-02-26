"""
Disjointness smoke test: synthetic vs organic signal separation.

Verifies that KPI queries can correctly filter by source_api to discriminate
synthetic (manual_seed_buzz) from organic signals, and that the two sets
are disjoint.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest


@pytest.fixture
def kpi_db(tmp_path):
    """Create a minimal DB with signals table (v43-compatible)."""
    db_path = str(tmp_path / "kpi_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)
    """)
    conn.execute("INSERT INTO schema_migrations (version) VALUES (43)")
    conn.execute("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            canonical_key_v2 TEXT,
            source_api TEXT NOT NULL,
            signal_type TEXT DEFAULT 'news',
            company_name TEXT DEFAULT '',
            confidence REAL DEFAULT 0.5,
            raw_data TEXT DEFAULT '{}',
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            company_id TEXT DEFAULT '',
            evidence_family TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def seeded_db(kpi_db):
    """Seed the DB with a mix of synthetic and organic signals."""
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(kpi_db)

    # Synthetic signals (manual_seed_buzz)
    conn.execute(
        "INSERT INTO signals (canonical_key, canonical_key_v2, source_api, evidence_family, detected_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("domain:acme.ai", "domain:acme.ai", "manual_seed_buzz", "public_buzz", now, now),
    )
    conn.execute(
        "INSERT INTO signals (canonical_key, canonical_key_v2, source_api, evidence_family, detected_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("domain:freshly.com", "domain:freshly.com", "manual_seed_buzz", "public_buzz", now, now),
    )

    # Organic signals (various collectors)
    conn.execute(
        "INSERT INTO signals (canonical_key, canonical_key_v2, source_api, evidence_family, detected_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("domain:acme.ai", "domain:acme.ai", "github", "developer", now, now),
    )
    conn.execute(
        "INSERT INTO signals (canonical_key, canonical_key_v2, source_api, evidence_family, detected_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("domain:olipop.com", "domain:olipop.com", "news_api", "public_buzz", now, now),
    )
    conn.execute(
        "INSERT INTO signals (canonical_key, canonical_key_v2, source_api, evidence_family, detected_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("domain:olipop.com", "domain:olipop.com", "sec_edgar", "regulatory", now, now),
    )

    conn.commit()
    conn.close()
    return kpi_db


class TestSyntheticOrganicDisjointness:
    """Verify synthetic and organic signal sets are disjoint by source_api."""

    def test_synthetic_filter_excludes_organic(self, seeded_db):
        """Filtering source_api='manual_seed_buzz' returns only synthetic rows."""
        conn = sqlite3.connect(seeded_db)
        rows = conn.execute(
            "SELECT source_api FROM signals WHERE source_api = 'manual_seed_buzz'"
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert all(r[0] == "manual_seed_buzz" for r in rows)

    def test_organic_filter_excludes_synthetic(self, seeded_db):
        """Filtering source_api != 'manual_seed_buzz' returns only organic rows."""
        conn = sqlite3.connect(seeded_db)
        rows = conn.execute(
            "SELECT source_api FROM signals WHERE source_api != 'manual_seed_buzz'"
        ).fetchall()
        conn.close()

        assert len(rows) == 3
        assert all(r[0] != "manual_seed_buzz" for r in rows)

    def test_sets_are_disjoint(self, seeded_db):
        """Synthetic and organic rowid sets have zero intersection."""
        conn = sqlite3.connect(seeded_db)
        synthetic_ids = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM signals WHERE source_api = 'manual_seed_buzz'"
            ).fetchall()
        }
        organic_ids = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM signals WHERE source_api != 'manual_seed_buzz'"
            ).fetchall()
        }
        conn.close()

        assert synthetic_ids & organic_ids == set(), "Synthetic and organic sets must be disjoint"
        assert len(synthetic_ids) + len(organic_ids) == 5  # total signals

    def test_kpi_query_filters_by_source_api(self, seeded_db):
        """KPI convergence query can be scoped to organic-only signals."""
        conn = sqlite3.connect(seeded_db)

        # Full KPI (synthetic + organic): acme.ai has 2 source_apis
        all_converged = conn.execute("""
            SELECT canonical_key_v2, COUNT(DISTINCT source_api) as apis
            FROM signals
            WHERE canonical_key_v2 IS NOT NULL
              AND detected_at >= datetime('now', '-30 days')
            GROUP BY canonical_key_v2
            HAVING apis >= 2
        """).fetchall()

        # Organic-only KPI: exclude manual_seed_buzz
        organic_converged = conn.execute("""
            SELECT canonical_key_v2, COUNT(DISTINCT source_api) as apis
            FROM signals
            WHERE canonical_key_v2 IS NOT NULL
              AND source_api != 'manual_seed_buzz'
              AND detected_at >= datetime('now', '-30 days')
            GROUP BY canonical_key_v2
            HAVING apis >= 2
        """).fetchall()

        conn.close()

        # acme.ai has github + manual_seed_buzz (full) but only github (organic) = 1 api
        # olipop.com has news_api + sec_edgar = 2 apis (organic)
        all_keys = {r[0] for r in all_converged}
        organic_keys = {r[0] for r in organic_converged}

        assert "domain:acme.ai" in all_keys, "acme.ai converges with synthetic included"
        assert "domain:olipop.com" in organic_keys, "olipop.com converges organically"
        # acme.ai should NOT converge organically (only 1 organic source)
        assert "domain:acme.ai" not in organic_keys, "acme.ai should not converge with organic-only filter"
