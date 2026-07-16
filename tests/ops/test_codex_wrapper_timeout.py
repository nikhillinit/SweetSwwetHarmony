"""Deadline-enforcement contract for the Codex reviewer lane.

Regression cover for the 2026-07-15 Q10 Track B incident: a codex reviewer
lane spawned under the Hermes panel hung for ~11h with the 300s deadline
never taking effect. Root cause: on Windows the wrapper launched via
``create_subprocess_shell`` (cmd.exe -> codex.CMD -> node -> N grandchildren),
every grandchild inherited the stdout/stderr PIPE handles so ``communicate()``
never reached EOF, and the timeout handler's ``process.kill()`` only killed
cmd.exe -- the tree, and the pipes it held open, lived on.

The whole-tree reap now lives in the shared owned boundary
``integrations.process_runtime`` and its real-OS regression is
``tests/ops/test_process_runtime.py::test_run_process_timeout_reaps_whole_tree``.
This module pins the *codex wrapper's* public contract: it delegates to that
boundary and maps each owned-process outcome to the historical CodexResponse
shape (timeout -> exit -1, establishment failure -> exit 127, completion ->
exit code + captured content).
"""

from __future__ import annotations

import integrations.codex_wrapper as codex_wrapper
from integrations.cli_errors import missing_binary_error
from integrations.codex_wrapper import CodexCLI
from integrations.process_runtime import ProcessOutcome, ProcessRunResult


def _fake_run_returning(result: ProcessRunResult):
    async def _fake_run(argv, **kwargs):
        return result

    return _fake_run


async def test_timeout_outcome_maps_to_deadline_response(monkeypatch, tmp_path) -> None:
    """A TIMED_OUT owned run must surface as the enforced-deadline response:
    exit_code -1 and a 'timed out' diagnostic. Otherwise a hung lane looks like
    an ordinary empty success."""
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(
        codex_wrapper,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(ProcessOutcome.TIMED_OUT, None, b"", b"")
        ),
    )

    codex = CodexCLI(timeout_seconds=7)
    codex._codex_path = "codex"

    response = await codex.exec("short prompt")

    assert response.exit_code == -1
    assert response.error and "timed out" in response.error.lower()
    assert response.execution_time_ms == 7 * 1000


async def test_completed_outcome_maps_to_success(monkeypatch, tmp_path) -> None:
    """A finished lane maps to its exit code and captured content, with no
    spurious error text on a clean exit."""
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(
        codex_wrapper,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(ProcessOutcome.COMPLETED, 0, b"codex says hi", b"noise")
        ),
    )

    codex = CodexCLI(timeout_seconds=30)
    codex._codex_path = "codex"

    response = await codex.exec("short prompt")

    assert response.exit_code == 0
    assert response.content == "codex says hi"
    assert response.error is None  # clean exit suppresses stderr text
    assert response.success is True


async def test_not_established_outcome_maps_to_missing_binary(
    monkeypatch, tmp_path
) -> None:
    """An establishment failure (provider code never ran) keeps the historical
    exit-127 missing-binary shape so the Hermes classifier reads spawn_error."""
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(
        codex_wrapper,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(
                ProcessOutcome.PROVIDER_NOT_ESTABLISHED,
                None,
                b"",
                b"",
                establishment_error="[WinError 2] The system cannot find the file",
            )
        ),
    )

    codex = CodexCLI(timeout_seconds=30)
    codex._codex_path = "codex"

    response = await codex.exec("short prompt")

    assert response.exit_code == 127
    assert missing_binary_error("codex") in (response.error or "")
    assert response.success is False


def test_codex_wrapper_no_longer_owns_a_pid_kill_helper() -> None:
    """Termination is owned by process_runtime; the wrapper must not resurrect a
    module-level pid-kill helper that a caller could aim at the wrong tree."""
    assert not hasattr(codex_wrapper, "_terminate_process_tree")
