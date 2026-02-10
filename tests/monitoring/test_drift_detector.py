"""Tests for monitoring/drift_detector.py — Version-compatible canary drift analysis."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import pytest_asyncio

from monitoring.drift_detector import (
    DriftAlert,
    DriftResult,
    _safe_json,
    detect_drift,
    store_drift_alerts,
)
from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================


@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with temp file DB and Wave 2 tables."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _insert_canary_run(
    store,
    *,
    run_id: int,
    golden_set_hash: str = "hash123",
    config_hash: str | None = None,
    pass_rate: float = 0.9,
    verdict: str = "pass",
    results_json: str | None = None,
    stratification_json: str | None = None,
    created_at: str = "2026-01-01T00:00:00",
):
    """Insert a canary_runs row directly for testing."""
    db = store._db
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        """
        INSERT INTO canary_runs (
            id, run_id, golden_set_size, golden_set_hash, config_hash,
            total_scored, passed, failed, skipped,
            pass_rate, verdict, results_json, stratification_json, created_at
        ) VALUES (?, ?, 10, ?, ?, 10, ?, ?, 0, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            f"run-{run_id}",
            golden_set_hash,
            config_hash,
            int(pass_rate * 10),        # passed
            10 - int(pass_rate * 10),    # failed
            pass_rate,
            verdict,
            results_json,
            stratification_json,
            created_at,
        ),
    )
    await db.commit()


# =============================================================================
# detect_drift
# =============================================================================


