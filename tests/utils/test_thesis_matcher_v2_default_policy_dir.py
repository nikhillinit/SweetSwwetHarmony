"""
Integration test: ThesisMatcher v2 default policy directory.

Verifies that when V2_ENABLEMENT=shadow (or live), ThesisMatcher can:
1. Discover config/v2/ via resolve_policy_dir() auto-discovery
2. Load the marker file (negative_keyword_policy.yaml)
3. Initialize without errors

This test ensures the Phase 0A blocker is resolved - the config/v2/
directory and marker file exist at the default discovery location.
"""

import pytest

from utils.thesis_matcher import ThesisMatcher


class TestV2DefaultPolicyDirectory:
    """Test that v2 works with default config/v2/ discovery."""

    def test_shadow_mode_loads_from_default_directory(self, monkeypatch):
        """V2_ENABLEMENT=shadow should load policy from config/v2/ without explicit path."""
        # Clear any env vars that might override discovery
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Create matcher in shadow mode - should auto-discover config/v2/
        matcher = ThesisMatcher(v2_enablement="shadow")

        # Verify controls are set correctly
        assert matcher._controls.v2_enablement == "shadow"
        assert matcher._controls.policy_loader_mode == "strict"
        assert matcher._controls.v2_execution_enabled is True

        # Verify policy bundle was loaded from config/v2/
        assert matcher._policy_bundle is not None
        assert matcher._policy_bundle.base_dir.name == "v2"
        assert matcher._policy_bundle.base_dir.parent.name == "config"

        # Verify marker file was loaded
        assert "negative_keyword_policy" in matcher.config
        policy = matcher.config["negative_keyword_policy"]
        # Phase 0B-1: version exists but value not pinned; schema is the stable contract
        assert policy.get("version") is not None
        assert policy.get("schema") == "negative_keyword_policy_v1"
        assert "negative_keywords" in policy

        # Phase 0B-1: Verify negative_keywords is populated with all 40 keywords
        from utils.thesis_matcher import NEGATIVE_KEYWORDS
        assert len(policy.get("negative_keywords", {})) == len(NEGATIVE_KEYWORDS)

        # Phase 0B-1: Verify typed policy object is available
        assert matcher._negative_keyword_policy is not None
        assert len(matcher._negative_keyword_policy.keywords) == 40

    def test_live_mode_loads_from_default_directory(self, monkeypatch):
        """V2_ENABLEMENT=live should load policy from config/v2/ without explicit path."""
        # Clear any env vars that might override discovery
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Create matcher in live mode - should auto-discover config/v2/
        matcher = ThesisMatcher(v2_enablement="live")

        # Verify controls are set correctly
        assert matcher._controls.v2_enablement == "live"
        assert matcher._controls.policy_loader_mode == "strict"
        assert matcher._controls.v2_execution_enabled is True

        # Verify policy bundle was loaded
        assert matcher._policy_bundle is not None
        assert "negative_keyword_policy" in matcher.config

    def test_disabled_mode_does_not_load_policy(self, monkeypatch):
        """V2_ENABLEMENT=disabled should not load policy (zero I/O cost)."""
        # Clear any env vars
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Create matcher in disabled mode - should NOT load policy
        matcher = ThesisMatcher(v2_enablement="disabled")

        # Verify controls
        assert matcher._controls.v2_enablement == "disabled"

        # Verify NO policy bundle loaded (zero I/O)
        assert matcher._policy_bundle is None
        assert matcher.config == {}

    def test_env_var_shadow_loads_from_default_directory(self, monkeypatch):
        """V2_ENABLEMENT env var set to 'shadow' should work."""
        # Clear explicit path env vars
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Set enablement via env var
        monkeypatch.setenv("V2_ENABLEMENT", "shadow")

        # Create matcher without explicit enablement - should pick up env var
        matcher = ThesisMatcher()

        # Verify shadow mode was enabled from env
        assert matcher._controls.v2_enablement == "shadow"
        assert matcher._policy_bundle is not None
        assert "negative_keyword_policy" in matcher.config
