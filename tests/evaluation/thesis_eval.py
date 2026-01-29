"""
Thesis Classification Evaluation Tasks

Main evaluation tasks for benchmarking thesis classification accuracy.
Uses Inspect AI framework with custom solvers and scorers.

Usage:
    # Run basic evaluation
    inspect eval tests/evaluation/thesis_eval.py --model google/gemini-1.5-flash

    # Run specific task
    inspect eval tests/evaluation/thesis_eval.py:thesis_with_cot --model openai/gpt-4o

    # View results
    inspect view

    # Run parameter sweep with inspect-flow
    flow run tests/evaluation/flow_config.py
"""

from __future__ import annotations

import os
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset, Sample
from inspect_ai.scorer import accuracy, stderr
from inspect_ai.solver import chain, chain_of_thought, generate, self_critique, system_message

from tests.evaluation.solvers import (
    THESIS_SYSTEM_PROMPT,
    SELF_CRITIQUE_PROMPT,
    thesis_basic_solver,
    thesis_cot_solver,
    thesis_self_critique_solver,
    thesis_verbose_cot_solver,
)
from tests.evaluation.scorers import thesis_match, thesis_match_lenient


# =============================================================================
# DATASET PATH
# =============================================================================

# Default dataset path (relative to project root)
DEFAULT_DATASET = "datasets/thesis_ground_truth.jsonl"


def get_dataset_path() -> str:
    """Get the dataset path, checking environment variable first."""
    return os.environ.get("THESIS_DATASET", DEFAULT_DATASET)


def dataset_exists() -> bool:
    """Check if the ground truth dataset exists."""
    return Path(get_dataset_path()).exists()


# =============================================================================
# EVALUATION TASKS
# =============================================================================

@task
def thesis_classification() -> Task:
    """
    Basic thesis classification evaluation.

    Uses simple prompt → generate pattern.
    Good baseline for comparing against more sophisticated approaches.
    """
    return Task(
        dataset=json_dataset(get_dataset_path()),
        solver=thesis_basic_solver(),
        scorer=thesis_match(),
        metrics=[accuracy(), stderr()],
    )


@task
def thesis_with_cot() -> Task:
    """
    Thesis classification with chain-of-thought reasoning.

    Adds "Think step by step" instruction before generation.
    Expected to improve accuracy on borderline cases.
    """
    return Task(
        dataset=json_dataset(get_dataset_path()),
        solver=thesis_cot_solver(),
        scorer=thesis_match(),
        metrics=[accuracy(), stderr()],
    )


@task
def thesis_with_self_critique() -> Task:
    """
    Thesis classification with self-critique loop.

    Generates initial classification, then critiques and refines.
    Best for catching false positives (B2B misclassified as consumer).
    """
    return Task(
        dataset=json_dataset(get_dataset_path()),
        solver=thesis_self_critique_solver(),
        scorer=thesis_match(),
        metrics=[accuracy(), stderr()],
    )


@task
def thesis_verbose_cot() -> Task:
    """
    Thesis classification with verbose structured reasoning.

    Explicitly reasons through each thesis criterion.
    Most verbose but may have best accuracy on complex cases.
    """
    return Task(
        dataset=json_dataset(get_dataset_path()),
        solver=thesis_verbose_cot_solver(),
        scorer=thesis_match(),
        metrics=[accuracy(), stderr()],
    )


@task
def thesis_lenient_scoring() -> Task:
    """
    Thesis classification with lenient scoring.

    Gives partial credit for adjacent classifications:
    - QUALIFIED↔HELD: partial
    - HELD↔REJECTED: partial
    - QUALIFIED↔REJECTED: incorrect

    Useful for understanding severity of errors.
    """
    return Task(
        dataset=json_dataset(get_dataset_path()),
        solver=thesis_cot_solver(),
        scorer=thesis_match_lenient(),
        metrics=[accuracy(), stderr()],
    )


