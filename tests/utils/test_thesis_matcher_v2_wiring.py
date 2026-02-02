"""
Tests for ThesisMatcher v2 policy wiring (Phase 0A).

Tests:
1. Legacy precedence beats env - enable_v2_policy=True overrides V2_ENABLEMENT="disabled"
2. Zero-cost disabled - monkeypatch resolve/load to raise; disabled doesn't trigger
3. No scoring changes - matcher_v1.score(x) == matcher_v2_disabled.score(x)
4. Config path warning when disabled - logs WARNING
5. Shallow copy contract - mutations to self.config don't affect bundle
6. Controls are accessible - _controls attribute is set correctly
"""

import pytest
from unittest.mock import patch, MagicMock


class TestLegacyPrecedenceBeatsEnv:
    """Test that legacy enable_v2_policy kwarg beats environment variables."""

    def test_legacy_true_overrides_env_disabled(self, monkeypatch, tmp_path):
        """enable_v2_policy=True should override V2_ENABLEMENT='disabled' from env."""
        # Set env to disabled
        monkeypatch.setenv("V2_ENABLEMENT", "disabled")

        # Create a minimal policy file for the loader
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text("version: '2.0'\ntest: true\n")

        from utils.thesis_matcher import ThesisMatcher

        # Legacy kwarg should override env
        matcher = ThesisMatcher(
            enable_v2_policy=True,
            config_path=str(tmp_path),
        )

        # Should be in shadow mode (legacy True → shadow)
        assert matcher._controls is not None
        assert matcher._controls.v2_enablement == "shadow"
        assert matcher._controls.policy_loader_mode == "strict"  # derived from shadow

    def test_legacy_false_overrides_env_shadow(self, monkeypatch):
        """enable_v2_policy=False should override V2_ENABLEMENT='shadow' from env."""
        # Set env to shadow
        monkeypatch.setenv("V2_ENABLEMENT", "shadow")

        from utils.thesis_matcher import ThesisMatcher

        # Legacy kwarg should override env
        matcher = ThesisMatcher(enable_v2_policy=False)

        # Should be disabled (legacy False → disabled)
        assert matcher._controls is not None
        assert matcher._controls.v2_enablement == "disabled"

    def test_explicit_v2_enablement_beats_legacy(self, monkeypatch, tmp_path):
        """Explicit v2_enablement kwarg should beat legacy enable_v2_policy."""
        # Create a minimal policy file
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text("version: '2.0'\ntest: true\n")

        from utils.thesis_matcher import ThesisMatcher

        # Modern kwarg should win over legacy
        matcher = ThesisMatcher(
            enable_v2_policy=False,  # legacy says disabled
            v2_enablement="shadow",  # modern says shadow
            config_path=str(tmp_path),
        )

        # Modern wins
        assert matcher._controls.v2_enablement == "shadow"


class TestZeroCostDisabled:
    """Test that disabled mode does NO I/O (resolve_policy_dir/load_policy_bundle not called)."""

    def test_disabled_does_not_call_resolve_or_load(self, monkeypatch):
        """When v2_enablement='disabled', resolve_policy_dir and load_policy_bundle should NOT be called."""
        # Track if these functions were called
        resolve_called = []
        load_called = []

        original_resolve = None
        original_load = None

        def tracking_resolve(*args, **kwargs):
            resolve_called.append(True)
            raise AssertionError("resolve_policy_dir was called but should not have been!")

        def tracking_load(*args, **kwargs):
            load_called.append(True)
            raise AssertionError("load_policy_bundle was called but should not have been!")

        # Patch at the source module level
        monkeypatch.setattr("utils.policy_loader.resolve_policy_dir", tracking_resolve)
        monkeypatch.setattr("utils.policy_loader.load_policy_bundle", tracking_load)

        # Now import ThesisMatcher fresh (it imports from policy_loader inside __init__)
        from utils.thesis_matcher import ThesisMatcher

        # This should NOT raise because disabled mode short-circuits before I/O
        matcher = ThesisMatcher(v2_enablement="disabled")

        assert matcher._controls.v2_enablement == "disabled"
        assert matcher._policy_bundle is None
        assert matcher.config == {}
        # Verify the functions were never called
        assert len(resolve_called) == 0, "resolve_policy_dir should not have been called"
        assert len(load_called) == 0, "load_policy_bundle should not have been called"

    def test_disabled_with_legacy_false_no_io(self, monkeypatch):
        """Legacy enable_v2_policy=False should also result in zero I/O."""
        resolve_called = []

        def tracking_resolve(*args, **kwargs):
            resolve_called.append(True)
            raise AssertionError("resolve_policy_dir was called!")

        # Patch at the source module level
        monkeypatch.setattr("utils.policy_loader.resolve_policy_dir", tracking_resolve)

        from utils.thesis_matcher import ThesisMatcher

        # Legacy False → disabled, no I/O
        matcher = ThesisMatcher(enable_v2_policy=False)

        assert matcher._controls.v2_enablement == "disabled"
        assert len(resolve_called) == 0, "resolve_policy_dir should not have been called"


