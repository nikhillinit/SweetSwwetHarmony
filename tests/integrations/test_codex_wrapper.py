"""Tests for integrations/codex_wrapper.py -- Milestone D3.

Covers:
  D3.1  Sandbox success - mock subprocess, verify stdout captured
  D3.2  Non-zero exit - subprocess returns non-zero, wrapper surfaces error
  D3.3  Timeout/stderr - subprocess times out or has stderr output
  D3.4  Clean error surfacing - exceptions don't leak internal details
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.codex_wrapper import (
    ApprovalMode,
    CodexCLI,
    CodexResponse,
    ForensicPhase,
    ReasoningLevel,
    SandboxMode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def codex_cli():
    """CodexCLI instance with a mocked codex path (no real binary needed)."""
    cli = CodexCLI(
        sandbox_mode=SandboxMode.READ_ONLY,
        approval_mode=ApprovalMode.SUGGEST,
        timeout_seconds=10,
    )
    cli._codex_path = "/usr/local/bin/codex"  # fake path
    return cli


def _make_mock_process(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# D3.1  Sandbox success
# ---------------------------------------------------------------------------

class TestSandboxSuccess:
    """D3.1: Mock subprocess, verify stdout captured."""

    @pytest.mark.asyncio
    async def test_exec_captures_stdout(self, codex_cli):
        """exec() should capture and return subprocess stdout."""
        mock_proc = _make_mock_process(
            stdout=b"Here is my analysis of the codebase.",
            returncode=0,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Analyze the signal pipeline")

        assert response.success is True
        assert response.exit_code == 0
        assert "analysis" in response.content

    @pytest.mark.asyncio
    async def test_exec_uses_read_only_sandbox(self, codex_cli):
        """exec() should default to read-only sandbox mode."""
        mock_proc = _make_mock_process(stdout=b"OK", returncode=0)

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_shell, \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await codex_cli.exec("Test prompt")

        # On Windows, create_subprocess_shell is used
        # Check that the command includes --sandbox read-only
        if mock_shell.called:
            cmd = mock_shell.call_args[0][0]
            assert "read-only" in cmd
        elif mock_exec.called:
            cmd_args = mock_exec.call_args[0]
            assert "read-only" in cmd_args

    @pytest.mark.asyncio
    async def test_exec_returns_codex_response(self, codex_cli):
        """exec() should return a CodexResponse dataclass."""
        mock_proc = _make_mock_process(stdout=b"Response", returncode=0)

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Prompt")

        assert isinstance(response, CodexResponse)
        assert response.content == "Response"
        assert response.sandbox_mode == "read-only"

    @pytest.mark.asyncio
    async def test_exec_records_execution_time(self, codex_cli):
        """exec() should record execution time in ms."""
        mock_proc = _make_mock_process(stdout=b"OK", returncode=0)

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Prompt")

        assert response.execution_time_ms >= 0

    def test_codex_response_success_property(self):
        """CodexResponse.success should be True when exit_code == 0."""
        ok = CodexResponse(
            content="OK",
            exit_code=0,
            command="codex exec",
            sandbox_mode="read-only",
            execution_time_ms=100,
        )
        assert ok.success is True

        fail = CodexResponse(
            content="",
            exit_code=1,
            command="codex exec",
            sandbox_mode="read-only",
            execution_time_ms=100,
            error="Something failed",
        )
        assert fail.success is False

    def test_codex_response_to_dict(self):
        """CodexResponse.to_dict() should include all fields."""
        resp = CodexResponse(
            content="Result",
            exit_code=0,
            command="codex exec 'hello'",
            sandbox_mode="read-only",
            execution_time_ms=250,
        )
        d = resp.to_dict()
        assert d["content"] == "Result"
        assert d["exit_code"] == 0
        assert d["success"] is True
        assert d["sandbox_mode"] == "read-only"


# ---------------------------------------------------------------------------
# D3.2  Non-zero exit
# ---------------------------------------------------------------------------

class TestNonZeroExit:
    """D3.2: Subprocess returns non-zero, wrapper surfaces error."""

    @pytest.mark.asyncio
    async def test_nonzero_exit_surfaces_error(self, codex_cli):
        """Non-zero exit code should set error field and success=False."""
        mock_proc = _make_mock_process(
            stdout=b"",
            stderr=b"Error: authentication required",
            returncode=1,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Test prompt")

        assert response.success is False
        assert response.exit_code == 1
        assert response.error is not None
        assert "authentication" in response.error

    @pytest.mark.asyncio
    async def test_exit_code_127_file_not_found(self, codex_cli):
        """FileNotFoundError should return exit_code 127 with helpful message."""
        with patch("asyncio.create_subprocess_shell", side_effect=FileNotFoundError), \
             patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            response = await codex_cli.exec("Test prompt")

        assert response.exit_code == 127
        assert "not found" in response.error.lower()

    @pytest.mark.asyncio
    async def test_nonzero_with_stdout_and_stderr(self, codex_cli):
        """Non-zero exit with both stdout and stderr should include error."""
        mock_proc = _make_mock_process(
            stdout=b"partial output",
            stderr=b"fatal error occurred",
            returncode=2,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Test prompt")

        assert response.success is False
        assert response.exit_code == 2
        # stdout still captured
        assert response.content == "partial output"
        assert response.error is not None


# ---------------------------------------------------------------------------
# D3.3  Timeout/stderr
# ---------------------------------------------------------------------------

class TestTimeoutAndStderr:
    """D3.3: Subprocess times out or has stderr output."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error_response(self, codex_cli):
        """Timeout should return error response with -1 exit code."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Very slow prompt")

        assert response.exit_code == -1
        assert "timed out" in response.error.lower()
        assert response.execution_time_ms == codex_cli.timeout_seconds * 1000

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, codex_cli):
        """On timeout, the subprocess should be killed."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await codex_cli.exec("Slow prompt")

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stderr_on_success_is_ignored(self, codex_cli):
        """Stderr with exit_code 0 should not set error field."""
        mock_proc = _make_mock_process(
            stdout=b"Good output",
            stderr=b"Warning: deprecated API",
            returncode=0,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Prompt")

        assert response.success is True
        # error should be None because exit code was 0
        assert response.error is None

    @pytest.mark.asyncio
    async def test_stderr_on_failure_is_captured(self, codex_cli):
        """Stderr with non-zero exit should be captured as error."""
        mock_proc = _make_mock_process(
            stdout=b"",
            stderr=b"FATAL: out of memory",
            returncode=137,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec("Prompt")

        assert response.success is False
        assert "out of memory" in response.error


# ---------------------------------------------------------------------------
# D3.4  Clean error surfacing
# ---------------------------------------------------------------------------

class TestCleanErrorSurfacing:
    """D3.4: Exceptions don't leak internal details."""

    @pytest.mark.asyncio
    async def test_generic_exception_returns_codex_response(self, codex_cli):
        """Generic exceptions should produce a CodexResponse, not propagate."""
        with patch(
            "asyncio.create_subprocess_shell",
            side_effect=OSError("Permission denied"),
        ), patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("Permission denied"),
        ):
            response = await codex_cli.exec("Prompt")

        assert isinstance(response, CodexResponse)
        assert response.success is False
        assert response.exit_code == 1
        assert response.error is not None

    @pytest.mark.asyncio
    async def test_review_nonexistent_file(self, codex_cli):
        """review() on a nonexistent file should return error without crashing."""
        response = await codex_cli.review("/nonexistent/path/file.py")

        assert response.success is False
        assert "not found" in response.error.lower()
        assert response.exit_code == 1

    def test_is_installed_returns_bool(self):
        """is_installed() should return a boolean without raising."""
        cli = CodexCLI()
        result = cli.is_installed()
        assert isinstance(result, bool)

    def test_codex_path_raises_if_not_installed(self):
        """codex_path property should raise RuntimeError if binary not found."""
        cli = CodexCLI()
        with patch("shutil.which", return_value=None):
            cli._codex_path = None  # force re-check
            with pytest.raises(RuntimeError, match="Codex CLI not found"):
                _ = cli.codex_path

    @pytest.mark.asyncio
    async def test_exec_with_large_prompt_uses_stdin(self, codex_cli):
        """Prompts > 8000 chars should be piped via stdin, not command args."""
        large_prompt = "x" * 9000
        mock_proc = _make_mock_process(stdout=b"OK", returncode=0)

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_shell, \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.exec(large_prompt)

        assert response.success is True
        # The process should have received stdin data
        if mock_shell.called:
            # communicate was called with input data
            comm_call = mock_proc.communicate.call_args
            assert comm_call.kwargs.get("input") is not None or (
                comm_call.args and comm_call.args[0] is not None
            )


# ---------------------------------------------------------------------------
# Forensic workflow methods
# ---------------------------------------------------------------------------

class TestForensicMethods:
    """Test forensic workflow convenience methods (analyze, plan, execute, verify)."""

    @pytest.mark.asyncio
    async def test_analyze_calls_exec(self, codex_cli):
        """analyze() should delegate to exec() with FORENSIC AUDIT prompt."""
        mock_proc = _make_mock_process(
            stdout=b"Ground truth findings from sandbox analysis",
            returncode=0,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.analyze(
                task="Audit the dedup logic",
            )

        assert response.success is True
        assert isinstance(response, CodexResponse)

    @pytest.mark.asyncio
    async def test_plan_calls_exec(self, codex_cli):
        """plan() should delegate to exec() with STRATEGY REFINEMENT prompt."""
        mock_proc = _make_mock_process(
            stdout=b"Revised plan with atomic steps",
            returncode=0,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.plan(
                task="Add rate limiting",
                findings="GitHub API returns 403 at high volume",
            )

        assert response.success is True

    @pytest.mark.asyncio
    async def test_verify_calls_exec(self, codex_cli):
        """verify() should delegate to exec() with FINAL VERIFICATION prompt."""
        mock_proc = _make_mock_process(
            stdout=b"All requirements met",
            returncode=0,
        )

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            response = await codex_cli.verify(
                task="Add rate limiting",
                implementation_summary="Added tenacity retries",
                requirements="1. Respect 5000 req/hr\n2. Exponential backoff",
            )

        assert response.success is True


# ---------------------------------------------------------------------------
# Enum and configuration tests
# ---------------------------------------------------------------------------

class TestEnumsAndConfig:
    """Test enum values and configuration defaults."""

    def test_sandbox_modes(self):
        """All SandboxMode values should be accessible."""
        assert SandboxMode.READ_ONLY.value == "read-only"
        assert SandboxMode.FULL.value == "full"
        assert SandboxMode.NONE.value == "none"

    def test_approval_modes(self):
        """All ApprovalMode values should be accessible."""
        assert ApprovalMode.SUGGEST.value == "suggest"
        assert ApprovalMode.AUTO_EDIT.value == "auto-edit"
        assert ApprovalMode.FULL_AUTO.value == "full-auto"

    def test_reasoning_levels(self):
        """All ReasoningLevel values should be accessible."""
        assert ReasoningLevel.LOW.value == "low"
        assert ReasoningLevel.MEDIUM.value == "medium"
        assert ReasoningLevel.HIGH.value == "high"

    def test_default_model(self):
        """Default model should be gpt-5.3-codex."""
        from integrations.codex_wrapper import DEFAULT_MODEL
        assert DEFAULT_MODEL == "gpt-5.3-codex"

    def test_default_reasoning_level(self):
        """Default reasoning level should be high."""
        from integrations.codex_wrapper import DEFAULT_REASONING_LEVEL
        assert DEFAULT_REASONING_LEVEL == ReasoningLevel.HIGH


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
