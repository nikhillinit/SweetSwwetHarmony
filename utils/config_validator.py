"""
Config Validator - Pre-flight configuration checks for the Discovery Engine.

Validates environment variables and configuration values on startup,
reporting errors and warnings before the pipeline begins processing.
This does NOT block startup -- it only reports issues.

The delivery_policy module handles invalid DELIVERY_MODE gracefully
at runtime. This validator gives the operator a clear view of ALL
config issues up front.

Usage:
    from utils.config_validator import validate_config, print_config_report

    issues = validate_config()
    has_errors = print_config_report(issues)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

VALID_DELIVERY_MODES = frozenset({
    "staging_only",
    "manual_publish",
    "batch_publish",
    "auto_publish",
})

# Write feature env vars and their valid values.
WRITE_FEATURE_ENV_VARS = {
    "MERGE_WRITES_ENABLED": {
        "description": "Entity merge write operations",
        "valid": frozenset({"disabled", "shadow", "active"}),
    },
    "BULK_TRIAGE_ENABLED": {
        "description": "Bulk triage actions",
        "valid": frozenset({"disabled", "active"}),
    },
    "HUNTER_PROMOTE_ENABLED": {
        "description": "Hunter result promotion",
        "valid": frozenset({"disabled", "active"}),
    },
    "DRIFT_MONITORING_ENABLED": {
        "description": "Drift monitoring and alert mutations",
        "valid": frozenset({"disabled", "active"}),
    },
}

# Threshold env vars that must be in [0.0, 1.0] when set.
# Maps env var name -> human-readable description.
THRESHOLD_ENV_VARS = {
    "MATCHING_HIGH_CONFIDENCE": "Matching high confidence threshold",
    "MATCHING_MEDIUM_CONFIDENCE": "Matching medium confidence threshold",
    "MATCHING_IS_FIT_THRESHOLD": "Matching is-fit threshold",
    "MATCHING_QUALIFIED_THRESHOLD": "Matching qualified threshold",
    "MATCHING_HELD_THRESHOLD": "Matching held threshold",
    "MATCHING_THESIS_THRESHOLD": "Matching thesis assignment threshold",
    "WORKFLOW_HOLD_THRESHOLD": "Workflow hold threshold",
    "WORKFLOW_SKIP_LLM_THRESHOLD": "Workflow skip-LLM threshold",
    "WORKFLOW_KEYWORD_HIGH": "Workflow keyword high threshold",
    "WORKFLOW_KEYWORD_LOW": "Workflow keyword low threshold",
    "WORKFLOW_LLM_REVIEW_THRESHOLD": "Workflow LLM review threshold",
    "WORKFLOW_LLM_AUTO_APPROVE_THRESHOLD": "Workflow LLM auto-approve threshold",
}

SPC_BOOTSTRAP_DEFAULTS = {
    "SPC_MIN_BASELINE_DAYS": ("14", "SPC minimum baseline days"),
    "SPC_MIN_TOTAL_SAMPLES": ("100", "SPC minimum total samples"),
    "SPC_MIN_LABELED_PER_DAY": ("10", "SPC minimum labeled signals per day"),
    "SPC_RECOMPUTE_WINDOW_DAYS": ("7", "SPC recompute window (days)"),
}


# =============================================================================
# DATA CLASS
# =============================================================================

@dataclass
class ConfigIssue:
    """A single config validation result.

    Attributes:
        level: One of 'error', 'warning', 'info'.
               - error:   Will cause failures if not fixed.
               - warning: May cause unexpected behavior.
               - info:    Informational (config is valid).
        key:     The config key or env var name.
        message: Human-readable description of the issue.
    """
    level: str   # 'error' | 'warning' | 'info'
    key: str
    message: str


# =============================================================================
# VALIDATORS
# =============================================================================

def _validate_delivery_mode() -> List[ConfigIssue]:
    """Validate the DELIVERY_MODE env var."""
    raw = os.environ.get("DELIVERY_MODE")

    if raw is None:
        return [
            ConfigIssue(
                level="info",
                key="DELIVERY_MODE",
                message="Not set, defaults to staging_only",
            )
        ]

    normalized = raw.strip().lower()
    if normalized in VALID_DELIVERY_MODES:
        return [
            ConfigIssue(
                level="info",
                key="DELIVERY_MODE",
                message=f"Set to {normalized}",
            )
        ]

    return [
        ConfigIssue(
            level="error",
            key="DELIVERY_MODE",
            message=(
                f"'{raw}' is not a valid mode. "
                f"Valid modes: {', '.join(sorted(VALID_DELIVERY_MODES))}"
            ),
        )
    ]


def _validate_thresholds() -> List[ConfigIssue]:
    """Validate confidence threshold env vars are in [0.0, 1.0]."""
    issues: List[ConfigIssue] = []

    for env_var, description in THRESHOLD_ENV_VARS.items():
        raw = os.environ.get(env_var)
        if raw is None:
            # Not set -- defaults will be used; nothing to report.
            continue

        raw = raw.strip()
        try:
            value = float(raw)
        except ValueError:
            issues.append(ConfigIssue(
                level="error",
                key=env_var,
                message=f"'{raw}' is not a valid number ({description})",
            ))
            continue

        if value < 0.0 or value > 1.0:
            issues.append(ConfigIssue(
                level="error",
                key=env_var,
                message=(
                    f"{value} is out of range [0.0, 1.0] ({description})"
                ),
            ))
        else:
            issues.append(ConfigIssue(
                level="info",
                key=env_var,
                message=f"Set to {value} ({description})",
            ))

    # If no threshold env vars were set, emit a single OK line.
    if not issues:
        issues.append(ConfigIssue(
            level="info",
            key="thresholds",
            message="All thresholds using defaults (valid)",
        ))

    return issues


def _validate_write_features() -> List[ConfigIssue]:
    """Validate write feature env vars have valid values."""
    issues: List[ConfigIssue] = []

    for env_var, info in WRITE_FEATURE_ENV_VARS.items():
        raw = os.environ.get(env_var)
        if raw is None:
            issues.append(ConfigIssue(
                level="info",
                key=env_var,
                message=f"Not set, defaults to disabled ({info['description']})",
            ))
            continue

        normalized = raw.strip().lower()
        if normalized in info["valid"]:
            issues.append(ConfigIssue(
                level="info",
                key=env_var,
                message=f"Set to {normalized} ({info['description']})",
            ))
        else:
            issues.append(ConfigIssue(
                level="error",
                key=env_var,
                message=(
                    f"'{raw}' is not valid. "
                    f"Valid values: {', '.join(sorted(info['valid']))} "
                    f"({info['description']})"
                ),
            ))

    return issues


def _validate_notion_keys() -> List[ConfigIssue]:
    """Check that Notion API key and database ID are configured.

    When DELIVERY_MODE requires Notion (manual_publish, batch_publish,
    auto_publish), missing keys are errors. Otherwise they are warnings.
    """
    issues: List[ConfigIssue] = []
    delivery_mode = os.environ.get("DELIVERY_MODE", "staging_only").strip().lower()
    needs_notion = delivery_mode in ("manual_publish", "batch_publish", "auto_publish")

    notion_key = os.environ.get("NOTION_API_KEY", "").strip()
    if not notion_key:
        level = "error" if needs_notion else "warning"
        issues.append(ConfigIssue(
            level=level,
            key="NOTION_API_KEY",
            message=f"Not configured (required for {delivery_mode})"
                    if needs_notion else "Not configured (Notion push will fail)",
        ))
    else:
        issues.append(ConfigIssue(
            level="info",
            key="NOTION_API_KEY",
            message="Configured",
        ))

    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not notion_db:
        level = "error" if needs_notion else "warning"
        issues.append(ConfigIssue(
            level=level,
            key="NOTION_DATABASE_ID",
            message=f"Not configured (required for {delivery_mode})"
                    if needs_notion else "Not configured (Notion push will fail)",
        ))
    else:
        issues.append(ConfigIssue(
            level="info",
            key="NOTION_DATABASE_ID",
            message="Configured",
        ))

    return issues


def _validate_spc_bootstrap_overrides() -> List[ConfigIssue]:
    """Flag temporary SPC overrides so promotion decisions stay explicit."""
    issues: List[ConfigIssue] = []

    for env_var, (default_raw, description) in SPC_BOOTSTRAP_DEFAULTS.items():
        raw = os.environ.get(env_var)
        if raw is None:
            continue

        value = raw.strip()
        try:
            parsed = int(value)
        except ValueError:
            issues.append(ConfigIssue(
                level="error",
                key=env_var,
                message=f"'{raw}' is not a valid integer ({description})",
            ))
            continue

        if parsed <= 0:
            issues.append(ConfigIssue(
                level="error",
                key=env_var,
                message=f"{parsed} must be > 0 ({description})",
            ))
            continue

        default_value = int(default_raw)
        if parsed == default_value:
            issues.append(ConfigIssue(
                level="info",
                key=env_var,
                message=f"Set to default {default_value} ({description})",
            ))
        else:
            issues.append(ConfigIssue(
                level="warning",
                key=env_var,
                message=(
                    f"Non-default SPC bootstrap override active: {parsed} "
                    f"(default {default_value}, {description}). "
                    "Document an explicit Step 3/4 promotion decision before relying on it."
                ),
            ))

    return issues


# =============================================================================
# PUBLIC API
# =============================================================================

def validate_config() -> List[ConfigIssue]:
    """
    Run all config validations and return a list of issues.

    Returns:
        List of ConfigIssue objects (may include errors, warnings, and
        info-level entries). An empty list means nothing was checked,
        which should not happen with the built-in validators.
    """
    issues: List[ConfigIssue] = []
    issues.extend(_validate_delivery_mode())
    issues.extend(_validate_thresholds())
    issues.extend(_validate_write_features())
    issues.extend(_validate_notion_keys())
    issues.extend(_validate_spc_bootstrap_overrides())
    return issues


def print_config_report(issues: List[ConfigIssue]) -> bool:
    """
    Print a human-readable config validation report.

    Args:
        issues: List of ConfigIssue from validate_config().

    Returns:
        True if there are any errors, False otherwise.
    """
    level_prefix = {
        "error": "[ERROR]",
        "warning": "[WARN]",
        "info": "[OK]",
    }

    print("Config Validation:")
    for issue in issues:
        prefix = level_prefix.get(issue.level, "[???]")
        print(f"  {prefix} {issue.key}: {issue.message}")

    has_errors = any(i.level == "error" for i in issues)
    if not has_errors:
        print("  All checks passed.")

    return has_errors
