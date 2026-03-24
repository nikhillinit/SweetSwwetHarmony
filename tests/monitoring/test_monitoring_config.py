"""
Tests for MonitoringConfigV2 + ConfigLoader contract.

Covers:
- failure_configs parsed from failure_handling, omitted from to_dict()/to_json()
- severity_weights populate both config.severity_weights and config.gating.weight_*
- Single backoff_minutes value => fixed; multiple => exponential
- Unknown failure categories log warning and are omitted
- Formatting-only JSON changes produce different config_hash values
- Cache identity: second load returns same object; force_reload returns different object
"""

import hashlib
import json
import logging
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import monitoring.config as _config_mod
from monitoring.config import (
    ConfigLoader,
    MonitoringConfigV2,
    load_config,
)
from monitoring.failure_classifier import FailureCategory, FailureCategoryConfig


@pytest.fixture(autouse=True)
def _fresh_loader(monkeypatch):
    """Reset the module-level singleton _loader per test."""
    monkeypatch.setattr(_config_mod, "_loader", ConfigLoader())


def _write_config(data: dict) -> str:
    """Write config dict to a temp JSON file, return path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# ---------------------------------------------------------------------------
# failure_configs
# ---------------------------------------------------------------------------

class TestFailureConfigs:

    def test_failure_configs_parsed_from_failure_handling(self):
        data = {
            "failure_handling": {
                "transient": {"max_failures": 8, "backoff_minutes": [1, 5, 30]},
            }
        }
        path = _write_config(data)
        try:
            loader = ConfigLoader()
            config = loader.load(path)

            assert FailureCategory.TRANSIENT in config.failure_configs
            fc = config.failure_configs[FailureCategory.TRANSIENT]
            assert fc.max_consecutive_failures == 8
            assert fc.backoff_values_minutes == [1, 5, 30]
        finally:
            os.unlink(path)

    def test_failure_configs_omitted_from_to_dict(self):
        data = {
            "failure_handling": {
                "transient": {"max_failures": 5, "backoff_minutes": [1]},
            }
        }
        path = _write_config(data)
        try:
            config = ConfigLoader().load(path)
            d = config.to_dict()
            assert "failure_configs" not in d
            assert "failure_handling" not in d
        finally:
            os.unlink(path)

    def test_failure_configs_omitted_from_to_json(self):
        data = {
            "failure_handling": {
                "client_error": {"max_failures": 3, "backoff_minutes": [1440]},
            }
        }
        path = _write_config(data)
        try:
            config = ConfigLoader().load(path)
            j = config.to_json()
            parsed = json.loads(j)
            assert "failure_configs" not in parsed
            assert "failure_handling" not in parsed
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# severity_weights dual-population
# ---------------------------------------------------------------------------

class TestSeverityWeights:

    def test_severity_weights_populate_config_and_gating(self):
        data = {
            "severity_weights": {
                "content_delta": 0.40,
                "redirect_change": 0.20,
                "state_change": 0.30,
                "semantic_drift": 0.10,
            }
        }
        path = _write_config(data)
        try:
            config = ConfigLoader().load(path)

            # Config-level dict
            assert config.severity_weights["content_delta"] == 0.40
            assert config.severity_weights["redirect_change"] == 0.20

            # Gating sub-object mirrored
            assert config.gating.weight_content_delta == 0.40
            assert config.gating.weight_redirect_change == 0.20
            assert config.gating.weight_state_change == 0.30
            assert config.gating.weight_semantic_drift == 0.10
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# backoff_minutes interpretation
# ---------------------------------------------------------------------------

class TestBackoffMinutes:

    def test_single_backoff_is_fixed(self):
        data = {
            "failure_handling": {
                "client_error": {"max_failures": 3, "backoff_minutes": [1440]},
            }
        }
        path = _write_config(data)
        try:
            config = ConfigLoader().load(path)
            fc = config.failure_configs[FailureCategory.CLIENT_ERROR]
            assert fc.backoff_type == "fixed"
        finally:
            os.unlink(path)

    def test_multiple_backoff_is_exponential(self):
        data = {
            "failure_handling": {
                "transient": {"max_failures": 10, "backoff_minutes": [1, 5, 15, 60]},
            }
        }
        path = _write_config(data)
        try:
            config = ConfigLoader().load(path)
            fc = config.failure_configs[FailureCategory.TRANSIENT]
            assert fc.backoff_type == "exponential"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Unknown failure categories
# ---------------------------------------------------------------------------

class TestUnknownFailureCategory:

    def test_unknown_category_logged_and_omitted(self, caplog):
        data = {
            "failure_handling": {
                "nonexistent_category": {"max_failures": 1, "backoff_minutes": [10]},
            }
        }
        path = _write_config(data)
        try:
            with caplog.at_level(logging.WARNING, logger="monitoring.config"):
                config = ConfigLoader().load(path)

            assert len(config.failure_configs) == 0
            assert any("Unknown failure category" in r.message for r in caplog.records)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# config_hash — raw-JSON-byte path
# ---------------------------------------------------------------------------

class TestConfigHash:

    def test_formatting_only_changes_produce_different_hashes(self):
        """Two files with the same logical content but different whitespace get different hashes."""
        base = {"version": "2.4", "gating": {"alert_threshold": 0.3}}

        compact = json.dumps(base, separators=(",", ":"))
        pretty = json.dumps(base, indent=4)
        assert compact != pretty

        fd1, path1 = tempfile.mkstemp(suffix=".json")
        fd2, path2 = tempfile.mkstemp(suffix=".json")
        os.close(fd1)
        os.close(fd2)
        try:
            with open(path1, "w") as f:
                f.write(compact)
            with open(path2, "w") as f:
                f.write(pretty)

            loader1 = ConfigLoader()
            loader2 = ConfigLoader()
            c1 = loader1.load(path1)
            c2 = loader2.load(path2)

            assert c1.config_hash != c2.config_hash
        finally:
            os.unlink(path1)
            os.unlink(path2)


# ---------------------------------------------------------------------------
# Cache: object identity
# ---------------------------------------------------------------------------

class TestConfigLoaderCache:

    def test_cached_load_returns_same_object(self):
        data = {"version": "2.4"}
        path = _write_config(data)
        try:
            loader = ConfigLoader()
            first = loader.load(path)
            second = loader.load(path)
            assert first is second
        finally:
            os.unlink(path)

    def test_force_reload_returns_different_object(self):
        data = {"version": "2.4"}
        path = _write_config(data)
        try:
            loader = ConfigLoader()
            first = loader.load(path)
            reloaded = loader.load(path, force_reload=True)
            assert first is not reloaded
        finally:
            os.unlink(path)
