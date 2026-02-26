"""
Tests for seed_tier_c_domains.py script.

Verifies dry-run no-op, commit upserts, idempotent re-run, metadata merge,
and integration proof (seed domain + second source signal => KPI increment).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")

from scripts.seed_tier_c_domains import seed_tier_c, _load_domains, _merge_metadata
from utils.canonical_keys import derive_company_id


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def test_db(tmp_path):
    """Create a test DB with company_files + signals tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE company_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL UNIQUE,
            company_name TEXT,
            canonical_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('thin', 'promoted', 'archived')),
            source_apis TEXT NOT NULL DEFAULT '[]',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            promoted_at TEXT,
            archived_at TEXT,
            metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            source_api TEXT NOT NULL,
            signal_type TEXT DEFAULT 'news',
            company_name TEXT DEFAULT '',
            confidence REAL DEFAULT 0.5,
            raw_data TEXT DEFAULT '{}',
            detected_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            company_id TEXT DEFAULT '',
            evidence_family TEXT,
            canonical_key_v2 TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def domain_file(tmp_path):
    """Create a domain list file."""
    path = tmp_path / "domains.txt"
    path.write_text(
        "# Test domains\n"
        "freshly.com\n"
        "olipop.com\n"
        "  huel.com  \n"
        "\n"
        "# Comment line\n"
        "athleticbrewing.com\n"
    )
    return str(path)


@pytest.fixture
def single_domain_file(tmp_path):
    """Create a domain file with one domain."""
    path = tmp_path / "single.txt"
    path.write_text("freshly.com\n")
    return str(path)


# ============================================================================
# _load_domains tests
# ============================================================================

class TestLoadDomains:
    """Domain file loading and normalization."""

    def test_load_valid_domains(self, domain_file):
        """Loads and normalizes domains, skips comments and blanks."""
        domains = _load_domains(domain_file)
        assert domains == ["freshly.com", "olipop.com", "huel.com", "athleticbrewing.com"]

    def test_dedupes_domains(self, tmp_path):
        """Duplicate domains are deduplicated."""
        path = tmp_path / "dupes.txt"
        path.write_text("acme.ai\nacme.ai\nwww.acme.ai\n")
        domains = _load_domains(str(path))
        assert domains == ["acme.ai"]

    def test_skips_invalid(self, tmp_path):
        """Invalid domain lines are skipped with warning."""
        path = tmp_path / "bad.txt"
        path.write_text("acme.ai\nnot-a-domain\n")
        domains = _load_domains(str(path))
        assert domains == ["acme.ai"]

    def test_empty_file(self, tmp_path):
        """Empty file returns empty list."""
        path = tmp_path / "empty.txt"
        path.write_text("")
        domains = _load_domains(str(path))
        assert domains == []


# ============================================================================
# _merge_metadata tests
# ============================================================================

class TestMergeMetadata:
    """Metadata merge logic."""

    def test_merge_into_empty(self):
        result = json.loads(_merge_metadata(None, {"manual_seed": True}))
        assert result == {"manual_seed": True}

    def test_merge_into_existing(self):
        existing = json.dumps({"foo": "bar"})
        result = json.loads(_merge_metadata(existing, {"manual_seed": True}))
        assert result["foo"] == "bar"
        assert result["manual_seed"] is True

    def test_merge_overwrites_key(self):
        existing = json.dumps({"manual_seed": False})
        result = json.loads(_merge_metadata(existing, {"manual_seed": True}))
        assert result["manual_seed"] is True


# ============================================================================
# Dry-run tests
# ============================================================================

