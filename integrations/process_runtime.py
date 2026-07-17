"""Owned, cross-platform process boundary for Hermes provider wrappers.

This module is the single place that *establishes* a provider subprocess and
*owns its termination*. The owning function captures the tree/session group at
spawn time and exposes termination only through the returned handle, so a
caller can never pass an arbitrary PID to a group-kill helper -- doing so is how
you accidentally kill your own process group.

Why this exists (Q10 Track B, 2026-07-15): a Codex reviewer lane hung for ~11h
because the timeout handler called ``process.kill()``, which terminates only the
immediate child (``cmd.exe``). Its descendants (``node`` grandchildren) survived
and kept the inherited stdout/stderr pipe handles open, so ``communicate()``
never reached EOF and the 300s deadline was silently defeated. The Kimi and
Gemini/Antigravity wrappers carried the same parent-only-kill bug. The fix is a
single owned boundary that reaps the WHOLE tree on both Windows and POSIX.

Termination model:

- **Windows:** own the launched process and terminate its descendant tree with
  ``taskkill /T /F`` -- the reliable way to close pipe handles held by
  grandchildren.
- **POSIX:** launch in a dedicated session (``start_new_session=True``) so the
  child is its own process-group leader, then ``killpg`` ONLY that owned group.
  Because we created the session, the group we kill can never be our own.

Establishment failures (missing binary, ``FileNotFoundError`` /
``PermissionError`` / ``ENOEXEC`` at spawn) prove provider code never started;
they map to a typed :data:`ProcessOutcome.PROVIDER_NOT_ESTABLISHED` result. Any
failure that occurs *after* the process is established surfaces as an exit code
(``COMPLETED``) or a :data:`ProcessOutcome.TIMED_OUT`, never as
``PROVIDER_NOT_ESTABLISHED``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

# Bound on how long to wait for a killed tree to be reaped after the deadline
# fires. Tearing down the tree closes the inherited pipes, so ``communicate()``
# should return almost immediately; this only guards against a stuck reap.
_REAP_TIMEOUT_SECONDS = 10


class ProcessOutcome(str, Enum):
    """Terminal outcome of an owned process run.

    ``PROVIDER_NOT_ESTABLISHED`` is reserved for failures where the provider
    executable/session was never established and no provider code ran. It is a
    derived safety property, not a text match.
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    PROVIDER_NOT_ESTABLISHED = "provider_not_established"


@dataclass(frozen=True)
class ProcessRunResult:
    """Immutable result of :func:`run_process`."""

    outcome: ProcessOutcome
    exit_code: Optional[int]
    stdout: bytes
    stderr: bytes
    establishment_error: Optional[str] = None

    @property
    def completed(self) -> bool:
        return self.outcome is ProcessOutcome.COMPLETED

    @property
    def timed_out(self) -> bool:
        return self.outcome is ProcessOutcome.TIMED_OUT

    @property
    def not_established(self) -> bool:
        return self.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED


def resolve_executable(binary: str) -> Optional[str]:
    """Resolve ``binary`` on PATH, or ``None`` when it cannot be found.

    A ``None`` result is a provider-not-established condition detectable before
    any subprocess is created.
    """
    return shutil.which(binary)


def should_use_shell(executable: str) -> bool:
    """Whether ``executable`` must be launched through the shell.

    Only Windows ``.cmd`` / ``.bat`` shims (e.g. npm-installed ``codex.CMD``,
    ``gemini.CMD``) require the shell -- ``create_subprocess_exec`` raises
    ``FileNotFoundError`` on them. Real executables (``.exe`` on Windows, any
    binary on POSIX) launch directly so their tree can be owned cleanly.
    """
    if sys.platform != "win32":
        return False
    return Path(executable).suffix.lower() in {".cmd", ".bat"}


