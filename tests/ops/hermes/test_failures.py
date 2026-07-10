from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.config import load_config
from integrations.hermes.failures import (
    FAILURE_NONZERO_EXIT,
    FAILURE_RATE_LIMITED,
    FAILURE_SPAWN_ERROR,
    FAILURE_TIMEOUT,
    classify_execution,
    compile_rate_limit_signatures,
    parse_retry_after,
)

from .conftest import minimal_config_dict

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _result(
    *,
    success: bool = False,
    exit_code: int = 1,
    content: str = "",
    error: str | None = None,
) -> ExecutorResult:
    return ExecutorResult(
        executor="kimi",
        success=success,
        exit_code=exit_code,
        content=content,
        duration_ms=1,
        error=error,
    )


def test_classify_returns_none_for_success() -> None:
    result = _result(success=True, exit_code=0)
    assert classify_execution(result, signatures=["429"]) is None


def test_classify_matches_rate_limit_signature_in_error() -> None:
    result = _result(error="HTTP 429 Too Many Requests")
    assert classify_execution(result, signatures=["429"]) == FAILURE_RATE_LIMITED


def test_classify_matches_rate_limit_signature_in_content_case_insensitive() -> None:
    result = _result(content="Rate Limit Exceeded, slow down")
    assert (
        classify_execution(result, signatures=["rate limit"]) == FAILURE_RATE_LIMITED
    )


def test_classify_rate_limit_takes_precedence_over_timeout_text() -> None:
    result = _result(error="request timed out after 429 rate limit hit")
    assert classify_execution(result, signatures=["429"]) == FAILURE_RATE_LIMITED


def test_classify_detects_timeout() -> None:
    result = _result(error="process timed out after 600s")
    assert classify_execution(result, signatures=[]) == FAILURE_TIMEOUT


def test_classify_detects_spawn_error() -> None:
    result = _result(error="[WinError 2] The system cannot find the file specified")
    assert classify_execution(result, signatures=[]) == FAILURE_SPAWN_ERROR


def test_classify_detects_spawn_error_posix_wording() -> None:
    result = _result(error="[Errno 2] No such file or directory: 'kimi-cli'")
    assert classify_execution(result, signatures=[]) == FAILURE_SPAWN_ERROR


def test_classify_defaults_to_nonzero_exit() -> None:
    result = _result(exit_code=42, error="executor boom")
    assert classify_execution(result, signatures=[]) == FAILURE_NONZERO_EXIT


def test_parse_retry_after_relative_seconds() -> None:
    until = parse_retry_after("Please retry after 30 seconds.", now=NOW)
    assert until == NOW + timedelta(seconds=30)


def test_parse_retry_after_relative_minutes() -> None:
    until = parse_retry_after("try again in 2 minutes", now=NOW)
    assert until == NOW + timedelta(minutes=2)


def test_parse_retry_after_bare_number_defaults_to_seconds() -> None:
    until = parse_retry_after("retry after: 90", now=NOW)
    assert until == NOW + timedelta(seconds=90)


def test_parse_retry_after_absolute_future_timestamp() -> None:
    until = parse_retry_after("quota resets at 2026-07-10T13:30:00+00:00", now=NOW)
    assert until == datetime(2026, 7, 10, 13, 30, 0, tzinfo=timezone.utc)


def test_parse_retry_after_absolute_past_timestamp_is_ignored() -> None:
    assert parse_retry_after("resets at 2026-07-09T00:00:00+00:00", now=NOW) is None


def test_parse_retry_after_without_hint_returns_none() -> None:
    assert parse_retry_after("something exploded", now=NOW) is None


def test_compile_signatures_empty_when_disabled() -> None:
    config = _config_with_rate_limits(enabled=False)
    assert compile_rate_limit_signatures(config) == {}


def test_compile_signatures_keyed_by_executor_when_enabled() -> None:
    config = _config_with_rate_limits(enabled=True)
    compiled = compile_rate_limit_signatures(config)
    assert set(compiled) == {"kimi"}
    assert any(pattern.search("http 429") for pattern in compiled["kimi"])


def test_config_defaults_leave_rate_limits_disabled(tmp_path: Path) -> None:
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(minimal_config_dict()), encoding="utf-8")
    config = load_config(path)
    assert config.rate_limits.enabled is False
    assert config.rate_limits.default_cooldown_minutes == 60
    assert config.routing.runtime_fallback_enabled is False


def test_config_parses_rate_limits_and_fallback_flag(tmp_path: Path) -> None:
    data = minimal_config_dict()
    data["rateLimits"] = {
        "enabled": True,
        "defaultCooldownMinutes": 15,
        "signatures": {"kimi": ["429", "rate limit"]},
    }
    data["routing"]["runtimeFallbackEnabled"] = True
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    config = load_config(path)
    assert config.rate_limits.enabled is True
    assert config.rate_limits.default_cooldown_minutes == 15
    assert config.rate_limits.signatures == {"kimi": ["429", "rate limit"]}
    assert config.routing.runtime_fallback_enabled is True


def _config_with_rate_limits(*, enabled: bool):
    data = minimal_config_dict()
    data["rateLimits"] = {
        "enabled": enabled,
        "signatures": {"kimi": ["429"]},
    }
    from integrations.hermes.config import RoutingConfig

    return RoutingConfig.model_validate(data)
