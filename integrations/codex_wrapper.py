"""
Codex CLI Wrapper for Strategy Iteration.

This module provides a Python interface to OpenAI's Codex CLI,
enabling sandbox-isolated strategy iteration with your ChatGPT Pro subscription.

Architecture (from Maestro pattern):
- Claude Code acts as the orchestrator
- Codex CLI provides sandbox-isolated suggestions (read-only mode)
- Claude validates and executes all actions

Benefits:
- No incremental API costs with ChatGPT Pro
- Sandbox isolation for safe experimentation
- High reasoning capability for strategy development

Usage:
    from integrations.codex_wrapper import CodexCLI

    codex = CodexCLI()

    # Quick consultation
    response = await codex.exec("How should I optimize the thesis matcher?")

    # Interactive code review
    response = await codex.review("collectors/github.py")

    # Strategy iteration
    response = await codex.iterate_strategy(
        context="Current signals have 30% false positive rate...",
        question="How can we improve thesis matching accuracy?"
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("codex-cli")


class SandboxMode(str, Enum):
    """Codex sandbox isolation levels."""
    READ_ONLY = "read-only"  # Can only read files, no writes
    NETWORK_ONLY = "network-only"  # Network access but no file writes
    FULL = "full"  # Full sandbox isolation
    NONE = "none"  # No sandbox (use with caution)


class ApprovalMode(str, Enum):
    """Codex approval modes for actions."""
    SUGGEST = "suggest"  # Only suggest, never execute
    AUTO_EDIT = "auto-edit"  # Auto-approve edits, ask for commands
    FULL_AUTO = "full-auto"  # Auto-approve everything (dangerous)


@dataclass
class CodexResponse:
    """Response from Codex CLI."""
    content: str
    exit_code: int
    command: str
    sandbox_mode: str
    execution_time_ms: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "exit_code": self.exit_code,
            "command": self.command,
            "sandbox_mode": self.sandbox_mode,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp,
            "error": self.error,
            "success": self.success,
        }


class CodexCLI:
    """
    Python wrapper for OpenAI Codex CLI.

    Provides structured access to Codex capabilities:
    - Quick consultations (exec)
    - Code reviews (review)
    - Interactive sessions (chat)
    - Strategy iteration workflows

    Requires:
    - Codex CLI installed: npm install -g @openai/codex
    - ChatGPT Pro login: codex login
    """

    def __init__(
        self,
        sandbox_mode: SandboxMode = SandboxMode.READ_ONLY,
        approval_mode: ApprovalMode = ApprovalMode.SUGGEST,
        timeout_seconds: int = 300,
    ):
        """
        Initialize Codex CLI wrapper.

        Args:
            sandbox_mode: Sandbox isolation level (default: read-only)
            approval_mode: Action approval mode (default: suggest only)
            timeout_seconds: Max execution time (default: 5 minutes)
        """
        self.sandbox_mode = sandbox_mode
        self.approval_mode = approval_mode
        self.timeout_seconds = timeout_seconds
        self._codex_path: Optional[str] = None

    @property
    def codex_path(self) -> str:
        """Get path to Codex CLI binary."""
        if self._codex_path is None:
            self._codex_path = shutil.which("codex")
            if not self._codex_path:
                raise RuntimeError(
                    "Codex CLI not found. Install with: npm install -g @openai/codex"
                )
        return self._codex_path

    def is_installed(self) -> bool:
        """Check if Codex CLI is installed."""
        return shutil.which("codex") is not None

    async def check_auth(self) -> tuple[bool, str]:
        """
        Check Codex authentication status.

        Returns:
            Tuple of (is_authenticated, status_message)
        """
        try:
            result = await self._run_command(["login", "status"])
            is_auth = "logged in" in result.content.lower()
            return is_auth, result.content
        except Exception as e:
            return False, f"Auth check failed: {str(e)}"

    async def exec(
        self,
        prompt: str,
        sandbox: Optional[SandboxMode] = None,
        context_files: Optional[list[str]] = None,
    ) -> CodexResponse:
        """
        Execute a quick Codex consultation.

        This is the primary method for getting Codex's perspective on
        strategy questions, code analysis, or thesis refinement.

        Args:
            prompt: Question or task for Codex
            sandbox: Override default sandbox mode
            context_files: Optional files to include as context

        Returns:
            CodexResponse with Codex's analysis

        Example:
            response = await codex.exec(
                "How should I improve the thesis matcher's false positive rate?"
            )
        """
        args = ["exec", prompt]

        sandbox_mode = sandbox or self.sandbox_mode
        args.extend(["--sandbox", sandbox_mode.value])

        if context_files:
            for file_path in context_files:
                if os.path.exists(file_path):
                    args.extend(["--file", file_path])

        return await self._run_command(args)

    async def review(
        self,
        file_path: str,
        focus_areas: Optional[list[str]] = None,
    ) -> CodexResponse:
        """
        Request a code review from Codex.

        Args:
            file_path: Path to file to review
            focus_areas: Optional specific areas to focus on

        Returns:
            CodexResponse with code review

        Example:
            response = await codex.review(
                "collectors/github.py",
                focus_areas=["rate limiting", "error handling"]
            )
        """
        if not os.path.exists(file_path):
            return CodexResponse(
                content="",
                exit_code=1,
                command=f"review {file_path}",
                sandbox_mode=self.sandbox_mode.value,
                execution_time_ms=0,
                error=f"File not found: {file_path}",
            )

        args = ["review", file_path]

        if focus_areas:
            focus_prompt = f"Focus on: {', '.join(focus_areas)}"
            args.extend(["--message", focus_prompt])

        return await self._run_command(args)

    async def iterate_strategy(
        self,
        context: str,
        question: str,
        thesis_file: Optional[str] = None,
    ) -> CodexResponse:
        """
        Run a strategy iteration session with Codex.

        This method is optimized for thesis refinement and signal
        strategy development.

        Args:
            context: Current state, metrics, or observations
            question: Specific strategy question
            thesis_file: Optional path to thesis config file

        Returns:
            CodexResponse with strategy recommendations

        Example:
            response = await codex.iterate_strategy(
                context="30% of GitHub signals are false positives (B2B tools)",
                question="How should we refine the thesis keywords?"
            )
        """
        prompt = f"""## Strategy Iteration Request

