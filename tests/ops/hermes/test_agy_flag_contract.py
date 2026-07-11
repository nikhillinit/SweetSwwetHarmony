"""Wrapper <-> binary flag contract for the Hermes Google lane (agy).

The installed agy binary rejects unknown flags with "flags provided but not
defined" (observed live with -approval-mode, 2026-07-10), which killed the
Google lane. These tests pin two things:

1. Unit level: the antigravity flavor of GeminiAntigravityClient builds its
   invocation from agy's real flag surface (no gemini-cli flags).
2. Contract level: every long flag the wrapper passes is defined in the
   installed binary's ``agy --help`` output. Skips cleanly when agy is not
   installed (e.g. CI).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from integrations.gemini_antigravity_client import GeminiAntigravityClient

_AGY_WELL_KNOWN = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.EXE"

# Flags gemini-cli accepts but the installed agy binary rejects.
_GEMINI_ONLY_FLAGS = ("--approval-mode", "--output-format", "--skip-trust", "--prompt")


def _agy_binary() -> str | None:
    found = shutil.which("agy")
    if found:
        return found
    if _AGY_WELL_KNOWN.exists():
        return str(_AGY_WELL_KNOWN)
    return None


async def _capture_antigravity_args(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run the antigravity client's exec path and capture the argv it builds."""
    captured: dict[str, list[str]] = {}

    class _FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            return b"agy done", b""

        def kill(self):  # pragma: no cover - not reached
            pass

    async def _fake_create(args, **kwargs):
        captured["args"] = list(args)
        return _FakeProcess()

    monkeypatch.setattr(
        "integrations.gemini_antigravity_client.shutil.which",
        lambda name: r"C:\Users\test\agy\bin\agy.EXE",
    )
    monkeypatch.setattr(
        "integrations.gemini_antigravity_client._create_cli_process",
        _fake_create,
    )
    client = GeminiAntigravityClient(
        binary="agy", model="antigravity", flavor="antigravity"
    )
    response = await client.exec("review this plan")
    assert response.success is True
    return captured["args"]


def test_antigravity_flavor_drops_gemini_only_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = asyncio.run(_capture_antigravity_args(monkeypatch))

    for flag in _GEMINI_ONLY_FLAGS:
        assert flag not in args, f"{flag} is not defined by the agy binary"
    assert "--print" in args


def test_gemini_flavor_keeps_gemini_cli_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    class _FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            return b"gemini done", b""

        def kill(self):  # pragma: no cover - not reached
            pass

    async def _fake_create(args, **kwargs):
        captured["args"] = list(args)
        return _FakeProcess()

    monkeypatch.setattr(
        "integrations.gemini_antigravity_client.shutil.which",
        lambda name: "/usr/bin/gemini",
    )
    monkeypatch.setattr(
        "integrations.gemini_antigravity_client._create_cli_process",
        _fake_create,
    )
    client = GeminiAntigravityClient(binary="gemini")

    response = asyncio.run(client.exec("review this"))

    assert response.success is True
    assert "--approval-mode" in captured["args"]
    assert "--skip-trust" in captured["args"]


def test_unknown_flavor_is_rejected() -> None:
    with pytest.raises(ValueError, match="flavor"):
        GeminiAntigravityClient(binary="agy", flavor="mystery")


def test_agy_binary_accepts_every_wrapper_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = _agy_binary()
    if binary is None:
        pytest.skip(
            "agy binary not installed on this machine; "
            "wrapper<->binary flag contract needs the real CLI"
        )

    completed = subprocess.run(
        [binary, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    help_text = (completed.stdout or "") + (completed.stderr or "")
    defined = set(re.findall(r"^\s+(--[a-z][a-z-]*)", help_text, re.MULTILINE))
    assert defined, f"could not parse any flags from agy --help:\n{help_text}"

    wrapper_args = asyncio.run(_capture_antigravity_args(monkeypatch))
    wrapper_flags = [
        token.split("=", 1)[0]
        for token in wrapper_args[1:]
        if token.startswith("--")
    ]
    assert wrapper_flags, "antigravity wrapper passed no long flags"
    undefined = [flag for flag in wrapper_flags if flag not in defined]
    assert not undefined, (
        f"wrapper passes flags not defined by the installed agy binary: "
        f"{undefined}; defined flags: {sorted(defined)}"
    )
