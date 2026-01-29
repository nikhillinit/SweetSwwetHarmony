"""
Custom solvers for thesis classification evaluation.

Provides chain-of-thought and self-critique variants for improved classification.
"""

from __future__ import annotations

from inspect_ai.solver import (
    Generate,
    Solver,
    TaskState,
    chain,
    chain_of_thought,
    generate,
    self_critique,
    solver,
    system_message,
)


# =============================================================================
# THESIS SYSTEM PROMPTS
# =============================================================================

THESIS_SYSTEM_PROMPT = """You are a venture capital analyst evaluating early-stage consumer startups.

Your task: Determine if a company matches our investment thesis and classify it.

## Investment Thesis
We invest in PRE-SEED to SERIES A consumer companies:
- Consumer CPG: Food, beverage, snacks, beauty, personal care, household products
- Consumer Health Tech: Fitness apps, wellness, mental health, supplements, wearables
- Travel & Hospitality: Travel booking, hospitality tech, restaurants, experiences
- Consumer Marketplaces: Consumer-facing two-sided markets

## NOT In Thesis (Exclude)
- B2B/Enterprise software
- Developer tools, APIs, infrastructure
- Crypto/Web3/NFT
- Services/Consulting/Agencies
- Late-stage companies (Series B+)
- Hardware-only (no software/data moat)

## Classification Categories
Output one of these classifications:
- **QUALIFIED**: Strong thesis match, clear consumer focus, appropriate stage
- **HELD**: Marginal fit, some consumer elements but unclear or mixed signals
- **REJECTED**: Does not match thesis - B2B, wrong stage, excluded category

## Output Format
After your analysis, output your classification on a final line:
Classification: [QUALIFIED/HELD/REJECTED]
"""

SELF_CRITIQUE_PROMPT = """Review your thesis classification for accuracy.

Check for common mistakes:
1. Did you correctly identify if this is B2B or B2C?
2. Is the company in an excluded category (crypto, services, hardware-only)?
3. Is the stage appropriate (pre-seed to Series A)?
4. Are there clear consumer signals in the description?

If you made an error, provide a corrected classification.
If your original classification was correct, confirm it.

Output your final classification:
Classification: [QUALIFIED/HELD/REJECTED]
"""


# =============================================================================
# SOLVER CHAINS
# =============================================================================

def thesis_basic_solver() -> Solver:
    """
    Basic thesis classification solver.

    Simple prompt → generate pattern.
    """
    return chain(
        system_message(THESIS_SYSTEM_PROMPT),
        generate(),
    )


def thesis_cot_solver() -> Solver:
    """
    Chain-of-thought thesis classification solver.

    Adds "Think step by step" instruction before generation.
    """
    return chain(
        system_message(THESIS_SYSTEM_PROMPT),
        chain_of_thought(),
        generate(),
    )


def thesis_self_critique_solver() -> Solver:
    """
    Self-critique thesis classification solver.

    Generates initial classification, then critiques and refines.
    """
    return chain(
        system_message(THESIS_SYSTEM_PROMPT),
        chain_of_thought(),
        generate(),
        self_critique(
            critique_template=SELF_CRITIQUE_PROMPT,
            completion_template="Based on my critique, my final answer is:\n\n",
        ),
    )


def thesis_verbose_cot_solver() -> Solver:
    """
    Verbose chain-of-thought with structured reasoning.

    Explicitly asks for reasoning about each thesis criterion.
    """
    structured_cot = """Before classifying, think through these questions step by step:

1. **Consumer vs B2B**: Who is the end customer? Individual consumers or businesses?
2. **Category Fit**: Does this match CPG, Health Tech, Travel/Hospitality, or Marketplace?
3. **Excluded Categories**: Is this crypto, services, developer tools, or hardware-only?
4. **Stage Assessment**: Does this appear to be pre-seed to Series A stage?
5. **Thesis Strength**: How strongly does this align with our investment thesis?

Now provide your reasoning and classification."""

    return chain(
        system_message(THESIS_SYSTEM_PROMPT),
        system_message(structured_cot),
        generate(),
    )


# =============================================================================
# SOLVER FACTORY
# =============================================================================

SOLVER_REGISTRY = {
    "basic": thesis_basic_solver,
    "cot": thesis_cot_solver,
    "self_critique": thesis_self_critique_solver,
    "verbose_cot": thesis_verbose_cot_solver,
}


def get_solver(name: str) -> Solver:
    """
    Get a thesis classification solver by name.

    Args:
        name: Solver name (basic, cot, self_critique, verbose_cot)

    Returns:
        Configured Solver instance
    """
    if name not in SOLVER_REGISTRY:
        raise ValueError(
            f"Unknown solver: {name}. "
            f"Available: {list(SOLVER_REGISTRY.keys())}"
        )
    return SOLVER_REGISTRY[name]()


def list_solvers() -> list[str]:
    """List available solver names."""
    return list(SOLVER_REGISTRY.keys())