class _OwnedProcess:
    """A spawned process plus the identity needed to reap its whole tree.

    Only :func:`_spawn_owned` constructs this. The termination target (Windows
    tree by PID, POSIX owned process group) is captured at spawn time; callers
    cannot substitute an arbitrary PID.
    """

    __slots__ = ("_process", "_pgid")

    def __init__(self, process: asyncio.subprocess.Process, pgid: Optional[int]) -> None:
        self._process = process
        self._pgid = pgid

    @property
    def process(self) -> asyncio.subprocess.Process:
        return self._process

    def terminate_tree(self) -> None:
        """Forcefully terminate the owned process and every descendant.

        Best-effort and non-raising: the caller is already returning a timeout
        result and cleanup must never mask it.
        """
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self._process.pid)],
                    capture_output=True,
                    check=False,
                )
            elif self._pgid is not None:
                # We created this session, so this group is exclusively ours.
                try:
                    os.killpg(self._pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:  # pragma: no cover - POSIX spawn always records a pgid
                try:
                    os.kill(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception:  # pragma: no cover - reaping is strictly best-effort
            pass


async def _spawn_owned(
    argv: Sequence[str],
    *,
    env: Optional[dict[str, str]],
    cwd: Optional[str],
    use_shell: bool,
) -> _OwnedProcess:
    """Establish an owned child process.

    Raises ``OSError`` (``FileNotFoundError`` / ``PermissionError`` / ``ENOEXEC``
    subtypes) when the process cannot be created -- i.e. provider code never
    ran. On POSIX the stdlib subprocess layer reaps the transient failed-``exec``
    bootstrap before re-raising, so no zombie is left behind.
    """
    common = dict(
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    if sys.platform != "win32":
        # Own the group: setsid() makes the child a session/group leader whose
        # pgid == pid, so killpg targets only this group.
        process = await asyncio.create_subprocess_exec(
            *argv, start_new_session=True, **common
        )
        return _OwnedProcess(process, pgid=process.pid)

    if use_shell:
        process = await asyncio.create_subprocess_shell(
            subprocess.list2cmdline(list(argv)), **common
        )
    else:
        process = await asyncio.create_subprocess_exec(*argv, **common)
    return _OwnedProcess(process, pgid=None)


async def run_process(
    argv: Sequence[str],
    *,
    stdin_data: Optional[bytes] = None,
    env: Optional[dict[str, str]] = None,
    cwd: "str | os.PathLike[str] | None" = None,
    timeout_seconds: float,
) -> ProcessRunResult:
    """Run ``argv`` as an owned subprocess and return a typed result.

    - Establishment failure (spawn ``OSError``) -> ``PROVIDER_NOT_ESTABLISHED``.
    - Deadline expiry -> ``TIMED_OUT`` after the whole owned tree is reaped.
    - Otherwise -> ``COMPLETED`` with captured stdout/stderr and exit code.

    ``argv[0]`` must be a resolved executable path (callers use
    :func:`resolve_executable`). ``stdin`` is always an explicitly closed pipe:
    some CLIs (codex >= 0.144) read piped stdin to EOF even when the prompt is
    an argument, so an inherited open pipe under a non-TTY parent hangs the run.
    """
    argv = list(argv)
    cwd_str: Optional[str] = None
    if cwd is not None:
        cwd_path = Path(cwd)
        cwd_path.mkdir(parents=True, exist_ok=True)
        cwd_str = str(cwd_path)

    use_shell = should_use_shell(argv[0])

    try:
        owned = await _spawn_owned(argv, env=env, cwd=cwd_str, use_shell=use_shell)
    except OSError as exc:
        # FileNotFoundError / PermissionError / ENOEXEC: the process was never
        # created, so provider code did not run. Typed, terminal, safe.
        return ProcessRunResult(
            outcome=ProcessOutcome.PROVIDER_NOT_ESTABLISHED,
            exit_code=None,
            stdout=b"",
            stderr=b"",
            establishment_error=str(exc),
        )

    # Enforce the deadline WITHOUT cancelling communicate(): on the Windows
    # Proactor loop, cancelling communicate() can itself block on the same pipe
    # drain that is stuck (grandchildren hold the inherited pipe handles open).
    # Wait on the task, and on expiry kill the WHOLE owned tree first -- that
    # closes the inherited pipes so communicate() can return -- then reap it.
    comm_task = asyncio.ensure_future(
        owned.process.communicate(input=stdin_data or b"")
    )
    done, _pending = await asyncio.wait({comm_task}, timeout=timeout_seconds)
    if comm_task not in done:
        owned.terminate_tree()
        try:
            # Bounded reap: the tree kill should have closed the pipes, so this
            # returns fast. If the reap itself stalls, cancel and move on with a
            # TIMED_OUT result rather than blocking the caller further.
            await asyncio.wait_for(comm_task, timeout=_REAP_TIMEOUT_SECONDS)
        except Exception:
            comm_task.cancel()
        return ProcessRunResult(
            outcome=ProcessOutcome.TIMED_OUT,
            exit_code=None,
            stdout=b"",
            stderr=b"",
        )

    stdout, stderr = comm_task.result()
    returncode = owned.process.returncode
    return ProcessRunResult(
        outcome=ProcessOutcome.COMPLETED,
        exit_code=returncode if returncode is not None else 0,
        stdout=stdout,
        stderr=stderr,
    )
