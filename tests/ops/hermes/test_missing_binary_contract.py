"""Wrapper -> classifier contract: missing-binary failures classify as spawn_error.

Each production wrapper (Kimi, Gemini/Antigravity, Codex) formats its
missing-binary error through the shared ``integrations.cli_errors`` template,
and ``integrations.hermes.failures`` derives its spawn hint from the same
module. These tests exercise the wrappers' real error-formatting paths and
pin the contract end to end, including the hard prohibition that a bare
inner exit code 127 (post-spawn, possibly post-mutation) stays terminal.
"""

from __future__ import annotations

import asyncio

import pytest

from integrations.cli_errors import MISSING_BINARY_HINT, missing_binary_error
from integrations.codex_wrapper import CodexCLI
from integrations.gemini_antigravity_client import GeminiAntigravityClient
from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.failures import (
    FAILURE_NONZERO_EXIT,
    FAILURE_SPAWN_ERROR,
    FAILURE_TIMEOUT,
    classify_execution,
)
from integrations.llm_cli import KimiCLIClient

MISSING = "hermes-contract-missing-binary-xyz"


def _as_executor_result(
    executor: str,
    *,
    exit_code: int,
    error: str | None,
    content: str = "",
) -> ExecutorResult:
    return ExecutorResult(
        executor=executor,
        success=False,
        exit_code=exit_code,
        content=content,
        duration_ms=1,
        error=error,
    )


def test_kimi_missing_binary_classifies_as_spawn_error() -> None:
    response = asyncio.run(KimiCLIClient(binary=MISSING).exec("hello"))

    assert response.success is False
    assert response.error == missing_binary_error(MISSING)
    result = _as_executor_result(
        "kimi",
        exit_code=response.exit_code,
        error=response.error,
        content=response.content,
    )
    assert classify_execution(result, signatures=()) == FAILURE_SPAWN_ERROR


def test_antigravity_missing_binary_classifies_as_spawn_error() -> None:
    client = GeminiAntigravityClient(binary=MISSING, model="antigravity")
    response = asyncio.run(client.exec("hello"))

    assert response.success is False
    assert response.error == missing_binary_error(MISSING)
    result = _as_executor_result(
        "antigravity",
        exit_code=response.exit_code,
        error=response.error,
        content=response.content,
    )
    assert classify_execution(result, signatures=()) == FAILURE_SPAWN_ERROR


def test_codex_missing_binary_runtime_error_classifies_as_spawn_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.codex_wrapper.shutil.which", lambda name: None
    )
    codex = CodexCLI()

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(codex.exec("hello"))

    # Mirror run.py's ladder: executor exceptions become
    # ExecutorResult(error=str(exc)) before classification.
    message = str(excinfo.value)
    assert missing_binary_error("codex") in message
    result = _as_executor_result("codex", exit_code=1, error=message)
    assert classify_execution(result, signatures=()) == FAILURE_SPAWN_ERROR


def test_codex_spawn_file_not_found_classifies_as_spawn_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.codex_wrapper.shutil.which",
        lambda name: "/fake/path/codex",
    )

    async def _raise_fnf(*args, **kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _raise_fnf)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_fnf)
    codex = CodexCLI()

    response = asyncio.run(codex.exec("hello"))

    assert response.exit_code == 127
    assert missing_binary_error("codex") in (response.error or "")
    result = _as_executor_result(
        "codex", exit_code=response.exit_code, error=response.error
    )
    assert classify_execution(result, signatures=()) == FAILURE_SPAWN_ERROR


def test_spawn_hint_is_derived_from_shared_template() -> None:
    assert MISSING_BINARY_HINT in missing_binary_error("anything")
    result = _as_executor_result(
        "kimi", exit_code=127, error=missing_binary_error("anything")
    )
    assert classify_execution(result, signatures=()) == FAILURE_SPAWN_ERROR


def test_inner_exit_127_without_missing_binary_wording_stays_terminal() -> None:
    # HARD PROHIBITION: exit_code == 127 alone must never classify as
    # spawn_error -- a post-spawn inner 127 may follow partial mutation.
    result = _as_executor_result(
        "kimi", exit_code=127, error="inner tool exited with code 127"
    )
    assert classify_execution(result, signatures=()) == FAILURE_NONZERO_EXIT


def test_wrapper_timeout_wording_stays_timeout_not_spawn_error() -> None:
    result = _as_executor_result(
        "kimi", exit_code=-1, error="'kimi-cli' timed out after 300s: "
    )
    assert classify_execution(result, signatures=()) == FAILURE_TIMEOUT
