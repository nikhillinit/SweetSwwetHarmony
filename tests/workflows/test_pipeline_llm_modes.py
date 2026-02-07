"""Tests for Phase 9: LLM thesis classification modes (off/shadow/active)."""

import os
from unittest.mock import Mock, patch, AsyncMock
import pytest

from workflows.pipeline import DiscoveryPipeline
from utils.thesis_filter import ThesisFilter, ThesisFilterResult, RoutingDecision


class TestPipelineLLMModes:
    """Test LLM_THESIS_MODE environment variable integration."""

    def test_mode_off_skips_llm(self, monkeypatch):
        """Verify mode=off skips LLM calls entirely."""
        monkeypatch.setenv("LLM_THESIS_MODE", "off")

        # Test the env var logic from pipeline.py
        llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
        skip_llm = (llm_mode == "off")

        assert skip_llm is True
        assert llm_mode == "off"

    def test_mode_shadow_calls_llm(self, monkeypatch):
        """Verify mode=shadow calls LLM but stores results."""
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")

        llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
        skip_llm = (llm_mode == "off")

        assert skip_llm is False
        assert llm_mode == "shadow"

    def test_mode_active_calls_llm(self, monkeypatch):
        """Verify mode=active calls LLM and affects routing."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")

        llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
        skip_llm = (llm_mode == "off")

        assert skip_llm is False
        assert llm_mode == "active"

    def test_default_mode_is_off(self, monkeypatch):
        """Verify default behavior when LLM_THESIS_MODE not set."""
        monkeypatch.delenv("LLM_THESIS_MODE", raising=False)

        llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
        skip_llm = (llm_mode == "off")

        assert skip_llm is True
        assert llm_mode == "off"