# =============================================================================
# COMBINED TASKS (for parameter sweeping)
# =============================================================================

@task
def thesis_all_solvers() -> list[Task]:
    """
    Return all solver variants for comparison.

    Use with inspect-flow for matrix evaluation.
    """
    return [
        thesis_classification(),
        thesis_with_cot(),
        thesis_with_self_critique(),
        thesis_verbose_cot(),
    ]


# =============================================================================
# CUSTOM DATASET TASKS
# =============================================================================

def thesis_eval_custom(dataset_path: str, solver_name: str = "cot") -> Task:
    """
    Create thesis evaluation task with custom dataset.

    Args:
        dataset_path: Path to JSONL dataset
        solver_name: Solver to use (basic, cot, self_critique, verbose_cot)

    Returns:
        Configured Task instance
    """
    from tests.evaluation.solvers import get_solver

    return Task(
        dataset=json_dataset(dataset_path),
        solver=get_solver(solver_name),
        scorer=thesis_match(),
        metrics=[accuracy(), stderr()],
    )


# =============================================================================
# SAMPLE DATA FOR TESTING (if no dataset exists)
# =============================================================================

SAMPLE_DATA = [
    Sample(
        input="Company: Wellness Labs\nDescription: B2C fitness app for guided meditation and workout tracking\nSector: Consumer Health Tech\nSignals: Product Hunt launch (500 upvotes)",
        target="QUALIFIED",
        id="sample_1",
        metadata={"company_name": "Wellness Labs", "actual_outcome": "Funded"},
    ),
    Sample(
        input="Company: DevOps Platform\nDescription: CI/CD automation platform for enterprise engineering teams\nSector: Developer Tools\nSignals: GitHub trending (1000 stars)",
        target="REJECTED",
        id="sample_2",
        metadata={"company_name": "DevOps Platform", "actual_outcome": "Passed"},
    ),
    Sample(
        input="Company: Snack Startup\nDescription: Healthy snack subscription box targeting millennials\nSector: CPG\nSignals: Companies House incorporation",
        target="QUALIFIED",
        id="sample_3",
        metadata={"company_name": "Snack Startup", "actual_outcome": "Funded"},
    ),
    Sample(
        input="Company: Crypto Exchange\nDescription: Decentralized cryptocurrency trading platform\nSector: Crypto/Web3\nSignals: Product Hunt launch",
        target="REJECTED",
        id="sample_4",
        metadata={"company_name": "Crypto Exchange", "actual_outcome": "Passed"},
    ),
    Sample(
        input="Company: Travel App\nDescription: Hotel booking aggregator with personalized recommendations\nSector: Travel & Hospitality\nSignals: SEC Form D filing",
        target="QUALIFIED",
        id="sample_5",
        metadata={"company_name": "Travel App", "actual_outcome": "Committed"},
    ),
]


@task
def thesis_sample_data() -> Task:
    """
    Thesis evaluation using sample data (for testing without Notion export).

    Use this to verify the evaluation pipeline works before running
    with real data from Notion.
    """
    from inspect_ai.dataset import MemoryDataset

    return Task(
        dataset=MemoryDataset(samples=SAMPLE_DATA),
        solver=thesis_cot_solver(),
        scorer=thesis_match(),
        metrics=[accuracy(), stderr()],
    )


# =============================================================================
# CLI HELPERS
# =============================================================================

def check_setup():
    """Check if evaluation setup is complete."""
    issues = []

    # Check dataset
    if not dataset_exists():
        issues.append(
            f"Dataset not found at {get_dataset_path()}. "
            "Run: python scripts/export_notion_ground_truth.py"
        )

    # Check environment
    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        issues.append(
            "No API key found. Set GOOGLE_API_KEY or OPENAI_API_KEY."
        )

    if issues:
        print("Setup issues:")
        for issue in issues:
            print(f"  - {issue}")
        return False

    print("Setup complete. Ready to run evaluation.")
    return True


if __name__ == "__main__":
    check_setup()
