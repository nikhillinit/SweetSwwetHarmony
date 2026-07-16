"""Cross-wrapper parity for the owned-process-boundary migration.

Codex, Kimi, and Gemini/Antigravity now route their subprocess lifetime through
``integrations.process_runtime``. This pins the behaviour that the migration
changed or must preserve, per wrapper:

- timeout maps to the historical timeout diagnostic (Kimi and Gemini previously
  used a parent-only ``process.kill()`` -- the ~11h-hang class of bug);
- a provider-not-established outcome maps to the historical exit-127
  missing-binary shape so the Hermes classifier still reads spawn_error;
- a clean completion preserves exit code, captured stdout, and success;
- the wrapper-provided environment is handed through to the boundary;
- no wrapper keeps a module-local spawn/kill seam -- the boundary owns it.

Codex's own outcome mapping is pinned in ``test_codex_wrapper_timeout.py``; the
real-OS whole-tree reap is in ``test_process_runtime.py``.
"""

from __future__ import annotations

import integrations.codex_wrapper as codex_mod
import integrations.gemini_antigravity_client as gemini_mod
import integrations.llm_cli.kimi as kimi_mod
from integrations.gemini_antigravity_client import GeminiAntigravityClient
from integrations.llm_cli import KimiCLIClient
from integrations.process_runtime import ProcessOutcome, ProcessRunResult


def _fake_run_returning(result: ProcessRunResult):
    async def _run(argv, **kwargs):
        return result

    return _run


def _resolve_to(monkeypatch, path: str) -> None:
    monkeypatch.setattr(
        "integrations.process_runtime.shutil.which", lambda binary: path
    )


# --------------------------------------------------------------------------- #
# Kimi
# --------------------------------------------------------------------------- #
async def test_kimi_timeout_maps_to_timeout_shape(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/kimi-cli")
    monkeypatch.setattr(
        kimi_mod,
        "run_process",
        _fake_run_returning(ProcessRunResult(ProcessOutcome.TIMED_OUT, None, b"", b"")),
    )
    response = await KimiCLIClient(binary="kimi-cli", timeout_seconds=5).exec("hi")
    assert response.finish_reason == "timeout"
    assert response.exit_code == -1
    assert response.content == ""
    assert response.success is False
    assert "timed out" in (response.error or "")


async def test_kimi_not_established_maps_to_missing_binary(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/kimi-cli")
    monkeypatch.setattr(
        kimi_mod,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(
                ProcessOutcome.PROVIDER_NOT_ESTABLISHED, None, b"", b"",
                establishment_error="[Errno 13] Permission denied",
            )
        ),
    )
    response = await KimiCLIClient(binary="kimi-cli").exec("hi")
    assert response.finish_reason == "missing_binary"
    assert response.exit_code == 127
    assert response.success is False


async def test_kimi_completed_maps_to_success(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/kimi-cli")
    monkeypatch.setattr(
        kimi_mod,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(ProcessOutcome.COMPLETED, 0, b"kimi ok", b"noise")
        ),
    )
    response = await KimiCLIClient(binary="kimi-cli").exec("hi")
    assert response.exit_code == 0
    assert response.content == "kimi ok"
    assert response.finish_reason == "stop"
    assert response.error is None
    assert response.success is True


async def test_kimi_hands_wrapper_env_to_boundary(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/kimi-cli")
    seen: dict = {}

    async def _capture(argv, **kwargs):
        seen.update(kwargs)
        return ProcessRunResult(ProcessOutcome.COMPLETED, 0, b"", b"")

    monkeypatch.setattr(kimi_mod, "run_process", _capture)
    await KimiCLIClient(binary="kimi-cli", env={"KIMI_PARITY_PROBE": "1"}).exec("hi")
    assert seen["env"]["KIMI_PARITY_PROBE"] == "1"


# --------------------------------------------------------------------------- #
# Gemini / Antigravity
# --------------------------------------------------------------------------- #
async def test_gemini_timeout_maps_to_timeout_shape(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/gemini")
    monkeypatch.setattr(
        gemini_mod,
        "run_process",
        _fake_run_returning(ProcessRunResult(ProcessOutcome.TIMED_OUT, None, b"", b"")),
    )
    response = await GeminiAntigravityClient(binary="gemini", timeout_seconds=5).exec("hi")
    assert response.finish_reason == "timeout"
    assert response.exit_code == -1
    assert response.content == ""
    assert response.success is False


async def test_gemini_not_established_maps_to_missing_binary(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/gemini")
    monkeypatch.setattr(
        gemini_mod,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(ProcessOutcome.PROVIDER_NOT_ESTABLISHED, None, b"", b"")
        ),
    )
    response = await GeminiAntigravityClient(binary="gemini").exec("hi")
    assert response.finish_reason == "missing_binary"
    assert response.exit_code == 127
    assert response.success is False


async def test_gemini_completed_maps_to_success(monkeypatch) -> None:
    _resolve_to(monkeypatch, "/usr/bin/gemini")
    monkeypatch.setattr(
        gemini_mod,
        "run_process",
        _fake_run_returning(
            ProcessRunResult(ProcessOutcome.COMPLETED, 0, b"gemini ok", b"")
        ),
    )
    response = await GeminiAntigravityClient(binary="gemini").exec("hi")
    assert response.exit_code == 0
    assert response.content == "gemini ok"
    assert response.success is True


# --------------------------------------------------------------------------- #
# Structural invariant: the boundary owns spawn + kill for every wrapper
# --------------------------------------------------------------------------- #
def test_every_wrapper_delegates_to_the_owned_boundary() -> None:
    for module in (codex_mod, kimi_mod, gemini_mod):
        # Each wrapper imports the owned run_process...
        assert hasattr(module, "run_process")
        # ...and keeps no module-local spawn/kill seam that could reach an
        # unowned process or an arbitrary PID.
        assert not hasattr(module, "_create_cli_process")
        assert not hasattr(module, "_terminate_process_tree")