### Context
{context}

### Question
{question}

### Instructions
Provide actionable recommendations for a VC deal sourcing system.
Focus on early-stage consumer companies (Pre-Seed to Series A).
Consider: Consumer CPG, Health Tech, Travel & Hospitality, Marketplaces.
Exclude: B2B/Enterprise, crypto, cleantech, hardware-only.

Provide:
1. Specific recommendations (not generic advice)
2. Implementation steps
3. Expected impact
4. Risks to consider"""

        context_files = []
        if thesis_file and os.path.exists(thesis_file):
            context_files.append(thesis_file)

        # Also include CLAUDE.md for thesis context
        claude_md = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "CLAUDE.md"
        )
        if os.path.exists(claude_md):
            context_files.append(claude_md)

        return await self.exec(
            prompt=prompt,
            sandbox=SandboxMode.READ_ONLY,
            context_files=context_files,
        )

    async def compare_perspectives(
        self,
        question: str,
        claude_perspective: str,
        context: Optional[str] = None,
    ) -> CodexResponse:
        """
        Get Codex's perspective to compare with Claude's analysis.

        Useful for multi-LLM consensus on important decisions.

        Args:
            question: The question being analyzed
            claude_perspective: Claude's current answer
            context: Optional shared context

        Returns:
            CodexResponse with Codex's independent analysis and comparison

        Example:
            response = await codex.compare_perspectives(
                question="Should we add a Wellfound collector?",
                claude_perspective="Research shows API was deprecated in 2023..."
            )
        """
        prompt = f"""## Multi-LLM Consensus Request

### Question
{question}

### Context
{context or "No additional context"}

### Another AI's Perspective
{claude_perspective}

### Your Task
1. Provide your independent analysis (without being influenced by the other perspective)
2. Compare your analysis with the other AI's perspective
3. Note areas of agreement (with confidence level)
4. Note areas of disagreement (with reasoning)
5. Suggest a synthesized consensus view

