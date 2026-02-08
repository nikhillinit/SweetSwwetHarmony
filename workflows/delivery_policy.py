"""
Delivery Policy for Discovery Engine

Controls when Notion writes are allowed based on the DELIVERY_MODE
environment variable. This prevents accidental Notion pollution during
development, testing, and staged rollout.

Modes:
    staging_only    - Block ALL Notion writes (default, safest)
    manual_publish  - Allow single-item manual push only
    batch_publish   - Allow batch workflow + manual push
    auto_publish    - Allow all writes (pipeline automatic push)

Usage:
    from workflows.delivery_policy import (
        assert_notion_write_allowed, DeliveryIntent
    )

    # Before any Notion write, call the guard:
    assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)

    # Raises DeliveryPolicyError if the current mode does not
    # permit the requested intent.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class DeliveryMode(str, Enum):
    """Operator-controlled delivery mode, read from DELIVERY_MODE env var."""
    STAGING_ONLY = "staging_only"
    MANUAL_PUBLISH = "manual_publish"
    BATCH_PUBLISH = "batch_publish"
    AUTO_PUBLISH = "auto_publish"


class DeliveryIntent(str, Enum):
    """The type of Notion write being attempted."""
    AUTO_PUSH = "auto_push"      # Pipeline automatic push
    MANUAL_PUSH = "manual_push"  # Single-item manual push
    BATCH_PUSH = "batch_push"    # Batch publish workflow


# =============================================================================
# EXCEPTION
# =============================================================================

class DeliveryPolicyError(RuntimeError):
    """Raised when a Notion write is blocked by the delivery policy."""
    pass


# =============================================================================
# PERMISSION MATRIX
# =============================================================================

# Maps each mode to the set of intents it permits.
_ALLOWED_INTENTS: dict[DeliveryMode, set[DeliveryIntent]] = {
    DeliveryMode.STAGING_ONLY: set(),
    DeliveryMode.MANUAL_PUBLISH: {DeliveryIntent.MANUAL_PUSH},
    DeliveryMode.BATCH_PUBLISH: {DeliveryIntent.MANUAL_PUSH, DeliveryIntent.BATCH_PUSH},
    DeliveryMode.AUTO_PUBLISH: {
        DeliveryIntent.AUTO_PUSH,
        DeliveryIntent.MANUAL_PUSH,
        DeliveryIntent.BATCH_PUSH,
    },
}


# =============================================================================
# PUBLIC API
# =============================================================================

def get_delivery_mode() -> DeliveryMode:
    """
    Read the current delivery mode from the DELIVERY_MODE env var.

    Returns DeliveryMode.STAGING_ONLY if the variable is unset or invalid.
    """
    raw = os.environ.get("DELIVERY_MODE", "staging_only").strip().lower()
    try:
        return DeliveryMode(raw)
    except ValueError:
        logger.warning(
            "Invalid DELIVERY_MODE=%r, falling back to staging_only", raw
        )
        return DeliveryMode.STAGING_ONLY


def assert_notion_write_allowed(
    intent: DeliveryIntent,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Guard function: raises DeliveryPolicyError if the current delivery
    mode does not permit the given intent.

    Call this before every Notion write operation.

    Args:
        intent: The type of write being attempted.
        context: Optional publish context for audit logging.

    Raises:
        DeliveryPolicyError: If the write is not allowed.
    """
    mode = get_delivery_mode()
    allowed = _ALLOWED_INTENTS[mode]

    if intent not in allowed:
        raise DeliveryPolicyError(
            f"Notion write blocked: intent={intent.value} is not allowed "
            f"in DELIVERY_MODE={mode.value}. "
            f"Set DELIVERY_MODE to a permissive mode to proceed."
        )

    logger.debug(
        "Delivery policy: %s permitted in mode %s", intent.value, mode.value
    )
