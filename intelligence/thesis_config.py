"""Thesis configuration loading for vertical-specific matching rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml


@dataclass
class ThesisConfig:
    """Configuration for vertical-specific thesis matching."""

    vertical: str
    version: str
    description: str
    scoring_weights: Dict[str, float]
    positive_signals: Dict[str, List[str]]
    negative_signals: List[str]
    stage_filters: Dict[str, List[str]]


def load_thesis_config(vertical: str) -> ThesisConfig:
    """Load thesis configuration for a specific vertical.

    Args:
        vertical: The vertical name (e.g., "travel", "healthcare")

    Returns:
        ThesisConfig instance with loaded configuration

    Raises:
        FileNotFoundError: If config file not found for vertical
    """
    config_paths = [
        Path(f"config/{vertical}_thesis_rules.yaml"),
        Path(__file__).parent.parent / "config" / f"{vertical}_thesis_rules.yaml",
    ]

    config_path = None
    for path in config_paths:
        if path.exists():
            config_path = path
            break

    if config_path is None:
        raise FileNotFoundError(
            f"Thesis config not found for vertical '{vertical}'. "
            f"Searched: {[str(p) for p in config_paths]}"
        )

    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    return ThesisConfig(
        vertical=data["vertical"],
        version=data["version"],
        description=data.get("description", ""),
        scoring_weights=data["scoring_weights"],
        positive_signals=data["positive_signals"],
        negative_signals=data["negative_signals"],
        stage_filters=data.get("stage_filters", {"included": [], "excluded": []}),
    )
