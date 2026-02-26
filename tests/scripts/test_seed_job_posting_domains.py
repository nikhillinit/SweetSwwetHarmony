"""
Tests for seed_job_posting_domains.py script.

Verifies frequency ranking, three-layer filtering, domain normalization,
time window, --top limit, --source company_files, --seed-filter,
and backward-compat symbol imports.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, ".")

from scripts.seed_job_posting_domains import (
    seed_domains,
    _is_infra_domain,
    _is_social_platform,
    _is_filtered,
    INFRA_DOMAIN_SUFFIXES,
    SOCIAL_PLATFORM_SUFFIXES,
    # Backward-compat aliases
    INFRA_DENYLIST,
    SOCIAL_PLATFORM_DENYLIST,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def test_db(tmp_path):
    """Create a test DB with signals + company_files tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            source_api TEXT NOT NULL,
            created_at TEXT NOT NULL,
            signal_type TEXT DEFAULT 'news',
            confidence REAL DEFAULT 0.5,
            source_url TEXT DEFAULT '',
            detected_at TEXT DEFAULT '',
            raw_data TEXT DEFAULT '{}'
        )
    """)
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
    conn.commit()
    conn.close()
    return db_path


def _insert_signal(db_path: str, canonical_key: str, source_api: str = "hacker_news",
                   created_at: str | None = None):
    """Insert a test signal."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO signals (canonical_key, source_api, created_at) VALUES (?, ?, ?)",
        (canonical_key, source_api, created_at),
    )
    conn.commit()
    conn.close()


def _insert_company_file(db_path: str, domain: str, metadata: dict | None = None,
                         status: str = "thin"):
    """Insert a company_files row with a domain: canonical key."""
    now = datetime.now(timezone.utc).isoformat()
    canonical_key = f"domain:{domain}"
    import hashlib
    company_id = hashlib.sha256(canonical_key.encode()).hexdigest()[:16]
    meta_str = json.dumps(metadata or {})
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (company_id, domain, canonical_key, status, '["manual_seed"]', now, now, meta_str),
    )
    conn.commit()
    conn.close()


# ============================================================================
# Original tests (source=signals)
# ============================================================================

