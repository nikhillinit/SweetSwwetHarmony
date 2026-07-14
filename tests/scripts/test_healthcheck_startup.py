"""Tests for scripts/healthcheck_startup.py — discover-and-poll startup health probe."""

import http.client
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from scripts.healthcheck_startup import check_health, main


HOST = "127.0.0.1"
PORT = 8000
HEALTH_PATH = "/health"


def _argv(*extra: str) -> list[str]:
    """Build a sys.argv for main(); argparse reads sys.argv under pytest."""
    return ["healthcheck_startup.py", *extra]


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
            assert check_health(HOST, PORT, HEALTH_PATH) is True

        mock_conn.request.assert_called_once_with("GET", HEALTH_PATH)
        mock_conn.close.assert_called_once()

    def test_returns_false_on_500(self):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health(HOST, PORT, HEALTH_PATH) is False

    def test_returns_false_on_503(self):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health(HOST, PORT, HEALTH_PATH) is False

    def test_returns_false_on_connection_refused(self):
        with patch(
            "scripts.healthcheck_startup.http.client.HTTPConnection",
            side_effect=ConnectionRefusedError,
        ):
            assert check_health(HOST, PORT, HEALTH_PATH) is False

    def test_returns_false_on_os_error(self):
        mock_conn = MagicMock()
        mock_conn.request.side_effect = OSError("Network unreachable")

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health(HOST, PORT, HEALTH_PATH) is False

    def test_returns_false_on_http_exception(self):
        mock_conn = MagicMock()
        mock_conn.request.side_effect = http.client.HTTPException("bad response")

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn):
            assert check_health(HOST, PORT, HEALTH_PATH) is False

    def test_uses_correct_host_port_and_timeout(self):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp

        with patch("scripts.healthcheck_startup.http.client.HTTPConnection", return_value=mock_conn) as mock_cls:
            check_health("localhost", 9999, HEALTH_PATH, timeout=5)
            mock_cls.assert_called_once_with("localhost", 9999, timeout=5)


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------


class TestMain:
    """Test the retry loop and exit codes (discovery mocked to a single path)."""

    def _run_main(self, *, retries: int, check_health_mock, sleep_mock=None):
        """Run main() with discovery pinned to one path and no real sleeping."""
        if sleep_mock is None:
            sleep_mock = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(
                patch("sys.argv", _argv("--retries", str(retries), "--delay", "0"))
            )
            stack.enter_context(
                patch("scripts.healthcheck_startup.discover", return_value=[HEALTH_PATH])
            )
            stack.enter_context(
                patch("scripts.healthcheck_startup.check_health", check_health_mock)
            )
            stack.enter_context(
                patch("scripts.healthcheck_startup.time.sleep", sleep_mock)
            )
            return main()

    def test_immediate_success_returns_0(self):
        mock_check = MagicMock(return_value=True)
        assert self._run_main(retries=5, check_health_mock=mock_check) == 0

    def test_failure_then_success_returns_0(self):
        mock_check = MagicMock(side_effect=[False, False, True])
        assert self._run_main(retries=5, check_health_mock=mock_check) == 0

    def test_all_failures_returns_1(self):
        mock_check = MagicMock(return_value=False)
        assert self._run_main(retries=3, check_health_mock=mock_check) == 1

    def test_respects_max_retries(self):
        mock_check = MagicMock(return_value=False)
        self._run_main(retries=4, check_health_mock=mock_check)
        assert mock_check.call_count == 4

    def test_success_on_last_retry_returns_0(self):
        mock_check = MagicMock(side_effect=[False, False, False, False, True])
        assert self._run_main(retries=5, check_health_mock=mock_check) == 0

    def test_does_not_sleep_after_last_failed_attempt(self):
        mock_check = MagicMock(return_value=False)
        mock_sleep = MagicMock()
        self._run_main(retries=3, check_health_mock=mock_check, sleep_mock=mock_sleep)
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
        from scripts.healthcheck_startup import PORT as MODULE_PORT
        assert MODULE_PORT == 8000

    def test_default_delay(self):
        from scripts.healthcheck_startup import RETRY_DELAY
        assert isinstance(RETRY_DELAY, int)
        assert RETRY_DELAY > 0

    def test_health_path(self):
        from scripts.healthcheck_startup import DEFAULT_CANDIDATES, PATH
        # PATH defaults to /health and is updated at runtime by discovery;
        # it must always be one of the known candidate endpoints.
        assert PATH in DEFAULT_CANDIDATES
        assert DEFAULT_CANDIDATES[0] == "/health"
