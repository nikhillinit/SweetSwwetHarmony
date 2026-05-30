"""CLI-backed Kimi generation wrapper."""

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
class KimiCLIResponse:
    content: str
    model: str = "kimi-cli"
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


class KimiCLIClient:
    """Small async subprocess wrapper for Kimi CLI generation."""

    def __init__(
        self,
        *,
        binary: str = "kimi-cli",
        model: str = "kimi-cli",
        timeout_seconds: int = 300,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self.binary = binary
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.env = env
        self.cwd = Path(cwd) if cwd is not None else _default_cli_cwd()

    async def exec(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> KimiCLIResponse:
        start = time.perf_counter()
        resolved = shutil.which(self.binary)
        if resolved is None:
            return KimiCLIResponse(
                content="",
                model=self.model,
                finish_reason="missing_binary",
                execution_time_ms=int((time.perf_counter() - start) * 1000),
                error=f"{self.binary!r} not found on PATH",
                exit_code=127,
            )

        stdin = _prompt_with_context(prompt, context_files)
        env = os.environ.copy()
        if self.env:
            env.update(self.env)

        process = await _create_cli_process(
            _kimi_cli_args(resolved, work_dir=self.cwd),
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
            return KimiCLIResponse(
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
        return KimiCLIResponse(
            content=stdout.decode("utf-8", errors="replace"),
            model=self.model,
            finish_reason="stop" if exit_code == 0 else "error",
            execution_time_ms=int((time.perf_counter() - start) * 1000),
            error=error,
            exit_code=exit_code,
        )


def _kimi_cli_args(resolved_binary: str, *, work_dir: Path) -> list[str]:
    return [
        resolved_binary,
        "--work-dir",
        str(work_dir),
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "text",
        "--final-message-only",
    ]


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
    return Path(tempfile.gettempdir()) / "hermes-kimi-cli"


def _prompt_with_context(prompt: str, context_files: list[str] | None) -> str:
    context = _read_context_files(context_files)
    if not context:
        return prompt
    return f"{prompt}\n\n# Context files\n{context}"


def _read_context_files(context_files: list[str] | None) -> str:
    if not context_files:
        return ""

    chunks: list[str] = []
    for path_text in context_files:
        try:
            content = Path(path_text).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            content = f"[unreadable: {exc}]"
        chunks.append(f"## {path_text}\n{content}")
    return "\n\n".join(chunks)