class TestNoScoringChanges:
    """Test that scoring behavior is unchanged when v2 is disabled."""

    @pytest.mark.parametrize("text,expected_thesis", [
        ("We make healthy meal kits delivered to your door", "consumer_cpg"),
        ("A fitness app for tracking your workouts and wellness", "consumer_health_tech"),
        ("Travel booking platform for unique hotel experiences", "travel_hospitality"),
        ("Consumer marketplace connecting buyers and sellers", "consumer_marketplace"),
        ("Enterprise B2B SaaS platform for developers", "unknown"),
    ])
    def test_scoring_identical_v1_vs_v2_disabled(self, text, expected_thesis):
        """v1 matcher and v2 disabled matcher should produce identical scores."""
        from utils.thesis_matcher import ThesisMatcher

        # v1 style (no v2 args)
        matcher_v1 = ThesisMatcher()

        # v2 explicitly disabled
        matcher_v2_disabled = ThesisMatcher(v2_enablement="disabled")

        # Score the same text
        fit_v1 = matcher_v1.score(text)
        fit_v2 = matcher_v2_disabled.score(text)

        # Results should be identical
        assert fit_v1.thesis.value == fit_v2.thesis.value
        assert fit_v1.score == fit_v2.score
        assert fit_v1.matched_keywords == fit_v2.matched_keywords
        assert fit_v1.negative_keywords == fit_v2.negative_keywords
        assert fit_v1.confidence == fit_v2.confidence

    def test_scoring_identical_with_domain(self):
        """Domain analysis should also be identical between v1 and v2 disabled."""
        from utils.thesis_matcher import ThesisMatcher

        matcher_v1 = ThesisMatcher()
        matcher_v2_disabled = ThesisMatcher(v2_enablement="disabled")

        text = "Health and wellness platform"
        domain = "getfitness.com"

        fit_v1 = matcher_v1.score(text, domain_name=domain)
        fit_v2 = matcher_v2_disabled.score(text, domain_name=domain)

        assert fit_v1.score == fit_v2.score
        assert fit_v1.domain_match == fit_v2.domain_match

    def test_scoring_identical_empty_text(self):
        """Empty text should produce identical results."""
        from utils.thesis_matcher import ThesisMatcher

        matcher_v1 = ThesisMatcher()
        matcher_v2_disabled = ThesisMatcher(v2_enablement="disabled")

        fit_v1 = matcher_v1.score("")
        fit_v2 = matcher_v2_disabled.score("")

        assert fit_v1.score == fit_v2.score == 0.0
        assert fit_v1.thesis == fit_v2.thesis


class TestConfigPathWarningWhenDisabled:
    """Test that config_path supplied while disabled logs a WARNING."""

    def test_config_path_with_disabled_logs_warning(self, caplog, tmp_path):
        """Supplying config_path when v2 is disabled should log a warning."""
        import logging
        from utils.thesis_matcher import ThesisMatcher

        with caplog.at_level(logging.WARNING):
            matcher = ThesisMatcher(
                v2_enablement="disabled",
                config_path=str(tmp_path),
            )

        # Should have logged a warning
        assert any(
            "config_path" in record.message and "disabled" in record.message
            for record in caplog.records
        ), f"Expected warning about config_path with disabled, got: {[r.message for r in caplog.records]}"


class TestShallowCopyContract:
    """Test that self.config is a shallow copy of bundle.policies."""

    def test_config_is_shallow_copy(self, tmp_path):
        """Mutating self.config should not affect bundle.policies."""
        # Create a policy file
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text("version: '2.0'\ntest_key: test_value\n")

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(
            v2_enablement="shadow",
            config_path=str(tmp_path),
        )

        # Verify config was loaded
        assert "negative_keyword_policy" in matcher.config

        # Get reference to original bundle policies
        original_bundle_keys = set(matcher._policy_bundle.policies.keys())

        # Mutate self.config at top level
        matcher.config["new_key"] = "new_value"
        del matcher.config["negative_keyword_policy"]

        # Bundle should be unchanged
        assert set(matcher._policy_bundle.policies.keys()) == original_bundle_keys
        assert "new_key" not in matcher._policy_bundle.policies


class TestControlsAccessible:
    """Test that _controls attribute is correctly set."""

    def test_controls_set_on_disabled(self):
        """_controls should be set even when disabled."""
        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(v2_enablement="disabled")

        assert matcher._controls is not None
        assert matcher._controls.v2_enablement == "disabled"

    def test_controls_set_on_shadow(self, tmp_path):
        """_controls should be set in shadow mode."""
        policy_file = tmp_path / "negative_keyword_policy.yaml"
        policy_file.write_text("version: '2.0'\n")

        from utils.thesis_matcher import ThesisMatcher

        matcher = ThesisMatcher(
            v2_enablement="shadow",
            config_path=str(tmp_path),
        )

        assert matcher._controls is not None
        assert matcher._controls.v2_enablement == "shadow"
        assert matcher._controls.policy_loader_mode == "strict"
        assert matcher._controls.v2_execution_enabled is True
