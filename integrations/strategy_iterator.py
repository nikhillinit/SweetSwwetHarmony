"""
Strategy Iterator: Multi-LLM Consensus for Thesis Development.

This module orchestrates strategy iteration between Claude and OpenAI/Codex,
using consensus patterns to reduce hallucinations and improve decision quality.

Architecture (inspired by PR #267 ConsensusOrchestrator):
- Multiple LLM perspectives gathered independently
- Agreement areas identified with confidence scoring
- Disagreements highlighted with reasoning
- Consensus synthesis for final recommendations

Usage:
    from integrations.strategy_iterator import StrategyIterator

    iterator = StrategyIterator()

    # Thesis refinement session
    result = await iterator.refine_thesis(
        current_metrics={
            "false_positive_rate": 0.30,
            "signals_per_day": 45,
            "thesis_fit_rate": 0.65,
        },
        observations=[
            "Many GitHub signals are B2B developer tools",
            "Consumer health signals have high conversion",
        ]
    )

    # Collector evaluation
    result = await iterator.evaluate_collector(
        collector_name="wellfound",
        research_notes="API deprecated in 2023, scraping violates ToS"
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

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger("strategy-iterator")


class ConsensusLevel(str, Enum):
    """Level of agreement between LLMs."""
    STRONG = "strong"  # Both agree with high confidence
    MODERATE = "moderate"  # General agreement, minor differences
    SPLIT = "split"  # Significant disagreement
    INCONCLUSIVE = "inconclusive"  # One or both couldn't provide clear answer


@dataclass
class LLMPerspective:
    """A perspective from a single LLM."""
    source: str  # "claude" or "openai"
    content: str
    confidence: float  # 0-1 confidence in own answer
    key_points: list[str]
    recommendations: list[str]
    concerns: list[str]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "content": self.content,
            "confidence": self.confidence,
            "key_points": self.key_points,
            "recommendations": self.recommendations,
            "concerns": self.concerns,
            "timestamp": self.timestamp,
        }


@dataclass
class ConsensusResult:
    """Result of multi-LLM consensus process."""
    question: str
    consensus_level: ConsensusLevel
    agreement_areas: list[str]
    disagreement_areas: list[str]
    synthesized_recommendation: str
    claude_perspective: LLMPerspective
    openai_perspective: Optional[LLMPerspective]
    confidence_score: float  # Overall confidence in consensus
    action_items: list[str]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "consensus_level": self.consensus_level.value,
            "agreement_areas": self.agreement_areas,
            "disagreement_areas": self.disagreement_areas,
            "synthesized_recommendation": self.synthesized_recommendation,
            "claude_perspective": self.claude_perspective.to_dict(),
            "openai_perspective": self.openai_perspective.to_dict() if self.openai_perspective else None,
            "confidence_score": self.confidence_score,
            "action_items": self.action_items,
            "timestamp": self.timestamp,
        }


class StrategyIterator:
    """
    Multi-LLM strategy iteration orchestrator.

    Combines Claude and OpenAI/Codex perspectives for:
    - Thesis refinement
    - Collector evaluation
    - Signal strategy optimization
    - Architecture decisions

    Uses consensus patterns from PR #267 to reduce hallucinations
    and improve decision quality through multi-model validation.
    """

    def __init__(
        self,
        enable_openai: bool = True,
        enable_codex: bool = True,
        openai_api_key: Optional[str] = None,
    ):
        """
        Initialize strategy iterator.

        Args:
            enable_openai: Enable OpenAI API integration
            enable_codex: Enable Codex CLI integration
            openai_api_key: Optional API key (uses env var if not provided)
        """
        self.enable_openai = enable_openai
        self.enable_codex = enable_codex

        self._openai_client = None
        self._codex_client = None

        if enable_openai:
            try:
                from .openai_mcp import OpenAIMCPServer
                self._openai_client = OpenAIMCPServer(api_key=openai_api_key)
            except (ImportError, ValueError) as e:
                logger.warning(f"OpenAI integration unavailable: {e}")
                self.enable_openai = False

        if enable_codex:
            try:
                from .codex_wrapper import CodexCLI
                self._codex_client = CodexCLI()
                if not self._codex_client.is_installed():
                    logger.warning("Codex CLI not installed")
                    self.enable_codex = False
            except Exception as e:
                logger.warning(f"Codex integration unavailable: {e}")
                self.enable_codex = False

    def has_multi_llm(self) -> bool:
        """Check if multi-LLM consensus is available."""
        return self.enable_openai or self.enable_codex

    async def refine_thesis(
        self,
        current_metrics: dict[str, float],
        observations: list[str],
        specific_question: Optional[str] = None,
    ) -> ConsensusResult:
        """
        Iterate on investment thesis based on signal performance.

        Args:
            current_metrics: Current performance metrics (e.g., false_positive_rate)
            observations: Qualitative observations about signal quality
            specific_question: Optional specific question to focus on

        Returns:
            ConsensusResult with thesis refinement recommendations
        """
        # Format context
        metrics_str = "\n".join(f"- {k}: {v}" for k, v in current_metrics.items())
        observations_str = "\n".join(f"- {obs}" for obs in observations)

        context = f"""## Current Performance Metrics
{metrics_str}

