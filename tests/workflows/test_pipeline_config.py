"""
Tests for PipelineConfig extensions.

Phase C0: Timeout configuration, warm intro enrichment, and validation.
"""

import os
from unittest import mock

import pytest

from workflows.pipeline import PipelineConfig


class TestPipelineConfigTimeouts:
    """Tests for timeout configuration fields."""

    def test_default_timeout_values(self):
        """C0.1: Default timeout values are sensible."""
        config = PipelineConfig()

        assert config.collector_connect_timeout == 10.0
        assert config.collector_search_timeout == 60.0
        assert config.collector_enrich_timeout == 45.0
        assert config.collector_download_timeout == 90.0

    def test_timeout_values_from_env(self):
        """C0.3: Timeout values loaded from environment."""
        env = {
            "COLLECTOR_CONNECT_TIMEOUT": "15.0",
            "COLLECTOR_SEARCH_TIMEOUT": "120.0",
            "COLLECTOR_ENRICH_TIMEOUT": "60.0",
            "COLLECTOR_DOWNLOAD_TIMEOUT": "180.0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = PipelineConfig.from_env()

            assert config.collector_connect_timeout == 15.0
            assert config.collector_search_timeout == 120.0
            assert config.collector_enrich_timeout == 60.0
            assert config.collector_download_timeout == 180.0


class TestPipelineConfigWarmIntro:
    """Tests for warm intro enrichment configuration."""

    def test_warm_intro_defaults_disabled(self):
        """C0.2: Warm intro enrichment is disabled by default."""
        config = PipelineConfig()

        assert config.use_warm_intro_enrichment is False
        assert config.user_email is None
        assert config.private_graph_db_path == "private_graph.db"

    def test_warm_intro_fields_from_env(self):
        """C0.3: Warm intro fields loaded from environment."""
        env = {
            "ENABLE_WARM_INTRO_ENRICHMENT": "true",
            "USER_EMAIL": "user@example.com",
            "PRIVATE_GRAPH_DB_PATH": "/custom/path/private.db",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = PipelineConfig.from_env()

            assert config.use_warm_intro_enrichment is True
            assert config.user_email == "user@example.com"
            assert config.private_graph_db_path == "/custom/path/private.db"


class TestPipelineConfigValidation:
    """Tests for PipelineConfig validation."""

    def test_warm_intro_without_email_raises_error(self):
        """C0.4: Enabling warm intro without user_email raises ValueError."""
        with pytest.raises(ValueError, match="USER_EMAIL.*required"):
            PipelineConfig(use_warm_intro_enrichment=True, user_email=None)

    def test_warm_intro_with_email_succeeds(self):
        """C0.4: Enabling warm intro with user_email succeeds."""
        config = PipelineConfig(
            use_warm_intro_enrichment=True,
            user_email="user@example.com",
        )
        assert config.use_warm_intro_enrichment is True
        assert config.user_email == "user@example.com"

    def test_warm_intro_disabled_no_email_required(self):
        """C0.4: Warm intro disabled doesn't require user_email."""
        config = PipelineConfig(use_warm_intro_enrichment=False, user_email=None)
        assert config.use_warm_intro_enrichment is False
        assert config.user_email is None

    def test_validation_from_env_warm_intro_without_email(self):
        """C0.4: from_env() raises if ENABLE_WARM_INTRO_ENRICHMENT=true without USER_EMAIL."""
        env = {
            "ENABLE_WARM_INTRO_ENRICHMENT": "true",
            # USER_EMAIL not set
        }
        # Clear USER_EMAIL to ensure it's not set
        with mock.patch.dict(os.environ, env, clear=False):
            # Remove USER_EMAIL if it exists
            os.environ.pop("USER_EMAIL", None)
            with pytest.raises(ValueError, match="USER_EMAIL.*required"):
                PipelineConfig.from_env()


class TestPipelineConfigDbPathGuard:
    """from_env() routes db_path through the #149 in-tree fail-fast guard."""

    def test_from_env_fails_closed_on_in_tree_default(self, monkeypatch):
        """The bare "signals.db" default resolves in-tree (the incident path) and
        must fail closed once from_env() is wired through resolve_canonical_db_path()."""
        from storage.db_paths import REPO_ROOT, InTreeDatabaseError

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)
        with pytest.raises(InTreeDatabaseError):
            PipelineConfig.from_env()

    def test_from_env_succeeds_with_out_of_tree_path(self, monkeypatch, tmp_path):
        """An out-of-tree DISCOVERY_DB_PATH resolves cleanly to the absolute path."""
        target = tmp_path / "signals.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(target))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        config = PipelineConfig.from_env()
        assert config.db_path == str(target.resolve())

    def test_from_env_in_tree_allowed_with_flag(self, monkeypatch):
        """HARMONIC_ALLOW_IN_TREE_DB lets fixtures keep an in-tree DB (absolute path)."""
        from storage.db_paths import REPO_ROOT

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
        monkeypatch.chdir(REPO_ROOT)
        config = PipelineConfig.from_env()
        assert config.db_path == str((REPO_ROOT / "signals.db").resolve())
