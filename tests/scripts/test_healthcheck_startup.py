"""Tests for scripts/healthcheck_startup.py — systemd startup health probe."""

import http.client
from unittest.mock import MagicMock, patch

import pytest

from scripts.healthcheck_startup import check_health, main


# ---------------------------------------------------------------------------
# check_health() unit tests
# ---------------------------------------------------------------------------


class TestCheckHealth:
    """Test the single-attempt health check function."""

    def test_returns_true_on_200(self):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health() is True

        mock_conn.request.assert_called_once_with("GET", "/api/v1/health")
        mock_conn.close.assert_called_once()

    def test_returns_false_on_500(self):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health() is False

    def test_returns_false_on_503(self):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health() is False

    def test_returns_false_on_connection_refused(self):
        with patch(
            "scripts.healthcheck_startup.http.client.HTTPConnection",
            side_effect=ConnectionRefusedError,
        ):
            assert check_health() is False

    def test_returns_false_on_os_error(self):
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("Network unreachable")

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health() is False

    def test_returns_false_on_http_exception(self):
        mock_conn = MagicMock()
        mock_conn.request.side_effect = http.client.HTTPException("bad response")

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health() is False

    def test_uses_correct_port(self, monkeypatch):
        monkeypatch.setattr("scripts.healthcheck_startup.PORT", 9999)

        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn) as mock_cls:
            check_health()
            mock_cls.assert_called_once_with("localhost", 9999, timeout=5)


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------


class TestMain:
    """Test the retry loop and exit codes."""

    def test_immediate_success_returns_0(self):
        with patch("scripts.healthcheck_startup.check_health", return_value=True):
            with patch("scripts.healthcheck_startup.MAX_RETRIES", 5):
                assert main() == 0

    def test_failure_then_success_returns_0(self):
        with patch("scripts.healthcheck_startup.check_health", side_effect=[False, False, True]):
            with patch("scripts.healthcheck_startup.MAX_RETRIES", 5):
                with patch("scripts.healthcheck_startup.RETRY_DELAY", 0):
                    assert main() == 0

    def test_all_failures_returns_1(self):
        with patch("scripts.healthcheck_startup.check_health", return_value=False):
            with patch("scripts.healthcheck_startup.MAX_RETRIES", 3):
                with patch("scripts.healthcheck_startup.RETRY_DELAY", 0):
                    assert main() == 1

    def test_respects_max_retries(self):
        with patch("scripts.healthcheck_startup.check_health", return_value=False) as mock_check:
            with patch("scripts.healthcheck_startup.MAX_RETRIES", 4):
                with patch("scripts.healthcheck_startup.RETRY_DELAY", 0):
                    main()
                    assert mock_check.call_count == 4

    def test_success_on_last_retry_returns_0(self):
        side_effects = [False, False, False, False, True]
        with patch("scripts.healthcheck_startup.check_health", side_effect=side_effects):
            with patch("scripts.healthcheck_startup.MAX_RETRIES", 5):
                with patch("scripts.healthcheck_startup.RETRY_DELAY", 0):
                    assert main() == 0

    def test_does_not_sleep_after_last_failed_attempt(self):
        with patch("scripts.healthcheck_startup.check_health", return_value=False):
            with patch("scripts.healthcheck_startup.MAX_RETRIES", 3):
                with patch("scripts.healthcheck_startup.RETRY_DELAY", 0):
                    with patch("scripts.healthcheck_startup.time.sleep") as mock_sleep:
                        main()
                        # Should sleep between attempts 1-2 and 2-3, but NOT after attempt 3
                        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# Environment variable parsing
# ---------------------------------------------------------------------------


class TestEnvConfig:
    """Test that module-level constants can be overridden via env vars."""

    def test_default_retries(self):
        from scripts.healthcheck_startup import MAX_RETRIES
        # Default is 10 (may differ if env is set in CI, so just check it's an int)
        assert isinstance(MAX_RETRIES, int)
        assert MAX_RETRIES > 0

    def test_default_port(self):
        from scripts.healthcheck_startup import PORT
        assert PORT == 8000

    def test_default_delay(self):
        from scripts.healthcheck_startup import RETRY_DELAY
        assert isinstance(RETRY_DELAY, int)
        assert RETRY_DELAY > 0

    def test_health_path(self):
        from scripts.healthcheck_startup import PATH
        assert PATH == "/api/v1/health"
