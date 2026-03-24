"""Tests for utils/slack_notifier.py

Covers:
- SlackConfig.from_env() reads environment variables correctly
- is_configured returns False when webhook URL is not set
- All notify methods POST to the webhook URL with correct payload shape
- Payload formatting for notify_high_confidence_signal
- Terminal failure (4xx/5xx) returns False without raising
- Retry / error handling on network exceptions
- No secrets (webhook URL) appear in log output
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.slack_notifier import (
    SlackConfig,
    SlackNotifier,
    get_notifier,
    notify_health,
    notify_high_confidence,
    notify_summary,
)


# =============================================================================
# FIXTURES
# =============================================================================

FAKE_WEBHOOK = "https://hooks.slack.com/services/T00/B00/XXXX"


@pytest.fixture
def config():
    """SlackConfig with a fake webhook URL."""
    return SlackConfig(webhook_url=FAKE_WEBHOOK)


@pytest.fixture
def unconfigured_config():
    """SlackConfig with no webhook URL."""
    return SlackConfig(webhook_url=None)


@pytest.fixture
def notifier(config):
    """SlackNotifier with a fake webhook."""
    return SlackNotifier(config=config)


@pytest.fixture
def unconfigured_notifier(unconfigured_config):
    """SlackNotifier without a webhook."""
    return SlackNotifier(config=unconfigured_config)


@pytest.fixture
def mock_http_post():
    """Patch httpx.AsyncClient.post to return a 200 response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "ok"

    with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        yield mock_post


# =============================================================================
# SlackConfig.from_env
# =============================================================================


class TestSlackConfigFromEnv:
    """Test SlackConfig.from_env() reads environment variables."""

    def test_reads_webhook_url(self, monkeypatch):
        """from_env should read SLACK_WEBHOOK_URL."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", FAKE_WEBHOOK)
        cfg = SlackConfig.from_env()
        assert cfg.webhook_url == FAKE_WEBHOOK

    def test_reads_channel(self, monkeypatch):
        """from_env should read SLACK_CHANNEL."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", FAKE_WEBHOOK)
        monkeypatch.setenv("SLACK_CHANNEL", "#discovery")
        cfg = SlackConfig.from_env()
        assert cfg.channel == "#discovery"

    def test_webhook_url_none_when_missing(self, monkeypatch):
        """from_env should return None webhook_url when env var is absent."""
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        cfg = SlackConfig.from_env()
        assert cfg.webhook_url is None

    def test_reads_notify_flags(self, monkeypatch):
        """from_env should parse boolean notification flags."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", FAKE_WEBHOOK)
        monkeypatch.setenv("SLACK_NOTIFY_HIGH_CONFIDENCE", "false")
        monkeypatch.setenv("SLACK_NOTIFY_HEALTH_ALERTS", "false")
        monkeypatch.setenv("SLACK_NOTIFY_DAILY_SUMMARY", "false")
        cfg = SlackConfig.from_env()
        assert cfg.notify_high_confidence is False
        assert cfg.notify_health_alerts is False
        assert cfg.notify_daily_summary is False

    def test_reads_threshold(self, monkeypatch):
        """from_env should parse SLACK_HIGH_CONFIDENCE_THRESHOLD as float."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", FAKE_WEBHOOK)
        monkeypatch.setenv("SLACK_HIGH_CONFIDENCE_THRESHOLD", "0.85")
        cfg = SlackConfig.from_env()
        assert cfg.high_confidence_threshold == pytest.approx(0.85)

    def test_defaults_when_no_env_vars(self, monkeypatch):
        """from_env should use defaults when optional env vars are absent."""
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_CHANNEL", raising=False)
        monkeypatch.delenv("SLACK_NOTIFY_HIGH_CONFIDENCE", raising=False)
        monkeypatch.delenv("SLACK_NOTIFY_HEALTH_ALERTS", raising=False)
        monkeypatch.delenv("SLACK_NOTIFY_DAILY_SUMMARY", raising=False)
        monkeypatch.delenv("SLACK_HIGH_CONFIDENCE_THRESHOLD", raising=False)
        cfg = SlackConfig.from_env()
        assert cfg.notify_high_confidence is True
        assert cfg.notify_health_alerts is True
        assert cfg.notify_daily_summary is True
        assert cfg.high_confidence_threshold == pytest.approx(0.7)


