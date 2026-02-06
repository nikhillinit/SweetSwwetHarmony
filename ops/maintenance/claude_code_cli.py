"""Wrapper around `claude -p` for non-interactive repair sessions.

Prerequisites:
- claude CLI installed: npm install -g @anthropic-ai/claude
- ANTHROPIC_API_KEY environment variable set
"""

import json
import logging
import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ClaudeCodeCLI:
    """Wrapper for claude CLI non-interactive mode."""

    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        self._claude_path = shutil.which("claude")

    @property
    def available(self) -> bool:
        """Check if claude CLI is available."""
        return self._claude_path is not None

    def call(
        self,
        prompt: str,
        session_id: Optional[str] = None,
        output_format: str = "text",
        allowed_tools: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Call claude -p with a prompt.

        Args:
            prompt: The prompt to send
            session_id: Optional session ID for continuation
            output_format: 'text' or 'json'
            allowed_tools: Optional list of tools to auto-approve

        Returns:
            Dict with 'success', 'output', and optionally 'error'
        """
        if not self.available:
            return {
                "success": False,
                "output": "",
                "error": "claude CLI not found. Install with: npm install -g @anthropic-ai/claude",
            }

        cmd = [self._claude_path, "-p", prompt]

        if session_id:
            cmd.extend(["--resume", session_id])

        if output_format == "json":
            cmd.extend(["--output-format", "json"])

        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowedTools", tool])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(Path.cwd()),
                env={**os.environ},
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "output": result.stdout,
                    "error": result.stderr or f"Exit code: {result.returncode}",
                }

            return {
                "success": True,
                "output": result.stdout,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Timeout after {self.timeout}s",
            }
        except FileNotFoundError:
            return {
                "success": False,
                "output": "",
                "error": "claude CLI not found on PATH",
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
            }
