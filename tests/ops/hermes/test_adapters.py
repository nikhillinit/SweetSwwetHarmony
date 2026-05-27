from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from integrations.codex_wrapper import CodexResponse
from integrations.hermes.adapters import (
    CodexHermesExecutor,
    ExecutorResult,
    KimiHermesExecutor,
    build_executor,
)
from integrations.hermes.config import RoutingConfig
from integrations.kimi_client import KimiResponse

from .conftest import minimal_config_dict


class FakeCodexClient:
    def __init__(self) -> None:
        self.calls = []

    async def exec(self, prompt, context_files=None):
        self.calls.append((prompt, context_files))
        return CodexResponse(
            content="codex done",
            exit_code=0,
            command="codex exec",
            sandbox_mode="read-only",
            execution_time_ms=12,
        )


class FakeKimiClient:
    def __init__(self) -> None:
        self.calls = []

    async def exec(self, prompt, context_files=None):
        self.calls.append((prompt, context_files))
        return KimiResponse(
            content="kimi done",
            model="kimi-k2.5",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            finish_reason="stop",
            execution_time_ms=34,
        )


async def test_codex_executor_delegates_to_existing_wrapper() -> None:
    client = FakeCodexClient()
    executor = CodexHermesExecutor(client)

    result = await executor.execute("fix thesis filter", context_files=["a.py"])

    assert client.calls == [("fix thesis filter", ["a.py"])]
    assert result.executor == "codex"
    assert result.success is True
    assert result.exit_code == 0
    assert result.content == "codex done"
    assert result.duration_ms == 12


async def test_kimi_executor_delegates_and_surfaces_token_usage() -> None:
    client = FakeKimiClient()
    executor = KimiHermesExecutor(client)

    result = await executor.execute("schema migration", context_files=["b.py"])

    assert client.calls == [("schema migration", ["b.py"])]
    assert result.executor == "kimi"
    assert result.success is True
    assert result.token_usage == {
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
    }


def test_executor_result_is_frozen_and_json_serializable() -> None:
    result = ExecutorResult(
        executor="codex",
        success=True,
        exit_code=0,
        content="ok",
        duration_ms=1,
    )

    with pytest.raises(FrozenInstanceError):
        result.content = "mutated"

    assert result.to_dict()["executor"] == "codex"


def test_build_executor_refuses_deferred_or_unknown_executor() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    with pytest.raises(ValueError, match="deferred executor"):
        build_executor("gemini", config)

    with pytest.raises(ValueError, match="unknown executor"):
        build_executor("missing", config)


def test_build_executor_uses_injected_clients() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())
    codex_client = FakeCodexClient()
    kimi_client = FakeKimiClient()

    assert isinstance(
        build_executor("codex", config, codex_client=codex_client),
        CodexHermesExecutor,
    )
    assert isinstance(
        build_executor("kimi", config, kimi_client=kimi_client),
        KimiHermesExecutor,
    )


def test_build_executor_refuses_executor_marked_non_executable() -> None:
    data = minimal_config_dict()
    data["executors"]["codex"]["supportsExecute"] = False
    config = RoutingConfig.model_validate(data)

    with pytest.raises(ValueError, match="does not support execute"):
        build_executor("codex", config)


def test_build_executor_refuses_disabled_executor() -> None:
    data = minimal_config_dict()
    data["executors"]["codex"]["enabled"] = False
    config = RoutingConfig.model_validate(data)

    with pytest.raises(ValueError, match="disabled executor"):
        build_executor("codex", config)
