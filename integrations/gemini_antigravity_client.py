"""Reviewer-only Gemini CLI and Antigravity adapter primitives for Hermes."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        cwd: str | Path | None = None,
    ) -> None:
        self.binary = binary
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.env = env
        self.approval_mode = approval_mode
        self.output_format = output_format
        self.skip_trust = skip_trust
        self.cwd = Path(cwd) if cwd is not None else _default_cli_cwd()

    async def exec(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> GeminiResponse:
        start = time.perf_counter()
        resolved = shutil.which(self.binary)
        if resolved is None:
            return GeminiResponse(
                content="",
                model=self.model,
                finish_reason="missing_binary",
                execution_time_ms=int((time.perf_counter() - start) * 1000),
                error=f"{self.binary!r} not found on PATH",
                exit_code=127,
            )

        stdin = prompt
        context = _read_context_files(context_files)
        if context:
            stdin = f"{prompt}\n\n# Context files\n{context}"

        env = os.environ.copy()
        if self.env:
            env.update(self.env)

        process = await _create_cli_process(
            _gemini_cli_args(
                resolved,
                approval_mode=self.approval_mode,
                output_format=self.output_format,
                skip_trust=self.skip_trust,
            ),
            env=env,
            cwd=self.cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return GeminiResponse(
                content=stdout.decode("utf-8", errors="replace"),
                model=self.model,
                finish_reason="timeout",
                execution_time_ms=int((time.perf_counter() - start) * 1000),
                error=(
                    f"{self.binary!r} timed out after {self.timeout_seconds}s: "
                    + stderr.decode("utf-8", errors="replace")
                ),
                exit_code=-1,
            )

        exit_code = process.returncode or 0
        error = stderr.decode("utf-8", errors="replace") if exit_code else None
        return GeminiResponse(
            content=stdout.decode("utf-8", errors="replace"),
            model=self.model,
            finish_reason="stop" if exit_code == 0 else "error",
            execution_time_ms=int((time.perf_counter() - start) * 1000),
            error=error,
            exit_code=exit_code,
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


async def _create_cli_process(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
) -> asyncio.subprocess.Process:
    cwd.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32" and Path(args[0]).suffix.lower() in {".cmd", ".bat"}:
        return await asyncio.create_subprocess_shell(
            subprocess.list2cmdline(args),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd),
        )
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(cwd),
    )


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