class TestFrequencyRanking:
    """Highest count first."""

    def test_frequency_order(self, test_db):
        """More frequent domains appear first."""
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(3):
            _insert_signal(test_db, "domain:acme.ai", created_at=now)
        _insert_signal(test_db, "domain:betaco.com", created_at=now)
        for _ in range(2):
            _insert_signal(test_db, "domain:gamma.io", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert result.index("acme.ai") < result.index("gamma.io")
        assert result.index("gamma.io") < result.index("betaco.com")


class TestPublisherFiltering:
    """Publisher domains are filtered out."""

    def test_techcrunch_filtered(self, test_db):
        """techcrunch.com should be removed (publisher)."""
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(5):
            _insert_signal(test_db, "domain:techcrunch.com", created_at=now)
        _insert_signal(test_db, "domain:acme.ai", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert "techcrunch.com" not in result
        assert "acme.ai" in result


class TestInfraFiltering:
    """Infrastructure domains are filtered out."""

    def test_vercel_app_filtered(self, test_db):
        """vercel.app (infra) should be removed."""
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(5):
            _insert_signal(test_db, "domain:myapp.vercel.app", created_at=now)
        _insert_signal(test_db, "domain:acme.ai", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert "myapp.vercel.app" not in result
        assert "acme.ai" in result

    def test_all_infra_domains_blocked(self):
        """All INFRA_DOMAIN_SUFFIXES entries should be caught."""
        for d in INFRA_DOMAIN_SUFFIXES:
            assert _is_infra_domain(d) is True, f"{d} not blocked"
            assert _is_infra_domain(f"myapp.{d}") is True, f"myapp.{d} not blocked"


class TestSocialFiltering:
    """Social platform domains are filtered out."""

    def test_ycombinator_filtered(self, test_db):
        """ycombinator.com should be removed (social platform)."""
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(5):
            _insert_signal(test_db, "domain:ycombinator.com", created_at=now)
        _insert_signal(test_db, "domain:acme.ai", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert "ycombinator.com" not in result
        assert "acme.ai" in result

    def test_notion_site_filtered(self, test_db):
        """notion.site should be removed."""
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(3):
            _insert_signal(test_db, "domain:myco.notion.site", created_at=now)
        _insert_signal(test_db, "domain:acme.ai", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert "myco.notion.site" not in result

    def test_all_social_domains_blocked(self):
        """All SOCIAL_PLATFORM_SUFFIXES entries should be caught."""
        for d in SOCIAL_PLATFORM_SUFFIXES:
            assert _is_social_platform(d) is True, f"{d} not blocked"


class TestDomainNormalization:
    """www.acme.ai and acme.ai merged before ranking."""

    def test_www_merged(self, test_db):
        """www.acme.ai and acme.ai should be counted together."""
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(2):
            _insert_signal(test_db, "domain:www.acme.ai", created_at=now)
        _insert_signal(test_db, "domain:acme.ai", created_at=now)
        for _ in range(2):
            _insert_signal(test_db, "domain:betaco.com", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert "acme.ai" in result
        assert "www.acme.ai" not in result
        if "betaco.com" in result:
            assert result.index("acme.ai") < result.index("betaco.com")


class TestTimeWindow:
    """Old signals are excluded."""

    def test_old_signals_excluded(self, test_db):
        """Signals older than --days are excluded."""
        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

        _insert_signal(test_db, "domain:acme.ai", created_at=now)
        for _ in range(5):
            _insert_signal(test_db, "domain:oldco.com", created_at=old)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        assert "acme.ai" in result
        assert "oldco.com" not in result


class TestTopLimit:
    """--top N limit honored."""

    def test_top_limit(self, test_db):
        """Only top N domains returned."""
        now = datetime.now(timezone.utc).isoformat()
        for i in range(10):
            for _ in range(10 - i):
                _insert_signal(test_db, f"domain:company{i}.com", created_at=now)

        result = seed_domains(test_db, days=90, top=3, output_format="list")

        assert len(result) <= 3


class TestEmptyDB:
    """Empty DB returns empty list."""

    def test_empty_db(self, test_db):
        """No signals -> empty list."""
        result = seed_domains(test_db, days=90, top=10, output_format="list")
        assert result == []


class TestFilteringBreakdown:
    """Filtering breakdown shows counts per layer."""

    def test_breakdown_output(self, test_db, capsys):
        """Output includes filtering breakdown counts."""
        now = datetime.now(timezone.utc).isoformat()
        _insert_signal(test_db, "domain:acme.ai", created_at=now)
        _insert_signal(test_db, "domain:techcrunch.com", created_at=now)
        _insert_signal(test_db, "domain:myapp.vercel.app", created_at=now)
        _insert_signal(test_db, "domain:ycombinator.com", created_at=now)

        seed_domains(test_db, days=90, top=10, output_format="list")

        captured = capsys.readouterr()
        assert "Filtering:" in captured.out
        assert "publisher/suffix-blocked" in captured.out
        assert "infra-blocked" in captured.out
        assert "social-blocked" in captured.out


# ============================================================================
# New tests: --source company_files and --seed-filter
# ============================================================================

class TestSourceCompanyFiles:
    """Tests for --source company_files."""

    def test_company_files_returns_domains(self, test_db):
        """company_files source returns seeded domains."""
        _insert_company_file(test_db, "freshly.com", metadata={"manual_seed": True})
        _insert_company_file(test_db, "olipop.com", metadata={"manual_seed": True})

        result = seed_domains(
            test_db, source="company_files", output_format="list", top=10
        )

        assert "freshly.com" in result
        assert "olipop.com" in result

    def test_seed_filter_only_manual(self, test_db):
        """--seed-filter only returns manual_seed=true rows."""
        _insert_company_file(test_db, "freshly.com", metadata={"manual_seed": True})
        _insert_company_file(test_db, "olipop.com", metadata={})

        result = seed_domains(
            test_db, source="company_files", seed_filter=True, output_format="list", top=10
        )

        assert "freshly.com" in result
        assert "olipop.com" not in result

    def test_seed_filter_false_returns_all(self, test_db):
        """Without --seed-filter, all company_files domains returned."""
        _insert_company_file(test_db, "freshly.com", metadata={"manual_seed": True})
        _insert_company_file(test_db, "olipop.com", metadata={})

        result = seed_domains(
            test_db, source="company_files", seed_filter=False, output_format="list", top=10
        )

        assert "freshly.com" in result
        assert "olipop.com" in result

    def test_company_files_infra_filtered(self, test_db):
        """Infra domains in company_files are filtered out."""
        _insert_company_file(test_db, "myapp.vercel.app", metadata={"manual_seed": True})
        _insert_company_file(test_db, "freshly.com", metadata={"manual_seed": True})

        result = seed_domains(
            test_db, source="company_files", output_format="list", top=10
        )

        assert "myapp.vercel.app" not in result
        assert "freshly.com" in result

    def test_empty_company_files(self, test_db):
        """Empty company_files returns empty list."""
        result = seed_domains(
            test_db, source="company_files", output_format="list", top=10
        )
        assert result == []


# ============================================================================
# Backward-compat symbol tests
# ============================================================================

class TestBackwardCompat:
    """Backward-compat aliases are importable and correct."""

    def test_infra_denylist_alias(self):
        """INFRA_DENYLIST is a set equal to INFRA_DOMAIN_SUFFIXES."""
        assert isinstance(INFRA_DENYLIST, set)
        assert INFRA_DENYLIST == INFRA_DOMAIN_SUFFIXES

    def test_social_platform_denylist_alias(self):
        """SOCIAL_PLATFORM_DENYLIST is a set equal to SOCIAL_PLATFORM_SUFFIXES."""
        assert isinstance(SOCIAL_PLATFORM_DENYLIST, set)
        assert SOCIAL_PLATFORM_DENYLIST == SOCIAL_PLATFORM_SUFFIXES

    def test_is_infra_domain_importable(self):
        """_is_infra_domain is importable and works."""
        assert _is_infra_domain("vercel.app") is True
        assert _is_infra_domain("acme.ai") is False

    def test_is_social_platform_importable(self):
        """_is_social_platform is importable and works."""
        assert _is_social_platform("ycombinator.com") is True
        assert _is_social_platform("acme.ai") is False


# ============================================================================
# _is_filtered combined helper
# ============================================================================

class TestIsFiltered:
    """Combined filter helper."""

    def test_publisher_filtered(self):
        assert _is_filtered("techcrunch.com") is True

    def test_infra_filtered(self):
        assert _is_filtered("myapp.vercel.app") is True

    def test_social_filtered(self):
        assert _is_filtered("ycombinator.com") is True

    def test_clean_domain_passes(self):
        assert _is_filtered("acme.ai") is False
