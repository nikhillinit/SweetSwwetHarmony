"""
Feature Guards for Wave 4 Write Operations.

Controls when write features are enabled via environment variables.
Each write feature (merge, bulk triage, hunter promote) can be
independently toggled between disabled, shadow, and active modes.

Usage:
    from workflows.feature_guards import assert_write_enabled, WriteFeature

    # Before any write operation:
    assert_write_enabled(WriteFeature.MERGE_WRITES)

    # Raises FeatureDisabledError if the feature is disabled.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class WriteFeature(str, Enum):
    """Write features that can be independently enabled/disabled."""
    MERGE_WRITES = "merge_writes"
    BULK_TRIAGE = "bulk_triage"
    HUNTER_PROMOTE = "hunter_promote"
    DRIFT_MONITORING = "drift_monitoring"


class WriteMode(str, Enum):
    """Operating mode for a write feature."""
    DISABLED = "disabled"
    SHADOW = "shadow"    # Log the plan but don't execute (merge only)
    ACTIVE = "active"


# =============================================================================
# EXCEPTION
# =============================================================================

class FeatureDisabledError(RuntimeError):
    """Raised when a write feature is disabled by configuration.

    Caught by API routers and mapped to HTTP 423 Locked.
    """

    def __init__(self, feature: WriteFeature, env_var: str, current_mode: str):
        self.feature = feature
        self.env_var = env_var
        self.current_mode = current_mode
        super().__init__(
            f"Write feature '{feature.value}' is disabled "
            f"(current mode: {current_mode}). "
            f"Set {env_var} to 'active' to enable."
        )


# =============================================================================
# FEATURE → ENV VAR MAPPING
# =============================================================================

_FEATURE_ENV_MAP: dict[WriteFeature, str] = {
    WriteFeature.MERGE_WRITES: "MERGE_WRITES_ENABLED",
    WriteFeature.BULK_TRIAGE: "BULK_TRIAGE_ENABLED",
    WriteFeature.HUNTER_PROMOTE: "HUNTER_PROMOTE_ENABLED",
    WriteFeature.DRIFT_MONITORING: "DRIFT_MONITORING_ENABLED",
}

# Valid modes per feature. Merge supports shadow; others don't.
_VALID_MODES: dict[WriteFeature, frozenset[str]] = {
    WriteFeature.MERGE_WRITES: frozenset({"disabled", "shadow", "active"}),
    WriteFeature.BULK_TRIAGE: frozenset({"disabled", "active"}),
    WriteFeature.HUNTER_PROMOTE: frozenset({"disabled", "active"}),
    WriteFeature.DRIFT_MONITORING: frozenset({"disabled", "active"}),
}


# =============================================================================
# PUBLIC API
# =============================================================================

def get_write_mode(feature: WriteFeature) -> WriteMode:
    """Read the current mode for a write feature from env vars.

    Returns WriteMode.DISABLED if the variable is unset or invalid.
    """
    env_var = _FEATURE_ENV_MAP[feature]
    raw = os.environ.get(env_var, "disabled").strip().lower()

    valid = _VALID_MODES[feature]
    if raw not in valid:
        logger.warning(
            "Invalid %s=%r, expected one of %s. Defaulting to disabled.",
            env_var, raw, sorted(valid),
        )
        return WriteMode.DISABLED

    return WriteMode(raw)


def assert_write_enabled(
    feature: WriteFeature,
    *,
    allow_shadow: bool = False,
) -> WriteMode:
    """Guard function: raises FeatureDisabledError if the feature is not active.

    For merge writes, shadow mode is allowed when allow_shadow=True
    (propose/approve succeed in shadow; apply logs plan but doesn't execute).

    Args:
        feature: The write feature to check.
        allow_shadow: If True, shadow mode does not raise (merge only).

    Returns:
        The current WriteMode (for callers that need to check shadow vs active).

    Raises:
        FeatureDisabledError: If the feature is disabled.
    """
    mode = get_write_mode(feature)
    env_var = _FEATURE_ENV_MAP[feature]

    if mode == WriteMode.ACTIVE:
        return mode

    if mode == WriteMode.SHADOW and allow_shadow:
        logger.info(
            "Feature '%s' in shadow mode — operation will be logged but not executed.",
            feature.value,
        )
        return mode

    raise FeatureDisabledError(feature, env_var, mode.value)


def is_feature_enabled(feature: WriteFeature) -> bool:
    """Check if a feature is enabled (active or shadow) without raising."""
    mode = get_write_mode(feature)
    return mode != WriteMode.DISABLED