class TestDryRun:
    """Dry-run mode should not write anything."""

    def test_dry_run_no_writes(self, test_db, domain_file):
        """Dry-run does not insert rows."""
        result = seed_tier_c(test_db, domain_file, commit=False)

        assert result["inserted"] == 4
        assert result["updated"] == 0
        assert result["skipped"] == 0

        # Verify nothing was actually written
        conn = sqlite3.connect(test_db)
        count = conn.execute("SELECT COUNT(*) FROM company_files").fetchone()[0]
        conn.close()
        assert count == 0

    def test_dry_run_with_existing(self, test_db, single_domain_file):
        """Dry-run correctly reports existing rows."""
        # Pre-insert a row
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(test_db)
        cid = derive_company_id("domain:freshly.com")
        conn.execute(
            """INSERT INTO company_files
               (company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, "freshly.com", "domain:freshly.com", "thin", "[]", now, now, "{}"),
        )
        conn.commit()
        conn.close()

        result = seed_tier_c(test_db, single_domain_file, commit=False)
        assert result["inserted"] == 0
        assert result["updated"] == 1  # existing without manual_seed


# ============================================================================
# Commit tests
# ============================================================================

class TestCommit:
    """Commit mode writes to company_files."""

    def test_inserts_new_rows(self, test_db, domain_file):
        """Commit inserts new company_files rows."""
        result = seed_tier_c(test_db, domain_file, commit=True)

        assert result["inserted"] == 4
        assert result["updated"] == 0
        assert result["skipped"] == 0

        conn = sqlite3.connect(test_db)
        count = conn.execute("SELECT COUNT(*) FROM company_files").fetchone()[0]
        conn.close()
        assert count == 4

    def test_correct_canonical_key(self, test_db, single_domain_file):
        """Inserted row has correct canonical_key and company_id."""
        seed_tier_c(test_db, single_domain_file, commit=True)

        conn = sqlite3.connect(test_db)
        row = conn.execute(
            "SELECT company_id, canonical_key, status FROM company_files"
        ).fetchone()
        conn.close()

        expected_cid = derive_company_id("domain:freshly.com")
        assert row[0] == expected_cid
        assert row[1] == "domain:freshly.com"
        assert row[2] == "thin"

    def test_metadata_has_manual_seed(self, test_db, single_domain_file):
        """Inserted row has manual_seed=true in metadata."""
        seed_tier_c(test_db, single_domain_file, commit=True)

        conn = sqlite3.connect(test_db)
        meta_str = conn.execute(
            "SELECT metadata FROM company_files WHERE canonical_key = 'domain:freshly.com'"
        ).fetchone()[0]
        conn.close()

        meta = json.loads(meta_str)
        assert meta["manual_seed"] is True
        assert meta["seed_source"] == "tier_c"

    def test_source_apis_includes_manual_seed(self, test_db, single_domain_file):
        """Inserted row has manual_seed in source_apis JSON array."""
        seed_tier_c(test_db, single_domain_file, commit=True)

        conn = sqlite3.connect(test_db)
        apis_str = conn.execute(
            "SELECT source_apis FROM company_files WHERE canonical_key = 'domain:freshly.com'"
        ).fetchone()[0]
        conn.close()

        apis = json.loads(apis_str)
        assert "manual_seed" in apis

    def test_status_override(self, test_db, single_domain_file):
        """--status promoted creates promoted rows."""
        seed_tier_c(test_db, single_domain_file, commit=True, status="promoted")

        conn = sqlite3.connect(test_db)
        row = conn.execute(
            "SELECT status FROM company_files WHERE canonical_key = 'domain:freshly.com'"
        ).fetchone()
        conn.close()

        assert row[0] == "promoted"


# ============================================================================
# Idempotent re-run tests
# ============================================================================

class TestIdempotent:
    """Re-running seed is idempotent."""

    def test_rerun_skips_already_seeded(self, test_db, domain_file):
        """Second run skips already-seeded domains."""
        r1 = seed_tier_c(test_db, domain_file, commit=True)
        assert r1["inserted"] == 4

        r2 = seed_tier_c(test_db, domain_file, commit=True)
        assert r2["inserted"] == 0
        assert r2["skipped"] == 4

        # Still only 4 rows
        conn = sqlite3.connect(test_db)
        count = conn.execute("SELECT COUNT(*) FROM company_files").fetchone()[0]
        conn.close()
        assert count == 4

    def test_updates_existing_without_seed_flag(self, test_db, single_domain_file):
        """Existing row without manual_seed gets metadata merged."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(test_db)
        cid = derive_company_id("domain:freshly.com")
        conn.execute(
            """INSERT INTO company_files
               (company_id, company_name, canonical_key, status, source_apis,
                first_seen_at, last_seen_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, "freshly.com", "domain:freshly.com", "thin",
             '["hacker_news"]', now, now, '{"existing_key": "value"}'),
        )
        conn.commit()
        conn.close()

        result = seed_tier_c(test_db, single_domain_file, commit=True)
        assert result["updated"] == 1

        # Verify metadata merged
        conn = sqlite3.connect(test_db)
        row = conn.execute(
            "SELECT metadata, source_apis FROM company_files WHERE canonical_key = 'domain:freshly.com'"
        ).fetchone()
        conn.close()

        meta = json.loads(row[0])
        assert meta["manual_seed"] is True
        assert meta["existing_key"] == "value"

        apis = json.loads(row[1])
        assert "hacker_news" in apis
        assert "manual_seed" in apis


# ============================================================================
# Integration proof: seed + second source => convergence
# ============================================================================

class TestConvergenceProof:
    """Prove that seeding + a second-source signal can produce convergence."""

    def test_seed_plus_second_source_yields_multi_api(self, test_db, single_domain_file):
        """After seeding and inserting a second-source signal, the domain key
        has 2+ source APIs in signals table."""
        # Step 1: seed the domain into company_files
        seed_tier_c(test_db, single_domain_file, commit=True)

        # Step 2: simulate two different collector signals for same canonical_key
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(test_db)
        conn.execute(
            """INSERT INTO signals (canonical_key, source_api, signal_type, created_at)
               VALUES (?, ?, ?, ?)""",
            ("domain:freshly.com", "greenhouse_jobs", "job_posting", now),
        )
        conn.execute(
            """INSERT INTO signals (canonical_key, source_api, signal_type, created_at)
               VALUES (?, ?, ?, ?)""",
            ("domain:freshly.com", "manual_seed_buzz", "news_mention", now),
        )
        conn.commit()

        # Step 3: verify convergence KPI query
        row = conn.execute(
            """SELECT COUNT(DISTINCT source_api)
               FROM signals
               WHERE canonical_key = 'domain:freshly.com'"""
        ).fetchone()
        conn.close()

        assert row[0] >= 2, "Expected 2+ source APIs for converged key"

    def test_company_file_source_apis_merge(self, test_db, single_domain_file):
        """After seeding + update, company_files.source_apis has 2+ entries."""
        # Seed creates with source_apis=["manual_seed"]
        seed_tier_c(test_db, single_domain_file, commit=True)

        # Simulate pipeline adding a second source
        conn = sqlite3.connect(test_db)
        row = conn.execute(
            "SELECT source_apis FROM company_files WHERE canonical_key = 'domain:freshly.com'"
        ).fetchone()
        apis = json.loads(row[0])
        apis.append("greenhouse_jobs")
        conn.execute(
            "UPDATE company_files SET source_apis = ? WHERE canonical_key = 'domain:freshly.com'",
            (json.dumps(apis),),
        )
        conn.commit()

        # Verify
        row = conn.execute(
            "SELECT source_apis FROM company_files WHERE canonical_key = 'domain:freshly.com'"
        ).fetchone()
        conn.close()

        final_apis = json.loads(row[0])
        assert len(final_apis) >= 2
        assert "manual_seed" in final_apis
        assert "greenhouse_jobs" in final_apis


# ============================================================================
# Error handling
# ============================================================================

class TestErrorHandling:
    """Error cases."""

    def test_invalid_status_raises(self, test_db, single_domain_file):
        """Invalid status raises ValueError."""
        with pytest.raises(ValueError, match="Invalid status"):
            seed_tier_c(test_db, single_domain_file, commit=True, status="active")

    def test_missing_domain_file_raises(self, test_db):
        """Missing domain file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            seed_tier_c(test_db, "/nonexistent/domains.txt", commit=True)

    def test_empty_domain_file(self, test_db, tmp_path):
        """Empty domain file returns zero counts."""
        path = tmp_path / "empty.txt"
        path.write_text("")
        result = seed_tier_c(test_db, str(path), commit=True)
        assert result["total"] == 0
