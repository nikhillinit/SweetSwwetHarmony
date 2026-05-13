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
