"""
Tests for seed_job_posting_domains.py script.

Verifies frequency ranking, three-layer filtering, domain normalization,
time window, and --top limit.
"""

from __future__ import annotations

import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, ".")

from scripts.seed_job_posting_domains import (
    seed_domains,
    _is_infra_domain,
    _is_social_platform,
    INFRA_DENYLIST,
    SOCIAL_PLATFORM_DENYLIST,
)


@pytest.fixture
def test_db(tmp_path):
    """Create a test DB with signals table."""
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


class TestFrequencyRanking:
    """Highest count first."""

    def test_frequency_order(self, test_db):
        """More frequent domains appear first."""
        now = datetime.now(timezone.utc).isoformat()
        # acme.ai appears 3 times
        for _ in range(3):
            _insert_signal(test_db, "domain:acme.ai", created_at=now)
        # betaco.com appears 1 time
        _insert_signal(test_db, "domain:betaco.com", created_at=now)
        # gamma.io appears 2 times
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
        """All INFRA_DENYLIST entries should be caught."""
        for d in INFRA_DENYLIST:
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
        """All SOCIAL_PLATFORM_DENYLIST entries should be caught."""
        for d in SOCIAL_PLATFORM_DENYLIST:
            assert _is_social_platform(d) is True, f"{d} not blocked"


class TestDomainNormalization:
    """www.acme.ai and acme.ai merged before ranking."""

    def test_www_merged(self, test_db):
        """www.acme.ai and acme.ai should be counted together."""
        now = datetime.now(timezone.utc).isoformat()
        # 2x www.acme.ai + 1x acme.ai = 3 total for acme.ai
        for _ in range(2):
            _insert_signal(test_db, "domain:www.acme.ai", created_at=now)
        _insert_signal(test_db, "domain:acme.ai", created_at=now)
        # 2x betaco.com
        for _ in range(2):
            _insert_signal(test_db, "domain:betaco.com", created_at=now)

        result = seed_domains(test_db, days=90, top=10, output_format="list")

        # acme.ai (3) should appear before betaco.com (2)
        assert "acme.ai" in result
        assert "www.acme.ai" not in result  # Normalized to acme.ai
        if "betaco.com" in result:
            assert result.index("acme.ai") < result.index("betaco.com")


class TestTimeWindow:
    """Old signals are excluded."""

    def test_old_signals_excluded(self, test_db):
        """Signals older than --days are excluded."""
        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

        # Recent signal
        _insert_signal(test_db, "domain:acme.ai", created_at=now)
        # Old signal (outside 90-day window)
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
        """No signals → empty list."""
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
