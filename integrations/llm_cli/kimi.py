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

from ..cli_errors import missing_binary_error


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
                error=missing_binary_error(self.binary),
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

    async def analyze(
        self,
        task: str,
        context_files: list[str] | None = None,
    ) -> KimiCLIResponse:
        """Run the Maestro forensic audit phase through Kimi CLI."""
        return await self.exec(
            _analyze_prompt(task),
            context_files=context_files,
        )

    async def plan(
        self,
        task: str,
        findings: str,
        context_files: list[str] | None = None,
    ) -> KimiCLIResponse:
        """Run the Maestro strategy refinement phase through Kimi CLI."""
        return await self.exec(
            _plan_prompt(task, findings),
            context_files=context_files,
        )

    async def execute(
        self,
        step: str,
        plan_context: str,
        context_files: list[str] | None = None,
    ) -> KimiCLIResponse:
        """Run the Maestro execution planning phase through Kimi CLI."""
        return await self.exec(
            _execute_prompt(step, plan_context),
            context_files=context_files,
        )

    async def verify(
        self,
        task: str,
        implementation_summary: str,
        requirements: str,
    ) -> KimiCLIResponse:
        """Run the Maestro final verification phase through Kimi CLI."""
        return await self.exec(
            _verify_prompt(task, implementation_summary, requirements),
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


def _analyze_prompt(task: str) -> str:
    return f"""## FORENSIC AUDIT - Iteration 0

### Objective
Validate assumptions against actual codebase state. Do NOT assume the plan is perfect.

### Task
{task}

### Instructions
1. Verify the current state of relevant files/modules
2. Check for discrepancies between plan assumptions and reality
3. Identify existing infrastructure that can be reused
4. Flag potential risks or missing dependencies
5. Document exact file paths and line numbers for findings

### Output Format
**Ground Truth Findings:**
1. [Finding with file:line reference]

**Discrepancies Found:**
- [Assumption vs Reality]

**Existing Infrastructure:**
- [Reusable component with path]

**Risks Identified:**
- [Risk with severity]
"""


def _plan_prompt(task: str, findings: str) -> str:
    return f"""## STRATEGY REFINEMENT - Iteration 1

### Objective
Refine the execution plan based on audit findings. Address identified risks.

### Task
{task}

### Audit Findings (from Iteration 0)
{findings}

### Instructions
1. Break down the task into atomic, verifiable steps
2. Define exact commands or code changes for each step
3. Identify verification steps for each stage
4. Address the risks identified in the audit
5. Note any blocking dependencies

### Output Format
**Revised Plan:**

**Phase 1: [Name]**
- Task 1.1: [Specific action]
  - File: [path]
  - Change: [what to modify]
  - Verify: [how to test]

**Decisions Made:**
- D1: [Decision with rationale]

**Risks Addressed:**
- R1: [How risk is mitigated]

**Open Questions:**
- [Questions needing human input]
"""


def _execute_prompt(step: str, plan_context: str) -> str:
    return f"""## STEP EXECUTION - Iteration 2

### Objective
Execute this step safely, verifying preconditions and postconditions.

### Step to Execute
{step}

### Plan Context
{plan_context}

### Instructions
1. Verify preconditions are met
2. Propose the exact code changes (with file paths and line numbers)
3. Provide verification command to confirm success
4. Note any side effects or dependent changes needed

### Output Format
**Preconditions Check:**
- [x] [Condition verified]

**Proposed Changes:**
```diff
--- a/path/to/file.py
+++ b/path/to/file.py
@@ -line,count +line,count @@
 context
-old line
+new line
 context
```

**Verification Command:**
```bash
[command to verify success]
```

**Side Effects:**
- [Any additional changes needed]
"""


def _verify_prompt(task: str, implementation_summary: str, requirements: str) -> str:
    return f"""## FINAL VERIFICATION - Iteration 3

### Objective
Verify the implementation meets all requirements. Identify any remaining issues.

### Original Task
{task}

### Implementation Summary
{implementation_summary}

### Success Requirements
{requirements}

### Instructions
1. Verify each requirement is met
2. Check for regressions or side effects
3. Identify any cleanup needed (temp files, debug code)
4. Document final metrics/state

### Output Format
**Requirements Check:**
- [x] [Requirement]: [Evidence it's met]
- [ ] [Requirement]: [Why not met / what's needed]

**Regression Check:**
- [Tests run and results]

**Cleanup Needed:**
- [Items to clean up]

**Final Metrics:**
- [Before/after comparison]

**Remaining Issues:**
- [Any open items]
"""


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