Be specific and constructive. Focus on facts and evidence."""

        return await self.exec(prompt, sandbox=SandboxMode.READ_ONLY)

    async def _run_command(
        self,
        args: list[str],
    ) -> CodexResponse:
        """
        Execute a Codex CLI command.

        Args:
            args: Command arguments (without 'codex' prefix)

        Returns:
            CodexResponse with command output
        """
        full_command = [self.codex_path] + args
        command_str = " ".join(full_command)

        start_time = datetime.now()

        try:
            process = await asyncio.create_subprocess_exec(
                *full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CODEX_APPROVAL": self.approval_mode.value},
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return CodexResponse(
                    content="",
                    exit_code=-1,
                    command=command_str,
                    sandbox_mode=self.sandbox_mode.value,
                    execution_time_ms=self.timeout_seconds * 1000,
                    error=f"Command timed out after {self.timeout_seconds} seconds",
                )

            end_time = datetime.now()
            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            content = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace") if stderr else None

            return CodexResponse(
                content=content,
                exit_code=process.returncode or 0,
                command=command_str,
                sandbox_mode=self.sandbox_mode.value,
                execution_time_ms=execution_time_ms,
                error=error if error and process.returncode != 0 else None,
            )

        except FileNotFoundError:
            return CodexResponse(
                content="",
                exit_code=127,
                command=command_str,
                sandbox_mode=self.sandbox_mode.value,
                execution_time_ms=0,
                error="Codex CLI not found. Install with: npm install -g @openai/codex",
            )
        except Exception as e:
            return CodexResponse(
                content="",
                exit_code=1,
                command=command_str,
                sandbox_mode=self.sandbox_mode.value,
                execution_time_ms=0,
                error=str(e),
            )


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def quick_consult(prompt: str) -> str:
    """
    Quick one-shot consultation with Codex.

    Args:
        prompt: Question for Codex

    Returns:
        Codex's response text

    Example:
        answer = await quick_consult("What's the best rate limiting strategy?")
    """
    codex = CodexCLI()
    response = await codex.exec(prompt)
    if response.success:
        return response.content
    else:
        return f"Error: {response.error}"


async def get_code_review(file_path: str) -> dict[str, Any]:
    """
    Get a code review for a file.

    Args:
        file_path: Path to file to review

    Returns:
        Dict with review results

    Example:
        review = await get_code_review("collectors/github.py")
    """
    codex = CodexCLI()
    response = await codex.review(file_path)
    return response.to_dict()


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """CLI interface for testing Codex wrapper."""
    import argparse

    parser = argparse.ArgumentParser(description="Codex CLI Wrapper")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Check command
    check_parser = subparsers.add_parser("check", help="Check Codex installation")

    # Exec command
    exec_parser = subparsers.add_parser("exec", help="Execute a prompt")
    exec_parser.add_argument("prompt", help="Prompt to execute")
    exec_parser.add_argument("--sandbox", default="read-only", help="Sandbox mode")

    # Review command
    review_parser = subparsers.add_parser("review", help="Review a file")
    review_parser.add_argument("file", help="File to review")
    review_parser.add_argument("--focus", help="Focus areas (comma-separated)")

    args = parser.parse_args()

    async def run():
        codex = CodexCLI()

        if args.command == "check":
            if codex.is_installed():
                print("✓ Codex CLI is installed")
                is_auth, status = await codex.check_auth()
                if is_auth:
                    print(f"✓ Authenticated: {status}")
                else:
                    print(f"✗ Not authenticated: {status}")
                    print("  Run: codex login")
            else:
                print("✗ Codex CLI not installed")
                print("  Run: npm install -g @openai/codex")

        elif args.command == "exec":
            sandbox = SandboxMode(args.sandbox)
            response = await codex.exec(args.prompt, sandbox=sandbox)
            print(json.dumps(response.to_dict(), indent=2))

        elif args.command == "review":
            focus = args.focus.split(",") if args.focus else None
            response = await codex.review(args.file, focus_areas=focus)
            print(json.dumps(response.to_dict(), indent=2))

        else:
            parser.print_help()

    asyncio.run(run())


if __name__ == "__main__":
    main()
