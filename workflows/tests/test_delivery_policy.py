"""Tests for delivery policy guard.

Verifies that DELIVERY_MODE env var controls which Notion write
intents are allowed, preventing accidental Notion pollution.
"""
import pytest
from workflows.delivery_policy import (
    DeliveryMode,
    DeliveryIntent,
    DeliveryPolicyError,
    assert_notion_write_allowed,
    get_delivery_mode,
)


# =============================================================================
# DeliveryMode enum
# =============================================================================


class TestDeliveryMode:
    """DeliveryMode enum values and string conversion."""

    def test_staging_only_value(self):
        assert DeliveryMode.STAGING_ONLY.value == "staging_only"

    def test_manual_publish_value(self):
        assert DeliveryMode.MANUAL_PUBLISH.value == "manual_publish"

    def test_batch_publish_value(self):
        assert DeliveryMode.BATCH_PUBLISH.value == "batch_publish"

    def test_auto_publish_value(self):
        assert DeliveryMode.AUTO_PUBLISH.value == "auto_publish"

    def test_from_string(self):
        assert DeliveryMode("staging_only") is DeliveryMode.STAGING_ONLY

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            DeliveryMode("invalid")


# =============================================================================
# DeliveryIntent enum
# =============================================================================


class TestDeliveryIntent:
    """DeliveryIntent enum values."""

    def test_auto_push_value(self):
        assert DeliveryIntent.AUTO_PUSH.value == "auto_push"

    def test_manual_push_value(self):
        assert DeliveryIntent.MANUAL_PUSH.value == "manual_push"

    def test_batch_push_value(self):
        assert DeliveryIntent.BATCH_PUSH.value == "batch_push"


# =============================================================================
# get_delivery_mode
# =============================================================================


class TestGetDeliveryMode:
    """Reading DELIVERY_MODE from environment."""

    def test_default_is_staging_only(self, monkeypatch):
        monkeypatch.delenv("DELIVERY_MODE", raising=False)
        assert get_delivery_mode() == DeliveryMode.STAGING_ONLY

    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
        assert get_delivery_mode() == DeliveryMode.AUTO_PUBLISH

    def test_reads_manual_publish(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        assert get_delivery_mode() == DeliveryMode.MANUAL_PUBLISH

    def test_reads_batch_publish(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        assert get_delivery_mode() == DeliveryMode.BATCH_PUBLISH

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "AUTO_PUBLISH")
        assert get_delivery_mode() == DeliveryMode.AUTO_PUBLISH

    def test_invalid_value_falls_back_to_staging(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "garbage")
        assert get_delivery_mode() == DeliveryMode.STAGING_ONLY


# =============================================================================
# assert_notion_write_allowed — staging_only
# =============================================================================


class TestStagingOnlyMode:
    """staging_only blocks ALL writes."""

    def test_blocks_auto_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        with pytest.raises(DeliveryPolicyError, match="staging_only"):
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)

    def test_blocks_manual_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        with pytest.raises(DeliveryPolicyError, match="staging_only"):
            assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)

    def test_blocks_batch_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        with pytest.raises(DeliveryPolicyError, match="staging_only"):
            assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)


# =============================================================================
# assert_notion_write_allowed — manual_publish
# =============================================================================


class TestManualPublishMode:
    """manual_publish allows single-item manual push only."""

    def test_allows_manual_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        # Should not raise
        assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)

    def test_blocks_auto_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        with pytest.raises(DeliveryPolicyError, match="manual_publish"):
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)

    def test_blocks_batch_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        with pytest.raises(DeliveryPolicyError, match="manual_publish"):
            assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)


# =============================================================================
# assert_notion_write_allowed — batch_publish
# =============================================================================


class TestBatchPublishMode:
    """batch_publish allows batch workflow and manual push."""

    def test_allows_manual_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)

    def test_allows_batch_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)

    def test_blocks_auto_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "batch_publish")
        with pytest.raises(DeliveryPolicyError, match="batch_publish"):
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)


# =============================================================================
# assert_notion_write_allowed — auto_publish
# =============================================================================


class TestAutoPublishMode:
    """auto_publish allows everything."""

    def test_allows_auto_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
        assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)

    def test_allows_manual_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
        assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)

    def test_allows_batch_push(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")
        assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)


# =============================================================================
# Error message quality
# =============================================================================


class TestErrorMessages:
    """Error messages should be clear and actionable."""

    def test_error_includes_current_mode(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        with pytest.raises(DeliveryPolicyError) as exc_info:
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)
        assert "staging_only" in str(exc_info.value)

    def test_error_includes_intent(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        with pytest.raises(DeliveryPolicyError) as exc_info:
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)
        assert "auto_push" in str(exc_info.value)

    def test_error_includes_env_var_hint(self, monkeypatch):
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        with pytest.raises(DeliveryPolicyError) as exc_info:
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)
        assert "DELIVERY_MODE" in str(exc_info.value)


# =============================================================================
# Default env (no DELIVERY_MODE set)
# =============================================================================


class TestDefaultMode:
    """When DELIVERY_MODE is not set, should default to staging_only."""

    def test_default_blocks_auto_push(self, monkeypatch):
        monkeypatch.delenv("DELIVERY_MODE", raising=False)
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)

    def test_default_blocks_manual_push(self, monkeypatch):
        monkeypatch.delenv("DELIVERY_MODE", raising=False)
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)

    def test_default_blocks_batch_push(self, monkeypatch):
        monkeypatch.delenv("DELIVERY_MODE", raising=False)
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)
