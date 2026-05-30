"""
Maestro: Iterative Consensus Orchestrator for Claude + Codex.

This module implements the iterative critique loop where:
1. Claude (orchestrator) sends task + context to Codex
2. Codex proposes solution in sandbox mode
3. Claude critically evaluates: feasibility, efficiency, sophistication
4. Claude sends critique back to Codex
5. Loop until consensus or max iterations

Codex is instructed to use/create skills where helpful.

Usage:
    from integrations.maestro import Maestro

    maestro = Maestro()
    result = await maestro.collaborate(
        task="Improve thesis matcher false positive rate",
        context="Currently at 30% FP, mostly B2B tools slipping through"
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("maestro")


class CritiqueCategory(str, Enum):
    """Categories for critiquing proposals."""
    FEASIBILITY = "feasibility"      # Will this actually work?
    EFFICIENCY = "efficiency"        # Is there a simpler/faster way?
    SOPHISTICATION = "sophistication" # Edge cases, robustness, completeness
    CORRECTNESS = "correctness"      # Is the logic/reasoning sound?


class ConsensusState(str, Enum):
    """State of consensus between Claude and Codex."""
    ITERATING = "iterating"      # Still exchanging critiques
    AGREED = "agreed"            # Reached consensus
    DISAGREED = "disagreed"      # Fundamental disagreement after max iterations
    PARTIAL = "partial"          # Agreed on some points, not others


class KimiMode(str, Enum):
    """Kimi usage modes for forensic workflow."""
    AUTO = "auto"        # Smart selection based on context size (default)
    ALWAYS = "always"    # Always use Kimi
    NEVER = "never"      # Never use Kimi (Codex only)
    DUAL = "dual"        # Run both for ANALYZE, synthesize findings


# Thresholds for auto-selection
KIMI_AUTO_FILE_THRESHOLD = 5       # Use Kimi if >= 5 context files
KIMI_AUTO_TOKEN_THRESHOLD = 20000  # Use Kimi if >= 20K estimated tokens
KIMI_DAILY_TOKEN_WARNING = 500000  # Warn if daily usage exceeds 500K


@dataclass
class Critique:
    """Structured critique of a proposal."""
    category: CritiqueCategory
    issue: str
    severity: str  # "blocking", "important", "minor"
    suggestion: Optional[str] = None

    def to_prompt(self) -> str:
        result = f"[{self.category.value.upper()}] ({self.severity}): {self.issue}"
        if self.suggestion:
            result += f"\n  Suggestion: {self.suggestion}"
        return result


@dataclass
class Proposal:
    """A proposal from Codex."""
    content: str
    iteration: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    skills_used: list[str] = field(default_factory=list)
    skills_created: list[str] = field(default_factory=list)


@dataclass
class CritiqueResponse:
    """Claude's critique of a Codex proposal."""
    critiques: list[Critique]
    iteration: int
    overall_assessment: str
    ready_to_accept: bool
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def blocking_issues(self) -> list[Critique]:
        return [c for c in self.critiques if c.severity == "blocking"]

    @property
    def has_blocking_issues(self) -> bool:
        return len(self.blocking_issues) > 0


@dataclass
class ConsensusResult:
    """Final result of the iterative collaboration."""
    state: ConsensusState
    final_proposal: str
    iterations: int
    history: list[dict[str, Any]]
    agreed_points: list[str]
    remaining_disagreements: list[str]
    skills_employed: list[str]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "final_proposal": self.final_proposal,
            "iterations": self.iterations,
            "history": self.history,
            "agreed_points": self.agreed_points,
            "remaining_disagreements": self.remaining_disagreements,
            "skills_employed": self.skills_employed,
            "timestamp": self.timestamp,
        }


# =============================================================================
# FORENSIC ENGINEER WORKFLOW TYPES
# =============================================================================

class ForensicPhase(str, Enum):
    """Forensic Engineer workflow phases."""
    ANALYZE = "analyze"   # Iteration 0: Forensic Audit & Validation
    PLAN = "plan"         # Iteration 1: Strategy Refinement
    EXECUTE = "execute"   # Iteration 2: Step-by-Step Execution
    VERIFY = "verify"     # Iteration 3: Final Verification


@dataclass
class ForensicIteration:
    """A single forensic workflow iteration."""
    phase: ForensicPhase
    iteration_number: int  # 0, 1, 2, 3
    objective: str
    codex_response: Optional[str]
    claude_critique: Optional[CritiqueResponse]
    findings: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    docs_updated: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "iteration_number": self.iteration_number,
            "objective": self.objective,
            "codex_response": self.codex_response,
            "claude_critique": {
                "critiques": [c.__dict__ for c in self.claude_critique.critiques],
                "overall_assessment": self.claude_critique.overall_assessment,
                "ready_to_accept": self.claude_critique.ready_to_accept,
            } if self.claude_critique else None,
            "findings": self.findings,
            "decisions": self.decisions,
            "docs_updated": self.docs_updated,
            "timestamp": self.timestamp,
        }


@dataclass
class ForensicResult:
    """Result of complete forensic workflow."""
    task: str
    iterations: list[ForensicIteration]
    final_state: ConsensusState
    agreed_points: list[str]
    remaining_issues: list[str]
    forensic_docs_path: Optional[str]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "iterations": [i.to_dict() for i in self.iterations],
            "final_state": self.final_state.value,
            "agreed_points": self.agreed_points,
            "remaining_issues": self.remaining_issues,
            "forensic_docs_path": self.forensic_docs_path,
            "timestamp": self.timestamp,
        }


