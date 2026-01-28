"""
Tests for profilers.config module.

Tests privacy configuration loading from environment variables.
Following TDD: These tests are written FIRST and should FAIL initially.
"""

import os
import pytest
from profilers.config import PrivacyConfig, load_privacy_config


class TestPrivacyConfig:
    """Tests for PrivacyConfig dataclass."""

    def test_privacy_config_defaults_to_false(self):
        """Test that PrivacyConfig defaults both flags to False for safety."""
        config = PrivacyConfig()

        assert config.allow_cloud_llm is False, "allow_cloud_llm should default to False"
        assert config.allow_cloud_vision is False, "allow_cloud_vision should default to False"

    def test_privacy_config_can_be_set_true(self):
        """Test that PrivacyConfig accepts True values."""
        config = PrivacyConfig(allow_cloud_llm=True, allow_cloud_vision=True)

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is True

    def test_privacy_config_mixed_values(self):
        """Test that flags can be set independently."""
        config1 = PrivacyConfig(allow_cloud_llm=True, allow_cloud_vision=False)
        assert config1.allow_cloud_llm is True
        assert config1.allow_cloud_vision is False

        config2 = PrivacyConfig(allow_cloud_llm=False, allow_cloud_vision=True)
        assert config2.allow_cloud_llm is False
        assert config2.allow_cloud_vision is True


class TestLoadPrivacyConfig:
    """Tests for load_privacy_config() function."""

    def test_load_privacy_config_defaults_when_no_env_vars(self, monkeypatch):
        """Test that load_privacy_config returns False defaults when env vars not set."""
        # Clear environment variables
        monkeypatch.delenv("ALLOW_CLOUD_LLM", raising=False)
        monkeypatch.delenv("ALLOW_CLOUD_VISION", raising=False)

        config = load_privacy_config()

        assert config.allow_cloud_llm is False
        assert config.allow_cloud_vision is False

    def test_load_privacy_config_reads_true_string(self, monkeypatch):
        """Test that load_privacy_config parses 'true' string correctly."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "true")
        monkeypatch.setenv("ALLOW_CLOUD_VISION", "true")

        config = load_privacy_config()

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is True

    def test_load_privacy_config_reads_false_string(self, monkeypatch):
        """Test that load_privacy_config parses 'false' string correctly."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "false")
        monkeypatch.setenv("ALLOW_CLOUD_VISION", "false")

        config = load_privacy_config()

        assert config.allow_cloud_llm is False
        assert config.allow_cloud_vision is False

    def test_load_privacy_config_case_insensitive(self, monkeypatch):
        """Test that string parsing is case-insensitive."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "TRUE")
        monkeypatch.setenv("ALLOW_CLOUD_VISION", "True")

        config = load_privacy_config()

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is True

    def test_load_privacy_config_handles_one_digit(self, monkeypatch):
        """Test that load_privacy_config handles '1' as true, '0' as false."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "1")
        monkeypatch.setenv("ALLOW_CLOUD_VISION", "0")

        config = load_privacy_config()

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is False

    def test_load_privacy_config_handles_yes_no(self, monkeypatch):
        """Test that load_privacy_config handles 'yes'/'no'."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "yes")
        monkeypatch.setenv("ALLOW_CLOUD_VISION", "no")

        config = load_privacy_config()

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is False

    def test_load_privacy_config_handles_on_off(self, monkeypatch):
        """Test that load_privacy_config handles 'on'/'off'."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "on")
        monkeypatch.setenv("ALLOW_CLOUD_VISION", "off")

        config = load_privacy_config()

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is False

    def test_load_privacy_config_mixed_settings(self, monkeypatch):
        """Test that env vars can be set independently."""
        monkeypatch.setenv("ALLOW_CLOUD_LLM", "true")
        monkeypatch.delenv("ALLOW_CLOUD_VISION", raising=False)

        config = load_privacy_config()

        assert config.allow_cloud_llm is True
        assert config.allow_cloud_vision is False
