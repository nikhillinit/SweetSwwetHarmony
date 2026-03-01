"""Tests for governance contracts — Pydantic validation enforcement."""

import pytest
from pydantic import ValidationError

from governance.contracts import (
    FeatureDemoteMetadata,
    FeaturePromoteMetadata,
    RegretCheckMetadata,
    GOVERNANCE_ACTION_TYPES,
)


class TestFeaturePromoteMetadata:
    def test_valid_promote(self):
        m = FeaturePromoteMetadata(
            feature_name="boilerplate_defense",
            from_state="shadow",
            to_state="active",
            regret_due_at="2026-03-14T00:00:00+00:00",
            config_snapshot_hash="abc123",
        )
        assert m.action_type == "feature_promote"
        assert m.feature_name == "boilerplate_defense"

    def test_with_optional_snapshot_flags(self):
        m = FeaturePromoteMetadata(
            feature_name="thesis_match",
            from_state="off",
            to_state="shadow",
            regret_due_at="2026-04-01",
            config_snapshot_hash="def456",
            config_snapshot_flags={"LLM_THESIS_MODE": "shadow"},
        )
        assert m.config_snapshot_flags == {"LLM_THESIS_MODE": "shadow"}

    def test_missing_feature_name_raises(self):
        with pytest.raises(ValidationError):
            FeaturePromoteMetadata(
                from_state="shadow",
                to_state="active",
                regret_due_at="2026-03-14",
                config_snapshot_hash="abc",
            )

    def test_invalid_state_raises(self):
        with pytest.raises(ValidationError, match="State must be"):
            FeaturePromoteMetadata(
                feature_name="x",
                from_state="invalid",
                to_state="active",
                regret_due_at="2026-03-14",
                config_snapshot_hash="abc",
            )

    def test_missing_regret_due_at_raises(self):
        with pytest.raises(ValidationError):
            FeaturePromoteMetadata(
                feature_name="x",
                from_state="shadow",
                to_state="active",
                config_snapshot_hash="abc",
            )

    def test_model_dump_includes_action_type(self):
        m = FeaturePromoteMetadata(
            feature_name="x",
            from_state="shadow",
            to_state="active",
            regret_due_at="2026-03-14",
            config_snapshot_hash="h",
        )
        d = m.model_dump()
        assert d["action_type"] == "feature_promote"


class TestRegretCheckMetadata:
    def test_valid_regret_check(self):
        m = RegretCheckMetadata(
            verdict="pass",
            canary_verdict="pass",
            drift_status="in_control",
        )
        assert m.window_days == 14

    def test_custom_window_days(self):
        m = RegretCheckMetadata(
            verdict="fail",
            canary_verdict="no_data",
            drift_status="warning",
            window_days=7,
        )
        assert m.window_days == 7

    def test_invalid_verdict_raises(self):
        with pytest.raises(ValidationError):
            RegretCheckMetadata(
                verdict="maybe",
                canary_verdict="pass",
                drift_status="in_control",
            )

    def test_invalid_canary_verdict_raises(self):
        with pytest.raises(ValidationError):
            RegretCheckMetadata(
                verdict="pass",
                canary_verdict="unknown",
                drift_status="in_control",
            )

    def test_invalid_drift_status_raises(self):
        with pytest.raises(ValidationError):
            RegretCheckMetadata(
                verdict="pass",
                canary_verdict="pass",
                drift_status="panic",
            )


class TestFeatureDemoteMetadata:
    def test_valid_demote(self):
        m = FeatureDemoteMetadata(
            from_state="active",
            to_state="shadow",
        )
        assert m.rollback_ticket is None

    def test_with_optional_fields(self):
        m = FeatureDemoteMetadata(
            from_state="active",
            to_state="off",
            rollback_ticket="JIRA-123",
            incident_id="INC-456",
        )
        assert m.rollback_ticket == "JIRA-123"

    def test_invalid_state_raises(self):
        with pytest.raises(ValidationError, match="State must be"):
            FeatureDemoteMetadata(
                from_state="running",
                to_state="shadow",
            )


class TestGovernanceActionTypes:
    def test_all_types_present(self):
        assert GOVERNANCE_ACTION_TYPES == {
            "feature_promote",
            "regret_check",
            "feature_demote",
            "feature_eval_completed",
        }