# =============================================================================
# is_configured
# =============================================================================


class TestIsConfigured:
    """Test SlackNotifier.is_configured property."""

    def test_configured_when_webhook_url_set(self, notifier):
        """is_configured should return True when webhook URL is present."""
        assert notifier.is_configured is True

    def test_not_configured_when_webhook_url_missing(self, unconfigured_notifier):
        """is_configured should return False when webhook URL is absent."""
        assert unconfigured_notifier.is_configured is False

    def test_not_configured_when_empty_string(self):
        """is_configured should return False when webhook URL is empty string."""
        cfg = SlackConfig(webhook_url="")
        n = SlackNotifier(config=cfg)
        assert n.is_configured is False


# =============================================================================
# _send - core HTTP POST
# =============================================================================


class TestCoreSend:
    """Test the _send method sends POST to webhook URL."""

    async def test_sends_post_to_webhook_url(self, notifier, mock_http_post):
        """_send should POST to the configured webhook URL."""
        result = await notifier._send({"text": "hello"})
        assert result is True
        mock_http_post.assert_called_once()
        call_args = mock_http_post.call_args
        assert call_args[0][0] == FAKE_WEBHOOK

    async def test_skips_when_not_configured(self, unconfigured_notifier):
        """_send should return False without calling HTTP when not configured."""
        result = await unconfigured_notifier._send({"text": "hello"})
        assert result is False

    async def test_adds_channel_to_payload(self, mock_http_post):
        """_send should inject channel into payload when configured."""
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK, channel="#alerts")
        n = SlackNotifier(config=cfg)
        await n._send({"text": "hello"})
        call_kwargs = mock_http_post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["channel"] == "#alerts"

    async def test_adds_username_defaults(self, notifier, mock_http_post):
        """_send should add default username and icon_emoji to payload."""
        await notifier._send({"text": "hello"})
        payload = mock_http_post.call_args[1]["json"]
        assert payload["username"] == "Discovery Engine"
        assert payload["icon_emoji"] == ":mag:"


# =============================================================================
# Terminal failure (4xx/5xx)
# =============================================================================


