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

    def test_warm_intro_defaults_disabled(self, monkeypatch, tmp_path):
        """C0.2: Warm intro enrichment is disabled by default; the private
        graph DB defaults BESIDE the canonical signals DB, not into the cwd."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("PRIVATE_GRAPH_DB_PATH", raising=False)
        config = PipelineConfig()

        assert config.use_warm_intro_enrichment is False
        assert config.user_email is None
        assert config.private_graph_db_path == str(
            (tmp_path / "private_graph.db").resolve()
        )

    def test_warm_intro_fields_from_env(self, tmp_path):
        """C0.3: Warm intro fields loaded from environment."""
        custom = tmp_path / "custom" / "private.db"
        env = {
            "ENABLE_WARM_INTRO_ENRICHMENT": "true",
            "USER_EMAIL": "user@example.com",
            "PRIVATE_GRAPH_DB_PATH": str(custom),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = PipelineConfig.from_env()

            assert config.use_warm_intro_enrichment is True
            assert config.user_email == "user@example.com"
            assert config.private_graph_db_path == str(custom.resolve())

    def test_pipeline_uses_configured_private_graph_path(self, tmp_path):
        """Warm intro relationship DB path should not be derived from the signal DB name."""
        from workflows.pipeline import DiscoveryPipeline

        config = PipelineConfig(
            db_path=str(tmp_path / "production.sqlite"),
            private_graph_db_path=str(tmp_path / "relationships.sqlite"),
        )
        pipeline = DiscoveryPipeline(config)

        assert pipeline._relationship_store_db_path() == str(
            tmp_path / "relationships.sqlite"
        )


class TestPipelineConfigPrivateGraphPath:
    """Q7: both PipelineConfig default sites resolve via the shared resolver."""

    def test_dataclass_default_resolves_beside_canonical_db(
        self, monkeypatch, tmp_path
    ):
        """AC1 (dataclass default path): no PRIVATE_GRAPH_DB_PATH -> beside signals DB."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("PRIVATE_GRAPH_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        config = PipelineConfig()
        assert config.private_graph_db_path == str(
            (tmp_path / "private_graph.db").resolve()
        )

    def test_from_env_default_resolves_beside_canonical_db(
        self, monkeypatch, tmp_path
    ):
        """AC1 (from_env path): no PRIVATE_GRAPH_DB_PATH -> beside signals DB."""
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("PRIVATE_GRAPH_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.delenv("ENABLE_WARM_INTRO_ENRICHMENT", raising=False)
        config = PipelineConfig.from_env()
        assert config.private_graph_db_path == str(
            (tmp_path / "private_graph.db").resolve()
        )

    def test_explicit_env_var_wins_in_dataclass_default(self, monkeypatch, tmp_path):
        """AC2 (dataclass default path): explicit PRIVATE_GRAPH_DB_PATH wins."""
        custom = tmp_path / "custom" / "graph.db"
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.setenv("PRIVATE_GRAPH_DB_PATH", str(custom))
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        config = PipelineConfig()
        assert config.private_graph_db_path == str(custom.resolve())

    def test_explicit_constructor_arg_is_preserved(self, monkeypatch, tmp_path):
        """An explicitly passed private_graph_db_path is kept verbatim."""
        explicit = str(tmp_path / "relationships.sqlite")
        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.setenv(
            "PRIVATE_GRAPH_DB_PATH", str(tmp_path / "ignored" / "graph.db")
        )
        config = PipelineConfig(private_graph_db_path=explicit)
        assert config.private_graph_db_path == explicit

    def test_in_tree_private_graph_env_fails_closed_from_env(
        self, monkeypatch, tmp_path
    ):
        """AC3: an unsafe in-tree PRIVATE_GRAPH_DB_PATH fails closed in from_env,
        even when the canonical signals DB itself is safely out of tree."""
        from storage.db_paths import REPO_ROOT, InTreeDatabaseError

        monkeypatch.setenv("DISCOVERY_DB_PATH", str(tmp_path / "signals.db"))
        monkeypatch.setenv(
            "PRIVATE_GRAPH_DB_PATH", str(REPO_ROOT / "private_graph.db")
        )
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        with pytest.raises(InTreeDatabaseError):
            PipelineConfig.from_env()


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

    def test_direct_config_fails_closed_on_in_tree_default(self, monkeypatch):
        """Direct PipelineConfig() construction must not retain bare signals.db."""
        from storage.db_paths import REPO_ROOT, InTreeDatabaseError

        monkeypatch.delenv("DISCOVERY_DB_PATH", raising=False)
        monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
        monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
        monkeypatch.chdir(REPO_ROOT)
        with pytest.raises(InTreeDatabaseError):
            PipelineConfig()

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
