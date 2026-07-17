"""Platform Feasibility Gate 0A -- prove the provider-establishment race exists.

Q10 runtime fallback is a bounded contingency for a provider that becomes
*unspawnable* in the TOCTOU window between a green route gate (the resolver
finding the executable) and the real process-creation call. Before investing in
PR 2's wrapper/provenance layer, this gate must show -- deterministically, with
no privileged host mutation and no *production* fault-injection seam -- that:

1. ``process_runtime.resolve_executable`` genuinely finds a disposable shim on a
   scratch ``PATH``;
2. if that shim is atomically invalidated in the window *after* resolution but
   *before* the real ``create_subprocess`` call, ``run_process`` maps the failure
   to typed :data:`ProcessOutcome.PROVIDER_NOT_ESTABLISHED`;
3. no provider code runs -- the shim would write a mutation sentinel if it ran,
   and it does not -- and any transient POSIX ``exec`` bootstrap is reaped
   (the event loop stays healthy afterward).

The synchronization barrier is a TEST-ONLY monkeypatch that wraps the module's
own ``_spawn_owned`` seam: it pauses at spawn entry (post-resolution), lets a
sibling thread perform the atomic invalidation inside that exact window, then
calls through to the *real* spawn. The establishment failure is therefore a
genuine OS ``exec`` failure on a now-missing path, never a synthetic injection.
No ledger, routing, or Hermes policy is exercised -- this gate operates purely at
the owned-boundary layer, below any canonical artifact.

Windows ``.cmd`` crux (the load-bearing go/no-go for PR 2): a ``.cmd``/``.bat``
resolves and launches via ``create_subprocess_shell(cmd.exe ...)``. A *missing*
``.cmd`` is a ``cmd.exe`` non-zero EXIT (outcome ``COMPLETED``), NOT a spawn
``OSError`` -- indistinguishable from a provider that ran and exited non-zero. So
the Windows ``.cmd`` launch form (codex.CMD, gemini.CMD) CANNOT structurally
attest not-established and must be declared INELIGIBLE for spawn-only fallback.
Only direct-exec binaries (``kimi-cli.exe``, ``agy.exe`` on Windows; everything
on POSIX) can attest.

POSIX-only tests are ``skipif`` on Windows and validated by the Ubuntu CI job,
mirroring the PR 1 pattern in ``test_process_runtime.py``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable

import pytest

import integrations.process_runtime as process_runtime
from integrations.process_runtime import (
    ProcessOutcome,
    resolve_executable,
    run_process,
    should_use_shell,
)


# --------------------------------------------------------------------------- #
# Test-only synchronization barrier: pause after resolution, invalidate inside
# the window, then call through to the REAL spawn.
# --------------------------------------------------------------------------- #
class _EstablishmentRace:
    """Deterministically collapse the resolver/spawn race onto its critical
    interleaving.

    Wraps ``process_runtime._spawn_owned`` (the seam entered *after* the caller
    has resolved ``argv[0]`` and ``run_process`` has decided the launch form, but
    *before* the real ``create_subprocess`` call). On spawn entry the wrapper
    blocks until a sibling thread runs ``invalidate`` -- the atomic invalidation
    lands squarely in the TOCTOU window -- then delegates to the real spawn so
    the establishment failure is a genuine OS ``exec`` failure.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, invalidate: Callable[[], None]) -> None:
        self.spawn_calls = 0
        self._invalidate = invalidate
        self._entered = threading.Event()
        self._released = threading.Event()
        self._error: "BaseException | None" = None
        real_spawn = process_runtime._spawn_owned

        async def _barrier_spawn(*args, **kwargs):
            self.spawn_calls += 1
            self._entered.set()  # post-resolution, at the spawn boundary
            if not self._released.wait(timeout=10):
                raise RuntimeError("establishment-race barrier: invalidation never released")
            return await real_spawn(*args, **kwargs)  # REAL establishment attempt

        def _invalidator() -> None:
            try:
                if not self._entered.wait(timeout=10):
                    raise RuntimeError("establishment-race barrier: spawn never reached")
                self._invalidate()  # atomic invalidation, inside the window
            except BaseException as exc:  # surfaced via join()
                self._error = exc
            finally:
                self._released.set()

        self._thread = threading.Thread(target=_invalidator, daemon=True)
        self._thread.start()
        monkeypatch.setattr(process_runtime, "_spawn_owned", _barrier_spawn)

    def join(self) -> None:
        self._thread.join(timeout=10)
        if self._error is not None:
            raise self._error


