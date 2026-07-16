from __future__ import annotations

import asyncio
from pathlib import Path

from integrations.hermes.config import PROJECT_ROOT, load_config
from integrations.hermes.providers import doctor
from integrations.llm_cli import KimiCLIClient


class FakeProcess:
    returncode = 0

    def __init__(self, captured):
        self._captured = captured

    async def communicate(self, input=None):
        self._captured["input"] = input
        return b"kimi done", b""

    def kill(self):
        self._captured["killed"] = True


def test_kimi_client_missing_binary_returns_structured_failure() -> None:
    client = KimiCLIClient(binary="definitely-missing-kimi-binary")

    response = asyncio.run(client.exec("hello"))

    assert response.success is False
    assert response.exit_code == 127
    assert response.finish_reason == "missing_binary"
    assert "not found" in (response.error or "")


async def test_kimi_cli_uses_print_mode_on_windows_cmd_shim(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = {}
    # The shell-vs-exec decision now lives in the owned process boundary.
    monkeypatch.setattr("integrations.process_runtime.sys.platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda binary: r"C:\Users\nikhi\AppData\Roaming\npm\kimi.CMD",
    )

    async def fake_exec(*args, **kwargs):  # pragma: no cover - assertion guard
        raise AssertionError("Windows .CMD shims must be launched through shell")

    async def fake_shell(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess(captured)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    context_path = tmp_path / "context.txt"
    context_path.write_text("context body", encoding="utf-8")
    client = KimiCLIClient(binary="kimi-cli")

    response = await client.exec("review this", context_files=[str(context_path)])

    assert response.success is True
    assert response.content == "kimi done"
    assert "--print" in captured["command"]
    assert "--input-format text" in captured["command"]
    assert "--output-format text" in captured["command"]
    assert "--final-message-only" in captured["command"]
    assert b"review this" in captured["input"]
    assert b"context body" in captured["input"]
    assert captured["kwargs"]["cwd"] != str(PROJECT_ROOT)


async def test_kimi_cli_uses_exec_for_windows_exe(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr("integrations.process_runtime.sys.platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda binary: r"C:\Users\nikhi\.local\bin\kimi-cli.exe",
    )

    async def fake_shell(*args, **kwargs):  # pragma: no cover - assertion guard
        raise AssertionError("Windows .exe binaries should not use shell")

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess(captured)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client = KimiCLIClient(binary="kimi-cli")

    response = await client.exec("review this")

    assert response.success is True
    assert captured["args"][0].endswith("kimi-cli.exe")
    assert "--print" in captured["args"]


def test_project_config_enables_kimi_cli_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    config = load_config(PROJECT_ROOT / ".claude" / "hermes" / "model-routing.json")

    kimi = config.executors["kimi"]
    assert kimi.binary == "kimi-cli"
    assert kimi.env == []
    assert kimi.supports_execute is True

    report = doctor(config)
    assert "env:KIMI_API_KEY" not in report.providers["kimi"].checks_by_name
    assert report.providers["kimi"].checks_by_name["binary"].ok is True
    assert report.providers["kimi"].success is True
