"""Tests for governance/state_policies.py — two-lane policy enforcement."""

import pytest

from governance.state_policies import (
    ALL_GOVERNANCE_STATES,
    GovernanceStatePolicyError,
    allowed_states_for_flag,
    ensure_registered_flag,
    is_registered_flag,
    validate_transition,
)


# ── Lane 1: env-backed flags ────────────────────────────────────────────


class TestEnvBackedFlags:
    def test_delivery_mode_registered(self):
        assert is_registered_flag("DELIVERY_MODE")

    def test_delivery_mode_states_ordered(self):
        states = allowed_states_for_flag("DELIVERY_MODE")
        assert states == (
            "staging_only", "manual_publish", "batch_publish", "auto_publish",
        )

    def test_merge_writes_registered(self):
        assert is_registered_flag("MERGE_WRITES_ENABLED")
        assert allowed_states_for_flag("MERGE_WRITES_ENABLED") == (
            "disabled", "shadow", "active",
        )

    def test_ml_enablement_registered(self):
        assert is_registered_flag("ML_ENABLEMENT")
        assert allowed_states_for_flag("ML_ENABLEMENT") == (
            "disabled", "shadow", "live",
        )

    def test_promote_delivery_mode_upward(self):
        validate_transition(
            "feature_promote", "DELIVERY_MODE",
            "manual_publish", "batch_publish",
        )

    def test_demote_delivery_mode_downward(self):
        validate_transition(
            "feature_demote", "DELIVERY_MODE",
            "batch_publish", "manual_publish",
        )

    def test_promote_wrong_direction_raises(self):
        with pytest.raises(GovernanceStatePolicyError, match="wrong direction"):
            validate_transition(
                "feature_promote", "DELIVERY_MODE",
                "batch_publish", "manual_publish",
            )

    def test_demote_wrong_direction_raises(self):
        with pytest.raises(GovernanceStatePolicyError, match="wrong direction"):
            validate_transition(
                "feature_demote", "DELIVERY_MODE",
                "manual_publish", "batch_publish",
            )

    def test_skip_level_promote_rejected(self):
        with pytest.raises(GovernanceStatePolicyError, match="Skip-level"):
            validate_transition(
                "feature_promote", "DELIVERY_MODE",
                "staging_only", "auto_publish",
            )

    def test_skip_level_demote_rejected(self):
        with pytest.raises(GovernanceStatePolicyError, match="Skip-level"):
            validate_transition(
                "feature_demote", "DELIVERY_MODE",
                "auto_publish", "staging_only",
            )

    def test_noop_rejected(self):
        with pytest.raises(GovernanceStatePolicyError, match="No-op"):
            validate_transition(
                "feature_promote", "DELIVERY_MODE",
                "batch_publish", "batch_publish",
            )

    def test_invalid_from_state_rejected(self):
        with pytest.raises(GovernanceStatePolicyError, match="Invalid from_state"):
            validate_transition(
                "feature_promote", "DELIVERY_MODE",
                "nonexistent", "batch_publish",
            )

    def test_invalid_to_state_rejected(self):
        with pytest.raises(GovernanceStatePolicyError, match="Invalid to_state"):
            validate_transition(
                "feature_promote", "DELIVERY_MODE",
                "staging_only", "nonexistent",
            )


# ── Lane 2: feature-registry experiments ─────────────────────────────────


class TestFeatureRegistryFlags:
    def test_boilerplate_defense_registered(self):
        assert is_registered_flag("boilerplate_defense")

    def test_thesis_match_registered(self):
        assert is_registered_flag("thesis_match")

    def test_feature_registry_states(self):
        assert allowed_states_for_flag("boilerplate_defense") == (
            "off", "shadow", "active",
        )

    def test_promote_upward(self):
        validate_transition(
            "feature_promote", "boilerplate_defense",
            "shadow", "active",
        )

    def test_demote_downward(self):
        validate_transition(
            "feature_demote", "boilerplate_defense",
            "active", "shadow",
        )

    def test_skip_level_allowed(self):
        """Feature-registry allows skip-level (preserves existing active→off flow)."""
        validate_transition(
            "feature_demote", "boilerplate_defense",
            "active", "off",
        )

    def test_promote_wrong_direction_raises(self):
        with pytest.raises(GovernanceStatePolicyError, match="wrong direction"):
            validate_transition(
                "feature_promote", "thesis_match",
                "active", "shadow",
            )

    def test_noop_rejected(self):
        with pytest.raises(GovernanceStatePolicyError, match="No-op"):
            validate_transition(
                "feature_promote", "thesis_match",
                "shadow", "shadow",
            )


# ── Case hints and unknown rejection ────────────────────────────────────


class TestCaseHints:
    def test_lowercase_env_flag_hint(self):
        with pytest.raises(GovernanceStatePolicyError, match="Did you mean 'DELIVERY_MODE'"):
            ensure_registered_flag("delivery_mode")

    def test_uppercase_registry_flag_hint(self):
        with pytest.raises(GovernanceStatePolicyError, match="Did you mean 'boilerplate_defense'"):
            ensure_registered_flag("BOILERPLATE_DEFENSE")

    def test_feature_prefix_hint(self):
        with pytest.raises(GovernanceStatePolicyError, match="Did you mean 'boilerplate_defense'"):
            ensure_registered_flag("FEATURE_BOILERPLATE_DEFENSE")

    def test_unknown_flag_no_hint(self):
        with pytest.raises(GovernanceStatePolicyError, match="not registered for governance"):
            ensure_registered_flag("NONEXISTENT")

    def test_unknown_not_registered(self):
        assert not is_registered_flag("NONEXISTENT")


# ── ALL_GOVERNANCE_STATES ────────────────────────────────────────────────


class TestAllGovernanceStates:
    def test_contains_env_backed_states(self):
        for s in ("staging_only", "manual_publish", "batch_publish", "auto_publish"):
            assert s in ALL_GOVERNANCE_STATES

    def test_contains_feature_registry_states(self):
        for s in ("off", "shadow", "active"):
            assert s in ALL_GOVERNANCE_STATES

    def test_contains_enablement_states(self):
        for s in ("disabled", "live"):
            assert s in ALL_GOVERNANCE_STATES

    def test_is_frozenset(self):
        assert isinstance(ALL_GOVERNANCE_STATES, frozenset)


# ── Sync tests: upstream constants ───────────────────────────────────────


class TestUpstreamSync:
    def test_delivery_mode_matches_valid_delivery_modes(self):
        from utils.config_validator import VALID_DELIVERY_MODES
        states = set(allowed_states_for_flag("DELIVERY_MODE"))
        assert states == set(VALID_DELIVERY_MODES)

    def test_ml_enablement_matches_valid_ml_enablements(self):
        from utils.runtime_controls import VALID_ML_ENABLEMENTS
        states = set(allowed_states_for_flag("ML_ENABLEMENT"))
        assert states == set(VALID_ML_ENABLEMENTS)

    def test_env_backed_flags_subset_of_config_keys(self):
        from monitoring.feature_gate import _CONFIG_KEYS
        from governance.state_policies import _ENV_BACKED_FLAGS
        for flag in _ENV_BACKED_FLAGS:
            assert flag in _CONFIG_KEYS, (
                f"Env-backed flag {flag} not in _CONFIG_KEYS — snapshot would miss it"
            )
