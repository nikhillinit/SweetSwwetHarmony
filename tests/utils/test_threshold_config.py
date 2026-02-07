"""Tests for ThresholdConfig (split Matching + Workflow)."""

import os
from unittest.mock import patch

import pytest

from utils.threshold_config import (
    MatchingThresholdConfig,
    WorkflowThresholdConfig,
)


class TestMatchingThresholdConfig:
    """Test matching threshold configuration."""

    def test_default_values_match_current_hardcoded(self):
        config = MatchingThresholdConfig.default()
        assert config.is_fit_threshold == 0.4
        assert config.qualified_threshold == 0.3
        assert config.held_threshold == 0.1
        assert config.high_confidence == 0.7
        assert config.medium_confidence == 0.4
        assert config.thesis_assignment_threshold == 0.1

    def test_frozen(self):
        config = MatchingThresholdConfig.default()
        with pytest.raises(AttributeError):
            config.is_fit_threshold = 0.5

    def test_from_env_with_overrides(self):
        with patch.dict(os.environ, {"MATCHING_IS_FIT_THRESHOLD": "0.35"}):
            config = MatchingThresholdConfig.from_env()
            assert config.is_fit_threshold == 0.35
            assert config.qualified_threshold == 0.3  # default

    def test_from_env_invalid_float_uses_default(self):
        with patch.dict(os.environ, {"MATCHING_IS_FIT_THRESHOLD": "not_a_number"}):
            config = MatchingThresholdConfig.from_env()
            assert config.is_fit_threshold == 0.4  # default


class TestWorkflowThresholdConfig:
    """Test workflow threshold configuration."""

    def test_default_values_match_current_hardcoded(self):
        config = WorkflowThresholdConfig.default()
        assert config.hold_threshold == 0.3
        assert config.skip_llm_if_keyword_below == 0.2
        assert config.keyword_high_threshold == 0.7
        assert config.keyword_low_threshold == 0.4
        assert config.high_boost == 0.08
        assert config.low_penalty == -0.08
        assert config.negative_keyword_penalty == -0.12
        assert config.llm_review_threshold == 0.50
        assert config.llm_auto_approve_threshold == 0.85

    def test_frozen(self):
        config = WorkflowThresholdConfig.default()
        with pytest.raises(AttributeError):
            config.hold_threshold = 0.5

    def test_from_env_with_overrides(self):
        with patch.dict(os.environ, {
            "WORKFLOW_HOLD_THRESHOLD": "0.25",
            "WORKFLOW_LLM_REVIEW_THRESHOLD": "0.45",
        }):
            config = WorkflowThresholdConfig.from_env()
            assert config.hold_threshold == 0.25
            assert config.llm_review_threshold == 0.45
            assert config.llm_auto_approve_threshold == 0.85  # default


class TestConfigSeparation:
    """Verify matching and workflow configs are independent."""

    def test_matching_and_workflow_are_separate_classes(self):
        matching = MatchingThresholdConfig.default()
        workflow = WorkflowThresholdConfig.default()

        # Different types
        assert type(matching) != type(workflow)

        # No shared attributes (except Python builtins)
        matching_fields = {f for f in dir(matching) if not f.startswith("_")}
        workflow_fields = {f for f in dir(workflow) if not f.startswith("_")}
        shared = matching_fields & workflow_fields - {"default", "from_env"}

        # Only factory methods should be shared
        assert len(shared) == 0, f"Unexpected shared fields: {shared}"
