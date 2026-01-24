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
    Iterative consensus orchestrator for Claude + Codex collaboration.

    Implements the Maestro pattern where Claude critically evaluates
    Codex proposals until reaching consensus or max iterations.
    """

    def __init__(
        self,
        max_iterations: int = 5,
        sandbox_mode: str = "read-only",
        auto_accept_threshold: int = 0,  # Accept if no blocking issues after N iterations
    ):
        """
        Initialize Maestro orchestrator.

        Args:
            max_iterations: Maximum critique iterations before forcing decision
            sandbox_mode: Codex sandbox isolation level
            auto_accept_threshold: Auto-accept after N iterations with no blocking issues
        """
        self.max_iterations = max_iterations
        self.sandbox_mode = sandbox_mode
        self.auto_accept_threshold = auto_accept_threshold
        self._codex = None
        self._history: list[dict[str, Any]] = []

    @property
    def codex(self):
        """Lazy-load Codex CLI wrapper."""
        if self._codex is None:
            from .codex_wrapper import CodexCLI, SandboxMode
            self._codex = CodexCLI(
                sandbox_mode=SandboxMode(self.sandbox_mode),
            )
        return self._codex

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
        description="Maestro: Iterative Claude + Codex Collaboration"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Collaborate command
    collab_parser = subparsers.add_parser("collaborate", help="Run collaboration")
    collab_parser.add_argument("task", help="Task description")
    collab_parser.add_argument("--context", required=True, help="Context/constraints")
    collab_parser.add_argument("--max-iterations", type=int, default=5)
    collab_parser.add_argument("--files", nargs="*", help="Context files")

    # Review command
    review_parser = subparsers.add_parser("review", help="Review a file")
    review_parser.add_argument("file", help="File to review")
    review_parser.add_argument("--focus", help="Focus area")

    args = parser.parse_args()

    async def run():
        if args.command == "collaborate":
            maestro = Maestro(max_iterations=args.max_iterations)
            result = await maestro.collaborate(
                task=args.task,
                context=args.context,
                context_files=args.files,
            )
            print(json.dumps(result.to_dict(), indent=2))

        elif args.command == "review":
            result = await review_with_consensus(args.file, args.focus)
            print(json.dumps(result.to_dict(), indent=2))

        else:
            parser.print_help()

    asyncio.run(run())


if __name__ == "__main__":
    main()
