"""
Kimi (Moonshot AI) Client for Maestro/Forensic Engineer Workflow.

Minimal integration for dev/debug use. Uses OpenAI SDK with Kimi's API endpoint.

Architecture:
- Drop-in alternative to CodexCLI for forensic workflow
- Uses same interface (analyze, plan, execute, verify)
- Returns compatible response objects

Usage:
    from integrations.kimi_client import KimiClient

    kimi = KimiClient()
    response = await kimi.analyze("Audit the signal processing pipeline")

Models:
    - kimi-k2.5: Primary model (recommended)
    - kimi-k2-thinking: Extended reasoning chains
    - moonshot-v1-128k: Legacy model, 128K context window
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("kimi-client")


class KimiModel(str, Enum):
    """Available Kimi models."""
    # New Kimi models (K2.5 quota)
    K2_5 = "kimi-k2.5"
    K2_THINKING = "kimi-k2-thinking"
    LATEST = "kimi-latest"
    # Legacy Moonshot models (legacy quota)
    MOONSHOT_128K = "moonshot-v1-128k"
    MOONSHOT_32K = "moonshot-v1-32k"
    MOONSHOT_8K = "moonshot-v1-8k"


# Default model for forensic workflows
DEFAULT_MODEL = KimiModel.K2_5


@dataclass
class KimiResponse:
    """
    Response from Kimi API.

    Compatible with CodexResponse interface for drop-in usage in maestro.py.
    """
    content: str
    model: str
    usage: dict[str, int]
    finish_reason: str
    execution_time_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """Compatible with CodexResponse.success."""
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp,
            "error": self.error,
            "success": self.success,
        }


class KimiClient:
    """
    Kimi API client for forensic workflow integration.

    Drop-in alternative to CodexCLI with same interface for forensic methods.
    Uses OpenAI SDK with Kimi's base URL (OpenAI API compatible).

    Tier0 limits (enforced by semaphore):
    - Concurrency: 3 requests
    - TPD: 1,500,000 tokens/day
    - RPM: 20 requests/minute
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: KimiModel = DEFAULT_MODEL,
        timeout_seconds: int = 120,
        max_concurrent: int = 3,
    ):
        """
        Initialize Kimi client.

        Args:
            api_key: Kimi/Moonshot API key (or from KIMI_API_KEY env var)
            model: Default model to use
            timeout_seconds: Request timeout
            max_concurrent: Max concurrent requests (Tier0 limit: 3)
        """
        self.api_key = (
            api_key
            or os.environ.get("KIMI_API_KEY")
            or os.environ.get("MOONSHOT_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "Kimi API key required. Set KIMI_API_KEY or MOONSHOT_API_KEY env var. "
                "Get key at: https://platform.moonshot.cn/console/api-keys"
            )

        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @property
    def client(self):
        """Lazy-load OpenAI client with Kimi base URL."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("OpenAI package required. Run: pip install openai")

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url="https://api.moonshot.cn/v1",
                timeout=self.timeout_seconds,
            )
        return self._client

    async def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[KimiModel] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> KimiResponse:
        """
        Send a chat completion request to Kimi API.

        Args:
            prompt: User message
            system_prompt: Optional system context
            model: Model to use (default: self.model)
            temperature: Creativity 0-2
            max_tokens: Max response length

        Returns:
            KimiResponse with content and metadata
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model_name = (model or self.model).value
        start_time = datetime.now()

        async with self._semaphore:  # Enforce concurrency limit
            try:
                response = await self.client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                end_time = datetime.now()
                execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

                choice = response.choices[0]
                return KimiResponse(
                    content=choice.message.content or "",
                    model=response.model,
                    usage={
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                        "total_tokens": response.usage.total_tokens if response.usage else 0,
                    },
                    finish_reason=choice.finish_reason or "unknown",
                    execution_time_ms=execution_time_ms,
                )

            except Exception as e:
                end_time = datetime.now()
                execution_time_ms = int((end_time - start_time).total_seconds() * 1000)
                logger.error(f"Kimi API error: {e}")
                return KimiResponse(
                    content="",
                    model=model_name,
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    finish_reason="error",
                    execution_time_ms=execution_time_ms,
                    error=str(e),
                )

    # =========================================================================
    # FORENSIC ENGINEER WORKFLOW METHODS
    # =========================================================================
    # Same interface as CodexCLI for drop-in usage in maestro.py

    async def analyze(
        self,
        task: str,
        context_files: Optional[list[str]] = None,
    ) -> KimiResponse:
        """
        Iteration 0: Forensic Audit & Validation.

        Validate assumptions against actual codebase state.

        Args:
            task: What to audit/analyze
            context_files: Files to examine

        Returns:
            KimiResponse with audit findings
        """
        # Build context from files
        file_context = await self._read_context_files(context_files)

        prompt = f"""## FORENSIC AUDIT - Iteration 0

### Objective
Validate assumptions against actual codebase state. Do NOT assume the plan is perfect.

### Task
{task}

### Context Files
{file_context}

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
        return await self.chat(prompt, model=KimiModel.K2_5)

    async def plan(
        self,
        task: str,
        findings: str,
        context_files: Optional[list[str]] = None,
    ) -> KimiResponse:
        """
        Iteration 1: Strategy Refinement.

        Convert high-level plan into concrete, executable steps.

        Args:
            task: The goal to achieve
            findings: Findings from Iteration 0 (analyze phase)
            context_files: Relevant files

        Returns:
            KimiResponse with refined plan
        """
        file_context = await self._read_context_files(context_files)

        prompt = f"""## STRATEGY REFINEMENT - Iteration 1

### Objective
Refine the execution plan based on audit findings. Address identified risks.

### Task
{task}

### Audit Findings (from Iteration 0)
{findings}

### Context Files
{file_context}

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
        return await self.chat(prompt, model=KimiModel.K2_5)

    async def execute(
        self,
        step: str,
        plan_context: str,
        context_files: Optional[list[str]] = None,
    ) -> KimiResponse:
        """
        Iteration 2: Step-by-Step Execution.

        Execute a specific step from the plan safely.

        Args:
            step: The specific step to execute
            plan_context: Relevant context from the plan
            context_files: Files involved in this step

        Returns:
            KimiResponse with implementation proposal
        """
        file_context = await self._read_context_files(context_files)

        prompt = f"""## STEP EXECUTION - Iteration 2

### Objective
Execute this step safely, verifying preconditions and postconditions.

### Step to Execute
{step}

### Plan Context
{plan_context}

### Context Files
{file_context}

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
        return await self.chat(prompt, model=KimiModel.K2_5)

    async def verify(
        self,
        task: str,
        implementation_summary: str,
        requirements: str,
    ) -> KimiResponse:
        """
        Iteration 3: Final Verification & Cleanup.

        Prove the task is complete and meets all requirements.

        Args:
            task: The original task
            implementation_summary: What was implemented
            requirements: Success criteria

        Returns:
            KimiResponse with verification analysis
        """
        prompt = f"""## FINAL VERIFICATION - Iteration 3

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
        return await self.chat(prompt, model=KimiModel.K2_5)

    async def exec(
        self,
        prompt: str,
        context_files: Optional[list[str]] = None,
    ) -> KimiResponse:
        """
        Execute a general prompt (compatible with CodexCLI.exec).

        Args:
            prompt: The prompt to send
            context_files: Optional files to include as context

        Returns:
            KimiResponse with analysis
        """
        file_context = await self._read_context_files(context_files)

        full_prompt = prompt
        if file_context and file_context != "No context files provided.":
            full_prompt = f"{prompt}\n\n### Context Files\n{file_context}"

        return await self.chat(full_prompt)

    async def _read_context_files(
        self,
        context_files: Optional[list[str]],
    ) -> str:
        """Read context files and format them for the prompt."""
        if not context_files:
            return "No context files provided."

        contents = []
        for file_path in context_files:
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Truncate very large files
                    if len(content) > 50000:
                        content = content[:50000] + "\n... [truncated]"
                    contents.append(f"**{file_path}:**\n```\n{content}\n```")
                except Exception as e:
                    contents.append(f"**{file_path}:** Error reading file: {e}")
            else:
                contents.append(f"**{file_path}:** File not found")

        return "\n\n".join(contents) if contents else "No context files provided."


# =============================================================================
# CLI CHECK
# =============================================================================

async def check_connection():
    """Quick connectivity check for Kimi API."""
    try:
        client = KimiClient()
        response = await client.chat("Hello, respond with just 'OK' if you receive this.")
        if response.success:
            print(f"[OK] Kimi API connected. Model: {response.model}")
            print(f"     Tokens used: {response.usage.get('total_tokens', 0)}")
            return True
        else:
            print(f"[ERROR] Kimi API error: {response.error}")
            return False
    except Exception as e:
        print(f"[ERROR] Failed to connect: {e}")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        asyncio.run(check_connection())
    else:
        print("Usage: python -m integrations.kimi_client check")
