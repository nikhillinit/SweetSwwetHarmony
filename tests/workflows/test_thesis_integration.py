"""Tests for thesis filter integration in pipeline."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from workflows.pipeline import DiscoveryPipeline, PipelineConfig, PipelineStats
from utils.thesis_filter import ThesisFilter, ThesisFilterConfig, RoutingDecision


class TestPipelineThesisConfig:
    """Test thesis config in pipeline."""

    def test_use_thesis_filter_default_true(self):
        config = PipelineConfig()
        assert config.use_thesis_filter is True

    def test_thesis_hold_threshold_default(self):
        config = PipelineConfig()
        assert config.thesis_hold_threshold == 0.3


class TestPipelineStatsThesisMetrics:
    """Test thesis-related metrics in PipelineStats."""

    def test_metrics_include_thesis_rejected(self):
        """PipelineStats should have thesis_rejected metric."""
        stats = PipelineStats()
        assert hasattr(stats, "thesis_rejected")

    def test_metrics_include_thesis_held(self):
        """PipelineStats should have thesis_held metric."""
        stats = PipelineStats()
        assert hasattr(stats, "thesis_held")

    def test_metrics_include_thesis_passed(self):
        """PipelineStats should have thesis_passed metric."""
        stats = PipelineStats()
        assert hasattr(stats, "thesis_passed")

    def test_thesis_metrics_default_zero(self):
        """Thesis metrics should default to 0."""
        stats = PipelineStats()
        assert stats.thesis_rejected == 0
        assert stats.thesis_held == 0
        assert stats.thesis_passed == 0


class TestPipelineThesisFilterInitialization:
    """Test thesis filter is initialized correctly in pipeline."""

    def test_pipeline_creates_thesis_filter_when_enabled(self):
        """Pipeline should create ThesisFilter when use_thesis_filter=True."""
        config = PipelineConfig(use_thesis_filter=True)
        pipeline = DiscoveryPipeline(config)
        assert pipeline._thesis_filter is not None
        assert isinstance(pipeline._thesis_filter, ThesisFilter)

    def test_pipeline_no_thesis_filter_when_disabled(self):
        """Pipeline should NOT create ThesisFilter when use_thesis_filter=False."""
        config = PipelineConfig(use_thesis_filter=False)
        pipeline = DiscoveryPipeline(config)
        assert pipeline._thesis_filter is None

    def test_thesis_filter_uses_config_threshold(self):
        """ThesisFilter should use threshold from PipelineConfig."""
        config = PipelineConfig(use_thesis_filter=True, thesis_hold_threshold=0.5)
        pipeline = DiscoveryPipeline(config)
        assert pipeline._thesis_filter is not None
        assert pipeline._thesis_filter.config.hold_threshold == 0.5


class TestPipelineStatsToDictThesis:
    """Test thesis metrics are included in to_dict output."""

    def test_to_dict_includes_thesis_section(self):
        """PipelineStats.to_dict() should include thesis section."""
        stats = PipelineStats()
        d = stats.to_dict()
        assert "thesis" in d

    def test_to_dict_thesis_has_all_metrics(self):
        """Thesis section should have rejected, held, passed."""
        stats = PipelineStats()
        d = stats.to_dict()
        assert "rejected" in d["thesis"]
        assert "held" in d["thesis"]
        assert "passed" in d["thesis"]

    def test_to_dict_thesis_reflects_stats_values(self):
        """Thesis section should reflect actual stats values."""
        stats = PipelineStats()
        stats.thesis_rejected = 5
        stats.thesis_held = 10
        stats.thesis_passed = 25
        d = stats.to_dict()
        assert d["thesis"]["rejected"] == 5
        assert d["thesis"]["held"] == 10
        assert d["thesis"]["passed"] == 25