# System prompt for Codex to understand its role and use skills
CODEX_SYSTEM_PROMPT = """You are a coding collaborator working with Claude Code on the Discovery Engine project.
Your role is to propose solutions that Claude will critically evaluate.

## Your Capabilities
- Use existing Codex skills when they help (e.g., /edit, /review, /test)
- Create new skills if a reusable pattern emerges
- Read files in sandbox mode to understand context
- Propose concrete, implementable solutions

## Skills Instructions
- When a task involves code modification, use relevant skills
- If you identify a reusable workflow, create a skill for it
- Document any skills you create with clear usage instructions

## Response Format
Structure your proposals clearly:
1. **Approach**: Your proposed solution
2. **Implementation**: Specific steps/code
3. **Rationale**: Why this approach
4. **Risks**: What could go wrong
5. **Skills Used**: List any skills employed
6. **Skills Created**: If you created any new skills

## Context
This is a VC deal sourcing system. Focus areas:
- Consumer companies (CPG, Health Tech, Travel, Marketplaces)
- Pre-Seed to Series A stage
- Exclude: B2B, enterprise, crypto, hardware

Be specific and actionable. Claude will critique your proposals for:
- Feasibility: Will this actually work?
- Efficiency: Is there a simpler way?
- Sophistication: Edge cases and robustness"""


