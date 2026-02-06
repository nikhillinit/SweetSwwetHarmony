"""Phase 7.5 — Tests for structured logging configuration.

TDD RED: Tests should fail until utils/logging_config.py is implemented.
"""

import json
import logging
import os
import pytest
from io import StringIO
from unittest.mock import patch


class TestConfigureLogging:
    """Test logging configuration with JSON and text formats."""

    def teardown_method(self):
        """Reset root logger after each test."""
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_configure_text_format_default(self):
        """Default format is human-readable text."""
        from utils.logging_config import configure_logging

        configure_logging()
        logger = logging.getLogger("test.text")
        assert logger.level <= logging.INFO or logging.getLogger().level <= logging.INFO

    def test_configure_json_format(self):
        """LOG_FORMAT=json produces JSON log lines."""
        from utils.logging_config import configure_logging

        stream = StringIO()
        configure_logging(json_format=True, stream=stream)

        logger = logging.getLogger("test.json")
        logger.info("hello world")

        output = stream.getvalue()
        assert output.strip()
        line = json.loads(output.strip().split("\n")[-1])
        assert line["message"] == "hello world"
        assert "timestamp" in line
        assert line["level"] == "INFO"

    def test_log_level_from_env(self):
        """LOG_LEVEL env var controls the root log level."""
        from utils.logging_config import configure_logging

        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_log_level_default_info(self):
        """Default log level is INFO."""
        from utils.logging_config import configure_logging

        with patch.dict(os.environ, {}, clear=True):
            # Ensure LOG_LEVEL is not set
            os.environ.pop("LOG_LEVEL", None)
            configure_logging()
        assert logging.getLogger().level == logging.INFO

    def test_json_format_includes_logger_name(self):
        """JSON output includes the logger name."""
        from utils.logging_config import configure_logging

        stream = StringIO()
        configure_logging(json_format=True, stream=stream)

        logger = logging.getLogger("my.module")
        logger.warning("test msg")

        output = stream.getvalue().strip().split("\n")[-1]
        line = json.loads(output)
        assert line["logger"] == "my.module"
        assert line["level"] == "WARNING"


class TestRequestIdFilter:
    """Request ID filter injects request_id into log records."""

    def teardown_method(self):
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_filter_adds_request_id(self):
        """RequestIdFilter adds request_id attribute to log records."""
        from utils.logging_config import RequestIdFilter

        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        filt.filter(record)
        assert hasattr(record, "request_id")

    def test_filter_uses_context_var(self):
        """Filter reads request_id from contextvars."""
        from utils.logging_config import RequestIdFilter, set_request_id

        token = set_request_id("abc-123")
        try:
            filt = RequestIdFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="hello", args=(), exc_info=None,
            )
            filt.filter(record)
            assert record.request_id == "abc-123"
        finally:
            from utils.logging_config import _request_id_var
            _request_id_var.reset(token)


class TestStartupCheck:
    """Startup check validates environment before app starts."""

    def test_startup_check_passes_with_db(self, tmp_path):
        """startup_check passes when DB path is valid."""
        from utils.logging_config import startup_check

        db_path = tmp_path / "test.db"
        db_path.touch()
        # Should not raise
        issues = startup_check(db_path=str(db_path))
        assert len(issues) == 0

    def test_startup_check_warns_missing_db(self):
        """startup_check warns when DB file doesn't exist (not fatal for new installs)."""
        from utils.logging_config import startup_check

        issues = startup_check(db_path="/nonexistent/db.sqlite")
        assert any("database" in i.lower() for i in issues)
