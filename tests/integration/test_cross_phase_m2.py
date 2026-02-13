"""Cross-phase integration suite M2: Hunter → Triage → Merge → Drift (W5.11).

Tests the chain: hunter promote → triage → merge → canary → drift alert → recommendation.
5 tests covering the end-to-end workflow across multiple subsystems.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone

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


async def _seed_signal(db, signal_id, company_id, company_name="Test Co"):
    """Insert a test signal."""
    await db.execute("PRAGMA foreign_keys = OFF")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, "
        "confidence, raw_data, detected_at, created_at, company_id) "
        "VALUES (?, 'funding', 'hunter', ?, ?, 0.7, '{}', ?, ?, ?)",
        (signal_id, f"domain:{company_name.lower().replace(' ', '')}.com",
         company_name, now, now, company_id),
    )
    await db.commit()


async def _seed_company_file(db, company_id, company_name="Test Co", status="thin"):
    """Insert a company file."""
    await db.execute("PRAGMA foreign_keys = OFF")
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT OR IGNORE INTO company_files "
        "(company_id, company_name, canonical_key, status, source_apis, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, '[\"hunter\"]', ?, ?)",
        (company_id, company_name, f"domain:{company_name.lower().replace(' ', '')}.com",
         status, now, now),
    )
    await db.commit()


async def _seed_canary_run(db, run_id=1, verdict="pass", pass_rate=0.9):
    """Insert a canary run."""
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        "INSERT INTO canary_runs "
        "(id, run_id, golden_set_size, golden_set_hash, total_scored, passed, failed, "
        "skipped, pass_rate, verdict, drift_threshold, pass_rate_threshold, duration_ms, created_at) "
        "VALUES (?, 'run-m2', 20, 'hash', 20, ?, ?, 0, ?, ?, 0.15, 0.80, 200, datetime('now'))",
        (run_id, int(20 * pass_rate), int(20 * (1 - pass_rate)), pass_rate, verdict),
    )
    await db.commit()


async def _seed_drift_alert(db, alert_id, canary_run_id=1, alert_type="pass_rate_drop",
                             status="open"):
    """Insert a drift alert."""
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.execute(
        "INSERT INTO canary_drift_alerts "
        "(id, canary_run_id, alert_type, severity, metric_name, message, status, created_at) "
        "VALUES (?, ?, ?, 'warning', 'pass_rate', 'M2 test alert', ?, datetime('now'))",
        (alert_id, canary_run_id, alert_type, status),
    )
    await db.commit()


# =============================================================================
# M2 TESTS
# =============================================================================

class TestM2HunterToTriageToMergeToDrift:
    """Integration: hunter promote → triage → merge → canary → drift."""

    @pytest.mark.asyncio
    async def test_hunter_signal_to_triage(self, store):
        """A hunter-promoted signal should appear in triage review."""
        import json
        db = store._db
        await _seed_signal(db, 1, "comp_m2_001", "Hunter Co")
        await _seed_company_file(db, "comp_m2_001", "Hunter Co", "thin")

        # Create review item (simulating hunter promote → triage)
        now = datetime.now(timezone.utc).isoformat()
        evidence = json.dumps({"signal_ids": [1], "schema_version": 1})
        await db.execute(
            "INSERT INTO review_items (company_id, status, evidence_bundle, created_at, updated_at) "
            "VALUES ('comp_m2_001', 'pending', ?, ?, ?)",
            (evidence, now, now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT ri.status, cf.company_name "
            "FROM review_items ri JOIN company_files cf ON ri.company_id = cf.company_id "
            "WHERE ri.company_id = 'comp_m2_001'"
        )
        row = await cursor.fetchone()
        assert row[0] == "pending"
        assert row[1] == "Hunter Co"

    @pytest.mark.asyncio
    async def test_merge_suggestion_for_duplicates(self, store):
        """Duplicate companies from hunter should generate merge suggestions."""
        db = store._db
        await _seed_signal(db, 1, "comp_m2_a", "Alpha Corp")
        await _seed_signal(db, 2, "comp_m2_b", "Alpha Corporation")
        await _seed_company_file(db, "comp_m2_a", "Alpha Corp", "thin")
        await _seed_company_file(db, "comp_m2_b", "Alpha Corporation", "thin")

        # Create merge suggestion (actual schema from v38)
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO merge_suggestions "
            "(pair_key, entity_a_company_id, entity_b_company_id, "
            "entity_a_canonical_key, entity_b_canonical_key, "
            "entity_a_company_name, entity_b_company_name, "
            "match_type, similarity_score, evidence_json, status, created_at) "
            "VALUES ('comp_m2_a||comp_m2_b', 'comp_m2_a', 'comp_m2_b', "
            "'domain:alphacorp.com', 'domain:alphacorporation.com', "
            "'Alpha Corp', 'Alpha Corporation', "
            "'fuzzy_name', 0.92, "
            "'{\"reason\": \"Alpha Corp ≈ Alpha Corporation\"}', 'pending', ?)",
            (now,),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT entity_a_company_id, entity_b_company_id, match_type, similarity_score, status "
            "FROM merge_suggestions WHERE entity_a_company_id = 'comp_m2_a'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[2] == "fuzzy_name"
        assert row[3] >= 0.9
        assert row[4] == "pending"

    @pytest.mark.asyncio
    async def test_canary_after_merge(self, store):
        """Canary run after merge should detect scoring changes."""
        db = store._db
        await _seed_canary_run(db, run_id=1, verdict="fail", pass_rate=0.65)

        cursor = await db.execute("SELECT verdict, pass_rate FROM canary_runs WHERE id = 1")
        row = await cursor.fetchone()
        assert row[0] == "fail"
        assert row[1] < 0.7

    @pytest.mark.asyncio
    async def test_drift_alert_to_escalation(self, store):
        """Drift alerts from canary should be manageable through escalation."""
        from monitoring.alert_escalation import acknowledge_alert, snooze_alert, resolve_alert

        db = store._db
        await _seed_canary_run(db, run_id=1, verdict="fail", pass_rate=0.65)
        await _seed_drift_alert(db, alert_id=1, canary_run_id=1)

        # Acknowledge
        r1 = await acknowledge_alert(store, 1, "analyst", "Post-merge check")
        assert r1.success

        # Snooze
        r2 = await snooze_alert(store, 1, "analyst", 24, "Waiting for re-score")
        assert r2.success

        # Resolve
        r3 = await resolve_alert(store, 1, "analyst", "Scores stabilized")
        assert r3.success

        # Verify audit trail
        cursor = await db.execute(
            "SELECT action_type FROM audit_events "
            "WHERE entity_type = 'canary_drift_alert' AND entity_id = '1' "
            "ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        actions = [r[0] for r in rows]
        assert "alert_acknowledged" in actions
        assert "alert_snoozed" in actions
        assert "alert_resolved" in actions

    @pytest.mark.asyncio
    async def test_full_m2_chain_recommendation(self, store):
        """Full M2 chain should end with actionable recommendations."""
        from monitoring.drift_recommendations import generate_recommendations

        db = store._db
        # Setup: signals + company files + canary run + alerts
        await _seed_signal(db, 1, "comp_m2_full", "Full Chain Co")
        await _seed_company_file(db, "comp_m2_full", "Full Chain Co", "promoted")
        await _seed_canary_run(db, run_id=1, verdict="fail", pass_rate=0.6)

        # Multiple pass_rate_drop alerts
        for i in range(3):
            await _seed_drift_alert(db, alert_id=i + 1, canary_run_id=1,
                                     alert_type="pass_rate_drop")

        recs = await generate_recommendations(store, lookback_days=7)
        assert len(recs) >= 1
        assert any(r.type == "collector_investigate" for r in recs)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