class TestDetectDrift:
    """Tests for the detect_drift function."""

    @pytest.mark.asyncio
    async def test_no_current_run(self, store):
        """When current_run_id does not exist, return no_baseline verdict."""
        result = await detect_drift(store, current_run_id=9999, golden_set_hash="abc")
        assert result.verdict == "no_baseline"
        assert "not found" in result.baseline_message.lower()

    @pytest.mark.asyncio
    async def test_no_compatible_baseline(self, store):
        """When no prior run matches golden_set_hash, return no_baseline."""
        await _insert_canary_run(store, run_id=1, golden_set_hash="unique_hash",
                                 created_at="2026-01-01T00:00:00")
        result = await detect_drift(store, current_run_id=1, golden_set_hash="unique_hash")
        assert result.verdict == "no_baseline"
        assert "unique_hash" in result.baseline_message

    @pytest.mark.asyncio
    async def test_pass_rate_critical_drop(self, store):
        """Pass rate drop >15% triggers critical alert and fail verdict."""
        # Baseline: 90% pass rate
        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 created_at="2026-01-01T00:00:00")
        # Current: 70% pass rate (20% drop)
        await _insert_canary_run(store, run_id=2, pass_rate=0.7,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        assert result.verdict == "fail"
        assert result.baseline_run_id == 1
        critical_alerts = [a for a in result.alerts if a.severity == "critical"]
        assert len(critical_alerts) >= 1
        assert critical_alerts[0].alert_type == "pass_rate_drop"
        assert critical_alerts[0].delta < -0.15

    @pytest.mark.asyncio
    async def test_pass_rate_warning_drop(self, store):
        """Pass rate drop 5-15% triggers warning alert and degraded verdict."""
        # Baseline: 90%
        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 created_at="2026-01-01T00:00:00")
        # Current: 82% (8% drop)
        await _insert_canary_run(store, run_id=2, pass_rate=0.82,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        assert result.verdict == "degraded"
        warnings = [a for a in result.alerts if a.severity == "warning"]
        assert len(warnings) >= 1
        assert warnings[0].alert_type == "pass_rate_drop"

    @pytest.mark.asyncio
    async def test_pass_rate_improvement(self, store):
        """Pass rate improvement >5% triggers info alert but pass verdict."""
        # Baseline: 80%
        await _insert_canary_run(store, run_id=1, pass_rate=0.8,
                                 created_at="2026-01-01T00:00:00")
        # Current: 92% (+12% improvement)
        await _insert_canary_run(store, run_id=2, pass_rate=0.92,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        assert result.verdict == "pass"
        info_alerts = [a for a in result.alerts if a.severity == "info"]
        assert len(info_alerts) >= 1
        assert info_alerts[0].alert_type == "pass_rate_improvement"
        assert info_alerts[0].delta > 0.05

    @pytest.mark.asyncio
    async def test_pass_rate_no_drift(self, store):
        """Pass rate change <5% triggers no alerts, verdict is pass."""
        # Baseline: 88%
        await _insert_canary_run(store, run_id=1, pass_rate=0.88,
                                 created_at="2026-01-01T00:00:00")
        # Current: 90% (only +2%)
        await _insert_canary_run(store, run_id=2, pass_rate=0.90,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        assert result.verdict == "pass"
        assert len(result.alerts) == 0

    @pytest.mark.asyncio
    async def test_individual_signal_drift(self, store):
        """Per-signal confidence delta > threshold triggers warning."""
        baseline_results = json.dumps([
            {"signal_id": 1, "actual_confidence": 0.8, "canonical_key": "domain:test.com"},
        ])
        current_results = json.dumps([
            {"signal_id": 1, "actual_confidence": 0.5, "canonical_key": "domain:test.com"},
        ])

        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 results_json=baseline_results,
                                 created_at="2026-01-01T00:00:00")
        await _insert_canary_run(store, run_id=2, pass_rate=0.9,
                                 results_json=current_results,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(
            store, current_run_id=2, golden_set_hash="hash123", drift_threshold=0.15
        )

        individual = [a for a in result.alerts if a.alert_type == "individual_drift"]
        assert len(individual) == 1
        assert individual[0].signal_id == 1
        assert individual[0].canonical_key == "domain:test.com"
        assert abs(individual[0].delta) > 0.15

    @pytest.mark.asyncio
    async def test_individual_signal_no_drift(self, store):
        """Per-signal confidence within threshold triggers no alert."""
        baseline_results = json.dumps([
            {"signal_id": 1, "actual_confidence": 0.8, "canonical_key": "domain:test.com"},
        ])
        current_results = json.dumps([
            {"signal_id": 1, "actual_confidence": 0.75, "canonical_key": "domain:test.com"},
        ])

        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 results_json=baseline_results,
                                 created_at="2026-01-01T00:00:00")
        await _insert_canary_run(store, run_id=2, pass_rate=0.9,
                                 results_json=current_results,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(
            store, current_run_id=2, golden_set_hash="hash123", drift_threshold=0.15
        )

        individual = [a for a in result.alerts if a.alert_type == "individual_drift"]
        assert len(individual) == 0

    @pytest.mark.asyncio
    async def test_archetype_regression(self, store):
        """Stratum pass_rate drop >10% triggers archetype_regression warning."""
        baseline_strat = json.dumps({
            "archetype:cpg": {"count": 5, "pass_rate": 0.8},
        })
        current_strat = json.dumps({
            "archetype:cpg": {"count": 5, "pass_rate": 0.6},
        })

        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 stratification_json=baseline_strat,
                                 created_at="2026-01-01T00:00:00")
        await _insert_canary_run(store, run_id=2, pass_rate=0.9,
                                 stratification_json=current_strat,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        arch_alerts = [a for a in result.alerts if a.alert_type == "archetype_regression"]
        assert len(arch_alerts) == 1
        assert arch_alerts[0].severity == "warning"
        assert arch_alerts[0].delta < -0.10
        assert "cpg" in arch_alerts[0].metric_name

    @pytest.mark.asyncio
    async def test_archetype_improvement(self, store):
        """Stratum pass_rate improvement >10% triggers archetype_improvement info."""
        baseline_strat = json.dumps({
            "archetype:cpg": {"count": 5, "pass_rate": 0.6},
        })
        current_strat = json.dumps({
            "archetype:cpg": {"count": 5, "pass_rate": 0.8},
        })

        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 stratification_json=baseline_strat,
                                 created_at="2026-01-01T00:00:00")
        await _insert_canary_run(store, run_id=2, pass_rate=0.9,
                                 stratification_json=current_strat,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        arch_alerts = [a for a in result.alerts if a.alert_type == "archetype_improvement"]
        assert len(arch_alerts) == 1
        assert arch_alerts[0].severity == "info"
        assert arch_alerts[0].delta > 0.10

    @pytest.mark.asyncio
    async def test_stratum_below_min_size_skipped(self, store):
        """Strata with count < MIN_STRATUM_SIZE (3) are skipped."""
        baseline_strat = json.dumps({
            "archetype:tiny": {"count": 2, "pass_rate": 0.9},
        })
        current_strat = json.dumps({
            "archetype:tiny": {"count": 2, "pass_rate": 0.3},
        })

        await _insert_canary_run(store, run_id=1, pass_rate=0.9,
                                 stratification_json=baseline_strat,
                                 created_at="2026-01-01T00:00:00")
        await _insert_canary_run(store, run_id=2, pass_rate=0.9,
                                 stratification_json=current_strat,
                                 created_at="2026-01-02T00:00:00")

        result = await detect_drift(store, current_run_id=2, golden_set_hash="hash123")

        arch_alerts = [
            a for a in result.alerts
            if a.alert_type in ("archetype_regression", "archetype_improvement")
        ]
        assert len(arch_alerts) == 0, "Small strata should not produce alerts"

    @pytest.mark.asyncio
    async def test_config_hash_matching(self, store):
        """When config_hash is specified, only baselines with matching config_hash are used."""
        # Baseline with config_hash=None (different)
        await _insert_canary_run(store, run_id=1, pass_rate=0.9, config_hash=None,
                                 created_at="2026-01-01T00:00:00")
        # Baseline with config_hash="cfg-A"
        await _insert_canary_run(store, run_id=2, pass_rate=0.95, config_hash="cfg-A",
                                 created_at="2026-01-02T00:00:00")
        # Current with config_hash="cfg-A"
        await _insert_canary_run(store, run_id=3, pass_rate=0.7, config_hash="cfg-A",
                                 created_at="2026-01-03T00:00:00")

        result = await detect_drift(
            store, current_run_id=3, golden_set_hash="hash123", config_hash="cfg-A"
        )

        # Should use run_id=2 as baseline (matching config_hash), not run_id=1
        assert result.baseline_run_id == 2


# =============================================================================
# store_drift_alerts
# =============================================================================


class TestStoreDriftAlerts:
    """Tests for persisting drift alerts."""

    @pytest.mark.asyncio
    async def test_store_alerts_returns_count(self, store):
        """store_drift_alerts returns the number of alerts stored."""
        # Insert a dummy canary run to satisfy FK
        await _insert_canary_run(store, run_id=1, created_at="2026-01-01T00:00:00")

        alerts = [
            DriftAlert(alert_type="pass_rate_drop", severity="critical",
                       metric_name="pass_rate", message="Dropped"),
            DriftAlert(alert_type="individual_drift", severity="warning",
                       signal_id=1, metric_name="confidence", message="Drifted"),
        ]
        count = await store_drift_alerts(store, canary_run_id=1, alerts=alerts)
        assert count == 2

    @pytest.mark.asyncio
    async def test_store_alerts_persisted_correctly(self, store):
        """Alerts are persisted with correct field values."""
        await _insert_canary_run(store, run_id=1, created_at="2026-01-01T00:00:00")

        alert = DriftAlert(
            alert_type="pass_rate_drop",
            severity="critical",
            signal_id=42,
            canonical_key="domain:example.com",
            metric_name="pass_rate",
            expected_value=0.9,
            actual_value=0.7,
            delta=-0.2,
            message="Pass rate dropped from 90% to 70%",
        )
        count = await store_drift_alerts(store, canary_run_id=1, alerts=[alert])
        assert count == 1

        db = store._db
        cursor = await db.execute(
            "SELECT alert_type, severity, signal_id, canonical_key, "
            "metric_name, expected_value, actual_value, delta, message, status "
            "FROM canary_drift_alerts WHERE canary_run_id = 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "pass_rate_drop"
        assert row[1] == "critical"
        assert row[2] == 42
        assert row[3] == "domain:example.com"
        assert row[4] == "pass_rate"
        assert row[5] == pytest.approx(0.9)
        assert row[6] == pytest.approx(0.7)
        assert row[7] == pytest.approx(-0.2)
        assert row[8] == "Pass rate dropped from 90% to 70%"
        assert row[9] == "open"


# =============================================================================
# _safe_json
# =============================================================================


class TestSafeJson:
    """Tests for the _safe_json helper."""

    def test_none_input(self):
        """None input returns None."""
        assert _safe_json(None) is None

    def test_valid_json_string(self):
        """Valid JSON string is parsed."""
        assert _safe_json('{"a": 1}') == {"a": 1}

    def test_json_list_string(self):
        """Valid JSON array string is parsed."""
        assert _safe_json('[1, 2, 3]') == [1, 2, 3]

    def test_already_dict(self):
        """A dict is returned as-is."""
        d = {"key": "value"}
        assert _safe_json(d) is d

    def test_already_list(self):
        """A list is returned as-is."""
        lst = [1, 2, 3]
        assert _safe_json(lst) is lst

    def test_invalid_json_string(self):
        """Invalid JSON string returns None."""
        assert _safe_json("{not valid json}") is None

    def test_non_string_non_collection(self):
        """Integer input returns None (cannot be parsed)."""
        assert _safe_json(42) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
