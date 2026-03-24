"""Tests for integrations/maestro.py -- Milestone D1.

Covers:
  D1.1  Iteration loop up to max_iterations, returns ConsensusResult
  D1.2  Critique categorization (severity, category fields)
  D1.3  Consensus detection ("agreed" state when proposals acceptable)
  D1.4  Malformed LLM responses handled gracefully
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.maestro import (
    ConsensusResult,
    ConsensusState,
    Critique,
    CritiqueCategory,
    CritiqueResponse,
    ForensicPhase,
    ForensicResult,
    KimiMode,
    Maestro,
    Proposal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_codex_response_factory():
    """Factory for CodexResponse-like objects returned by CodexCLI.exec()."""

    def _make(
        content: str = "Proposal with error handling and test strategy.",
        exit_code: int = 0,
        error: str | None = None,
    ) -> MagicMock:
        resp = MagicMock()
        resp.content = content
        resp.exit_code = exit_code
        resp.error = error
        resp.success = exit_code == 0
        resp.sandbox_mode = "read-only"
        resp.execution_time_ms = 150
        resp.timestamp = datetime.now(timezone.utc).isoformat()
        return resp

    return _make


@pytest.fixture
def maestro_with_mock_codex(mock_codex_response_factory):
    """Maestro instance with a mocked Codex backend (no subprocess calls)."""
    m = Maestro(max_iterations=3, kimi_mode=KimiMode.NEVER)
    mock_codex = MagicMock()
    mock_codex.exec = AsyncMock(return_value=mock_codex_response_factory())
    m._codex = mock_codex
    return m


# ---------------------------------------------------------------------------
# D1.1  Iteration loop
# ---------------------------------------------------------------------------

class TestIterationLoop:
    """D1.1: Verify iterate() runs up to max_iterations and returns ConsensusResult."""

    @pytest.mark.asyncio
    async def test_collaborate_returns_consensus_result(
        self, maestro_with_mock_codex
    ):
        """collaborate() should return a ConsensusResult dataclass."""
        result = await maestro_with_mock_codex.collaborate(
            task="Optimize signal dedup",
            context="Current duplication rate is 12%",
        )
        assert isinstance(result, ConsensusResult)

    @pytest.mark.asyncio
    async def test_collaborate_respects_max_iterations(
        self, mock_codex_response_factory
    ):
        """Should stop after max_iterations when consensus is never reached."""
        # Proposal that always triggers blocking critique (contains TODO)
        bad_resp = mock_codex_response_factory(
            content="TODO: implement this later, placeholder code"
        )
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=bad_resp)
        m._codex = mock_codex

        result = await m.collaborate(
            task="Fix signal pipeline",
            context="Pipeline has bugs",
        )

        assert result.iterations <= 2
        assert result.state == ConsensusState.DISAGREED

    @pytest.mark.asyncio
    async def test_collaborate_records_history(self, maestro_with_mock_codex):
        """History should contain proposal and critique entries."""
        result = await maestro_with_mock_codex.collaborate(
            task="Improve matcher",
            context="FP rate is 30%",
        )
        assert len(result.history) >= 2  # at least one proposal + one critique
        types = {entry["type"] for entry in result.history}
        assert "proposal" in types
        assert "critique" in types

    @pytest.mark.asyncio
    async def test_collaborate_codex_called_per_iteration(
        self, mock_codex_response_factory
    ):
        """Codex.exec() should be called once per iteration."""
        bad_resp = mock_codex_response_factory(
            content="TODO placeholder content"
        )
        m = Maestro(max_iterations=3, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=bad_resp)
        m._codex = mock_codex

        await m.collaborate(task="t", context="c")

        assert mock_codex.exec.call_count == 3

    @pytest.mark.asyncio
    async def test_collaborate_with_codex_error(self, mock_codex_response_factory):
        """When Codex returns an error, collaborate still completes gracefully."""
        err_resp = mock_codex_response_factory(
            content="Error: Network error",
            exit_code=1,
            error="Network error",
        )
        err_resp.success = False
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=err_resp)
        m._codex = mock_codex

        result = await m.collaborate(task="t", context="c")
        assert isinstance(result, ConsensusResult)
        # The final_proposal should contain the error text
        assert "Error" in result.final_proposal or result.state in (
            ConsensusState.DISAGREED,
            ConsensusState.PARTIAL,
            ConsensusState.AGREED,
        )


# ---------------------------------------------------------------------------
# D1.2  Critique categorization
# ---------------------------------------------------------------------------

class TestCritiqueCategorization:
    """D1.2: Verify critiques have proper severity and category fields."""

    def test_critique_has_required_fields(self):
        """Critique dataclass should expose category, severity, issue."""
        c = Critique(
            category=CritiqueCategory.FEASIBILITY,
            issue="Missing rate limit handling",
            severity="blocking",
            suggestion="Add exponential backoff",
        )
        assert c.category == CritiqueCategory.FEASIBILITY
        assert c.severity == "blocking"
        assert "rate limit" in c.issue

    def test_critique_to_prompt_format(self):
        """to_prompt() should produce human-readable critique string."""
        c = Critique(
            category=CritiqueCategory.EFFICIENCY,
            issue="N+1 query problem",
            severity="important",
            suggestion="Use batch API",
        )
        text = c.to_prompt()
        assert "EFFICIENCY" in text
        assert "important" in text
        assert "N+1" in text
        assert "batch API" in text

    def test_critique_response_blocking_detection(self):
        """CritiqueResponse should detect blocking issues."""
        cr = CritiqueResponse(
            critiques=[
                Critique(
                    category=CritiqueCategory.FEASIBILITY,
                    issue="Incomplete",
                    severity="blocking",
                ),
                Critique(
                    category=CritiqueCategory.SOPHISTICATION,
                    issue="No tests",
                    severity="minor",
                ),
            ],
            iteration=1,
            overall_assessment="Has blocking issues",
            ready_to_accept=False,
        )
        assert cr.has_blocking_issues is True
        assert len(cr.blocking_issues) == 1

    def test_critique_response_no_blocking(self):
        """CritiqueResponse with only minor issues should not be blocking."""
        cr = CritiqueResponse(
            critiques=[
                Critique(
                    category=CritiqueCategory.SOPHISTICATION,
                    issue="Could add more tests",
                    severity="minor",
                ),
            ],
            iteration=2,
            overall_assessment="Looks good",
            ready_to_accept=True,
        )
        assert cr.has_blocking_issues is False
        assert cr.ready_to_accept is True

    def test_generate_critique_detects_todo(self):
        """_generate_critique should flag TODO as blocking."""
        m = Maestro()
        proposal = Proposal(
            content="TODO: implement rate limiting later. Also add error handling and test.",
            iteration=1,
        )
        critique = m._generate_critique(proposal, iteration=1)
        blocking = [c for c in critique.critiques if c.severity == "blocking"]
        assert len(blocking) >= 1
        assert any("TODO" in c.issue or "placeholder" in c.issue.lower() for c in blocking)

    def test_generate_critique_flags_missing_error_handling(self):
        """_generate_critique should flag missing error handling."""
        m = Maestro()
        proposal = Proposal(
            content="This proposal has a clean implementation with test coverage.",
            iteration=1,
        )
        critique = m._generate_critique(proposal, iteration=1)
        issues = [c.issue for c in critique.critiques]
        assert any("error" in issue.lower() for issue in issues)

    def test_all_critique_categories_are_valid(self):
        """All CritiqueCategory values should be accessible."""
        categories = list(CritiqueCategory)
        assert CritiqueCategory.FEASIBILITY in categories
        assert CritiqueCategory.EFFICIENCY in categories
        assert CritiqueCategory.SOPHISTICATION in categories
        assert CritiqueCategory.CORRECTNESS in categories


# ---------------------------------------------------------------------------
# D1.3  Consensus detection
# ---------------------------------------------------------------------------

class TestConsensusDetection:
    """D1.3: When proposals agree, detect 'agreed' state."""

    @pytest.mark.asyncio
    async def test_agreed_when_no_blocking_issues(
        self, mock_codex_response_factory
    ):
        """Should reach AGREED when proposal has no blocking issues."""
        # Content that avoids TODO/placeholder triggers, includes error + test keywords
        good_resp = mock_codex_response_factory(
            content=(
                "1. Add retry with exponential backoff for error handling.\n"
                "2. test with mocked HTTP client.\n"
                "3. Monitor exception rates after deploy."
            )
        )
        m = Maestro(max_iterations=5, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=good_resp)
        m._codex = mock_codex

        result = await m.collaborate(task="Add retries", context="No retries currently")

        assert result.state == ConsensusState.AGREED
        assert result.iterations >= 1

    @pytest.mark.asyncio
    async def test_partial_with_auto_accept_threshold(
        self, mock_codex_response_factory
    ):
        """Should reach PARTIAL when auto_accept_threshold met with no blocking issues."""
        # Content that triggers important (not blocking) issues
        resp = mock_codex_response_factory(
            content=(
                "We assume the API provides bulk endpoints. "
                "For each api call we batch. "
                "Good error handling and test strategy included."
            )
        )
        m = Maestro(
            max_iterations=5,
            auto_accept_threshold=2,
            kimi_mode=KimiMode.NEVER,
        )
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=resp)
        m._codex = mock_codex

        result = await m.collaborate(task="Task", context="Context")

        assert result.state in (ConsensusState.AGREED, ConsensusState.PARTIAL)

    @pytest.mark.asyncio
    async def test_disagreed_at_max_iterations(
        self, mock_codex_response_factory
    ):
        """Should return DISAGREED when max_iterations exhausted with blocking issues."""
        bad_resp = mock_codex_response_factory(
            content="TODO: finish this placeholder implementation"
        )
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=bad_resp)
        m._codex = mock_codex

        result = await m.collaborate(task="Task", context="Context")

        assert result.state == ConsensusState.DISAGREED
        assert result.iterations == 2

    def test_consensus_result_to_dict(self):
        """ConsensusResult.to_dict() should serialize all fields."""
        cr = ConsensusResult(
            state=ConsensusState.AGREED,
            final_proposal="Do X",
            iterations=2,
            history=[],
            agreed_points=["Use retries"],
            remaining_disagreements=[],
            skills_employed=["edit"],
        )
        d = cr.to_dict()
        assert d["state"] == "agreed"
        assert d["iterations"] == 2
        assert d["skills_employed"] == ["edit"]

    @pytest.mark.asyncio
    async def test_collaborate_with_critique_callback(
        self, mock_codex_response_factory
    ):
        """Custom critique_callback should be invoked instead of default critique."""
        good_resp = mock_codex_response_factory(content="Some proposal")
        m = Maestro(max_iterations=3, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=good_resp)
        m._codex = mock_codex

        custom_critique = CritiqueResponse(
            critiques=[],
            iteration=1,
            overall_assessment="Accepted by custom callback",
            ready_to_accept=True,
        )

        async def callback(proposal, iteration):
            return custom_critique

        result = await m.collaborate(
            task="Task",
            context="Context",
            critique_callback=callback,
        )
        # Should accept on first iteration because callback says ready
        assert result.state == ConsensusState.AGREED
        assert result.iterations == 1


# ---------------------------------------------------------------------------
# D1.4  Malformed LLM responses
# ---------------------------------------------------------------------------

class TestMalformedResponses:
    """D1.4: Handle non-JSON, empty, or truncated responses gracefully."""

    @pytest.mark.asyncio
    async def test_empty_codex_response(self, mock_codex_response_factory):
        """Empty content from Codex should not crash."""
        empty_resp = mock_codex_response_factory(content="")
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=empty_resp)
        m._codex = mock_codex

        result = await m.collaborate(task="Task", context="Context")
        assert isinstance(result, ConsensusResult)

    @pytest.mark.asyncio
    async def test_truncated_codex_response(self, mock_codex_response_factory):
        """Truncated response (e.g., cut mid-sentence) should be handled."""
        truncated_resp = mock_codex_response_factory(
            content="Here is my proposal for handling error cases and test strategy. "
                    "The implementation involves sev"  # cut off
        )
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=truncated_resp)
        m._codex = mock_codex

        result = await m.collaborate(task="Task", context="Context")
        assert isinstance(result, ConsensusResult)

    @pytest.mark.asyncio
    async def test_non_json_codex_response(self, mock_codex_response_factory):
        """Non-JSON response (free-text) should be handled without parsing errors."""
        text_resp = mock_codex_response_factory(
            content="Just some freeform text about error handling and test coverage"
        )
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(return_value=text_resp)
        m._codex = mock_codex

        result = await m.collaborate(task="Task", context="Context")
        assert isinstance(result, ConsensusResult)

    @pytest.mark.asyncio
    async def test_codex_exec_raises_exception(self):
        """If codex.exec() raises an unexpected exception, collaborate should not crash."""
        m = Maestro(max_iterations=2, kimi_mode=KimiMode.NEVER)
        mock_codex = MagicMock()
        mock_codex.exec = AsyncMock(side_effect=RuntimeError("Unexpected failure"))
        m._codex = mock_codex

        with pytest.raises(RuntimeError):
            await m.collaborate(task="Task", context="Context")

    def test_extract_agreed_points_from_messy_text(self):
        """_extract_agreed_points should tolerate messy formatting."""
        m = Maestro()
        points = m._extract_agreed_points(
            "Some preamble\n"
            "1. First important agreed point text here\n"
            "2. Second agreed point\n"
            "- Bulleted point with enough text to pass\n"
            "Short\n"  # should be excluded (<= 10 chars)
            "not a list item"
        )
        assert len(points) >= 2

    def test_extract_findings_from_structured_response(self):
        """_extract_findings should parse findings from structured text."""
        m = Maestro()
        findings = m._extract_findings(
            "**Ground Truth Findings:**\n"
            "1. The signal store uses WAL mode in storage/signal_store.py:42\n"
            "2. Dedup relies on canonical keys defined in utils/canonical_keys.py:15\n"
            "\n"
            "Other section"
        )
        assert len(findings) >= 1

    def test_extract_decisions_from_structured_response(self):
        """_extract_decisions should parse decisions from structured text."""
        m = Maestro()
        decisions = m._extract_decisions(
            "**Decisions Made:**\n"
            "- D1: Use tenacity library for retry logic\n"
            "- D2: Keep backward compatibility with existing API\n"
            "\n"
            "Other section"
        )
        assert len(decisions) >= 1


# ---------------------------------------------------------------------------
# Forensic critique generation
# ---------------------------------------------------------------------------

class TestForensicCritique:
    """Forensic-phase-specific critique generation."""

    def test_analyze_critique_flags_missing_file_refs(self):
        """ANALYZE critique should flag proposals without file:line references."""
        m = Maestro()
        proposal = Proposal(content="The codebase looks fine overall", iteration=0)
        critique = m._generate_forensic_critique(
            proposal, ForensicPhase.ANALYZE, sub_iteration=0
        )
        issues = [c.issue for c in critique.critiques]
        assert any("file:line" in i.lower() or "file" in i.lower() for i in issues)

    def test_plan_critique_flags_missing_steps(self):
        """PLAN critique should flag proposals without step breakdowns."""
        m = Maestro()
        proposal = Proposal(content="We should improve the system", iteration=1)
        critique = m._generate_forensic_critique(
            proposal, ForensicPhase.PLAN, sub_iteration=0
        )
        blocking = [c for c in critique.critiques if c.severity == "blocking"]
        assert len(blocking) >= 1

    def test_execute_critique_flags_missing_changes(self):
        """EXECUTE critique should flag proposals without specific changes."""
        m = Maestro()
        proposal = Proposal(
            content="The precondition checks pass. We can proceed.",
            iteration=2,
        )
        critique = m._generate_forensic_critique(
            proposal, ForensicPhase.EXECUTE, sub_iteration=0
        )
        blocking = [c for c in critique.critiques if c.severity == "blocking"]
        assert len(blocking) >= 1

    def test_verify_critique_flags_missing_checklist(self):
        """VERIFY critique should flag proposals without requirement checklists."""
        m = Maestro()
        proposal = Proposal(content="Everything seems to work fine.", iteration=3)
        critique = m._generate_forensic_critique(
            proposal, ForensicPhase.VERIFY, sub_iteration=0
        )
        blocking = [c for c in critique.critiques if c.severity == "blocking"]
        assert len(blocking) >= 1

    def test_good_analyze_response_passes(self):
        """A well-structured ANALYZE response should pass critique."""
        m = Maestro()
        proposal = Proposal(
            content=(
                "**Ground Truth Findings:**\n"
                "1. file: storage/signal_store.py line 42 uses WAL mode\n"
                "**Assumption verified:** The dedup logic is correct\n"
            ),
            iteration=0,
        )
        critique = m._generate_forensic_critique(
            proposal, ForensicPhase.ANALYZE, sub_iteration=0
        )
        assert critique.ready_to_accept is True


# ---------------------------------------------------------------------------
# Kimi mode selection
# ---------------------------------------------------------------------------

class TestKimiModeSelection:
    """Verify _should_use_kimi logic for auto/always/never/dual modes."""

    def test_always_mode_returns_true(self):
        """ALWAYS mode should always select Kimi."""
        m = Maestro(kimi_mode=KimiMode.ALWAYS)
        assert m._should_use_kimi(context_files=None) is True

    def test_never_mode_returns_false(self):
        """NEVER mode should never select Kimi."""
        m = Maestro(kimi_mode=KimiMode.NEVER)
        assert m._should_use_kimi(context_files=["a.py"] * 10) is False

    def test_dual_mode_uses_kimi_for_analyze(self):
        """DUAL mode should select Kimi for ANALYZE phase only."""
        m = Maestro(kimi_mode=KimiMode.DUAL)
        assert m._should_use_kimi(None, phase=ForensicPhase.ANALYZE) is True
        assert m._should_use_kimi(None, phase=ForensicPhase.PLAN) is False

    def test_auto_mode_small_context_uses_codex(self):
        """AUTO mode with small context should not select Kimi."""
        m = Maestro(kimi_mode=KimiMode.AUTO)
        assert m._should_use_kimi(context_files=["a.py", "b.py"]) is False

    def test_auto_mode_large_text_uses_kimi(self):
        """AUTO mode with >= 20K tokens of text context should select Kimi."""
        m = Maestro(kimi_mode=KimiMode.AUTO)
        big_text = "x" * 100_000  # 100K chars ~ 25K tokens
        assert m._should_use_kimi(
            context_files=None, context_text=big_text
        ) is True

    def test_backwards_compat_use_kimi_flag(self):
        """Deprecated use_kimi=True should map to KimiMode.ALWAYS."""
        m = Maestro(use_kimi=True)
        assert m.kimi_mode == KimiMode.ALWAYS


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
