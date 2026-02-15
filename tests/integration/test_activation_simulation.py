"""
End-to-end activation simulation tests.

Validates the full 4-step activation sequence in a controlled environment.
Uses monkeypatch for env vars, mocked external APIs, and deterministic fixtures.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from workflows.feature_guards import (
    WriteFeature,
    WriteMode,
    FeatureDisabledError,
    get_write_mode,
    assert_write_enabled,
    is_feature_enabled,
)
from utils.config_validator import validate_config

# Deterministic fixture values
FROZEN_NOW = "2026-01-15T12:00:00Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_all_flags_off(monkeypatch):
    """Set all feature flags to disabled/off."""
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    monkeypatch.setenv("LLM_THESIS_MODE", "off")
    monkeypatch.setenv("ML_ENABLEMENT", "disabled")
    monkeypatch.setenv("MERGE_WRITES_ENABLED", "disabled")
    monkeypatch.setenv("USE_SHADOW_ENTITY_RESOLUTION", "false")
    monkeypatch.setenv("DRIFT_MONITORING_ENABLED", "disabled")
    monkeypatch.setenv("USE_THIN_FILES", "false")
    monkeypatch.setenv("V2_ENABLEMENT", "shadow")
    monkeypatch.setenv("BULK_TRIAGE_ENABLED", "disabled")
    monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "disabled")


def _set_step1_flags(monkeypatch):
    """Step 1: Shadow activation."""
    monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
    monkeypatch.setenv("ML_ENABLEMENT", "shadow")
    monkeypatch.setenv("MERGE_WRITES_ENABLED", "shadow")
    monkeypatch.setenv("USE_SHADOW_ENTITY_RESOLUTION", "true")


def _set_step2_flags(monkeypatch):
    """Step 2: Low-risk writes."""
    monkeypatch.setenv("DRIFT_MONITORING_ENABLED", "active")
    monkeypatch.setenv("USE_THIN_FILES", "true")
    monkeypatch.setenv("V2_ENABLEMENT", "live")


def _set_step3_flags(monkeypatch):
    """Step 3: Manual write operations."""
    monkeypatch.setenv("LLM_THESIS_MODE", "active")
    monkeypatch.setenv("ML_ENABLEMENT", "live")
    monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
    monkeypatch.setenv("BULK_TRIAGE_ENABLED", "active")
    monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")


def _set_step4_flags(monkeypatch):
    """Step 4: Batch publish (full production)."""
    monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
    monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")


def _create_test_db(path: Path) -> Path:
    """Create a minimal DB with required tables for activation gate."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
    conn.execute("INSERT INTO schema_migrations VALUES (41)")
    conn.execute(
        "CREATE TABLE canary_runs ("
        "id INTEGER PRIMARY KEY, verdict TEXT, pass_rate REAL, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE canary_drift_alerts ("
        "id INTEGER PRIMARY KEY, severity TEXT, status TEXT)"
    )
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Test 1: Baseline state — all flags off
# ---------------------------------------------------------------------------

