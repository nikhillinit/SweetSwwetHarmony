"""
Custom scorers for thesis classification evaluation.

Provides multi-class scoring for QUALIFIED/HELD/REJECTED classification.
"""

from __future__ import annotations

import re
from typing import List, Optional

from inspect_ai.scorer import (
    Score,
    Scorer,
    Target,
    accuracy,
    scorer,
    CORRECT,
    INCORRECT,
    PARTIAL,
)
from inspect_ai.solver import TaskState


# =============================================================================
# THESIS CLASSIFICATION SCORER
# =============================================================================

@scorer(metrics=[accuracy()])
def thesis_match() -> Scorer:
    """
    Score thesis classification output against target.

    Extracts classification from model output and compares to target.
    Handles various output formats (JSON, plain text, markdown).

    Valid classifications: QUALIFIED, HELD, REJECTED
    """
    async def score(state: TaskState, target: Target) -> Score:
        # Get model output
        model_output = state.output.completion if state.output else ""

        # Extract classification from output
        classification = extract_classification(model_output)

        if not classification:
            return Score(
                value=INCORRECT,
                answer=model_output[:100],
                explanation="Could not extract classification from output",
            )

        # Get target value
        target_value = target.text.strip().upper()

        # Compare
        if classification == target_value:
            return Score(
                value=CORRECT,
                answer=classification,
                explanation=f"Correctly classified as {classification}",
            )
        else:
            return Score(
                value=INCORRECT,
                answer=classification,
                explanation=f"Expected {target_value}, got {classification}",
            )

    return score


@scorer(metrics=[accuracy()])
def thesis_match_lenient() -> Scorer:
    """
    Lenient scorer that gives partial credit for close classifications.

    Scoring:
    - CORRECT: Exact match
    - PARTIAL: Close match (QUALIFIED↔HELD, HELD↔REJECTED)
    - INCORRECT: Opposite ends (QUALIFIED↔REJECTED)
    """
    async def score(state: TaskState, target: Target) -> Score:
        model_output = state.output.completion if state.output else ""
        classification = extract_classification(model_output)

        if not classification:
            return Score(
                value=INCORRECT,
                answer=model_output[:100],
                explanation="Could not extract classification",
            )

        target_value = target.text.strip().upper()

        if classification == target_value:
            return Score(
                value=CORRECT,
                answer=classification,
                explanation=f"Exact match: {classification}",
            )

        # Check for partial credit (adjacent categories)
        adjacent_pairs = [
            ("QUALIFIED", "HELD"),
            ("HELD", "REJECTED"),
        ]

        for pair in adjacent_pairs:
            if classification in pair and target_value in pair:
                return Score(
                    value=PARTIAL,
                    answer=classification,
                    explanation=f"Adjacent classification: expected {target_value}, got {classification}",
                )

        # QUALIFIED vs REJECTED = completely wrong
        return Score(
            value=INCORRECT,
            answer=classification,
            explanation=f"Opposite classification: expected {target_value}, got {classification}",
        )

    return score


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

VALID_CLASSIFICATIONS = {"QUALIFIED", "HELD", "REJECTED"}


def extract_classification(text: str) -> Optional[str]:
    """
    Extract thesis classification from model output.

    Handles multiple formats:
    - JSON: {"classification": "QUALIFIED"}
    - Plain text: "QUALIFIED"
    - Markdown: **QUALIFIED**
    - Sentence: "The classification is QUALIFIED"
    """
    if not text:
        return None

    text_upper = text.upper()

    # Try JSON extraction first
    json_patterns = [
        r'"classification"\s*:\s*"([A-Z]+)"',
        r'"thesis_classification"\s*:\s*"([A-Z]+)"',
        r'"result"\s*:\s*"([A-Z]+)"',
        r'"answer"\s*:\s*"([A-Z]+)"',
    ]

    for pattern in json_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).upper()
            if value in VALID_CLASSIFICATIONS:
                return value

    # Try to find standalone classification
    for classification in VALID_CLASSIFICATIONS:
        # Look for the classification as a standalone word
        pattern = rf'\b{classification}\b'
        if re.search(pattern, text_upper):
            return classification

    # Try to find classification in markdown bold
    bold_pattern = r'\*\*([A-Z]+)\*\*'
    match = re.search(bold_pattern, text_upper)
    if match:
        value = match.group(1)
        if value in VALID_CLASSIFICATIONS:
            return value

    return None


def calculate_class_metrics(
    predictions: List[str],
    targets: List[str],
) -> dict:
    """
    Calculate per-class precision, recall, and F1 scores.

    Args:
        predictions: List of predicted classifications
        targets: List of ground truth classifications

    Returns:
        Dict with per-class and overall metrics
    """
    classes = list(VALID_CLASSIFICATIONS)

    # Initialize confusion matrix
    confusion = {c: {"tp": 0, "fp": 0, "fn": 0} for c in classes}

    for pred, target in zip(predictions, targets):
        if pred == target:
            confusion[pred]["tp"] += 1
        else:
            if pred in confusion:
                confusion[pred]["fp"] += 1
            if target in confusion:
                confusion[target]["fn"] += 1

    # Calculate metrics per class
    metrics = {}
    for cls in classes:
        tp = confusion[cls]["tp"]
        fp = confusion[cls]["fp"]
        fn = confusion[cls]["fn"]

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        metrics[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": tp + fn,
        }

    # Calculate macro averages
    macro_precision = sum(m["precision"] for m in metrics.values()) / len(classes)
    macro_recall = sum(m["recall"] for m in metrics.values()) / len(classes)
    macro_f1 = sum(m["f1"] for m in metrics.values()) / len(classes)

    metrics["macro_avg"] = {
        "precision": round(macro_precision, 4),
        "recall": round(macro_recall, 4),
        "f1": round(macro_f1, 4),
    }

    return metrics
