from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from integrations.codex_wrapper import CodexCLI
from integrations.kimi_client import KimiClient

from .config import RoutingConfig


@dataclass(frozen=True)
class ExecutorResult:
    executor: str
    success: bool
    exit_code: int
    content: str
    duration_ms: int
    error: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "success": self.success,
            "exitCode": self.exit_code,
            "content": self.content,
            "durationMs": self.duration_ms,
            "error": self.error,
            "tokenUsage": self.token_usage,
        }


class HermesExecutor(Protocol):
    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        ...


@dataclass(frozen=True)
class CodexHermesExecutor:
    client: Any

    def __init__(self, client: Any | None = None):
        object.__setattr__(self, "client", client or CodexCLI())

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        response = await self.client.exec(prompt, context_files=context_files)
        return ExecutorResult(
            executor="codex",
            success=response.success,
            exit_code=response.exit_code,
            content=response.content,
            duration_ms=response.execution_time_ms,
            error=response.error,
        )


@dataclass(frozen=True)
class KimiHermesExecutor:
    client: Any

    def __init__(self, client: Any | None = None):
        object.__setattr__(self, "client", client or KimiClient())

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        response = await self.client.exec(prompt, context_files=context_files)
        return ExecutorResult(
            executor="kimi",
            success=response.success,
            exit_code=0 if response.success else 1,
            content=response.content,
            duration_ms=response.execution_time_ms,
            error=response.error,
            token_usage=dict(response.usage),
        )


def build_executor(
    name: str,
    config: RoutingConfig,
    *,
    codex_client: Any | None = None,
    kimi_client: Any | None = None,
) -> HermesExecutor:
    if name in config.deferred_executors:
        raise ValueError(f"{name!r} is a deferred executor")
    if name not in config.executors:
        raise ValueError(f"unknown executor {name!r}")

    executor = config.executors[name]
    if not executor.supports_execute:
        raise ValueError(f"executor {name!r} does not support execute")

    if executor.provider == "codex":
        return CodexHermesExecutor(codex_client)
    if executor.provider == "kimi":
        return KimiHermesExecutor(kimi_client)

    raise ValueError(f"executor {name!r} has no Hermes adapter")
