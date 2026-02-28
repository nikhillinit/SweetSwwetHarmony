"""
Tests for monitoring/activation_gate.py -- step-specific activation readiness.

15 tests covering the full step-specific policy matrix:
- canary pass/fail/degraded/missing per step
- critical/warning drift alerts per step
- stale canary thresholds (48h for steps 1-2, 24h for steps 3-4)
- SPC drift coverage: step 4 blocks, step 3 warns, steps 1-2 unaffected
- drift_coverage in to_dict
- env override validation with fallback
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
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


_run_counter = 0


async def _insert_canary_run(store, verdict="pass", pass_rate=1.0, created_at=None):
    """Insert a canary_runs row for testing."""
    global _run_counter
    _run_counter += 1
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    db = store._db
    run_id = f"run-{_run_counter}-{verdict}"
    # Insert parent run_history row to satisfy FK constraint
    await db.execute(
        "INSERT OR IGNORE INTO run_history (id, run_type, status, started_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, "canary", "completed", created_at, created_at),
    )
    await db.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored, passed, failed,
            skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            10, "abc123", 10,
            int(pass_rate * 10), int((1 - pass_rate) * 10), 0,
            pass_rate, verdict, created_at,
        ),
    )
    await db.commit()


async def _insert_drift_alert(store, severity="warning", status="open"):
    """Insert a canary_drift_alerts row for testing."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    await db.execute(
        """INSERT INTO canary_drift_alerts
           (alert_type, severity, metric_name, message, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("pass_rate_drop", severity, "test_metric", f"Test {severity} alert", status, now),
    )
    await db.commit()


# =============================================================================
# TESTS
# =============================================================================

class TestActivationGate:
    @pytest.mark.asyncio
    async def test_gate_ready_when_canary_pass_and_no_alerts(self, store):
        """Canary pass + no alerts -> ready for all steps."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)
        result = await check_activation_readiness(store, step=1)
        assert result.verdict == "ready"
        assert result.can_proceed is True

    @pytest.mark.asyncio
    async def test_gate_blocked_on_canary_fail(self, store):
        """Canary fail -> blocked for ALL steps."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="fail", pass_rate=0.5)
        for step in (1, 2, 3, 4):
            result = await check_activation_readiness(store, step=step)
            assert result.verdict == "blocked", f"step {step} should be blocked on canary fail"
            assert result.can_proceed is False

    @pytest.mark.asyncio
    async def test_gate_blocked_on_canary_degraded_step2(self, store):
        """Canary degraded -> blocked for step >= 2."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="degraded", pass_rate=0.8)
        result = await check_activation_readiness(store, step=2)
        assert result.verdict == "blocked"
        assert result.can_proceed is False

    @pytest.mark.asyncio
    async def test_gate_warns_on_canary_degraded_step1(self, store):
        """Canary degraded -> warn for step 1 (shadow lenient)."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="degraded", pass_rate=0.8)
        result = await check_activation_readiness(store, step=1)
        assert result.verdict == "warn"
        assert result.can_proceed is True

    @pytest.mark.asyncio
    async def test_gate_warns_on_no_canary_step1(self, store):
        """No canary data -> warn for step 1."""
        from monitoring.activation_gate import check_activation_readiness

        result = await check_activation_readiness(store, step=1)
        assert result.verdict == "warn"
        assert result.can_proceed is True
        assert any("no canary" in r.lower() for r in result.reasons)

    @pytest.mark.asyncio
    async def test_gate_blocked_on_no_canary_step3(self, store):
        """No canary data -> blocked for step >= 3."""
        from monitoring.activation_gate import check_activation_readiness

        for step in (3, 4):
            result = await check_activation_readiness(store, step=step)
            assert result.verdict == "blocked", f"step {step} should block with no canary"
            assert result.can_proceed is False

    @pytest.mark.asyncio
    async def test_gate_blocked_on_open_critical_drift_alerts(self, store):
        """Open critical drift alert -> blocked for ALL steps."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)
        await _insert_drift_alert(store, severity="critical", status="open")

        for step in (1, 2, 3, 4):
            result = await check_activation_readiness(store, step=step)
            assert result.verdict == "blocked", f"step {step} should block on critical alert"

    @pytest.mark.asyncio
    async def test_gate_warning_alerts_policy_by_step(self, store):
        """Warning alerts: step 1 passes, step 2 warns, step 4 blocks."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)
        await _insert_drift_alert(store, severity="warning", status="open")

        r1 = await check_activation_readiness(store, step=1)
        assert r1.verdict == "ready"  # warning alerts pass at step 1

        r2 = await check_activation_readiness(store, step=2)
        assert r2.verdict == "warn"  # warning alerts warn at step 2

        r4 = await check_activation_readiness(store, step=4)
        assert r4.verdict == "blocked"  # warning alerts block at step 4

    @pytest.mark.asyncio
    async def test_gate_stale_canary_step_thresholds(self, store):
        """25h-old canary: ready for steps 1-2 (within 48h), blocked for steps 3-4 (exceeds 24h)."""
        from monitoring.activation_gate import check_activation_readiness

        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        await _insert_canary_run(store, verdict="pass", pass_rate=1.0, created_at=stale_time)

        # 25h < 48h threshold -> ready for steps 1-2
        for step in (1, 2):
            result = await check_activation_readiness(store, step=step)
            assert result.verdict == "ready", f"step {step}: 25h canary within 48h threshold"
            assert result.can_proceed is True

        # 25h > 24h threshold -> blocked for steps 3-4
        for step in (3, 4):
            result = await check_activation_readiness(store, step=step)
            assert result.verdict == "blocked", f"step {step}: 25h canary exceeds 24h threshold"
            assert result.can_proceed is False


class TestSPCCoverage:
    """Tests for SPC drift coverage checks in activation gate."""

    @pytest.mark.asyncio
    async def test_step4_blocks_on_required_insufficient_data(self, store):
        """Step 4 blocks when required SPC metrics have insufficient data."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        # Mock _evaluate_spc_coverage to return insufficient_data for required metrics
        with patch(
            "monitoring.activation_gate._evaluate_spc_coverage",
            return_value={
                "collector_volume": "ok",
                "overall_fp_rate": "insufficient_data",
                "confidence_calibration_ece": "insufficient_data",
                "quarantine_regret": "insufficient_data",
            },
        ):
            result = await check_activation_readiness(store, step=4)

        assert result.verdict == "blocked"
        assert result.can_proceed is False
        assert any("required drift monitor" in r for r in result.reasons)
        assert "overall_fp_rate" in result.drift_coverage

    @pytest.mark.asyncio
    async def test_step3_warns_on_required_insufficient_data(self, store):
        """Step 3 warns (non-blocking) when required SPC metrics have insufficient data."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        with patch(
            "monitoring.activation_gate._evaluate_spc_coverage",
            return_value={
                "collector_volume": "insufficient_data",
                "overall_fp_rate": "insufficient_data",
                "confidence_calibration_ece": "insufficient_data",
                "quarantine_regret": "insufficient_data",
            },
        ):
            result = await check_activation_readiness(store, step=3)

        assert result.verdict == "warn"
        assert result.can_proceed is True
        assert any("required drift monitor" in r for r in result.reasons)

    @pytest.mark.asyncio
    async def test_step4_blocks_on_evaluator_error_fail_closed(self, store):
        """Step 4 blocks when SPC evaluator raises (fail-closed)."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        with patch(
            "monitoring.activation_gate._evaluate_spc_coverage",
            side_effect=RuntimeError("DB locked"),
        ):
            result = await check_activation_readiness(store, step=4)

        assert result.verdict == "blocked"
        assert result.can_proceed is False
        # All metrics should be "error"
        for m in ("collector_volume", "overall_fp_rate"):
            assert result.drift_coverage.get(m) == "error"

    @pytest.mark.asyncio
    async def test_steps_1_2_unaffected_without_spc_checks(self, store):
        """Steps 1 and 2 have no SPC requirements — drift_coverage is empty."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        for step in (1, 2):
            result = await check_activation_readiness(store, step=step)
            assert result.drift_coverage == {}, f"step {step} should have empty drift_coverage"
            assert result.verdict == "ready"

    @pytest.mark.asyncio
    async def test_drift_coverage_in_to_dict(self, store):
        """drift_coverage appears in to_dict() output."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        with patch(
            "monitoring.activation_gate._evaluate_spc_coverage",
            return_value={
                "collector_volume": "ok",
                "overall_fp_rate": "ok",
                "confidence_calibration_ece": "insufficient_data",
                "quarantine_regret": "insufficient_data",
            },
        ):
            result = await check_activation_readiness(store, step=3)

        d = result.to_dict()
        assert "drift_coverage" in d
        assert d["drift_coverage"]["collector_volume"] == "ok"
        assert d["drift_coverage"]["overall_fp_rate"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_spc_required_metrics_falls_back_to_defaults(self, store):
        """Invalid SPC_REQUIRED_METRICS env falls back to policy defaults."""
        from monitoring.activation_gate import check_activation_readiness

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        with patch(
            "monitoring.activation_gate._evaluate_spc_coverage",
            return_value={
                "collector_volume": "insufficient_data",
                "overall_fp_rate": "insufficient_data",
                "confidence_calibration_ece": "insufficient_data",
                "quarantine_regret": "insufficient_data",
            },
        ) as mock_eval:
            with patch.dict(os.environ, {"SPC_REQUIRED_METRICS": "bogus_metric,also_bad"}):
                result = await check_activation_readiness(store, step=4)

        # Should fall back to policy defaults and still block
        assert result.verdict == "blocked"
        # The evaluator should have been called with the default metrics
        call_args = mock_eval.call_args
        metrics_arg = call_args[0][1]  # positional arg 1 = metrics list
        assert "collector_volume" in metrics_arg
        assert "overall_fp_rate" in metrics_arg
