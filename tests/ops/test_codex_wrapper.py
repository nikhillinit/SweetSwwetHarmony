from __future__ import annotations

import asyncio

from integrations.codex_wrapper import CodexCLI, DEFAULT_MODEL


class _FakeProcess:
    # asyncio.subprocess.Process always exposes pid; the POSIX owned-process
    # boundary captures it when the process is established. Keep the sentinel
    # outside normal OS PID ranges so an accidental timeout path cannot target
    # a real process while this healthy-path fake is in use.
    pid = 2_147_483_647
    returncode = 0

    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def communicate(self, input=None):
        self._captured["input"] = input
        return (b"codex says hi", b"")


async def test_exec_always_hands_codex_a_closed_stdin(monkeypatch, tmp_path) -> None:
    """codex >= 0.144 reads piped stdin to EOF even when the prompt is an
    argument ("Reading additional input from stdin..."). Under a non-TTY
    parent (CI, Hermes orchestration) an inherited open pipe hangs the
    reviewer lane until timeout and returns empty content, so the wrapper
    must always pass an explicitly closed stdin."""
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    captured: dict = {}

    async def fake_spawn(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProcess(captured)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    codex = CodexCLI()
    codex._codex_path = "codex"

    response = await codex.exec("short prompt")

    assert response.exit_code == 0
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["input"] == b""


async def test_exec_still_pipes_long_prompts_via_stdin(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    captured: dict = {}

    async def fake_spawn(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProcess(captured)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    codex = CodexCLI()
    codex._codex_path = "codex"

    long_prompt = "x" * 9000
    await codex.exec(long_prompt)

    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["input"] == long_prompt.encode("utf-8")


def test_codex_cli_prefers_model_environment_variable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CODEX_MODEL", "env-model")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert CodexCLI().model == "env-model"


def test_codex_cli_reads_model_from_codex_config(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('model = "config-model"\n', encoding="utf-8")

    assert CodexCLI().model == "config-model"


def test_codex_cli_falls_back_to_supported_default(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert DEFAULT_MODEL == "gpt-5.5"
    assert CodexCLI().model == "gpt-5.5"