## Observations
{observations_str}

## Investment Thesis Context
- Focus: Consumer companies (Pre-Seed to Series A)
- Categories: Consumer CPG, Consumer Health Tech, Travel & Hospitality, Consumer Marketplaces
- Exclusions: B2B/Enterprise, crypto, cleantech, hardware-only
"""

        question = specific_question or "How should we refine the thesis matching criteria to improve signal quality?"

        return await self._get_consensus(
            question=question,
            context=context,
            task_type="thesis_refinement",
        )

    async def evaluate_collector(
        self,
        collector_name: str,
        research_notes: str,
        proposed_implementation: Optional[str] = None,
    ) -> ConsensusResult:
        """
        Evaluate whether to build a new collector.

        Args:
            collector_name: Name of proposed collector
            research_notes: Research findings about the data source
            proposed_implementation: Optional implementation approach

        Returns:
            ConsensusResult with BUILD/DEFER/ABANDON recommendation
        """
        context = f"""## Proposed Collector: {collector_name}

## Research Notes
{research_notes}

{f"## Proposed Implementation" + chr(10) + proposed_implementation if proposed_implementation else ""}

## Decision Framework
Evaluate against:
- API availability and reliability
- Signal quality potential (0.3-1.0 scale)
- Cost-benefit ratio
- Maintenance burden
- Thesis alignment

Recommend: BUILD, DEFER, or ABANDON with specific rationale.
"""

        question = f"Should we build the {collector_name} collector? Provide a clear BUILD/DEFER/ABANDON decision."

        return await self._get_consensus(
            question=question,
            context=context,
            task_type="collector_evaluation",
        )

    async def optimize_signal_flow(
        self,
        current_flow: str,
        bottlenecks: list[str],
        constraints: Optional[list[str]] = None,
    ) -> ConsensusResult:
        """
        Optimize the signal processing pipeline.

        Args:
            current_flow: Description of current signal flow
            bottlenecks: Identified bottlenecks or issues
            constraints: Optional constraints to consider

        Returns:
            ConsensusResult with optimization recommendations
        """
        bottlenecks_str = "\n".join(f"- {b}" for b in bottlenecks)
        constraints_str = "\n".join(f"- {c}" for c in (constraints or []))

        context = f"""## Current Signal Flow
{current_flow}

## Identified Bottlenecks
{bottlenecks_str}

{f"## Constraints" + chr(10) + constraints_str if constraints else ""}
"""

        question = "How can we optimize the signal processing pipeline to address these bottlenecks?"

        return await self._get_consensus(
            question=question,
            context=context,
            task_type="signal_optimization",
        )

    async def _get_consensus(
        self,
        question: str,
        context: str,
        task_type: str,
    ) -> ConsensusResult:
        """
        Get multi-LLM consensus on a question.

        Args:
            question: The question to get consensus on
            context: Shared context for all models
            task_type: Type of task for specialized prompting

        Returns:
            ConsensusResult with synthesized recommendations
        """
        # Get Claude's perspective (this is the primary orchestrator)
        claude_perspective = await self._get_claude_perspective(
            question=question,
            context=context,
            task_type=task_type,
        )

        # Get OpenAI/Codex perspective if available
        openai_perspective = None
        if self.enable_openai and self._openai_client:
            openai_perspective = await self._get_openai_perspective(
                question=question,
                context=context,
                claude_perspective=claude_perspective,
                task_type=task_type,
            )
        elif self.enable_codex and self._codex_client:
            openai_perspective = await self._get_codex_perspective(
                question=question,
                context=context,
                claude_perspective=claude_perspective,
                task_type=task_type,
            )

        # Synthesize consensus
        return self._synthesize_consensus(
            question=question,
            claude_perspective=claude_perspective,
            openai_perspective=openai_perspective,
        )

    async def _get_claude_perspective(
        self,
        question: str,
        context: str,
        task_type: str,
    ) -> LLMPerspective:
        """
        Get Claude's perspective (simulated - in practice, this is the calling context).

        In actual usage, Claude Code IS the orchestrator, so this method
        represents structuring Claude's analysis for comparison.
        """
        # This is a structured prompt that would be answered by Claude
        # In practice, the calling code (Claude Code) provides this analysis
        analysis_prompt = f"""Analyze this {task_type} question:

