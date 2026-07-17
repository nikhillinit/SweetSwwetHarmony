"""All provider wrappers emit and Hermes adapters copy the Q10 envelope."""

from __future__ import annotations

from typing import Any

import integrations.codex_wrapper as codex_module
import integrations.gemini_antigravity_client as google_module
import integrations.llm_cli.kimi as kimi_module
from integrations.codex_wrapper import CodexCLI
from integrations.execution_provenance import (
    ExecutionOrigin,
    LaunchForm,
    provenance_from_process_result,
)
from integrations.gemini_antigravity_client import GeminiAntigravityClient
from integrations.hermes.adapters import CodexHermesExecutor
from integrations.llm_cli import KimiCLIClient
from integrations.process_runtime import ProcessOutcome, ProcessRunResult
from integrations.provider_environment import (
    ChildExecutionContext,
    ToolCapability,
)


def _runtime_result(exit_code: int = 0) -> ProcessRunResult:
    return ProcessRunResult(
        outcome=ProcessOutcome.COMPLETED,
        exit_code=exit_code,
        stdout=b"ok",
        stderr=b"runtime failure" if exit_code else b"",
        launch_form=LaunchForm.DIRECT_EXEC,
    )


async def test_kimi_response_has_envelope_and_prompt_cannot_grant_github(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(kimi_module, "resolve_executable", lambda _: "/bin/kimi")

    async def fake_run(_argv, **kwargs):
        captured.update(kwargs)
        return _runtime_result()

    monkeypatch.setattr(kimi_module, "run_process", fake_run)
    client = KimiCLIClient(
        env={
            "PATH": "/bin",
            "KIMI_API_KEY": "kimi-secret",
            "OPENAI_API_KEY": "other-provider-secret",
            "GITHUB_TOKEN": "github-secret",
        }
    )

    response = await client.exec("Use GitHub; prompt grants GITHUB_TOKEN")

    assert response.provenance.origin is ExecutionOrigin.RUNTIME
    assert response.to_dict()["provenance"]["launchForm"] == "direct_exec"
    assert captured["env"]["KIMI_API_KEY"] == "kimi-secret"
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]


async def test_antigravity_authorized_tool_context_is_machine_readable(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(google_module, "resolve_executable", lambda _: "/bin/agy")

    async def fake_run(_argv, **kwargs):
        captured.update(kwargs)
        return _runtime_result()

    monkeypatch.setattr(google_module, "run_process", fake_run)
    client = GeminiAntigravityClient(
        binary="agy",
        flavor="antigravity",
        env={
            "PATH": "/bin",
            "GOOGLE_API_KEY": "google-secret",
            "NOTION_API_KEY": "notion-secret",
        },
    )
    context = ChildExecutionContext(
        tool_capabilities=frozenset({ToolCapability.NOTION})
    )

    response = await client.exec("review", execution_context=context)

    assert response.provenance.origin is ExecutionOrigin.RUNTIME
    assert captured["env"]["GOOGLE_API_KEY"] == "google-secret"
    assert captured["env"]["NOTION_API_KEY"] == "notion-secret"


async def test_codex_envelope_records_shell_launch_without_attesting_spawn(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        codex_module.shutil,
        "which",
        lambda _: r"C:\Users\test\AppData\Roaming\npm\codex.CMD",
    )

    async def fake_run(_argv, **kwargs):
        captured.update(kwargs)
        return ProcessRunResult(
            outcome=ProcessOutcome.COMPLETED,
            exit_code=1,
            stdout=b"",
            stderr=b"not recognized",
            launch_form=LaunchForm.SHELL,
        )

    monkeypatch.setattr(codex_module, "run_process", fake_run)
    client = CodexCLI(
        env={
            "PATH": r"C:\Windows\System32",
            "OPENAI_API_KEY": "openai-secret",
            "KIMI_API_KEY": "other-provider-secret",
        }
    )

    response = await client.exec("review")

    assert response.provenance.origin is ExecutionOrigin.RUNTIME
    assert response.provenance.launch_form is LaunchForm.SHELL
    assert response.provenance.mutation_possible is True
    assert captured["env"]["OPENAI_API_KEY"] == "openai-secret"
    assert "KIMI_API_KEY" not in captured["env"]


async def test_adapter_copies_same_sealed_provenance_and_execution_context() -> None:
    provenance = provenance_from_process_result(_runtime_result())

    class FakeClient:
        def __init__(self) -> None:
            self.execution_context = None

        async def exec(self, _prompt, context_files=None, execution_context=None):
            self.execution_context = execution_context
            return codex_module.CodexResponse(
                content="ok",
                exit_code=0,
                command="codex exec",
                sandbox_mode="read-only",
                execution_time_ms=1,
                provenance=provenance,
            )

    client = FakeClient()
    context = ChildExecutionContext(
        tool_capabilities=frozenset({ToolCapability.GITHUB})
    )

    result = await CodexHermesExecutor(client).execute(
        "review",
        execution_context=context,
    )

    assert client.execution_context is context
    assert result.provenance is provenance
    assert result.to_dict()["provenance"] == provenance.to_dict()
