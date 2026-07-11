from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from integrations.codex_wrapper import CodexCLI
from integrations.gemini_antigravity_client import GeminiAntigravityClient
from integrations.llm_cli import KimiCLIClient

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

    def __init__(self, client: Any | None = None, *, binary: str = "kimi-cli"):
        object.__setattr__(self, "client", client or KimiCLIClient(binary=binary))

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        response = await self.client.exec(prompt, context_files=context_files)
        return ExecutorResult(
            executor="kimi",
            success=response.success,
            exit_code=response.exit_code,
            content=response.content,
            duration_ms=response.execution_time_ms,
            error=response.error,
            token_usage=dict(response.usage),
        )


@dataclass(frozen=True)
class GeminiHermesExecutor:
    """Read-only Gemini/Antigravity reviewer adapter for Hermes artifacts."""

    client: Any

    def __init__(self, client: Any | None = None):
        object.__setattr__(self, "client", client or GeminiAntigravityClient())

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        response = await self.client.exec(prompt, context_files=context_files)
        return ExecutorResult(
            executor=getattr(response, "executor", "gemini"),
            success=response.success,
            exit_code=response.exit_code,
            content=response.content,
            duration_ms=response.execution_time_ms,
            error=response.error,
            token_usage=dict(getattr(response, "usage", {}) or {}),
        )


def build_executor(
    name: str,
    config: RoutingConfig,
    *,
    codex_client: Any | None = None,
    kimi_client: Any | None = None,
    gemini_client: Any | None = None,
) -> HermesExecutor:
    """Build a production-capable executor.

    This preserves the existing generic ``hermes run --execute`` contract: an
    executor whose config has ``supportsExecute: false`` is rejected here.
    Reviewer-only workflows should use ``build_reviewer_executor`` instead.
    """

    return _build_executor(
        name,
        config,
        require_execute=True,
        codex_client=codex_client,
        kimi_client=kimi_client,
        gemini_client=gemini_client,
    )


def build_reviewer_executor(
    name: str,
    config: RoutingConfig,
    *,
    codex_client: Any | None = None,
    kimi_client: Any | None = None,
    gemini_client: Any | None = None,
) -> HermesExecutor:
    """Build an executor for non-mutating review artifacts.

    Gemini and Antigravity stay reviewer-only in Track A v1, so this path does
    not require ``supportsExecute``. It still refuses deferred, disabled, or
    unknown providers.
    """

    return _build_executor(
        name,
        config,
        require_execute=False,
        codex_client=codex_client,
        kimi_client=kimi_client,
        gemini_client=gemini_client,
    )


def _build_executor(
    name: str,
    config: RoutingConfig,
    *,
    require_execute: bool,
    codex_client: Any | None = None,
    kimi_client: Any | None = None,
    gemini_client: Any | None = None,
) -> HermesExecutor:
    if name in config.deferred_executors:
        raise ValueError(f"{name!r} is a deferred executor")
    if name not in config.executors:
        raise ValueError(f"unknown executor {name!r}")

    executor = config.executors[name]
    if not executor.enabled:
        raise ValueError(f"disabled executor {name!r}")
    if require_execute and not executor.supports_execute:
        raise ValueError(f"executor {name!r} does not support execute")

    if executor.provider == "codex":
        return CodexHermesExecutor(codex_client)
    if executor.provider == "kimi":
        return KimiHermesExecutor(kimi_client, binary=executor.binary or "kimi-cli")
    if executor.provider == "gemini":
        return GeminiHermesExecutor(
            gemini_client
            or GeminiAntigravityClient(
                binary=executor.binary or "gemini",
                model="gemini-cli",
            )
        )
    if executor.provider == "antigravity":
        return GeminiHermesExecutor(
            gemini_client
            or GeminiAntigravityClient(
                binary=executor.binary or "antigravity",
                model="antigravity",
                flavor="antigravity",
            )
        )

    raise ValueError(f"executor {name!r} has no Hermes adapter")


def build_prompt_packet(
    *,
    title: str,
    body: str,
    required_json_keys: list[str] | None = None,
) -> str:
    """Create a deterministic reviewer prompt packet.

    The packet is intentionally plain text and asks for strict JSON so reviewer
    outputs can be archived, parsed, and audited as first-class Hermes artifacts.
    """

    keys = required_json_keys or []
    schema = {key: "..." for key in keys}
    return "\n".join(
        [
            f"# {title}",
            "",
            "Review the following Hermes plan or task contract artifact.",
            "Do not mutate files, databases, config, Notion, or external systems.",
            "Return strict JSON only.",
            "",
            "Required JSON shape:",
            json.dumps(schema, indent=2),
            "",
            "Packet:",
            body,
            "",
        ]
    )
