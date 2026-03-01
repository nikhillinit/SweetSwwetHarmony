"""Tests for config pinning in pipeline run tracking.

Verifies that compute_config_snapshot() is included in inputs_summary
when pipeline runs are tracked.
"""

import os

import pytest

from monitoring.feature_gate import compute_config_snapshot


class TestConfigPinning:
    def test_snapshot_included_in_inputs(self, monkeypatch):
        """Config snapshot should be a valid dict with hash and flags."""
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")

        snapshot = compute_config_snapshot()
        inputs = {
            "collectors": ["github"],
            "dry_run": True,
            "mode": "full",
            "config_snapshot": snapshot,
        }

        assert "config_snapshot" in inputs
        assert "hash" in inputs["config_snapshot"]
        assert len(inputs["config_snapshot"]["hash"]) == 16
        assert inputs["config_snapshot"]["flags"]["DELIVERY_MODE"] == "manual_publish"

    def test_snapshot_deterministic(self, monkeypatch):
        """Same env → same hash (reproducibility check)."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        monkeypatch.setenv("LLM_THESIS_MODE", "off")
        for key in ["ML_ENABLEMENT", "V2_ENABLEMENT"]:
            monkeypatch.delenv(key, raising=False)

        s1 = compute_config_snapshot()
        s2 = compute_config_snapshot()
        assert s1["hash"] == s2["hash"]
        assert s1["flags"] == s2["flags"]

    def test_snapshot_changes_with_env(self, monkeypatch):
        """Different env → different hash."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        s1 = compute_config_snapshot()

        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        s2 = compute_config_snapshot()

        assert s1["hash"] != s2["hash"]
