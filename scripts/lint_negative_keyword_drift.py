#!/usr/bin/env python
"""Lint: detect drift between YAML policy and Python keyword dicts.

Checks that:
1. Keywords match (YAML ⊇ Python and Python ⊇ YAML)
2. Weights agree
3. Tiers in YAML match Python HARD_REJECT/HARD_HOLD/SOFT dicts

Exit codes:
  0 — no drift
  1 — drift detected (details printed to stderr)

Usage:
  python scripts/lint_negative_keyword_drift.py
"""

import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from utils.thesis_matcher import (
    HARD_HOLD_KEYWORDS,
    HARD_REJECT_KEYWORDS,
    NEGATIVE_KEYWORDS,
    SOFT_PENALTY_KEYWORDS,
)


def main() -> int:
    policy_path = Path(__file__).resolve().parent.parent / "config" / "v2" / "negative_keyword_policy.yaml"
    if not policy_path.exists():
        print(f"ERROR: Policy file not found: {policy_path}", file=sys.stderr)
        return 1

    with open(policy_path) as f:
        policy = yaml.safe_load(f)

    yaml_keywords = policy.get("negative_keywords", {})
    errors: list[str] = []

    # 1. Keyword set equality
    yaml_set = set(yaml_keywords.keys())
    python_set = set(NEGATIVE_KEYWORDS.keys())

    missing_from_yaml = python_set - yaml_set
    extra_in_yaml = yaml_set - python_set

    if missing_from_yaml:
        errors.append(f"Keywords in Python but not YAML: {sorted(missing_from_yaml)}")
    if extra_in_yaml:
        errors.append(f"Keywords in YAML but not Python: {sorted(extra_in_yaml)}")

    # 2. Weight agreement
    for kw in python_set & yaml_set:
        py_weight = NEGATIVE_KEYWORDS[kw]
        yaml_weight = yaml_keywords[kw].get("weight")
        if yaml_weight is not None and abs(py_weight - yaml_weight) > 0.001:
            errors.append(
                f"Weight mismatch for '{kw}': Python={py_weight}, YAML={yaml_weight}"
            )

    # 3. Tier agreement (YAML tier must match Python dict membership)
    tier_map = {}
    for kw in HARD_REJECT_KEYWORDS:
        tier_map[kw] = "hard_reject"
    for kw in HARD_HOLD_KEYWORDS:
        tier_map[kw] = "hard_hold"
    for kw in SOFT_PENALTY_KEYWORDS:
        tier_map[kw] = "soft"

    for kw in python_set & yaml_set:
        yaml_tier = yaml_keywords[kw].get("tier")
        python_tier = tier_map.get(kw)
        if yaml_tier is not None and python_tier is not None and yaml_tier != python_tier:
            errors.append(
                f"Tier mismatch for '{kw}': Python={python_tier}, YAML={yaml_tier}"
            )

    if errors:
        print("DRIFT DETECTED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"OK: {len(yaml_set)} keywords, 0 drift issues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
