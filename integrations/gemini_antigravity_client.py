"""Reviewer-only Gemini CLI and Antigravity adapter primitives for Hermes."""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cli_errors import missing_binary_error
from .execution_provenance import (
    ExecutionProvenance,
    provenance_from_process_result,
    unknown_execution_provenance,
    unresolved_provider_provenance,
)
from .process_runtime import ProcessOutcome, resolve_executable, run_process

# Flag surfaces differ per installed binary: "gemini" is the Gemini CLI
# (approval-mode/output-format/skip-trust), "antigravity" is the agy binary,
# which rejects those flags ("flags provided but not defined").
_SUPPORTED_FLAVORS = ("gemini", "antigravity")


@dataclass(frozen=True)
class GeminiResponse:
    content: str
    model: str = "gemini-cli"
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    execution_time_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str | None = None
    exit_code: int = 0
    provenance: ExecutionProvenance = field(
        default_factory=unknown_execution_provenance
    )

    @property
    def success(self) -> bool:
        return self.error is None and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp,
            "error": self.error,
            "exit_code": self.exit_code,
            "success": self.success,
            "provenance": self.provenance.to_dict(),
        }


class GeminiAntigravityClient:
    """Small async subprocess wrapper for non-mutating Hermes review tasks."""

    def __init__(
        self,
        *,
        binary: str = "gemini",
        model: str = "gemini-cli",
        timeout_seconds: int = 300,
        env: dict[str, str] | None = None,
        approval_mode: str = "plan",
        output_format: str = "text",
        skip_trust: bool = True,
        flavor: str = "gemini",
        cwd: str | Path | None = None,
    ) -> None:
        if flavor not in _SUPPORTED_FLAVORS:
            raise ValueError(
                f"unsupported flavor {flavor!r}; expected one of {_SUPPORTED_FLAVORS}"
            )
        self.binary = binary
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.env = env
        self.approval_mode = approval_mode
        self.output_format = output_format
        self.skip_trust = skip_trust
        self.flavor = flavor
        self.cwd = Path(cwd) if cwd is not None else _default_cli_cwd()

    async def exec(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> GeminiResponse:
        start = time.perf_counter()
        resolved = resolve_executable(self.binary)
        if resolved is None:
            return GeminiResponse(
                content="",
                model=self.model,
                finish_reason="missing_binary",
                execution_time_ms=int((time.perf_counter() - start) * 1000),
                error=missing_binary_error(self.binary),
                exit_code=127,
                provenance=unresolved_provider_provenance(),
            )

        stdin = prompt
        context = _read_context_files(context_files)
        if context:
            stdin = f"{prompt}\n\n# Context files\n{context}"

        env = os.environ.copy()
        if self.env:
            env.update(self.env)

        if self.flavor == "antigravity":
            cli_args = _agy_cli_args(
                resolved,
                print_timeout_seconds=self.timeout_seconds,
            )
        else:
            cli_args = _gemini_cli_args(
                resolved,
                approval_mode=self.approval_mode,
                output_format=self.output_format,
                skip_trust=self.skip_trust,
            )
        # The owned process boundary reaps the WHOLE tree on timeout. This
        # reviewer wrapper previously used a parent-only ``process.kill()`` --
        # the same class of bug that hung a codex lane ~11h (Q10 Track B,
        # 2026-07-15) by leaving pipe-holding grandchildren alive.
        result = await run_process(
            cli_args,
            stdin_data=stdin.encode("utf-8"),
            env=env,
            cwd=self.cwd,
            timeout_seconds=self.timeout_seconds,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        provenance = provenance_from_process_result(result)

        if result.outcome is ProcessOutcome.PROVIDER_NOT_ESTABLISHED:
            # Resolved but the provider was never established (exec failure).
            # Keep the missing-binary shape so the classifier reads spawn_error.
            return GeminiResponse(
                content="",
                model=self.model,
                finish_reason="missing_binary",
                execution_time_ms=elapsed_ms,
                error=missing_binary_error(self.binary),
                exit_code=127,
                provenance=provenance,
            )

        if result.outcome is ProcessOutcome.TIMED_OUT:
            return GeminiResponse(
                content="",
                model=self.model,
                finish_reason="timeout",
                execution_time_ms=elapsed_ms,
                error=f"{self.binary!r} timed out after {self.timeout_seconds}s",
                exit_code=-1,
                provenance=provenance,
            )

        exit_code = result.exit_code if result.exit_code is not None else 0
        error = result.stderr.decode("utf-8", errors="replace") if exit_code else None
        return GeminiResponse(
            content=result.stdout.decode("utf-8", errors="replace"),
            model=self.model,
            finish_reason="stop" if exit_code == 0 else "error",
            execution_time_ms=elapsed_ms,
            error=error,
            exit_code=exit_code,
            provenance=provenance,
        )


GeminiClient = GeminiAntigravityClient


def _gemini_cli_args(
    resolved_binary: str,
    *,
    approval_mode: str,
    output_format: str,
    skip_trust: bool,
) -> list[str]:
    args = [
        resolved_binary,
        "--prompt",
        "",
        "--approval-mode",
        approval_mode,
        "--output-format",
        output_format,
    ]
    if skip_trust:
        args.append("--skip-trust")
    return args


def _agy_cli_args(
    resolved_binary: str,
    *,
    print_timeout_seconds: int,
) -> list[str]:
    """Build a headless invocation for the installed agy binary.

    Verified against ``agy --help`` (2026-07-10): agy defines --print (alias
    --prompt), --print-timeout, --sandbox, --add-dir, --log-file, etc. It does
    NOT define --approval-mode, --output-format, or --skip-trust and rejects
    them with "flags provided but not defined". The prompt is supplied on
    stdin; --print runs a single prompt non-interactively. --print is kept
    last so a flag-parse failure surfaces at spawn rather than swallowing a
    following flag.
    """
    return [
        resolved_binary,
        "--print-timeout",
        f"{print_timeout_seconds}s",
        "--print",
    ]


def _default_cli_cwd() -> Path:
    return Path(tempfile.gettempdir()) / "hermes-gemini-cli"


def _read_context_files(context_files: list[str] | None) -> str:
    if not context_files:
        return ""

    chunks: list[str] = []
    for path_text in context_files:
        try:
            with open(path_text, "r", encoding="utf-8") as fh:
                chunks.append(f"## {path_text}\n{fh.read()}")
        except OSError as exc:
            chunks.append(f"## {path_text}\n[unreadable: {exc}]")
    return "\n\n".join(chunks)
