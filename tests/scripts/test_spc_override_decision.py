"""Tests for scripts/spc_override_decision.py."""

from __future__ import annotations

import tempfile
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from scripts.spc_override_decision import evaluate_override_decision
from storage.signal_store import SignalStore


async def _insert_canary_run(store, verdict: str = "pass", pass_rate: float = 0.95) -> None:
    now = datetime.now(timezone.utc).isoformat()
    run_id = f"spc-override-{verdict}"
    await store._db.execute(
        "INSERT OR IGNORE INTO run_history (id, run_type, status, started_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, "canary", "completed", now, now),
    )
    await store._db.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored, passed, failed,
            skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            20,
            "golden-hash",
            20,
            int(pass_rate * 20),
            20 - int(pass_rate * 20),
            0,
            pass_rate,
            verdict,
            now,
        ),
    )
    await store._db.commit()


async def _seed_labeled_signals(store, labels_per_day: int, days: int = 20) -> None:
    signal_id = 1
    base = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)

    for day_offset in range(1, days + 1):
        detected_at = (base - timedelta(days=day_offset)).isoformat()
        for idx in range(labels_per_day):
            confidence = 0.8 if idx == 0 else 0.7
            label = "TP" if idx == 0 else "FP"
            await store._db.execute(
                "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    signal_id,
                    "github_repo",
                    "github",
                    f"domain:example-{signal_id}.com",
                    f"Example {signal_id}",
                    confidence,
                    "{}",
                    detected_at,
                    detected_at,
                ),
            )
            await store._db.execute(
                "INSERT INTO signal_quality_metrics (signal_id, canonical_key, human_label, labeled_at, label_source) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    signal_id,
                    f"domain:example-{signal_id}.com",
                    label,
                    detected_at,
                    "test",
                ),
            )
            signal_id += 1

    await store._db.commit()


@pytest_asyncio.fixture
async def sparse_db_path() -> Path:
    fd, raw_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Path(raw_path).unlink(missing_ok=True)
    db_path = Path(raw_path)
    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        await _insert_canary_run(store)
        await _seed_labeled_signals(store, labels_per_day=5, days=20)
    finally:
        await store.close()
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def dense_db_path() -> Path:
    fd, raw_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Path(raw_path).unlink(missing_ok=True)
    db_path = Path(raw_path)
    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        await _insert_canary_run(store)
        await _seed_labeled_signals(store, labels_per_day=10, days=20)
    finally:
        await store.close()
    yield db_path
    db_path.unlink(missing_ok=True)


class TestEvaluateOverrideDecision:
    def test_reports_proceed_with_exception_when_defaults_lose_overall_fp_rate(self, sparse_db_path, monkeypatch):
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        monkeypatch.setenv("SPC_MIN_LABELED_PER_DAY", "3")

        report = evaluate_override_decision(sparse_db_path, steps=(3, 4), backfill_days=30)

        assert report["outcome"] == "proceed_with_exception"
        assert report["active_overrides"] == {
            "SPC_MIN_BASELINE_DAYS": 7,
            "SPC_MIN_LABELED_PER_DAY": 3,
        }
        assert report["required_metric_losses"]["3"] == ["overall_fp_rate"]
        assert report["required_metric_losses"]["4"] == ["overall_fp_rate"]
        assert report["profiles"]["active"]["steps"]["4"]["can_proceed"] is True
        assert report["profiles"]["defaults"]["steps"]["4"]["can_proceed"] is False

    def test_reports_proceed_without_exception_when_defaults_also_pass(self, dense_db_path, monkeypatch):
        monkeypatch.setenv("SPC_MIN_BASELINE_DAYS", "7")
        monkeypatch.setenv("SPC_MIN_LABELED_PER_DAY", "3")

        report = evaluate_override_decision(dense_db_path, steps=(3, 4), backfill_days=30)

        assert report["outcome"] == "proceed_without_exception"
        assert report["required_metric_losses"] == {}
        assert report["profiles"]["defaults"]["steps"]["4"]["can_proceed"] is True