# --------------------------------------------------------------------------- #
# Shim + scratch-PATH helpers
# --------------------------------------------------------------------------- #
def _scratch_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Prepend ``directory`` to the process PATH the resolver reads."""
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))


def _posix_shim(directory: Path, sentinel: Path) -> Path:
    """A runnable POSIX shim that writes a mutation sentinel when it executes."""
    shim = directory / "providershim"
    shim.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n", encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _windows_exe_shim(directory: Path) -> "tuple[Path, dict[str, str]]":
    """A runnable direct-exec Windows shim (disposable copy of the base
    interpreter) plus the child env that lets it find its DLLs. Passing ``-c``
    lets it write a mutation sentinel, so a run is observable."""
    base = Path(sys.base_prefix) / "python.exe"
    shim = directory / "providershim.exe"
    shutil.copy2(base, shim)
    env = dict(os.environ)
    env["PATH"] = str(base.parent) + os.pathsep + env.get("PATH", "")
    return shim, env


def _windows_cmd_shim(directory: Path) -> "tuple[Path, Path]":
    """A runnable ``.cmd`` shim that writes a mutation sentinel when it runs."""
    cmd_sentinel = directory / "cmd_provider_ran.sentinel"
    shim = directory / "providercmd.cmd"
    shim.write_text(
        "@echo off\r\n" f'echo ran > "{cmd_sentinel}"\r\n',
        encoding="ascii",
    )
    return shim, cmd_sentinel


# =========================================================================== #
# POSIX (validated by the Ubuntu CI job)
# =========================================================================== #
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec establishment semantics")
async def test_posix_shim_control_runs_and_writes_sentinel(tmp_path, monkeypatch) -> None:
    """Anti-vacuity control: the shim is genuinely resolvable AND runnable, so a
    later 'sentinel absent' assertion actually means provider code did not run."""
    sentinel = tmp_path / "provider_ran.sentinel"
    shim = _posix_shim(tmp_path, sentinel)
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providershim")
    assert resolved == str(shim)

    result = await run_process([resolved], timeout_seconds=30)
    assert result.outcome is ProcessOutcome.COMPLETED
    assert sentinel.exists(), "control shim did not run -- race tests would be vacuous"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec establishment semantics")
async def test_posix_establishment_race_maps_to_not_established(tmp_path, monkeypatch) -> None:
    """Vanished-after-resolution (ENOENT): the resolver found the shim, it was
    atomically renamed away inside the spawn window, and ``run_process`` returns
    typed PROVIDER_NOT_ESTABLISHED with no provider/sentinel run."""
    sentinel = tmp_path / "provider_ran.sentinel"
    shim = _posix_shim(tmp_path, sentinel)
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providershim")
    assert resolved == str(shim)  # (1) resolver finds it

    gone = tmp_path / "providershim.gone"
    race = _EstablishmentRace(monkeypatch, invalidate=lambda: os.replace(shim, gone))

    result = await run_process([resolved], timeout_seconds=30)
    race.join()

    assert race.spawn_calls == 1  # (2) spawn boundary entered once, post-resolution
    # (3) the typed outcome can only arise from the REAL spawn raising OSError:
    assert result.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED
    assert result.exit_code is None
    assert result.establishment_error
    assert not sentinel.exists()  # (4) provider/mutation code never ran
    assert not shim.exists() and gone.exists()  # invalidation happened in-window

    # (5) no transient bootstrap wedged the loop: a healthy run still completes.
    live = await run_process([sys.executable, "-c", "print('ok')"], timeout_seconds=30)
    assert live.outcome is ProcessOutcome.COMPLETED


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork/exec bootstrap semantics")
async def test_posix_permission_race_reaps_failed_exec_bootstrap(tmp_path, monkeypatch) -> None:
    """Exec-permission revoked inside the window (EACCES): on POSIX this forks a
    transient bootstrap whose ``exec`` then fails. It must map to
    PROVIDER_NOT_ESTABLISHED, run no provider code, and leave nothing that wedges
    the loop -- the bootstrap is reaped before the OSError is re-raised."""
    sentinel = tmp_path / "provider_ran.sentinel"
    shim = _posix_shim(tmp_path, sentinel)
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providershim")
    assert resolved == str(shim)

    # mode 000 -> no exec bits: even a CI root user gets EACCES on exec.
    race = _EstablishmentRace(monkeypatch, invalidate=lambda: os.chmod(shim, 0o000))

    result = await run_process([resolved], timeout_seconds=30)
    race.join()

    assert race.spawn_calls == 1
    assert result.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED
    assert result.establishment_error
    assert not sentinel.exists()

    live = await run_process([sys.executable, "-c", "print('ok')"], timeout_seconds=30)
    assert live.outcome is ProcessOutcome.COMPLETED


# =========================================================================== #
# Windows direct-exec (.exe) -- CAN attest
# =========================================================================== #
@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess exec semantics")
async def test_windows_direct_exec_control_runs_and_writes_sentinel(tmp_path, monkeypatch) -> None:
    """Anti-vacuity control for the direct-exec race: the disposable .exe shim is
    resolvable, launched via exec (not the shell), and genuinely runs."""
    shim, env = _windows_exe_shim(tmp_path)
    sentinel = tmp_path / "provider_ran.sentinel"
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providershim")
    assert resolved and resolved.lower() == str(shim).lower()
    assert should_use_shell(resolved) is False

    result = await run_process(
        [resolved, "-c", f"open(r'{sentinel}','w').write('ran')"],
        env=env,
        timeout_seconds=30,
    )
    assert result.outcome is ProcessOutcome.COMPLETED
    assert sentinel.exists(), "control .exe did not run -- race test would be vacuous"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CreateProcess exec semantics")
async def test_windows_direct_exec_establishment_race_maps_to_not_established(
    tmp_path, monkeypatch
) -> None:
    """Direct-exec CAN attest: a resolved .exe that vanishes inside the spawn
    window fails CreateProcess (WinError 2) -> typed PROVIDER_NOT_ESTABLISHED,
    no provider/sentinel run."""
    shim, env = _windows_exe_shim(tmp_path)
    sentinel = tmp_path / "provider_ran.sentinel"
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providershim")
    assert resolved and should_use_shell(resolved) is False  # (1) resolver + direct-exec form

    gone = tmp_path / "providershim.gone"
    race = _EstablishmentRace(monkeypatch, invalidate=lambda: os.replace(shim, gone))

    result = await run_process(
        [resolved, "-c", f"open(r'{sentinel}','w').write('ran')"],
        env=env,
        timeout_seconds=30,
    )
    race.join()

    assert race.spawn_calls == 1  # (2) spawn boundary entered once, post-resolution
    # (3) the typed outcome can only arise from the REAL spawn raising OSError:
    assert result.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED
    assert result.exit_code is None
    assert result.establishment_error
    assert not sentinel.exists()  # (4) provider/mutation code never ran
    assert not shim.exists() and gone.exists()


# =========================================================================== #
# Windows .cmd crux -- CANNOT attest (the go/no-go for PR 2 eligibility)
# =========================================================================== #
@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe shell-launch semantics")
async def test_windows_cmd_control_runs_and_writes_sentinel(tmp_path, monkeypatch) -> None:
    """Anti-vacuity control: the .cmd shim resolves, is launched via the shell,
    and genuinely runs + mutates."""
    shim, cmd_sentinel = _windows_cmd_shim(tmp_path)
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providercmd")
    assert resolved and should_use_shell(resolved) is True

    result = await run_process([resolved], timeout_seconds=30)
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.exit_code == 0
    assert cmd_sentinel.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe shell-launch semantics")
async def test_windows_cmd_missing_is_completed_not_established(tmp_path, monkeypatch) -> None:
    """CRUX: a resolved .cmd that vanishes inside the spawn window launches
    cmd.exe, which cannot find the batch file and exits NON-ZERO -- outcome
    COMPLETED, not PROVIDER_NOT_ESTABLISHED. That is indistinguishable from a
    provider that ran and exited non-zero, so a .cmd launch form CANNOT
    structurally attest not-established. Windows codex.CMD / gemini.CMD lanes are
    therefore INELIGIBLE for spawn-only fallback; PR 2 must gate eligibility on
    the launch form, never on the cmd.exe exit code or output."""
    shim, cmd_sentinel = _windows_cmd_shim(tmp_path)
    _scratch_path(monkeypatch, tmp_path)

    resolved = resolve_executable("providercmd")
    assert resolved and should_use_shell(resolved) is True  # resolves as a shell launch form

    gone = tmp_path / "providercmd.gone"
    race = _EstablishmentRace(monkeypatch, invalidate=lambda: os.replace(shim, gone))

    result = await run_process([resolved], timeout_seconds=30)
    race.join()

    assert race.spawn_calls == 1
    # The decisive crux: cmd.exe ran and failed -> COMPLETED non-zero, NOT typed
    # not-established. The boundary cannot tell this apart from a real provider
    # failure, so it must never be treated as an attestation.
    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.outcome is not ProcessOutcome.PROVIDER_NOT_ESTABLISHED
    assert result.exit_code not in (None, 0)  # "not recognized" -> non-zero errorlevel
    assert not cmd_sentinel.exists()
