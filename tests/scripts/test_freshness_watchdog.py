from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "red-team-hybrid" / "freshness_watchdog.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("freshness_watchdog", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_operational_collectors_exclude_optional_news_api() -> None:
    module = _load_module()

    assert module.DEFAULT_OPERATIONAL_COLLECTORS == (
        "hacker_news",
        "arxiv",
        "rss_feeds",
    )
    assert "news_api" not in module.DEFAULT_OPERATIONAL_COLLECTORS


def test_min_created_at_blocks_duplicate_only_success() -> None:
    module = _load_module()
    now = datetime(2026, 5, 13, 15, 0, 24, tzinfo=timezone.utc)
    run_start = datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)
    freshness = {
        "greenhouse_jobs": datetime(2026, 5, 13, 8, 53, 22, tzinfo=timezone.utc),
        "ashby_jobs": datetime(2026, 5, 13, 8, 53, 22, tzinfo=timezone.utc),
    }

    records = module.classify(
        freshness,
        ("greenhouse_jobs", "ashby_jobs"),
        timedelta(hours=12),
        now,
        min_created_at=run_start,
    )
    exit_code, failures = module.verdict(records)

    assert exit_code == 1
    assert {record["source_api"]: record["status"] for record in records} == {
        "ashby_jobs": "STALE",
        "greenhouse_jobs": "STALE",
    }
    assert all(record["stale_reason"] == "no_post_run_rows" for record in records)
    assert "not after required 2026-05-13T15:00:00+00:00" in failures[0]


def test_rolling_freshness_still_passes_without_min_created_at() -> None:
    module = _load_module()
    now = datetime(2026, 5, 13, 15, 0, 24, tzinfo=timezone.utc)
    freshness = {
        "greenhouse_jobs": datetime(2026, 5, 13, 8, 53, 22, tzinfo=timezone.utc),
        "ashby_jobs": datetime(2026, 5, 13, 8, 53, 22, tzinfo=timezone.utc),
    }

    records = module.classify(
        freshness,
        ("greenhouse_jobs", "ashby_jobs"),
        timedelta(hours=12),
        now,
    )
    exit_code, failures = module.verdict(records)

    assert exit_code == 0
    assert failures == []
    assert {record["source_api"]: record["status"] for record in records} == {
        "ashby_jobs": "FRESH",
        "greenhouse_jobs": "FRESH",
    }


def test_fresh_empty_expected_stale_not_counted_as_failure() -> None:
    """A stale operational collector marked fresh_empty_expected should not fail the gate."""
    module = _load_module()
    now = datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)
    freshness = {
        "hacker_news": now - timedelta(hours=1),
        "news_api": now - timedelta(hours=72),  # stale
    }
    records = module.classify(
        freshness,
        operational=("hacker_news", "news_api"),
        threshold=timedelta(hours=36),
        now=now,
        fresh_empty=("news_api",),
    )
    exit_code, failures = module.verdict(records)

    statuses = {r["source_api"]: r["status"] for r in records}
    assert statuses["news_api"] == "fresh_empty_expected"
    assert statuses["hacker_news"] == "FRESH"
    assert exit_code == 0
    assert failures == []


def test_fresh_empty_expected_missing_not_counted_as_failure() -> None:
    """A missing operational collector marked fresh_empty_expected should not fail the gate."""
    module = _load_module()
    now = datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)
    freshness = {"hacker_news": now - timedelta(hours=1)}
    records = module.classify(
        freshness,
        operational=("hacker_news", "news_api"),
        threshold=timedelta(hours=36),
        now=now,
        fresh_empty=("news_api",),
    )
    exit_code, failures = module.verdict(records)

    statuses = {r["source_api"]: r["status"] for r in records}
    assert statuses["news_api"] == "fresh_empty_expected"
    assert exit_code == 0
    assert failures == []


def test_fresh_empty_expected_does_not_suppress_other_failures() -> None:
    """news_api being fresh_empty_expected must not mask a real hacker_news failure."""
    module = _load_module()
    now = datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)
    freshness = {
        "hacker_news": now - timedelta(hours=72),  # stale — real failure
        "news_api": now - timedelta(hours=72),
    }
    records = module.classify(
        freshness,
        operational=("hacker_news", "news_api"),
        threshold=timedelta(hours=36),
        now=now,
        fresh_empty=("news_api",),
    )
    exit_code, failures = module.verdict(records)

    assert exit_code == 1
    assert any("hacker_news" in f for f in failures)
    assert not any("news_api" in f for f in failures)
