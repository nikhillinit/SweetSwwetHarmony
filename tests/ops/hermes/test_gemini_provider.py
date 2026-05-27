from __future__ import annotations

import asyncio

import pytest

from integrations.gemini_antigravity_client import (
    GeminiAntigravityClient,
    GeminiResponse,
)
from integrations.hermes.adapters import (
    GeminiHermesExecutor,
    build_executor,
    build_prompt_packet,
    build_reviewer_executor,
)
from integrations.hermes.config import PROJECT_ROOT, RoutingConfig, load_config
from integrations.hermes.providers import doctor

from .conftest import minimal_config_dict


class FakeGeminiClient:
    async def exec(self, prompt, context_files=None):
        return GeminiResponse(
            content='{"verdict":"approve","confidence":1}',
            execution_time_ms=5,
        )


class FakeProcess:
    returncode = 0

    def __init__(self, captured):
        self._captured = captured

    async def communicate(self, input=None):
        self._captured["input"] = input
        return b"gemini done", b""

    def kill(self):
        self._captured["killed"] = True


def _config_with_active_gemini_reviewer() -> RoutingConfig:
    data = minimal_config_dict()
    data["deferredExecutors"].pop("gemini")
    data["executors"]["gemini"] = {
        "provider": "gemini",
        "displayName": "Gemini CLI",
        "enabled": True,
        "required": False,
        "binary": "gemini",
        "env": ["GEMINI_API_KEY"],
        "supportsExecute": False,
    }
    return RoutingConfig.model_validate(data)


async def test_gemini_executor_is_reviewer_compatible() -> None:
    executor = GeminiHermesExecutor(FakeGeminiClient())

    result = await executor.execute("review this", context_files=["plan.json"])

    assert result.executor == "gemini"
    assert result.success is True
    assert result.exit_code == 0
    assert "approve" in result.content


def test_reviewer_executor_allows_active_non_executable_gemini() -> None:
    config = _config_with_active_gemini_reviewer()

    executor = build_reviewer_executor(
        "gemini",
        config,
        gemini_client=FakeGeminiClient(),
    )

    assert isinstance(executor, GeminiHermesExecutor)


def test_reviewer_executor_still_rejects_deferred_gemini() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    with pytest.raises(ValueError, match="deferred executor"):
        build_reviewer_executor("gemini", config, gemini_client=FakeGeminiClient())


def test_execute_executor_rejects_non_executable_gemini() -> None:
    config = _config_with_active_gemini_reviewer()

    with pytest.raises(ValueError, match="does not support execute"):
        build_executor("gemini", config, gemini_client=FakeGeminiClient())


def test_provider_doctor_knows_optional_gemini_wrapper() -> None:
    config = _config_with_active_gemini_reviewer()

    report = doctor(config)

    assert report.providers["gemini"].checks_by_name["wrapper_import"].ok is True
    assert report.providers["gemini"].success is True


def test_prompt_packet_keeps_reviewer_non_mutating_contract() -> None:
    packet = build_prompt_packet(
        title="Review plan",
        body='{"task":"contract-check"}',
        required_json_keys=["verdict", "concerns"],
    )

    assert "Do not mutate files, databases, config, Notion, or external systems." in packet
    assert "Return strict JSON only." in packet
    assert '"verdict": "..."' in packet
    assert '{"task":"contract-check"}' in packet


def test_gemini_client_missing_binary_returns_structured_failure() -> None:
    client = GeminiAntigravityClient(binary="definitely-missing-gemini-binary")

    response = asyncio.run(client.exec("hello"))

    assert response.success is False
    assert response.exit_code == 127
    assert "not found" in (response.error or "")


async def test_gemini_cli_uses_headless_plan_mode_on_windows_cmd_shim(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        "shutil.which",
        lambda binary: r"C:\Users\nikhi\AppData\Roaming\npm\gemini.CMD",
    )

    async def fake_exec(*args, **kwargs):  # pragma: no cover - assertion guard
        raise AssertionError("Windows .CMD shims must be launched through shell")

    async def fake_shell(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess(captured)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    client = GeminiAntigravityClient(binary="gemini")

    response = await client.exec("review this")

    assert response.success is True
    assert response.content == "gemini done"
    assert "--prompt" in captured["command"]
    assert "--approval-mode plan" in captured["command"]
    assert "--skip-trust" in captured["command"]
    assert "--output-format text" in captured["command"]
    assert captured["input"] == b"review this"
    assert captured["kwargs"]["cwd"] != str(PROJECT_ROOT)


def test_project_config_enables_gemini_cli_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    config = load_config(PROJECT_ROOT / ".claude" / "hermes" / "model-routing.json")

    gemini = config.executors["gemini"]
    assert gemini.supports_execute is True
    assert gemini.env == []
    assert "gemini" in config.routing.fallback_order

    report = doctor(config)
    assert "env:GEMINI_API_KEY" not in report.providers["gemini"].checks_by_name
    assert report.providers["gemini"].success is True