class TestTerminalFailure:
    """Test that 4xx/5xx HTTP responses return False without raising."""

    async def test_400_returns_false(self, notifier):
        """400 response should return False, not raise."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_payload"
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await notifier._send({"text": "test"})
        assert result is False

    async def test_500_returns_false(self, notifier):
        """500 response should return False, not raise."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal_error"
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await notifier._send({"text": "test"})
        assert result is False

    async def test_403_returns_false(self, notifier):
        """403 response should return False, not raise."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "forbidden"
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await notifier._send({"text": "test"})
        assert result is False


# =============================================================================
# Network error handling (retry/exception)
# =============================================================================


class TestErrorHandling:
    """Test that network exceptions are caught and return False."""

    async def test_connection_error_returns_false(self, notifier):
        """ConnectionError should be caught and return False."""
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectionError("Connection refused")
            result = await notifier._send({"text": "test"})
        assert result is False

    async def test_timeout_returns_false(self, notifier):
        """Timeout should be caught and return False."""
        import httpx as httpx_module
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx_module.TimeoutException("Timed out")
            result = await notifier._send({"text": "test"})
        assert result is False

    async def test_generic_exception_returns_false(self, notifier):
        """Any exception should be caught and return False."""
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = RuntimeError("Unexpected error")
            result = await notifier._send({"text": "test"})
        assert result is False


# =============================================================================
# No secrets in logs
# =============================================================================


class TestNoSecretsInLogs:
    """Verify that the webhook URL does not leak into log output."""

    async def test_webhook_url_not_logged_on_success(self, notifier, mock_http_post, caplog):
        """Successful send should not log the webhook URL."""
        with caplog.at_level(logging.DEBUG, logger="utils.slack_notifier"):
            await notifier._send({"text": "hello"})
        for record in caplog.records:
            assert FAKE_WEBHOOK not in record.getMessage()

    async def test_webhook_url_not_logged_on_failure(self, notifier, caplog):
        """Failed send should not log the webhook URL."""
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectionError("Network error")
            with caplog.at_level(logging.DEBUG, logger="utils.slack_notifier"):
                await notifier._send({"text": "hello"})
        for record in caplog.records:
            assert FAKE_WEBHOOK not in record.getMessage()

    async def test_webhook_url_not_logged_on_bad_status(self, notifier, caplog):
        """Non-200 status code log should not contain the webhook URL."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "bad_request"
        with patch("utils.slack_notifier.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with caplog.at_level(logging.DEBUG, logger="utils.slack_notifier"):
                await notifier._send({"text": "hello"})
        for record in caplog.records:
            assert FAKE_WEBHOOK not in record.getMessage()


# =============================================================================
# notify_high_confidence_signal - payload formatting
# =============================================================================


class TestNotifyHighConfidenceSignal:
    """Test payload shape and content for high-confidence signal notifications."""

    async def test_posts_correct_payload_shape(self, notifier, mock_http_post):
        """Payload should contain blocks list and fallback text."""
        result = await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github", "sec_edgar"],
        )
        assert result is True
        payload = mock_http_post.call_args[1]["json"]
        assert "blocks" in payload
        assert "text" in payload
        assert isinstance(payload["blocks"], list)

    async def test_payload_includes_company_name(self, notifier, mock_http_post):
        """Payload blocks should include the company name."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github"],
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "Acme Corp" in blocks_str

    async def test_payload_includes_confidence(self, notifier, mock_http_post):
        """Payload blocks should include the confidence as a percentage."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github"],
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "85%" in blocks_str

    async def test_payload_includes_signal_types(self, notifier, mock_http_post):
        """Payload blocks should include the signal types."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github", "sec_edgar"],
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "github" in blocks_str
        assert "sec_edgar" in blocks_str

    async def test_payload_fallback_text(self, notifier, mock_http_post):
        """Fallback text should contain company name and confidence."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github"],
        )
        payload = mock_http_post.call_args[1]["json"]
        assert "Acme Corp" in payload["text"]
        assert "85%" in payload["text"]

    async def test_includes_why_now_block(self, notifier, mock_http_post):
        """Payload should include why_now section when provided."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github"],
            why_now="Funding round detected",
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "Funding round detected" in blocks_str

    async def test_includes_notion_url_button(self, notifier, mock_http_post):
        """Payload should include a Notion link button when URL is provided."""
        notion_url = "https://notion.so/abc123"
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.85,
            signal_types=["github"],
            notion_url=notion_url,
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert notion_url in blocks_str
        assert "View in Notion" in blocks_str

    async def test_skips_below_threshold(self, notifier):
        """Should return False without sending when confidence is below threshold."""
        result = await notifier.notify_high_confidence_signal(
            company_name="Low Score Co",
            confidence=0.5,
            signal_types=["github"],
        )
        assert result is False

    async def test_skips_when_notify_disabled(self, mock_http_post):
        """Should return False when notify_high_confidence is disabled."""
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK, notify_high_confidence=False)
        n = SlackNotifier(config=cfg)
        result = await n.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.9,
            signal_types=["github"],
        )
        assert result is False
        mock_http_post.assert_not_called()

    async def test_star2_emoji_for_very_high_confidence(self, notifier, mock_http_post):
        """Confidence >= 0.85 should use star2 emoji in header."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.90,
            signal_types=["github"],
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":star2:" in header_block["text"]["text"]

    async def test_star_emoji_for_high_confidence(self, notifier, mock_http_post):
        """Confidence >= 0.7 but < 0.85 should use star emoji in header."""
        await notifier.notify_high_confidence_signal(
            company_name="Acme Corp",
            confidence=0.75,
            signal_types=["github"],
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":star:" in header_block["text"]["text"]
        assert ":star2:" not in header_block["text"]["text"]


# =============================================================================
# notify_health_alert
# =============================================================================


class TestNotifyHealthAlert:
    """Test health alert notification."""

    async def test_posts_for_critical_status(self, notifier, mock_http_post):
        """Should send notification for CRITICAL status."""
        result = await notifier.notify_health_alert(
            status="CRITICAL",
            anomalies=["Volume spike"],
        )
        assert result is True
        mock_http_post.assert_called_once()

    async def test_posts_for_degraded_status(self, notifier, mock_http_post):
        """Should send notification for DEGRADED status."""
        result = await notifier.notify_health_alert(
            status="DEGRADED",
            anomalies=["Stale signals"],
        )
        assert result is True

    async def test_skips_healthy_status(self, notifier, mock_http_post):
        """Should return False for HEALTHY status without sending."""
        result = await notifier.notify_health_alert(
            status="HEALTHY",
            anomalies=[],
        )
        assert result is False
        mock_http_post.assert_not_called()

    async def test_skips_when_notify_disabled(self, mock_http_post):
        """Should return False when notify_health_alerts is disabled."""
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK, notify_health_alerts=False)
        n = SlackNotifier(config=cfg)
        result = await n.notify_health_alert(
            status="CRITICAL",
            anomalies=["Volume spike"],
        )
        assert result is False
        mock_http_post.assert_not_called()

    async def test_payload_contains_anomaly_list(self, notifier, mock_http_post):
        """Payload should include the anomaly descriptions."""
        await notifier.notify_health_alert(
            status="DEGRADED",
            anomalies=["Volume spike from github", "3 stale signals"],
            total_signals=150,
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "Volume spike from github" in blocks_str
        assert "3 stale signals" in blocks_str

    async def test_truncates_long_anomaly_list(self, notifier, mock_http_post):
        """Should truncate anomaly list at 5 items with overflow note."""
        anomalies = [f"anomaly_{i}" for i in range(8)]
        await notifier.notify_health_alert(
            status="CRITICAL",
            anomalies=anomalies,
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "anomaly_0" in blocks_str
        assert "anomaly_4" in blocks_str
        assert "and 3 more" in blocks_str

    async def test_critical_uses_rotating_light_emoji(self, notifier, mock_http_post):
        """CRITICAL status should use rotating_light emoji."""
        await notifier.notify_health_alert(
            status="CRITICAL",
            anomalies=["test"],
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":rotating_light:" in header_block["text"]["text"]

    async def test_degraded_uses_warning_emoji(self, notifier, mock_http_post):
        """DEGRADED status should use warning emoji."""
        await notifier.notify_health_alert(
            status="DEGRADED",
            anomalies=["test"],
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":warning:" in header_block["text"]["text"]


# =============================================================================
# notify_daily_summary
# =============================================================================


class TestNotifyDailySummary:
    """Test daily summary notification."""

    async def test_posts_summary(self, notifier, mock_http_post):
        """Should send daily summary successfully."""
        result = await notifier.notify_daily_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=7,
            collectors_failed=1,
        )
        assert result is True
        mock_http_post.assert_called_once()

    async def test_payload_contains_stats(self, notifier, mock_http_post):
        """Payload should contain signal and collector stats."""
        await notifier.notify_daily_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=7,
            collectors_failed=1,
        )
        payload = mock_http_post.call_args[1]["json"]
        blocks_str = str(payload["blocks"])
        assert "42" in blocks_str
        assert "8" in blocks_str

    async def test_skips_when_notify_disabled(self, mock_http_post):
        """Should return False when notify_daily_summary is disabled."""
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK, notify_daily_summary=False)
        n = SlackNotifier(config=cfg)
        result = await n.notify_daily_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=7,
            collectors_failed=1,
        )
        assert result is False
        mock_http_post.assert_not_called()

    async def test_healthy_no_failures_uses_checkmark(self, notifier, mock_http_post):
        """HEALTHY with 0 failures should use checkmark emoji."""
        await notifier.notify_daily_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=7,
            collectors_failed=0,
            health_status="HEALTHY",
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":white_check_mark:" in header_block["text"]["text"]

    async def test_critical_uses_x_emoji(self, notifier, mock_http_post):
        """CRITICAL health should use x emoji."""
        await notifier.notify_daily_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=5,
            collectors_failed=3,
            health_status="CRITICAL",
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":x:" in header_block["text"]["text"]

    async def test_many_failures_uses_x_emoji(self, notifier, mock_http_post):
        """More than 2 collector failures should use x emoji."""
        await notifier.notify_daily_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=5,
            collectors_failed=3,
            health_status="DEGRADED",
        )
        payload = mock_http_post.call_args[1]["json"]
        header_block = payload["blocks"][0]
        assert ":x:" in header_block["text"]["text"]