## Context
{context}

## Question
{question}

Provide:
1. Key observations (3-5 bullet points)
2. Specific recommendations (actionable items)
3. Concerns or risks to consider
4. Your confidence level (0-1) in this analysis

Format as JSON with keys: key_points, recommendations, concerns, confidence, summary
"""

        # For now, return a placeholder that will be filled by actual Claude analysis
        return LLMPerspective(
            source="claude",
            content=f"[Claude's analysis of: {question}]",
            confidence=0.8,
            key_points=["Analysis would be provided by Claude Code orchestrator"],
            recommendations=["Recommendations based on codebase context"],
            concerns=["Considerations from Claude's analysis"],
        )

    async def _get_openai_perspective(
        self,
        question: str,
        context: str,
        claude_perspective: LLMPerspective,
        task_type: str,
    ) -> LLMPerspective:
        """Get OpenAI's perspective via API."""
        if not self._openai_client:
            return None

        try:
            response = await self._openai_client.analyze_strategy(
                context=context,
                question=question,
            )

            return LLMPerspective(
                source="openai",
                content=response.content,
                confidence=0.75,  # Default confidence
                key_points=self._extract_points(response.content, "observation"),
                recommendations=self._extract_points(response.content, "recommend"),
                concerns=self._extract_points(response.content, "risk|concern|consider"),
            )
        except Exception as e:
            logger.error(f"OpenAI perspective failed: {e}")
            return None

    async def _get_codex_perspective(
        self,
        question: str,
        context: str,
        claude_perspective: LLMPerspective,
        task_type: str,
    ) -> LLMPerspective:
        """Get Codex's perspective via CLI."""
        if not self._codex_client:
            return None

        try:
            response = await self._codex_client.compare_perspectives(
                question=question,
                claude_perspective=claude_perspective.content,
                context=context,
            )

            if not response.success:
                logger.warning(f"Codex perspective failed: {response.error}")
                return None

            return LLMPerspective(
                source="codex",
                content=response.content,
                confidence=0.75,
                key_points=self._extract_points(response.content, "observation|analysis"),
                recommendations=self._extract_points(response.content, "recommend|suggest"),
                concerns=self._extract_points(response.content, "risk|concern|disagree"),
            )
        except Exception as e:
            logger.error(f"Codex perspective failed: {e}")
            return None

    def _extract_points(self, content: str, pattern: str) -> list[str]:
        """Extract bullet points matching a pattern from content."""
        import re

        points = []
        lines = content.split("\n")

        for i, line in enumerate(lines):
            # Check if line matches pattern
            if re.search(pattern, line.lower()):
                # Look for bullet points in nearby lines
                for j in range(i, min(i + 5, len(lines))):
                    if lines[j].strip().startswith(("-", "*", "•", "1.", "2.", "3.")):
                        point = re.sub(r"^[-*•\d.]+\s*", "", lines[j].strip())
                        if point and len(point) > 10:
                            points.append(point)

        return points[:5]  # Limit to 5 points

    def _synthesize_consensus(
        self,
        question: str,
        claude_perspective: LLMPerspective,
        openai_perspective: Optional[LLMPerspective],
    ) -> ConsensusResult:
        """Synthesize consensus from multiple perspectives."""
        if not openai_perspective:
            # Single-model result (Claude only)
            return ConsensusResult(
                question=question,
                consensus_level=ConsensusLevel.INCONCLUSIVE,
                agreement_areas=claude_perspective.key_points,
                disagreement_areas=[],
                synthesized_recommendation=claude_perspective.content,
                claude_perspective=claude_perspective,
                openai_perspective=None,
                confidence_score=claude_perspective.confidence,
                action_items=claude_perspective.recommendations,
            )

        # Multi-model consensus analysis
        agreement_areas = self._find_agreements(
            claude_perspective.key_points + claude_perspective.recommendations,
            openai_perspective.key_points + openai_perspective.recommendations,
        )

        disagreement_areas = self._find_disagreements(
            claude_perspective,
            openai_perspective,
        )

        # Determine consensus level
        if len(agreement_areas) >= 3 and len(disagreement_areas) <= 1:
            consensus_level = ConsensusLevel.STRONG
            confidence = 0.9
        elif len(agreement_areas) >= 2:
            consensus_level = ConsensusLevel.MODERATE
            confidence = 0.75
        elif len(disagreement_areas) >= 2:
            consensus_level = ConsensusLevel.SPLIT
            confidence = 0.5
        else:
            consensus_level = ConsensusLevel.INCONCLUSIVE
            confidence = 0.4

        # Synthesize recommendation
        synthesized = self._create_synthesis(
            claude_perspective,
            openai_perspective,
            agreement_areas,
            disagreement_areas,
        )

        # Merge action items (deduplicated)
        action_items = list(set(
            claude_perspective.recommendations + openai_perspective.recommendations
        ))

        return ConsensusResult(
            question=question,
            consensus_level=consensus_level,
            agreement_areas=agreement_areas,
            disagreement_areas=disagreement_areas,
            synthesized_recommendation=synthesized,
            claude_perspective=claude_perspective,
            openai_perspective=openai_perspective,
            confidence_score=confidence,
            action_items=action_items[:7],  # Limit to 7 action items
        )

    def _find_agreements(
        self,
        points_a: list[str],
        points_b: list[str],
    ) -> list[str]:
        """Find points of agreement between two sets of points."""
        agreements = []

        for point_a in points_a:
            for point_b in points_b:
                # Simple similarity check (could be enhanced with embeddings)
                a_words = set(point_a.lower().split())
                b_words = set(point_b.lower().split())
                overlap = len(a_words & b_words) / max(len(a_words | b_words), 1)

                if overlap > 0.3:  # 30% word overlap threshold
                    agreements.append(point_a)
                    break

        return list(set(agreements))[:5]

    def _find_disagreements(
        self,
        claude: LLMPerspective,
        openai: LLMPerspective,
    ) -> list[str]:
        """Find areas of disagreement."""
        disagreements = []

        # Check concerns from each that aren't in the other's recommendations
        for concern in claude.concerns:
            if not any(
                self._similar(concern, rec)
                for rec in openai.recommendations
            ):
                disagreements.append(f"Claude concern: {concern}")

        for concern in openai.concerns:
            if not any(
                self._similar(concern, rec)
                for rec in claude.recommendations
            ):
                disagreements.append(f"OpenAI concern: {concern}")

        return disagreements[:5]

    def _similar(self, text_a: str, text_b: str) -> bool:
        """Check if two texts are similar."""
        a_words = set(text_a.lower().split())
        b_words = set(text_b.lower().split())
        overlap = len(a_words & b_words) / max(len(a_words | b_words), 1)
        return overlap > 0.25

    def _create_synthesis(
        self,
        claude: LLMPerspective,
        openai: LLMPerspective,
        agreements: list[str],
        disagreements: list[str],
    ) -> str:
        """Create a synthesized recommendation."""
        synthesis_parts = []

        if agreements:
            synthesis_parts.append("**Consensus Points:**")
            for a in agreements[:3]:
                synthesis_parts.append(f"- {a}")

        if disagreements:
            synthesis_parts.append("\n**Points Requiring Further Analysis:**")
            for d in disagreements[:2]:
                synthesis_parts.append(f"- {d}")

        synthesis_parts.append("\n**Synthesized Recommendation:**")
        synthesis_parts.append(
            "Based on multi-LLM analysis, prioritize consensus areas "
            "while investigating disagreement points before implementation."
        )

        return "\n".join(synthesis_parts)


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """CLI interface for strategy iteration."""
    import argparse

    parser = argparse.ArgumentParser(description="Strategy Iterator")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Thesis command
    thesis_parser = subparsers.add_parser("thesis", help="Refine thesis")
    thesis_parser.add_argument("--question", help="Specific question")

    # Collector command
    collector_parser = subparsers.add_parser("collector", help="Evaluate collector")
    collector_parser.add_argument("name", help="Collector name")
    collector_parser.add_argument("--notes", required=True, help="Research notes")

    args = parser.parse_args()

    async def run():
        iterator = StrategyIterator()

        if args.command == "thesis":
            result = await iterator.refine_thesis(
                current_metrics={"false_positive_rate": 0.30},
                observations=["Example observation"],
                specific_question=args.question,
            )
            print(json.dumps(result.to_dict(), indent=2))

        elif args.command == "collector":
            result = await iterator.evaluate_collector(
                collector_name=args.name,
                research_notes=args.notes,
            )
            print(json.dumps(result.to_dict(), indent=2))

        else:
            parser.print_help()

    asyncio.run(run())


if __name__ == "__main__":
    main()
