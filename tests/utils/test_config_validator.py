"""
Tests for utils/config_validator.py

Validates the config validator reports correct issues for:
- DELIVERY_MODE validation
- Confidence threshold bounds
- Notion API key presence
"""

import os
import pytest

from utils.config_validator import (
    ConfigIssue,
    validate_config,
    print_config_report,
)


# =============================================================================
# ConfigIssue dataclass
# =============================================================================


class TestConfigIssue:
    """Tests for the ConfigIssue dataclass."""

    def test_error_issue(self):
        issue = ConfigIssue(level="error", key="DELIVERY_MODE", message="invalid")
        assert issue.level == "error"
        assert issue.key == "DELIVERY_MODE"
        assert issue.message == "invalid"

    def test_warning_issue(self):
        issue = ConfigIssue(level="warning", key="NOTION_API_KEY", message="not set")
        assert issue.level == "warning"

    def test_info_issue(self):
        issue = ConfigIssue(level="info", key="DELIVERY_MODE", message="ok")
        assert issue.level == "info"


# =============================================================================
# DELIVERY_MODE validation
# =============================================================================


class TestDeliveryModeValidation:
    """Tests for DELIVERY_MODE validation."""

    def test_valid_staging_only(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 0
        # Should have an info entry
        infos = [i for i in delivery_issues if i.level == "info"]
        assert len(infos) == 1
        assert "staging_only" in infos[0].message

    def test_valid_manual_publish(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 0

    def test_valid_batch_publish(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 0

    def test_valid_auto_publish(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 0

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "Batch_Publish")
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 0

    def test_invalid_mode_produces_error(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "foobar")
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 1
        assert "foobar" in errors[0].message

    def test_unset_mode_is_info_default(self, monkeypatch):
        monkeypatch.delenv("DELIVERY_MODE", raising=False)
        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]
        errors = [i for i in delivery_issues if i.level == "error"]
        assert len(errors) == 0
        # Should report info that default is used
        infos = [i for i in delivery_issues if i.level == "info"]
        assert len(infos) == 1
        assert "staging_only" in infos[0].message


# =============================================================================
# Confidence threshold validation
# =============================================================================


class TestThresholdValidation:
    """Tests for confidence threshold validation."""

    def test_valid_default_thresholds(self, monkeypatch):
        """Default thresholds (0.7, 0.4) should pass."""
        # Clear any overrides
        for var in [
            "MATCHING_HIGH_CONFIDENCE",
            "MATCHING_MEDIUM_CONFIDENCE",
            "MATCHING_IS_FIT_THRESHOLD",
            "MATCHING_QUALIFIED_THRESHOLD",
            "MATCHING_HELD_THRESHOLD",
            "WORKFLOW_HOLD_THRESHOLD",
            "WORKFLOW_LLM_REVIEW_THRESHOLD",
            "WORKFLOW_LLM_AUTO_APPROVE_THRESHOLD",
        ]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("DELIVERY_MODE", raising=False)

        issues = validate_config()
        threshold_errors = [
            i for i in issues if "threshold" in i.key.lower() and i.level == "error"
        ]
        assert len(threshold_errors) == 0

    def test_threshold_above_one_is_error(self, monkeypatch):
        monkeypatch.setenv("MATCHING_HIGH_CONFIDENCE", "1.5")
        issues = validate_config()
        threshold_errors = [
            i for i in issues
            if i.key == "MATCHING_HIGH_CONFIDENCE" and i.level == "error"
        ]
        assert len(threshold_errors) == 1
        assert "1.5" in threshold_errors[0].message

    def test_threshold_below_zero_is_error(self, monkeypatch):
        monkeypatch.setenv("MATCHING_MEDIUM_CONFIDENCE", "-0.1")
        issues = validate_config()
        threshold_errors = [
            i for i in issues
            if i.key == "MATCHING_MEDIUM_CONFIDENCE" and i.level == "error"
        ]
        assert len(threshold_errors) == 1

    def test_threshold_zero_is_valid(self, monkeypatch):
        monkeypatch.setenv("MATCHING_HELD_THRESHOLD", "0.0")
        issues = validate_config()
        threshold_errors = [
            i for i in issues
            if i.key == "MATCHING_HELD_THRESHOLD" and i.level == "error"
        ]
        assert len(threshold_errors) == 0

    def test_threshold_one_is_valid(self, monkeypatch):
        monkeypatch.setenv("MATCHING_HIGH_CONFIDENCE", "1.0")
        issues = validate_config()
        threshold_errors = [
            i for i in issues
            if i.key == "MATCHING_HIGH_CONFIDENCE" and i.level == "error"
        ]
        assert len(threshold_errors) == 0

    def test_non_numeric_threshold_is_error(self, monkeypatch):
        monkeypatch.setenv("MATCHING_HIGH_CONFIDENCE", "not_a_number")
        issues = validate_config()
        threshold_errors = [
            i for i in issues
            if i.key == "MATCHING_HIGH_CONFIDENCE" and i.level == "error"
        ]
        assert len(threshold_errors) == 1
        assert "not_a_number" in threshold_errors[0].message


# =============================================================================
# Notion API key validation
# =============================================================================


class TestNotionKeyValidation:
    """Tests for Notion API key presence checks."""

    def test_notion_key_present(self, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "secret_real_key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "real_db_id")
        issues = validate_config()
        notion_warnings = [
            i for i in issues
            if "NOTION" in i.key and i.level == "warning"
        ]
        assert len(notion_warnings) == 0

    def test_notion_key_missing_is_warning(self, monkeypatch):
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        issues = validate_config()
        notion_warnings = [
            i for i in issues
            if i.key == "NOTION_API_KEY" and i.level == "warning"
        ]
        assert len(notion_warnings) == 1

    def test_notion_database_id_missing_is_warning(self, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "secret_real_key")
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        issues = validate_config()
        notion_warnings = [
            i for i in issues
            if i.key == "NOTION_DATABASE_ID" and i.level == "warning"
        ]
        assert len(notion_warnings) == 1


class TestNotionKeyDeliveryModeAware:
    """Tests for delivery-mode-aware Notion key validation (M1.0)."""

    def test_staging_only_missing_keys_are_warnings(self, monkeypatch):
        """staging_only does not require Notion -- missing keys are warnings."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        issues = validate_config()
        notion_issues = [i for i in issues if "NOTION" in i.key]
        errors = [i for i in notion_issues if i.level == "error"]
        warnings = [i for i in notion_issues if i.level == "warning"]
        assert len(errors) == 0
        assert len(warnings) == 2

    def test_batch_publish_missing_keys_are_errors(self, monkeypatch):
        """batch_publish requires Notion -- missing keys must be errors."""
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        issues = validate_config()
        notion_issues = [i for i in issues if "NOTION" in i.key]
        errors = [i for i in notion_issues if i.level == "error"]
        assert len(errors) == 2
        assert all("required" in e.message.lower() for e in errors)

    def test_batch_publish_with_keys_present_is_info(self, monkeypatch):
        """batch_publish + keys present -- no errors or warnings."""
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        monkeypatch.setenv("NOTION_API_KEY", "secret_real_key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "real_db_id")
        issues = validate_config()
        notion_issues = [i for i in issues if "NOTION" in i.key]
        errors = [i for i in notion_issues if i.level == "error"]
        warnings = [i for i in notion_issues if i.level == "warning"]
        assert len(errors) == 0
        assert len(warnings) == 0

    def test_auto_publish_missing_database_id_is_error(self, monkeypatch):
        """auto_publish + missing NOTION_DATABASE_ID -- error on that key."""
        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
        monkeypatch.setenv("NOTION_API_KEY", "secret_real_key")
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
        issues = validate_config()
        db_errors = [
            i for i in issues
            if i.key == "NOTION_DATABASE_ID" and i.level == "error"
        ]
        assert len(db_errors) == 1
        # NOTION_API_KEY should be info (present)
        key_issues = [
            i for i in issues
            if i.key == "NOTION_API_KEY" and i.level == "info"
        ]
        assert len(key_issues) == 1


# =============================================================================
# print_config_report
# =============================================================================


class TestPrintConfigReport:
    """Tests for the human-readable report printer."""

    def test_empty_issues_prints_ok(self, capsys):
        print_config_report([])
        output = capsys.readouterr().out
        assert "Config Validation" in output

    def test_error_shown_in_output(self, capsys):
        issues = [
            ConfigIssue(
                level="error",
                key="DELIVERY_MODE",
                message="'foobar' is not a valid mode",
            )
        ]
        print_config_report(issues)
        output = capsys.readouterr().out
        assert "[ERROR]" in output
        assert "DELIVERY_MODE" in output

    def test_warning_shown_in_output(self, capsys):
        issues = [
            ConfigIssue(
                level="warning",
                key="NOTION_API_KEY",
                message="not configured",
            )
        ]
        print_config_report(issues)
        output = capsys.readouterr().out
        assert "[WARN]" in output

    def test_info_shown_as_ok(self, capsys):
        issues = [
            ConfigIssue(
                level="info",
                key="DELIVERY_MODE",
                message="staging_only",
            )
        ]
        print_config_report(issues)
        output = capsys.readouterr().out
        assert "[OK]" in output

    def test_report_returns_has_errors_flag(self):
        """print_config_report returns True if there are errors."""
        issues_with_errors = [
            ConfigIssue(level="error", key="X", message="bad"),
        ]
        issues_no_errors = [
            ConfigIssue(level="warning", key="X", message="meh"),
        ]
        assert print_config_report(issues_with_errors) is True
        assert print_config_report(issues_no_errors) is False


# =============================================================================
# Integration: validate_config returns structured results
# =============================================================================


class TestValidateConfigIntegration:
    """Integration tests verifying the full validate_config flow."""

    def test_all_valid_config(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        monkeypatch.setenv("NOTION_API_KEY", "secret_key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "db_id")
        # Clear threshold overrides
        for var in [
            "MATCHING_HIGH_CONFIDENCE",
            "MATCHING_MEDIUM_CONFIDENCE",
            "MATCHING_IS_FIT_THRESHOLD",
            "MATCHING_QUALIFIED_THRESHOLD",
            "MATCHING_HELD_THRESHOLD",
            "MATCHING_THESIS_THRESHOLD",
            "WORKFLOW_HOLD_THRESHOLD",
            "WORKFLOW_SKIP_LLM_THRESHOLD",
            "WORKFLOW_KEYWORD_HIGH",
            "WORKFLOW_KEYWORD_LOW",
            "WORKFLOW_LLM_REVIEW_THRESHOLD",
            "WORKFLOW_LLM_AUTO_APPROVE_THRESHOLD",
        ]:
            monkeypatch.delenv(var, raising=False)

        issues = validate_config()
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_multiple_issues_detected(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "invalid_mode")
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.setenv("MATCHING_HIGH_CONFIDENCE", "2.0")

        issues = validate_config()
        errors = [i for i in issues if i.level == "error"]
        warnings = [i for i in issues if i.level == "warning"]
        assert len(errors) >= 2  # DELIVERY_MODE + threshold
        assert len(warnings) >= 1  # NOTION_API_KEY