# =============================================================================
# notify_text
# =============================================================================


class TestNotifyText:
    """Test simple text notification."""

    async def test_sends_text(self, notifier, mock_http_post):
        """Should send plain text message."""
        result = await notifier.notify_text("Pipeline started")
        assert result is True

    async def test_payload_contains_emoji_and_message(self, notifier, mock_http_post):
        """Payload text should contain emoji prefix and message."""
        await notifier.notify_text("Pipeline started", emoji=":rocket:")
        payload = mock_http_post.call_args[1]["json"]
        assert ":rocket: Pipeline started" == payload["text"]


# =============================================================================
# Async context / close
# =============================================================================


class TestClientLifecycle:
    """Test HTTP client creation and cleanup."""

    async def test_close_without_client(self, notifier):
        """Closing before any request should not raise."""
        await notifier.close()  # no-op, should not raise

    async def test_close_after_request(self, notifier, mock_http_post):
        """Closing after a request should work cleanly."""
        await notifier._send({"text": "hello"})
        await notifier.close()
        assert notifier._client is None


# =============================================================================
# Convenience functions
# =============================================================================


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    async def test_get_notifier_returns_instance(self, monkeypatch):
        """get_notifier should return a SlackNotifier instance."""
        # Reset global state
        import utils.slack_notifier as mod
        monkeypatch.setattr(mod, "_notifier", None)
        notifier = get_notifier()
        assert isinstance(notifier, SlackNotifier)

    async def test_get_notifier_returns_same_instance(self, monkeypatch):
        """get_notifier should return the same instance on repeated calls."""
        import utils.slack_notifier as mod
        monkeypatch.setattr(mod, "_notifier", None)
        n1 = get_notifier()
        n2 = get_notifier()
        assert n1 is n2

    async def test_notify_high_confidence_delegates(self, mock_http_post, monkeypatch):
        """notify_high_confidence should delegate to SlackNotifier."""
        import utils.slack_notifier as mod
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK)
        monkeypatch.setattr(mod, "_notifier", SlackNotifier(config=cfg))
        result = await notify_high_confidence(
            company_name="Test Co",
            confidence=0.9,
            signal_types=["github"],
        )
        assert result is True

    async def test_notify_health_delegates(self, mock_http_post, monkeypatch):
        """notify_health should delegate to SlackNotifier."""
        import utils.slack_notifier as mod
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK)
        monkeypatch.setattr(mod, "_notifier", SlackNotifier(config=cfg))
        result = await notify_health(
            status="CRITICAL",
            anomalies=["Volume spike"],
        )
        assert result is True

    async def test_notify_summary_delegates(self, mock_http_post, monkeypatch):
        """notify_summary should delegate to SlackNotifier."""
        import utils.slack_notifier as mod
        cfg = SlackConfig(webhook_url=FAKE_WEBHOOK)
        monkeypatch.setattr(mod, "_notifier", SlackNotifier(config=cfg))
        result = await notify_summary(
            signals_collected=42,
            signals_pushed=8,
            high_confidence_count=3,
            collectors_succeeded=7,
            collectors_failed=0,
        )
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
