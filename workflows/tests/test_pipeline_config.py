"""Tests for PipelineConfig feature flags."""
import pytest
import os
from workflows.pipeline import PipelineConfig


class TestPipelineConfigFlags:
    """Test new feature flags in PipelineConfig."""

    def test_use_gating_default_false(self):
        """use_gating should default to False."""
        config = PipelineConfig()
        assert config.use_gating is False

    def test_use_entities_default_false(self):
        """use_entities should default to False."""
        config = PipelineConfig()
        assert config.use_entities is False

    def test_use_asset_store_default_false(self):
        """use_asset_store should default to False."""
        config = PipelineConfig()
        assert config.use_asset_store is False

    def test_asset_store_path_default(self):
        """asset_store_path should default to assets.db."""
        config = PipelineConfig()
        assert config.asset_store_path == "assets.db"

    def test_from_env_reads_use_gating(self):
        """from_env should read USE_GATING env var."""
        os.environ["USE_GATING"] = "true"
        try:
            config = PipelineConfig.from_env()
            assert config.use_gating is True
        finally:
            del os.environ["USE_GATING"]

    def test_from_env_reads_use_entities(self):
        """from_env should read USE_ENTITIES env var."""
        os.environ["USE_ENTITIES"] = "true"
        try:
            config = PipelineConfig.from_env()
            assert config.use_entities is True
        finally:
            del os.environ["USE_ENTITIES"]

    def test_from_env_reads_use_asset_store(self):
        """from_env should read USE_ASSET_STORE env var."""
        os.environ["USE_ASSET_STORE"] = "true"
        try:
            config = PipelineConfig.from_env()
            assert config.use_asset_store is True
        finally:
            del os.environ["USE_ASSET_STORE"]