class Maestro:
    """
    Iterative consensus orchestrator for Claude + Codex/Kimi collaboration.

    Implements the Maestro pattern where Claude critically evaluates
    Codex or Kimi proposals until reaching consensus or max iterations.

    Supports two LLM backends with smart selection:
    - Codex CLI (default): Sandbox-isolated, uses ChatGPT Pro subscription
    - Kimi: CLI-backed shared generation wrapper

    Kimi Modes:
    - auto: Smart selection based on context size (>=5 files or >=20K tokens)
    - always: Always use Kimi
    - never: Never use Kimi (Codex only)
    - dual: Run both for ANALYZE phase, synthesize findings
    """

    def __init__(
        self,
        max_iterations: int = 5,
        sandbox_mode: str = "read-only",
        auto_accept_threshold: int = 0,  # Accept if no blocking issues after N iterations
        kimi_mode: KimiMode = KimiMode.AUTO,  # Smart Kimi selection mode
        use_kimi: bool = False,  # DEPRECATED: Use kimi_mode instead
    ):
        """
        Initialize Maestro orchestrator.

        Args:
            max_iterations: Maximum critique iterations before forcing decision
            sandbox_mode: Codex sandbox isolation level
            auto_accept_threshold: Auto-accept after N iterations with no blocking issues
            kimi_mode: How to use Kimi (auto, always, never, dual)
            use_kimi: DEPRECATED - kept for backwards compatibility, use kimi_mode
        """
        self.max_iterations = max_iterations
        self.sandbox_mode = sandbox_mode
        self.auto_accept_threshold = auto_accept_threshold

        # Handle backwards compatibility: use_kimi=True maps to kimi_mode=ALWAYS
        if use_kimi and kimi_mode == KimiMode.AUTO:
            self.kimi_mode = KimiMode.ALWAYS
        else:
            self.kimi_mode = kimi_mode

        self._codex = None
        self._kimi = None
        self._history: list[dict[str, Any]] = []
        self._kimi_used_this_session = False

    @property
    def codex(self):
        """Lazy-load Codex CLI wrapper with default model config (gpt5-2, high reasoning)."""
        if self._codex is None:
            from .codex_wrapper import CodexCLI, SandboxMode, DEFAULT_MODEL, DEFAULT_REASONING_LEVEL
            self._codex = CodexCLI(
                sandbox_mode=SandboxMode(self.sandbox_mode),
                model=DEFAULT_MODEL,
                reasoning_level=DEFAULT_REASONING_LEVEL,
            )
        return self._codex

    @property
    def kimi(self):
        """Lazy-load Kimi client for forensic workflow."""
        if self._kimi is None:
            from .llm_cli import KimiCLIClient
            self._kimi = KimiCLIClient()
        return self._kimi

    def _estimate_context_size(
        self,
        context_files: Optional[list[str]] = None,
        context_text: Optional[str] = None,
    ) -> tuple[int, int]:
        """
        Estimate context size for smart Kimi selection.

        Returns:
            Tuple of (file_count, estimated_tokens)
        """
        file_count = len(context_files) if context_files else 0
        estimated_tokens = 0

        for file_path in (context_files or []):
            if os.path.exists(file_path):
                try:
                    size = os.path.getsize(file_path)
                    # Rough estimate: 4 chars per token
                    estimated_tokens += size // 4
                except OSError:
                    pass

        # Count context_text tokens (critical fix for text-only routing)
        if context_text:
            estimated_tokens += len(context_text) // 4

        return file_count, estimated_tokens

    def _should_use_kimi(
        self,
        context_files: Optional[list[str]],
        phase: Optional[ForensicPhase] = None,
        context_text: Optional[str] = None,
    ) -> bool:
        """
        Determine if Kimi should be used based on mode and context.

        Auto-selection rules:
        - Use Kimi if >= 5 context files
        - Use Kimi if >= 20K estimated tokens
        - Always use Kimi for ANALYZE phase in dual mode

        Args:
            context_files: Files to be analyzed
            phase: Current forensic phase (for dual mode)
            context_text: Raw text context to include in token estimation

        Returns:
            True if Kimi should be used
        """
        if self.kimi_mode == KimiMode.ALWAYS:
            return True

        if self.kimi_mode == KimiMode.NEVER:
            return False

        if self.kimi_mode == KimiMode.DUAL:
            # In dual mode, use Kimi for ANALYZE phase
            return phase == ForensicPhase.ANALYZE

        # AUTO mode: smart selection based on context size
        file_count, estimated_tokens = self._estimate_context_size(context_files, context_text)

        should_use = (
            file_count >= KIMI_AUTO_FILE_THRESHOLD or
            estimated_tokens >= KIMI_AUTO_TOKEN_THRESHOLD
        )

        if should_use:
            logger.info(
                f"Auto-selecting Kimi: {file_count} files, ~{estimated_tokens:,} tokens "
                f"(thresholds: {KIMI_AUTO_FILE_THRESHOLD} files, {KIMI_AUTO_TOKEN_THRESHOLD:,} tokens)"
            )

        return should_use

    def _get_backend_for_phase(
        self,
        phase: ForensicPhase,
        context_files: Optional[list[str]],
        context_text: Optional[str] = None,
    ):
        """Get the appropriate backend for a forensic phase."""
        use_kimi = self._should_use_kimi(context_files, phase, context_text)

        if use_kimi:
            self._kimi_used_this_session = True
            return self.kimi, "Kimi"
        else:
            return self.codex, "Codex"

    @property
    def llm_backend(self):
        """Get the active LLM backend. DEPRECATED: Use _get_backend_for_phase instead."""
        # Kept for backwards compatibility with collaborate()
        if self.kimi_mode == KimiMode.ALWAYS:
            return self.kimi
        return self.codex

    @property
    def backend_name(self) -> str:
        """Get the name of the active backend."""
        if self.kimi_mode == KimiMode.ALWAYS:
            return "Kimi"
        elif self.kimi_mode == KimiMode.NEVER:
            return "Codex"
        elif self.kimi_mode == KimiMode.DUAL:
            return "Dual (Kimi+Codex)"
        else:
            return "Auto (context-dependent)"

    async def collaborate(
        self,
        task: str,
        context: str,
        context_files: Optional[list[str]] = None,
        critique_callback: Optional[callable] = None,
    ) -> ConsensusResult:
        """
        Run iterative collaboration with Codex.

        Args:
            task: The task/goal to accomplish
            context: Current state, constraints, observations
            context_files: Optional files to provide as context
            critique_callback: Optional callback(proposal, iteration) for custom critique

        Returns:
            ConsensusResult with final agreed solution or disagreements
        """
        self._history = []
        skills_employed = []
        iteration = 0

        # Initial prompt to Codex
        prompt = self._build_initial_prompt(task, context)

        while iteration < self.max_iterations:
            iteration += 1
            logger.info(f"Iteration {iteration}/{self.max_iterations}")

            # Get proposal from Codex
            proposal = await self._get_codex_proposal(prompt, context_files)
            self._history.append({
                "type": "proposal",
                "iteration": iteration,
                "content": proposal.content,
                "skills_used": proposal.skills_used,
                "skills_created": proposal.skills_created,
            })

            # Track skills
            skills_employed.extend(proposal.skills_used)
            skills_employed.extend(proposal.skills_created)

            # Generate critique (or use callback)
            if critique_callback:
                critique = await critique_callback(proposal, iteration)
            else:
                critique = self._generate_critique(proposal, iteration)

            self._history.append({
                "type": "critique",
                "iteration": iteration,
                "critiques": [c.__dict__ for c in critique.critiques],
                "overall_assessment": critique.overall_assessment,
                "ready_to_accept": critique.ready_to_accept,
            })

            # Check for consensus
            if critique.ready_to_accept:
                return ConsensusResult(
                    state=ConsensusState.AGREED,
                    final_proposal=proposal.content,
                    iterations=iteration,
                    history=self._history,
                    agreed_points=self._extract_agreed_points(proposal.content),
                    remaining_disagreements=[],
                    skills_employed=list(set(skills_employed)),
                )

            # Auto-accept threshold
            if (self.auto_accept_threshold > 0 and
                iteration >= self.auto_accept_threshold and
                not critique.has_blocking_issues):
                return ConsensusResult(
                    state=ConsensusState.PARTIAL,
                    final_proposal=proposal.content,
                    iterations=iteration,
                    history=self._history,
                    agreed_points=self._extract_agreed_points(proposal.content),
                    remaining_disagreements=[c.issue for c in critique.critiques],
                    skills_employed=list(set(skills_employed)),
                )

            # Build critique prompt for next iteration
            prompt = self._build_critique_prompt(task, context, proposal, critique)

        # Max iterations reached without consensus
        final_proposal = self._history[-2]["content"] if len(self._history) >= 2 else ""
        final_critique = self._history[-1] if self._history else {}

        return ConsensusResult(
            state=ConsensusState.DISAGREED,
            final_proposal=final_proposal,
            iterations=iteration,
            history=self._history,
            agreed_points=[],
            remaining_disagreements=[
                c["issue"] for c in final_critique.get("critiques", [])
            ],
            skills_employed=list(set(skills_employed)),
        )

    async def forensic_collaborate(
        self,
        task: str,
        context: str,
        requirements: str,
        context_files: Optional[list[str]] = None,
        context_text: Optional[str] = None,
        mode_override: Optional[KimiMode] = None,
        docs_path: Optional[str] = None,
    ) -> ForensicResult:
        """
        Run Forensic Engineer workflow with Codex.

        The Forensic Engineer pattern executes 4 structured phases:
        - Iteration 0 (ANALYZE): Forensic audit - validate assumptions against codebase
        - Iteration 1 (PLAN): Strategy refinement - convert to executable steps
        - Iteration 2 (EXECUTE): Step-by-step execution with verification
        - Iteration 3 (VERIFY): Final verification against requirements

        Each phase:
        1. Codex proposes (in sandbox)
        2. Claude critiques (feasibility, efficiency, sophistication)
        3. Iterate until no blocking issues or max sub-iterations reached
        4. Capture findings/decisions before moving to next phase

        Args:
            task: The task/goal to accomplish
            context: Current state, constraints, observations
            requirements: Success criteria for verification phase
            context_files: Optional files to provide as context
            context_text: Optional raw text context for token estimation
            mode_override: Optional per-request Kimi mode override
            docs_path: Optional path to write forensic documentation

        Returns:
            ForensicResult with all phase iterations and final state
        """
        original_mode = self.kimi_mode
        if mode_override:
            self.kimi_mode = mode_override  # Temporarily set for _get_backend_for_phase

        iterations: list[ForensicIteration] = []
        agreed_points: list[str] = []
        remaining_issues: list[str] = []

        # Track accumulated findings across phases
        accumulated_findings: list[str] = []
        accumulated_decisions: list[str] = []
        implementation_summary: list[str] = []

        logger.info(f"Starting Forensic Engineer workflow for: {task}")
        logger.info(f"Kimi mode: {self.kimi_mode.value}")

        # Estimate context size for auto-selection logging
        file_count, est_tokens = self._estimate_context_size(context_files)
        logger.info(f"Context: {file_count} files, ~{est_tokens:,} estimated tokens")

        # =================================================================
        # ITERATION 0: ANALYZE - Forensic Audit & Validation
        # =================================================================
        analyze_backend, analyze_backend_name = self._get_backend_for_phase(
            ForensicPhase.ANALYZE, context_files, context_text
        )
        logger.info(f"Phase 0: ANALYZE - Forensic Audit (using {analyze_backend_name})")

        analyze_iteration = await self._run_forensic_phase(
            phase=ForensicPhase.ANALYZE,
            iteration_number=0,
            objective="Validate assumptions against actual codebase state",
            codex_method=lambda: analyze_backend.analyze(task, context_files),
            context_files=context_files,
        )
        iterations.append(analyze_iteration)

        # Extract findings for next phase
        if analyze_iteration.codex_response:
            accumulated_findings.extend(analyze_iteration.findings)
            # Parse findings from response for plan phase
            findings_text = analyze_iteration.codex_response
        else:
            findings_text = "No findings from analyze phase"
            remaining_issues.append("Analyze phase failed to produce findings")

        # Check for blocking issues before proceeding
        if analyze_iteration.claude_critique and analyze_iteration.claude_critique.has_blocking_issues:
            logger.warning("Analyze phase has blocking issues - proceeding with caution")

        # =================================================================
        # ITERATION 1: PLAN - Strategy Refinement
        # =================================================================
        plan_backend, plan_backend_name = self._get_backend_for_phase(
            ForensicPhase.PLAN, context_files, context_text
        )
        logger.info(f"Phase 1: PLAN - Strategy Refinement (using {plan_backend_name})")

        plan_iteration = await self._run_forensic_phase(
            phase=ForensicPhase.PLAN,
            iteration_number=1,
            objective="Convert high-level plan into concrete, executable steps",
            codex_method=lambda: plan_backend.plan(task, findings_text, context_files),
            context_files=context_files,
        )
        iterations.append(plan_iteration)

        # Extract plan for execute phase
        if plan_iteration.codex_response:
            accumulated_decisions.extend(plan_iteration.decisions)
            plan_text = plan_iteration.codex_response
        else:
            plan_text = "No plan generated"
            remaining_issues.append("Plan phase failed to produce executable steps")

        # =================================================================
        # ITERATION 2: EXECUTE - Step-by-Step Execution
        # =================================================================
        execute_backend, execute_backend_name = self._get_backend_for_phase(
            ForensicPhase.EXECUTE, context_files, context_text
        )
        logger.info(f"Phase 2: EXECUTE - Step-by-Step Execution (using {execute_backend_name})")

        # For execute phase, we pass the plan as context
        execute_iteration = await self._run_forensic_phase(
            phase=ForensicPhase.EXECUTE,
            iteration_number=2,
            objective="Execute plan steps safely with verification",
            codex_method=lambda: execute_backend.execute(
                step=f"Execute the plan for: {task}",
                plan_context=plan_text,
                context_files=context_files,
            ),
            context_files=context_files,
        )
        iterations.append(execute_iteration)

        # Capture implementation for verify phase
        if execute_iteration.codex_response:
            implementation_summary.append(execute_iteration.codex_response)
        else:
            remaining_issues.append("Execute phase failed to produce implementation")

        # =================================================================
        # ITERATION 3: VERIFY - Final Verification
        # =================================================================
        verify_backend, verify_backend_name = self._get_backend_for_phase(
            ForensicPhase.VERIFY, context_files, context_text
        )
        logger.info(f"Phase 3: VERIFY - Final Verification (using {verify_backend_name})")

        impl_summary = "\n\n".join(implementation_summary) if implementation_summary else "No implementation captured"

        verify_iteration = await self._run_forensic_phase(
            phase=ForensicPhase.VERIFY,
            iteration_number=3,
            objective="Verify implementation meets all requirements",
            codex_method=lambda: verify_backend.verify(
                task=task,
                implementation_summary=impl_summary,
                requirements=requirements,
            ),
            context_files=context_files,
        )
        iterations.append(verify_iteration)

        # Extract final verification results
        if verify_iteration.codex_response:
            # Parse agreed points from verification
            agreed_points = self._extract_agreed_points(verify_iteration.codex_response)

        if verify_iteration.claude_critique:
            remaining_issues.extend([
                c.issue for c in verify_iteration.claude_critique.critiques
                if c.severity in ("blocking", "important")
            ])

        # =================================================================
        # DETERMINE FINAL STATE
        # =================================================================
        blocking_count = sum(
            1 for it in iterations
            if it.claude_critique and it.claude_critique.has_blocking_issues
        )

        if blocking_count == 0 and not remaining_issues:
            final_state = ConsensusState.AGREED
        elif blocking_count == 0:
            final_state = ConsensusState.PARTIAL
        else:
            final_state = ConsensusState.DISAGREED

        logger.info(f"Forensic workflow complete: {final_state.value} ({len(iterations)} phases)")

        # Write documentation if path provided
        if docs_path:
            self._write_forensic_docs(docs_path, task, iterations, final_state)

        # Restore original mode if it was overridden
        if mode_override:
            self.kimi_mode = original_mode

        return ForensicResult(
            task=task,
            iterations=iterations,
            final_state=final_state,
            agreed_points=agreed_points,
            remaining_issues=remaining_issues,
            forensic_docs_path=docs_path,
        )

    async def _run_forensic_phase(
        self,
        phase: ForensicPhase,
        iteration_number: int,
        objective: str,
        codex_method: callable,
        context_files: Optional[list[str]] = None,
        max_sub_iterations: int = 3,
    ) -> ForensicIteration:
        """
        Run a single forensic phase with critique loop.

        Args:
            phase: Which forensic phase (ANALYZE, PLAN, EXECUTE, VERIFY)
            iteration_number: Phase number (0-3)
            objective: What this phase aims to achieve
            codex_method: Async callable that invokes the Codex forensic method
            context_files: Files for context
            max_sub_iterations: Max critique loops within this phase

        Returns:
            ForensicIteration with phase results
        """
        findings: list[str] = []
        decisions: list[str] = []
        docs_updated: list[str] = []

        codex_response: Optional[str] = None
        claude_critique: Optional[CritiqueResponse] = None

        for sub_iter in range(max_sub_iterations):
            logger.debug(f"  {phase.value} sub-iteration {sub_iter + 1}/{max_sub_iterations}")

            # Get Codex's response for this phase
            response = await codex_method()

            if not response.success:
                logger.error(f"Codex error in {phase.value}: {response.error}")
                codex_response = f"Error: {response.error}"
                break

            codex_response = response.content

            # Generate Claude's critique
            proposal = Proposal(content=codex_response, iteration=sub_iter)
            claude_critique = self._generate_forensic_critique(proposal, phase, sub_iter)

            # Extract findings and decisions from response
            findings.extend(self._extract_findings(codex_response))
            decisions.extend(self._extract_decisions(codex_response))

            # Check if we can proceed (no blocking issues)
            if not claude_critique.has_blocking_issues:
                logger.debug(f"  {phase.value} approved after {sub_iter + 1} sub-iterations")
                break

            # If blocking issues remain and not last iteration, log and continue
            if sub_iter < max_sub_iterations - 1:
                logger.debug(f"  {phase.value} has blocking issues, iterating...")

        return ForensicIteration(
            phase=phase,
            iteration_number=iteration_number,
            objective=objective,
            codex_response=codex_response,
            claude_critique=claude_critique,
            findings=findings,
            decisions=decisions,
            docs_updated=docs_updated,
        )

    def _generate_forensic_critique(
        self,
        proposal: Proposal,
        phase: ForensicPhase,
        sub_iteration: int,
    ) -> CritiqueResponse:
        """
        Generate phase-specific critique for forensic workflow.

        Each phase has different critique priorities:
        - ANALYZE: Focus on accuracy and completeness of findings
        - PLAN: Focus on feasibility and specificity of steps
        - EXECUTE: Focus on safety and verification
        - VERIFY: Focus on requirement coverage
        """
        critiques = []
        content = proposal.content.lower()

        # Phase-specific checks
        if phase == ForensicPhase.ANALYZE:
            # Analyze phase: check for ground truth verification
            if "file:" not in content and "line" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.CORRECTNESS,
                    issue="Findings lack specific file:line references",
                    severity="important",
                    suggestion="Include exact file paths and line numbers for each finding",
                ))
            if "assumption" not in content and "verify" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.SOPHISTICATION,
                    issue="No explicit assumption validation",
                    severity="important",
                    suggestion="List assumptions and verify each against codebase",
                ))

        elif phase == ForensicPhase.PLAN:
            # Plan phase: check for actionable steps
            if "step" not in content and "phase" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.FEASIBILITY,
                    issue="Plan lacks clear step breakdown",
                    severity="blocking",
                    suggestion="Break into numbered steps with specific actions",
                ))
            if "verify" not in content and "test" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.SOPHISTICATION,
                    issue="No verification steps defined",
                    severity="important",
                    suggestion="Add verification command for each step",
                ))

        elif phase == ForensicPhase.EXECUTE:
            # Execute phase: check for safety
            if "precondition" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.FEASIBILITY,
                    issue="No precondition checks mentioned",
                    severity="important",
                    suggestion="Verify preconditions before each change",
                ))
            if "diff" not in content and "change" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.CORRECTNESS,
                    issue="No specific changes documented",
                    severity="blocking",
                    suggestion="Show exact diff or change description",
                ))

        elif phase == ForensicPhase.VERIFY:
            # Verify phase: check for requirement coverage
            if "requirement" not in content and "[x]" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.CORRECTNESS,
                    issue="No requirement checklist provided",
                    severity="blocking",
                    suggestion="List each requirement with verification status",
                ))
            if "regression" not in content and "test" not in content:
                critiques.append(Critique(
                    category=CritiqueCategory.SOPHISTICATION,
                    issue="No regression testing mentioned",
                    severity="important",
                    suggestion="Run existing tests to check for regressions",
                ))

        # Common checks for all phases
        if "todo" in content or "placeholder" in content:
            critiques.append(Critique(
                category=CritiqueCategory.FEASIBILITY,
                issue="Contains TODOs or placeholders",
                severity="blocking",
                suggestion="Complete all sections before proceeding",
            ))

        # Determine readiness
        blocking_count = len([c for c in critiques if c.severity == "blocking"])
        ready = blocking_count == 0

        if blocking_count > 0:
            assessment = f"{phase.value.upper()} has {blocking_count} blocking issue(s)"
        elif critiques:
            assessment = f"{phase.value.upper()} acceptable with {len(critiques)} minor issue(s)"
        else:
            assessment = f"{phase.value.upper()} looks solid"

        return CritiqueResponse(
            critiques=critiques,
            iteration=sub_iteration,
            overall_assessment=assessment,
            ready_to_accept=ready,
        )

    def _extract_findings(self, content: str) -> list[str]:
        """Extract findings from Codex response."""
        findings = []
        lines = content.split('\n')
        in_findings = False

        for line in lines:
            line_lower = line.lower().strip()
            if 'finding' in line_lower or 'ground truth' in line_lower:
                in_findings = True
                continue
            if in_findings and line.strip().startswith(('-', '*', '1', '2', '3')):
                clean = line.strip().lstrip('-*0123456789.) ').strip()
                if clean and len(clean) > 5:
                    findings.append(clean[:300])
            if in_findings and line.strip() == '' and findings:
                in_findings = False

        return findings[:10]

    def _extract_decisions(self, content: str) -> list[str]:
        """Extract decisions from Codex response."""
        decisions = []
        lines = content.split('\n')
        in_decisions = False

        for line in lines:
            line_lower = line.lower().strip()
            if 'decision' in line_lower:
                in_decisions = True
                continue
            if in_decisions and line.strip().startswith(('-', '*', 'D')):
                clean = line.strip().lstrip('-*D0123456789:.) ').strip()
                if clean and len(clean) > 5:
                    decisions.append(clean[:300])
            if in_decisions and line.strip() == '' and decisions:
                in_decisions = False

        return decisions[:10]

    def _write_forensic_docs(
        self,
        docs_path: str,
        task: str,
        iterations: list[ForensicIteration],
        final_state: ConsensusState,
    ) -> None:
        """Write forensic documentation to file."""
        import os
        from datetime import datetime, timezone

        content = f"""# Forensic Engineer Report
Generated: {datetime.now(timezone.utc).isoformat()}

## Task
{task}

## Final State
{final_state.value}

## Phase Summary
"""
        for it in iterations:
            critique_status = "approved" if (it.claude_critique and it.claude_critique.ready_to_accept) else "issues remain"
            content += f"""
### {it.phase.value.upper()} (Iteration {it.iteration_number})
**Objective:** {it.objective}
**Status:** {critique_status}

**Findings:**
"""
            for f in it.findings:
                content += f"- {f}\n"

            content += "\n**Decisions:**\n"
            for d in it.decisions:
                content += f"- {d}\n"

            if it.claude_critique:
                content += f"\n**Critique:** {it.claude_critique.overall_assessment}\n"

        try:
            os.makedirs(os.path.dirname(docs_path), exist_ok=True)
            with open(docs_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Forensic docs written to: {docs_path}")
        except Exception as e:
            logger.error(f"Failed to write forensic docs: {e}")

    def _build_initial_prompt(self, task: str, context: str) -> str:
        """Build the initial prompt for Codex."""
        return f"""{CODEX_SYSTEM_PROMPT}

## Task
{task}

## Current Context
{context}

## Instructions
1. Analyze the task and context
2. Use or create Codex skills where helpful
3. Propose a concrete solution
4. Be specific about implementation details
5. Acknowledge potential risks

Please provide your proposal."""

    def _build_critique_prompt(
        self,
        task: str,
        context: str,
        proposal: Proposal,
        critique: CritiqueResponse,
    ) -> str:
        """Build a prompt incorporating the critique for the next iteration."""
        critique_text = "\n".join([c.to_prompt() for c in critique.critiques])

        return f"""{CODEX_SYSTEM_PROMPT}

## Task
{task}

## Current Context
{context}

## Your Previous Proposal
{proposal.content}

## Claude's Critique
{critique_text}

Overall Assessment: {critique.overall_assessment}

## Instructions for This Iteration
1. Address the blocking issues first
2. Consider the efficiency and sophistication suggestions
3. Revise your proposal to address the critique
4. Use or create skills if they help address the issues
5. Explain what you changed and why

Please provide your revised proposal."""

    async def _get_codex_proposal(
        self,
        prompt: str,
        context_files: Optional[list[str]] = None,
    ) -> Proposal:
        """Get a proposal from Codex."""
        response = await self.codex.exec(
            prompt=prompt,
            context_files=context_files,
        )

        if not response.success:
            logger.error(f"Codex error: {response.error}")
            return Proposal(
                content=f"Error: {response.error}",
                iteration=0,
            )

        # Parse skills from response
        skills_used = self._extract_skills_used(response.content)
        skills_created = self._extract_skills_created(response.content)

        return Proposal(
            content=response.content,
            iteration=0,  # Will be set by caller
            skills_used=skills_used,
            skills_created=skills_created,
        )

    def _generate_critique(self, proposal: Proposal, iteration: int) -> CritiqueResponse:
        """
        Generate Claude's critique of the proposal.

        This is where the critical evaluation happens:
        - Feasibility: Will this actually work?
        - Efficiency: Is there a simpler/faster approach?
        - Sophistication: What edge cases are missed?
        """
        critiques = []
        content = proposal.content.lower()

        # Feasibility checks
        if "todo" in content or "placeholder" in content:
            critiques.append(Critique(
                category=CritiqueCategory.FEASIBILITY,
                issue="Proposal contains TODOs or placeholders - incomplete implementation",
                severity="blocking",
                suggestion="Provide complete implementation details",
            ))

        if "assume" in content and "api" in content:
            critiques.append(Critique(
                category=CritiqueCategory.FEASIBILITY,
                issue="Makes assumptions about API availability without verification",
                severity="important",
                suggestion="Verify API exists and document rate limits/auth requirements",
            ))

        # Efficiency checks
        if "for each" in content and "api call" in content:
            critiques.append(Critique(
                category=CritiqueCategory.EFFICIENCY,
                issue="Suggests individual API calls in a loop - potential N+1 problem",
                severity="important",
                suggestion="Consider batch API calls or pagination",
            ))

        # Sophistication checks
        if "error" not in content and "exception" not in content:
            critiques.append(Critique(
                category=CritiqueCategory.SOPHISTICATION,
                issue="No error handling mentioned",
                severity="important",
                suggestion="Add error handling for API failures, rate limits, invalid data",
            ))

        if "test" not in content:
            critiques.append(Critique(
                category=CritiqueCategory.SOPHISTICATION,
                issue="No testing strategy mentioned",
                severity="minor",
                suggestion="Include unit test approach or validation strategy",
            ))

        # Determine if ready to accept
        blocking_count = len([c for c in critiques if c.severity == "blocking"])
        important_count = len([c for c in critiques if c.severity == "important"])

        ready_to_accept = blocking_count == 0 and (
            important_count == 0 or iteration >= 3
        )

        # Generate overall assessment
        if blocking_count > 0:
            assessment = f"Proposal has {blocking_count} blocking issue(s) that must be addressed."
        elif important_count > 0:
            assessment = f"Proposal is workable but has {important_count} important issue(s) to consider."
        else:
            assessment = "Proposal looks solid. Minor improvements suggested but not required."

        return CritiqueResponse(
            critiques=critiques,
            iteration=iteration,
            overall_assessment=assessment,
            ready_to_accept=ready_to_accept,
        )

    def _extract_skills_used(self, content: str) -> list[str]:
        """Extract skill names used from response content."""
        skills = []
        # Look for skill references like /edit, /review, etc.
        import re
        matches = re.findall(r'/(\w+)', content)
        for match in matches:
            if match in ['edit', 'review', 'test', 'search', 'explain']:
                skills.append(match)
        return list(set(skills))

    def _extract_skills_created(self, content: str) -> list[str]:
        """Extract any new skills created from response content."""
        skills = []
        if "skills created" in content.lower():
            # Parse the skills created section
            import re
            section = re.search(
                r'skills created[:\s]*(.+?)(?:\n\n|\Z)',
                content,
                re.IGNORECASE | re.DOTALL
            )
            if section:
                # Extract skill names
                names = re.findall(r'[`/](\w+)[`]?', section.group(1))
                skills.extend(names)
        return list(set(skills))

    def _extract_agreed_points(self, content: str) -> list[str]:
        """Extract key agreed points from the final proposal."""
        points = []
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            # Look for numbered or bulleted items
            if line and (line[0].isdigit() or line[0] in '-*'):
                # Clean up the line
                clean = line.lstrip('0123456789.-*) ').strip()
                if clean and len(clean) > 10:
                    points.append(clean[:200])  # Truncate long points
        return points[:10]  # Limit to 10 points


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def quick_collaborate(task: str, context: str) -> ConsensusResult:
    """
    Quick collaboration with default settings.

    Args:
        task: Task description
        context: Current context

    Returns:
        ConsensusResult
    """
    maestro = Maestro()
    return await maestro.collaborate(task, context)


async def review_with_consensus(
    file_path: str,
    focus: Optional[str] = None,
) -> ConsensusResult:
    """
    Review a file with iterative consensus.

    Args:
        file_path: Path to file to review
        focus: Optional focus area for review

    Returns:
        ConsensusResult with review findings
    """
    task = f"Review {file_path}"
    if focus:
        task += f" with focus on {focus}"

    context = f"File to review: {file_path}"

    maestro = Maestro(max_iterations=3)
    return await maestro.collaborate(
        task=task,
        context=context,
        context_files=[file_path] if os.path.exists(file_path) else None,
    )


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """CLI interface for Maestro orchestrator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Maestro: Iterative Claude + Codex/Kimi Collaboration"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Collaborate command
    collab_parser = subparsers.add_parser("collaborate", help="Run collaboration")
    collab_parser.add_argument("task", help="Task description")
    collab_parser.add_argument("--context", required=True, help="Context/constraints")
    collab_parser.add_argument("--max-iterations", type=int, default=5)
    collab_parser.add_argument("--files", nargs="*", help="Context files")
    collab_parser.add_argument(
        "--kimi-mode",
        choices=["auto", "always", "never", "dual"],
        default="auto",
        help="Kimi usage mode: auto (smart selection), always, never, dual (both for analyze)"
    )

    # Review command
    review_parser = subparsers.add_parser("review", help="Review a file")
    review_parser.add_argument("file", help="File to review")
    review_parser.add_argument("--focus", help="Focus area")

    # Forensic command
    forensic_parser = subparsers.add_parser(
        "forensic",
        help="Run Forensic Engineer workflow (4-phase structured collaboration)"
    )
    forensic_parser.add_argument("task", help="Task description")
    forensic_parser.add_argument("--context", required=True, help="Context/constraints")
    forensic_parser.add_argument(
        "--requirements",
        required=True,
        help="Success criteria for verification"
    )
    forensic_parser.add_argument("--files", nargs="*", help="Context files")
    forensic_parser.add_argument(
        "--docs",
        help="Path to write forensic documentation (e.g., docs/forensic-report.md)"
    )
    forensic_parser.add_argument(
        "--kimi-mode",
        choices=["auto", "always", "never", "dual"],
        default="auto",
        help="Kimi usage mode: auto (smart selection), always, never, dual (both for analyze)"
    )

    # Budget command
    budget_parser = subparsers.add_parser(
        "budget",
        help="Show legacy Kimi API budget status",
    )
    budget_parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset daily budget counter (use with caution)"
    )

    args = parser.parse_args()

    async def run():
        if args.command == "collaborate":
            kimi_mode = KimiMode(getattr(args, 'kimi_mode', 'auto'))
            maestro = Maestro(max_iterations=args.max_iterations, kimi_mode=kimi_mode)
            result = await maestro.collaborate(
                task=args.task,
                context=args.context,
                context_files=args.files,
            )
            print(json.dumps(result.to_dict(), indent=2))

        elif args.command == "review":
            result = await review_with_consensus(args.file, args.focus)
            print(json.dumps(result.to_dict(), indent=2))

        elif args.command == "budget":
            from .kimi_client import get_budget_status, _load_budget, _save_budget
            status = get_budget_status()
            print("Legacy Kimi API Budget Status")
            print(f"{'='*40}")
            print(f"Daily tokens:   {status['daily_tokens']:>10,} / {status['daily_limit']:,}")
            print(f"Daily remaining:{status['daily_remaining']:>10,} ({100-status['daily_percent']:.1f}%)")
            print(f"Monthly tokens: {status['monthly_tokens']:>10,}")
            print(f"Request count:  {status['request_count']:>10}")
            if status['warning']:
                print(f"\n[WARNING] Daily usage at {status['daily_percent']:.1f}% - consider using Codex")
            if getattr(args, 'reset', False):
                budget = _load_budget()
                budget.daily_tokens = 0
                _save_budget()
                print("\n[RESET] Daily budget counter reset to 0")

        elif args.command == "forensic":
            kimi_mode = KimiMode(getattr(args, 'kimi_mode', 'auto'))
            maestro = Maestro(kimi_mode=kimi_mode)

            # Show budget warning if applicable
            try:
                from .kimi_client import get_budget_status
                status = get_budget_status()
                if status['warning']:
                    print(
                        f"[LEGACY API BUDGET WARNING] "
                        f"Kimi at {status['daily_percent']:.1f}% daily limit"
                    )
                    print(f"                 {status['daily_remaining']:,} tokens remaining")
                    print()
            except Exception:
                pass

            print("Starting Forensic Engineer workflow...")
            print(f"  Task: {args.task}")
            print(f"  Kimi mode: {kimi_mode.value}")
            print("  Phases: ANALYZE -> PLAN -> EXECUTE -> VERIFY")
            print()

            result = await maestro.forensic_collaborate(
                task=args.task,
                context=args.context,
                requirements=args.requirements,
                context_files=args.files,
                docs_path=args.docs,
            )

            # Print phase summary
            print(f"\n{'='*60}")
            print("FORENSIC WORKFLOW COMPLETE")
            print(f"{'='*60}")
            print(f"Final State: {result.final_state.value}")
            print(f"Phases Completed: {len(result.iterations)}")

            for it in result.iterations:
                status = "OK" if (it.claude_critique and it.claude_critique.ready_to_accept) else "ISSUES"
                print(f"  [{status}] {it.phase.value.upper()}: {it.objective[:50]}...")

            if result.agreed_points:
                print("\nAgreed Points:")
                for p in result.agreed_points[:5]:
                    print(f"  - {p[:80]}...")

            if result.remaining_issues:
                print("\nRemaining Issues:")
                for i in result.remaining_issues[:5]:
                    print(f"  - {i[:80]}...")

            if args.docs:
                print(f"\nDocumentation written to: {args.docs}")

            # Also output full JSON for programmatic use
            print(f"\n{'='*60}")
            print("FULL RESULT (JSON):")
            print(json.dumps(result.to_dict(), indent=2))

        else:
            parser.print_help()

    asyncio.run(run())


if __name__ == "__main__":
    main()
