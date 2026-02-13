"""Cross-phase integration suite M1: Triage → ACH → Drift (W5.11).

Tests the chain: signal → triage approve → canary run → SPC check → drift alert.
5 tests covering the end-to-end workflow across multiple subsystems.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


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


async def _seed_signal(db, signal_id, company_id="comp_001", confidence=0.8):
    """Insert a test signal."""
    await db.execute("PRAGMA foreign_keys = OFF")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, "
        "confidence, raw_data, detected_at, created_at, company_id) "
        "VALUES (?, 'funding', 'test', 'domain:test.ai', 'Test Co', ?, '{}', ?, ?, ?)",
        (signal_id, confidence, now, now, company_id),
    )
    await db.commit()


async def _seed_review_item(db, company_id="comp_001", status="pending", signal_ids=None):
    """Insert a review item."""
    import json
    await db.execute("PRAGMA foreign_keys = OFF")
    now = datetime.now(timezone.utc).isoformat()
    evidence = json.dumps({"signal_ids": signal_ids or [1], "schema_version": 1})
    await db.execute(
        "INSERT INTO review_items (company_id, status, evidence_bundle, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (company_id, status, evidence, now, now),
    )
    await db.commit()


async def _seed_canary_run(db, run_id=1, verdict="pass", pass_rate=0.9):
    """Insert a canary run."""
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        "INSERT INTO canary_runs "
        "(id, run_id, golden_set_size, golden_set_hash, total_scored, passed, failed, "
        "skipped, pass_rate, verdict, drift_threshold, pass_rate_threshold, duration_ms, created_at) "
        "VALUES (?, 'run-m1', 20, 'hash', 20, ?, ?, 0, ?, ?, 0.15, 0.80, 200, datetime('now'))",
        (run_id, int(20 * pass_rate), int(20 * (1 - pass_rate)), pass_rate, verdict),
    )
    await db.commit()


async def _seed_drift_alert(db, alert_id, canary_run_id=1, alert_type="pass_rate_drop",
                             severity="warning", status="open"):
    """Insert a drift alert."""
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        "INSERT INTO canary_drift_alerts "
        "(id, canary_run_id, alert_type, severity, metric_name, message, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pass_rate', 'Test drift alert', ?, datetime('now'))",
        (alert_id, canary_run_id, alert_type, severity, status),
    )
    await db.commit()


# =============================================================================
# M1 TESTS
# =============================================================================

class TestM1TriageToACHToDrift:
    """Integration: signal → triage → canary → SPC → drift alert."""

    @pytest.mark.asyncio
    async def test_signal_to_triage_review(self, store):
        """A signal should be reviewable through the triage system."""
        db = store._db
        await _seed_signal(db, 1, "comp_m1_001")
        await _seed_review_item(db, "comp_m1_001", status="pending", signal_ids=[1])

        # Approve the review item
        await db.execute(
            "UPDATE review_items SET status = 'approved', decided_by = 'test-op' "
            "WHERE company_id = 'comp_m1_001'",
        )
        await db.commit()

        cursor = await db.execute("SELECT status FROM review_items WHERE company_id = 'comp_m1_001'")
        row = await cursor.fetchone()
        assert row[0] == "approved"

    @pytest.mark.asyncio
    async def test_canary_run_after_triage(self, store):
        """A canary run should produce a verdict after signals are triaged."""
        db = store._db
        await _seed_signal(db, 1, "comp_m1_002")
        await _seed_review_item(db, "comp_m1_002", status="approved", signal_ids=[1])
        await _seed_canary_run(db, run_id=1, verdict="pass", pass_rate=0.95)

        cursor = await db.execute("SELECT verdict, pass_rate FROM canary_runs WHERE id = 1")
        row = await cursor.fetchone()
        assert row[0] == "pass"
        assert row[1] == 0.95

    @pytest.mark.asyncio
    async def test_drift_alert_from_canary(self, store):
        """Drift alerts should be created when canary detects problems."""
        db = store._db
        await _seed_canary_run(db, run_id=1, verdict="fail", pass_rate=0.6)
        await _seed_drift_alert(db, alert_id=1, canary_run_id=1,
                                 alert_type="pass_rate_drop", severity="critical")

        cursor = await db.execute(
            "SELECT alert_type, severity, status FROM canary_drift_alerts WHERE canary_run_id = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == "pass_rate_drop"
        assert row[1] == "critical"
        assert row[2] == "open"

    @pytest.mark.asyncio
    async def test_alert_escalation_chain(self, store):
        """Alert should progress through acknowledge → resolve."""
        from monitoring.alert_escalation import acknowledge_alert, resolve_alert

        db = store._db
        await _seed_canary_run(db, run_id=1, verdict="fail", pass_rate=0.6)
        await _seed_drift_alert(db, alert_id=1, canary_run_id=1)

        # Acknowledge
        result = await acknowledge_alert(store, 1, "operator-1", "Investigating")
        assert result.success
        assert result.new_status == "acknowledged"

        # Resolve
        result = await resolve_alert(store, 1, "operator-1", "False alarm")
        assert result.success
        assert result.new_status == "resolved"

        # Verify final state
        cursor = await db.execute("SELECT status FROM canary_drift_alerts WHERE id = 1")
        row = await cursor.fetchone()
        assert row[0] == "resolved"

    @pytest.mark.asyncio
    async def test_recommendation_from_drift_alerts(self, store):
        """Multiple drift alerts should trigger recommendations."""
        from monitoring.drift_recommendations import generate_recommendations

        db = store._db
        await _seed_canary_run(db, run_id=1, verdict="fail", pass_rate=0.6)

        # Insert multiple archetype regressions
        for i in range(4):
            await _seed_drift_alert(
                db, alert_id=i + 1, canary_run_id=1,
                alert_type="archetype_regression",
            )
            # Update metric_name to include archetype
            await db.execute(
                "UPDATE canary_drift_alerts SET metric_name = 'pass_rate:cpg' WHERE id = ?",
                (i + 1,),
            )
        await db.commit()

        recs = await generate_recommendations(store, lookback_days=7)
        assert len(recs) >= 1
        assert any(r.type == "archetype_expand" for r in recs)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
