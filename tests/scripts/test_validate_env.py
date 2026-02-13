"""Tests for scripts/validate_env.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.validate_env import validate_env, load_env_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_env(tmp_path: Path, content: str) -> Path:
    env_file = tmp_path / ".env.test"
    env_file.write_text(content, encoding="utf-8")
    return env_file


# ---------------------------------------------------------------------------
# load_env_file tests
# ---------------------------------------------------------------------------

class TestLoadEnvFile:
    def test_simple_key_value(self, tmp_path):
        f = _write_env(tmp_path, "FOO=bar\n")
        assert load_env_file(f) == {"FOO": "bar"}

    def test_quoted_value(self, tmp_path):
        f = _write_env(tmp_path, 'FOO="hello world"\n')
        assert load_env_file(f) == {"FOO": "hello world"}

    def test_comments_and_blanks(self, tmp_path):
        f = _write_env(tmp_path, "# comment\n\nFOO=bar\n")
        assert load_env_file(f) == {"FOO": "bar"}

    def test_inline_comment(self, tmp_path):
        f = _write_env(tmp_path, "FOO=bar # comment\n")
        assert load_env_file(f) == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# validate_env tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure test env vars don't leak."""
    for key in [
        "DELIVERY_MODE", "STRICT_CONFIG_VALIDATION",
        "NOTION_API_KEY", "NOTION_DATABASE_ID",
        "MERGE_WRITES_ENABLED", "BULK_TRIAGE_ENABLED",
        "HUNTER_PROMOTE_ENABLED", "DRIFT_MONITORING_ENABLED",
        "LLM_THESIS_MODE", "ML_ENABLEMENT", "V2_ENABLEMENT",
    ]:
        monkeypatch.delenv(key, raising=False)


class TestValidateEnv:
    def test_valid_staging_env_passes(self, tmp_path, monkeypatch):
        env_file = _write_env(tmp_path, (
            "DELIVERY_MODE=staging_only\n"
            "STRICT_CONFIG_VALIDATION=true\n"
        ))
        result = validate_env(env_file)
        assert result == 0

    def test_missing_notion_key_with_batch_publish_errors(self, tmp_path, monkeypatch):
        env_file = _write_env(tmp_path, (
            "DELIVERY_MODE=batch_publish\n"
            "NOTION_API_KEY=\n"
            "NOTION_DATABASE_ID=\n"
        ))
        result = validate_env(env_file)
        assert result == 1

    def test_invalid_delivery_mode_errors(self, tmp_path, monkeypatch):
        env_file = _write_env(tmp_path, (
            "DELIVERY_MODE=invalid_mode\n"
        ))
        result = validate_env(env_file)
        assert result == 1

    def test_staging_only_without_notion_warns_only(self, tmp_path, monkeypatch):
        """staging_only with no Notion keys should warn but not error."""
        env_file = _write_env(tmp_path, (
            "DELIVERY_MODE=staging_only\n"
            "NOTION_API_KEY=\n"
            "NOTION_DATABASE_ID=\n"
        ))
        result = validate_env(env_file)
        # Warnings for missing Notion keys, but not errors since staging_only
        assert result == 0

    def test_file_not_found(self, tmp_path):
        result = validate_env(tmp_path / "nonexistent.env")
        assert result == 1

    def test_valid_full_production_env(self, tmp_path, monkeypatch):
        env_file = _write_env(tmp_path, (
            "DELIVERY_MODE=batch_publish\n"
            "NOTION_API_KEY=secret_real_key\n"
            "NOTION_DATABASE_ID=abc-123-def\n"
            "MERGE_WRITES_ENABLED=active\n"
            "BULK_TRIAGE_ENABLED=active\n"
            "HUNTER_PROMOTE_ENABLED=active\n"
            "DRIFT_MONITORING_ENABLED=active\n"
        ))
        result = validate_env(env_file)
        assert result == 0
