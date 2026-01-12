"""Tests for thesis configuration loading."""

from __future__ import annotations

import pytest

from intelligence.thesis_config import ThesisConfig, load_thesis_config


class TestLoadTravelThesisConfig:
    """Test loading travel thesis configuration."""

    def test_load_travel_thesis_config(self) -> None:
        """Test that travel thesis config loads successfully."""
        config = load_thesis_config("travel")
        assert config is not None
        assert isinstance(config, ThesisConfig)

    def test_config_has_scoring_weights(self) -> None:
        """Test that config has scoring weights."""
        config = load_thesis_config("travel")
        assert config.scoring_weights is not None
        assert isinstance(config.scoring_weights, dict)
        assert "distribution" in config.scoring_weights
        assert "category" in config.scoring_weights
        assert "traction" in config.scoring_weights
        assert "founder" in config.scoring_weights

    def test_weights_sum_to_one(self) -> None:
        """Test that scoring weights sum to 1.0."""
        config = load_thesis_config("travel")
        total = sum(config.scoring_weights.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_config_has_positive_signals(self) -> None:
        """Test that config has positive signals for all categories."""
        config = load_thesis_config("travel")
        assert config.positive_signals is not None
        assert isinstance(config.positive_signals, dict)
        assert "distribution" in config.positive_signals
        assert "category" in config.positive_signals
        assert "traction" in config.positive_signals
        assert "founder" in config.positive_signals
        # Verify each category has at least one signal
        for category, signals in config.positive_signals.items():
            assert len(signals) > 0, f"Category {category} has no signals"

    def test_config_has_negative_signals(self) -> None:
        """Test that config has negative signals."""
        config = load_thesis_config("travel")
        assert config.negative_signals is not None
        assert isinstance(config.negative_signals, list)
        assert len(config.negative_signals) > 0

    def test_invalid_vertical_raises_error(self) -> None:
        """Test that loading invalid vertical raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_thesis_config("nonexistent_vertical")
        assert "nonexistent_vertical" in str(exc_info.value)

    def test_config_attributes(self) -> None:
        """Test that config has all expected attributes."""
        config = load_thesis_config("travel")

        # Test basic attributes
        assert config.vertical == "travel"
        assert config.version == "1.0"
        assert "Travel & Hospitality" in config.description

        # Test stage filters
        assert config.stage_filters is not None
        assert "included" in config.stage_filters
        assert "excluded" in config.stage_filters
        assert "seed" in config.stage_filters["included"]
        assert "Series C" in config.stage_filters["excluded"]


class TestThesisConfigDataclass:
    """Test ThesisConfig dataclass."""

    def test_thesis_config_creation(self) -> None:
        """Test creating a ThesisConfig directly."""
        config = ThesisConfig(
            vertical="test",
            version="0.1",
            description="Test config",
            scoring_weights={"a": 0.5, "b": 0.5},
            positive_signals={"a": ["signal1"]},
            negative_signals=["negative1"],
            stage_filters={"included": ["seed"], "excluded": ["Series D"]},
        )
        assert config.vertical == "test"
        assert config.version == "0.1"
        assert config.description == "Test config"
        assert config.scoring_weights == {"a": 0.5, "b": 0.5}
        assert config.positive_signals == {"a": ["signal1"]}
        assert config.negative_signals == ["negative1"]
        assert config.stage_filters == {"included": ["seed"], "excluded": ["Series D"]}