class TestBaselineState:
    def test_all_features_disabled_at_baseline(self, monkeypatch):
        """Verify all features are disabled when flags are off."""
        _set_all_flags_off(monkeypatch)

        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.DISABLED
        assert get_write_mode(WriteFeature.BULK_TRIAGE) == WriteMode.DISABLED
        assert get_write_mode(WriteFeature.HUNTER_PROMOTE) == WriteMode.DISABLED
        assert get_write_mode(WriteFeature.DRIFT_MONITORING) == WriteMode.DISABLED

        assert not is_feature_enabled(WriteFeature.MERGE_WRITES)
        assert not is_feature_enabled(WriteFeature.BULK_TRIAGE)

    def test_config_validation_clean_at_baseline(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        issues = validate_config()
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_write_operations_blocked_at_baseline(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        with pytest.raises(FeatureDisabledError):
            assert_write_enabled(WriteFeature.MERGE_WRITES)
        with pytest.raises(FeatureDisabledError):
            assert_write_enabled(WriteFeature.BULK_TRIAGE)
        with pytest.raises(FeatureDisabledError):
            assert_write_enabled(WriteFeature.HUNTER_PROMOTE)


# ---------------------------------------------------------------------------
# Test 2: Step 1 — Shadow activation
# ---------------------------------------------------------------------------

class TestStep1Shadow:
    def test_shadow_features_running(self, monkeypatch):
        """Step 1: Shadow features are active but no real mutations."""
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)

        # Merge in shadow mode
        mode = get_write_mode(WriteFeature.MERGE_WRITES)
        assert mode == WriteMode.SHADOW
        assert is_feature_enabled(WriteFeature.MERGE_WRITES)

        # Shadow allows propose/approve but not apply
        mode = assert_write_enabled(WriteFeature.MERGE_WRITES, allow_shadow=True)
        assert mode == WriteMode.SHADOW

        # Active writes still blocked
        with pytest.raises(FeatureDisabledError):
            assert_write_enabled(WriteFeature.MERGE_WRITES, allow_shadow=False)

    def test_bulk_triage_still_disabled(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        assert not is_feature_enabled(WriteFeature.BULK_TRIAGE)

    def test_delivery_mode_still_staging(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        import os
        assert os.environ.get("DELIVERY_MODE") == "staging_only"


# ---------------------------------------------------------------------------
# Test 3: Step 2 — Low-risk writes
# ---------------------------------------------------------------------------

class TestStep2LowRisk:
    def test_drift_monitoring_active(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)

        assert get_write_mode(WriteFeature.DRIFT_MONITORING) == WriteMode.ACTIVE
        assert is_feature_enabled(WriteFeature.DRIFT_MONITORING)

    def test_thin_files_enabled(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)

        import os
        assert os.environ.get("USE_THIN_FILES") == "true"

    def test_delivery_still_staging(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)

        import os
        assert os.environ.get("DELIVERY_MODE") == "staging_only"


# ---------------------------------------------------------------------------
# Test 4: Step 3 — Manual write operations
# ---------------------------------------------------------------------------

class TestStep3ManualWrite:
    def test_manual_publish_enabled(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)

        import os
        assert os.environ.get("DELIVERY_MODE") == "manual_publish"

    def test_triage_actions_enabled(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)

        assert get_write_mode(WriteFeature.BULK_TRIAGE) == WriteMode.ACTIVE
        assert get_write_mode(WriteFeature.HUNTER_PROMOTE) == WriteMode.ACTIVE

    def test_merge_still_shadow(self, monkeypatch):
        """Merge should still be shadow at step 3 (goes active at step 4)."""
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)

        # Step 3 doesn't upgrade merge to active
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.SHADOW


# ---------------------------------------------------------------------------
# Test 5: Step 4 — Batch publish (full production)
# ---------------------------------------------------------------------------

class TestStep4BatchPublish:
    def test_batch_publish_enabled(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)
        _set_step4_flags(monkeypatch)

        import os
        assert os.environ.get("DELIVERY_MODE") == "batch_publish"

    def test_merge_writes_active(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)
        _set_step4_flags(monkeypatch)

        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.ACTIVE
        mode = assert_write_enabled(WriteFeature.MERGE_WRITES)
        assert mode == WriteMode.ACTIVE

    def test_all_features_enabled(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)
        _set_step4_flags(monkeypatch)

        for feature in WriteFeature:
            assert is_feature_enabled(feature), f"{feature.value} should be enabled"


# ---------------------------------------------------------------------------
# Test 6: Emergency rollback
# ---------------------------------------------------------------------------

class TestEmergencyRollback:
    def test_rollback_disables_all_features(self, monkeypatch):
        """After setting all step 4 flags, rollback returns to baseline."""
        # Start at full production
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)
        _set_step4_flags(monkeypatch)

        # Verify full production state
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.ACTIVE

        # Emergency rollback
        _set_all_flags_off(monkeypatch)

        # All disabled
        for feature in WriteFeature:
            assert not is_feature_enabled(feature), f"{feature.value} should be disabled"

        import os
        assert os.environ.get("DELIVERY_MODE") == "staging_only"

    def test_rollback_config_validation_clean(self, monkeypatch):
        _set_all_flags_off(monkeypatch)
        _set_step1_flags(monkeypatch)
        _set_step2_flags(monkeypatch)
        _set_step3_flags(monkeypatch)
        _set_step4_flags(monkeypatch)

        # Rollback
        _set_all_flags_off(monkeypatch)

        issues = validate_config()
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Test 7: Gate blocking prevents advancement
# ---------------------------------------------------------------------------

class TestGateBlocking:
    @pytest.mark.asyncio
    async def test_gate_blocks_on_canary_fail(self, tmp_path, monkeypatch):
        """Step 1 gate blocks when canary verdict is 'fail'."""
        from monitoring.activation_gate import check_activation_readiness
        from storage.signal_store import SignalStore

        _set_all_flags_off(monkeypatch)

        db = _create_test_db(tmp_path / "signals.db")

        # Insert a failing canary run
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO canary_runs (verdict, pass_rate, created_at) VALUES (?, ?, ?)",
            ("fail", 0.3, FROZEN_NOW),
        )
        conn.commit()
        conn.close()

        store = SignalStore(str(db))
        await store.initialize()
        try:
            result = await check_activation_readiness(store, step=1)
            assert result.verdict == "blocked"
            assert not result.can_proceed
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_gate_allows_step1_without_canary(self, tmp_path, monkeypatch):
        """Step 1 is lenient — no canary data doesn't block."""
        from monitoring.activation_gate import check_activation_readiness
        from storage.signal_store import SignalStore

        _set_all_flags_off(monkeypatch)

        db = _create_test_db(tmp_path / "signals.db")

        store = SignalStore(str(db))
        await store.initialize()
        try:
            result = await check_activation_readiness(store, step=1)
            assert result.can_proceed  # warn, not blocked
            assert result.verdict in ("ready", "warn")
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_gate_blocks_step3_without_canary(self, tmp_path, monkeypatch):
        """Step 3 requires canary data — blocks without it."""
        from monitoring.activation_gate import check_activation_readiness
        from storage.signal_store import SignalStore

        _set_all_flags_off(monkeypatch)

        db = _create_test_db(tmp_path / "signals.db")

        store = SignalStore(str(db))
        await store.initialize()
        try:
            result = await check_activation_readiness(store, step=3)
            assert result.verdict == "blocked"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_gate_blocks_on_critical_drift_alert(self, tmp_path, monkeypatch):
        """Critical drift alert blocks all steps."""
        from monitoring.activation_gate import check_activation_readiness
        from storage.signal_store import SignalStore

        _set_all_flags_off(monkeypatch)

        db = _create_test_db(tmp_path / "signals.db")

        # Insert open critical alert
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO canary_runs (verdict, pass_rate, created_at) VALUES (?, ?, ?)",
            ("pass", 1.0, FROZEN_NOW),
        )
        conn.execute(
            "INSERT INTO canary_drift_alerts (severity, status) VALUES (?, ?)",
            ("critical", "open"),
        )
        conn.commit()
        conn.close()

        store = SignalStore(str(db))
        await store.initialize()
        try:
            result = await check_activation_readiness(store, step=1)
            assert result.verdict == "blocked"
            assert result.open_critical_alerts > 0
        finally:
            await store.close()
