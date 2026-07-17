"""Owned cross-platform process boundary for Hermes provider wrappers.

``integrations.process_runtime`` is the single place that establishes a
provider subprocess AND owns its termination. The owning function captures the
process group / tree at spawn time, so a caller can never hand an arbitrary PID
to a group-kill helper (that is how you accidentally kill your own group).

These tests pin the safety contract that PR 1 exists to guarantee:

1. Whole-tree termination on timeout (Windows ``taskkill /T /F`` / POSIX
   ``killpg`` on the OWNED session group) -- the 2026-07-15 Q10 Track B
   incident (an 11h reviewer-lane hang) was a descendant surviving a
   parent-only ``process.kill()``.
2. Only the owned group dies; an unrelated sibling in our own group survives.
3. A resolver/spawn establishment failure returns a typed
   ``PROVIDER_NOT_ESTABLISHED`` result and provider code never runs.
4. Healthy completion is untouched (no tree kill) and stdout/stderr/exit code
   are captured; cwd/env are forwarded.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

import integrations.process_runtime as process_runtime
from integrations.execution_provenance import LaunchForm
from integrations.process_runtime import (
    ProcessOutcome,
    ProcessRunResult,
    resolve_executable,
    run_process,
    should_use_shell,
)


# --------------------------------------------------------------------------- #
# Helpers (shared with the incident regression that lived in the codex test)
# --------------------------------------------------------------------------- #
def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


# --------------------------------------------------------------------------- #
# resolver + shell decision (pure)
# --------------------------------------------------------------------------- #
def test_resolve_executable_returns_none_for_missing_binary() -> None:
    assert resolve_executable("definitely-not-a-real-binary-xyz-123") is None


def test_resolve_executable_delegates_to_which(monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.process_runtime.shutil.which",
        lambda binary: f"/resolved/{binary}",
    )
    assert resolve_executable("codex") == "/resolved/codex"


def test_should_use_shell_only_for_windows_cmd_and_bat(monkeypatch) -> None:
    monkeypatch.setattr("integrations.process_runtime.sys.platform", "win32")
    assert should_use_shell(r"C:\x\codex.CMD") is True
    assert should_use_shell(r"C:\x\codex.cmd") is True
    assert should_use_shell(r"C:\x\thing.bat") is True
    assert should_use_shell(r"C:\x\kimi-cli.EXE") is False
    assert should_use_shell(r"C:\x\codex") is False  # no extension -> exec


def test_should_use_shell_never_shells_on_posix(monkeypatch) -> None:
    monkeypatch.setattr("integrations.process_runtime.sys.platform", "linux")
    assert should_use_shell("/usr/bin/codex.cmd") is False
    assert should_use_shell("/usr/bin/codex") is False


# --------------------------------------------------------------------------- #
# healthy completion (parity: capture + no tree kill)
# --------------------------------------------------------------------------- #
async def test_run_process_completed_captures_stdout_stderr() -> None:
    result = await run_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('hello-out'); sys.stderr.write('hello-err')",
        ],
        timeout_seconds=30,
    )
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.launch_form is LaunchForm.DIRECT_EXEC
    assert result.exit_code == 0
    assert b"hello-out" in result.stdout
    assert b"hello-err" in result.stderr


async def test_run_process_completed_preserves_nonzero_exit() -> None:
    result = await run_process(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        timeout_seconds=30,
    )
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.exit_code == 3


async def test_run_process_forwards_cwd_and_env(tmp_path) -> None:
    env = dict(os.environ)
    env["HERMES_RUNTIME_PROBE"] = "probe-value"
    result = await run_process(
        [
            sys.executable,
            "-c",
            "import os; print(os.getcwd()); print(os.environ.get('HERMES_RUNTIME_PROBE'))",
        ],
        env=env,
        cwd=tmp_path,
        timeout_seconds=30,
    )
    assert result.outcome is ProcessOutcome.COMPLETED
    text = result.stdout.decode()
    assert str(tmp_path) in text
    assert "probe-value" in text


async def test_run_process_creates_missing_cwd(tmp_path) -> None:
    target = tmp_path / "nested" / "workdir"
    assert not target.exists()
    result = await run_process(
        [sys.executable, "-c", "print('ok')"],
        cwd=target,
        timeout_seconds=30,
    )
    assert result.outcome is ProcessOutcome.COMPLETED
    assert target.exists()


# --------------------------------------------------------------------------- #
# provider-not-established (typed; provider code never runs)
# --------------------------------------------------------------------------- #
async def test_run_process_missing_binary_is_not_established() -> None:
    result = await run_process(
        ["this-binary-does-not-exist-xyz-123", "--go"],
        timeout_seconds=10,
    )
    assert result.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED
    assert result.launch_form is LaunchForm.DIRECT_EXEC
    assert result.exit_code is None
    assert result.establishment_error
    assert result.not_established is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec/permission semantics")
async def test_run_process_posix_permission_denied_never_runs_provider(tmp_path) -> None:
    """A non-executable script fails exec (EACCES): the wrapper maps this to
    PROVIDER_NOT_ESTABLISHED and the provider's mutation sentinel is never
    written -- proof that provider code did not run before the failure."""
    sentinel = tmp_path / "provider_ran.sentinel"
    script = tmp_path / "provider.sh"
    script.write_text(
        f"#!/bin/sh\ntouch '{sentinel}'\n",
        encoding="utf-8",
    )
    script.chmod(0o644)  # readable but NOT executable -> exec raises EACCES

    result = await run_process([str(script)], timeout_seconds=10)

    assert result.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED
    assert not sentinel.exists(), "provider code ran despite establishment failure"


# --------------------------------------------------------------------------- #
# timeout -> whole-tree reap (the incident regression, relocated here)
# --------------------------------------------------------------------------- #
def _tree_spawn_script(tmp_path) -> tuple:
    pid_file = tmp_path / "grandchild.pid"
    spawn_script = tmp_path / "spawn_tree.py"
    spawn_script.write_text(
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    return spawn_script, pid_file


async def test_run_process_timeout_reaps_whole_tree(tmp_path) -> None:
    """On deadline expiry the OWNED process tree -- parent AND grandchild --
    must be terminated. A surviving grandchild that inherited the stdout/stderr
    pipe handles is exactly what hung a reviewer lane for ~11h (Q10, 2026-07-15).
    """
    spawn_script, pid_file = _tree_spawn_script(tmp_path)

    # Outer guard so a regression can never hang the suite itself.
    result = await asyncio.wait_for(
        run_process(
            [sys.executable, str(spawn_script), str(pid_file)],
            timeout_seconds=3,
        ),
        timeout=40,
    )

    assert result.outcome is ProcessOutcome.TIMED_OUT
    assert result.launch_form is LaunchForm.DIRECT_EXEC
    assert _wait_until(
        lambda: pid_file.exists() and pid_file.read_text().strip().isdigit()
    ), "grandchild pid never reported"
    grandchild_pid = int(pid_file.read_text().strip())
    assert _wait_until(
        lambda: not _pid_alive(grandchild_pid)
    ), "grandchild survived the owned-tree timeout kill"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
async def test_run_process_timeout_does_not_kill_unrelated_group(tmp_path) -> None:
    """The owned kill targets only the spawned session group. A sibling process
    living in OUR OWN group must survive -- proving we never killpg our own
    group."""
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )  # inherits the test runner's process group (no new session)
    try:
        spawn_script, pid_file = _tree_spawn_script(tmp_path)
        result = await asyncio.wait_for(
            run_process(
                [sys.executable, str(spawn_script), str(pid_file)],
                timeout_seconds=3,
            ),
            timeout=40,
        )
        assert result.outcome is ProcessOutcome.TIMED_OUT
        # The innocent sibling in our own group must still be alive.
        assert _pid_alive(innocent.pid), "owned kill hit an unrelated group"
    finally:
        if innocent.poll() is None:
            innocent.kill()
        innocent.wait(timeout=5)


# --------------------------------------------------------------------------- #
# owned-handle design guard: no arbitrary-PID group-kill on the public surface
# --------------------------------------------------------------------------- #
def test_module_exposes_no_arbitrary_pid_tree_kill() -> None:
    """The owned handle -- not a caller-supplied PID -- decides what gets
    killed. There must be no public ``terminate_process_tree(pid)`` style API
    that a caller could point at an arbitrary (or wrong) process group."""
    public = {name for name in dir(process_runtime) if not name.startswith("_")}
    assert "run_process" in public
    assert "resolve_executable" in public
    assert "should_use_shell" in public
    assert "ProcessOutcome" in public
    assert "ProcessRunResult" in public
    # No public kill-by-pid helper.
    assert not any("terminate" in name for name in public)
    assert not any(name.endswith("_process_tree") for name in public)


def test_process_run_result_is_immutable() -> None:
    result = ProcessRunResult(
        outcome=ProcessOutcome.COMPLETED,
        exit_code=0,
        stdout=b"",
        stderr=b"",
    )
    with pytest.raises((AttributeError, TypeError)):
        result.exit_code = 5  # type: ignore[misc]
