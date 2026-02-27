"""
DNS Phase 2 promotion config — accessor functions.

All values are read from environment at call time (no frozen globals).
This ensures monkeypatch and env changes are respected without restart.

Locked Integration Points (PR10b):
  1. workflows/notion_pusher.py :: _group_by_canonical_key()  — alias resolution
  2. collectors/base.py :: _extract_canonical_key()            — strongest-key selection
  3. run_pipeline.py export-queue grouping                     — if canonical key used
  4. scripts/convergence_kpi.py                                — NOT in PR10b scope

Env vars:
  DNS_PROBE_ENABLED              — gate DNS probing (default: false)
  DNS_PROBE_PROMOTE_ENABLED      — gate DNS promotion (default: false)
  DNS_PROBE_CONFIDENCE_PENALTY   — penalty for promoted keys (default: 0.03)
  DNS_PROMOTION_PENALTY          — DEPRECATED alias for above
  DNS_PHASE2_GUARDRAILS_OVERRIDE — bypass guardrail breach (default: false)
  DNS_PHASE2_GUARDRAILS_OVERRIDE_REASON — audit reason for override
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_PENALTY = 0.03


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


def is_dns_probe_enabled() -> bool:
    """Whether DNS probing is enabled for collectors."""
    return _parse_bool(os.environ.get("DNS_PROBE_ENABLED", "false"))


def is_dns_promote_enabled() -> bool:
    """Whether DNS-probed domains can be promoted to canonical keys."""
    return _parse_bool(os.environ.get("DNS_PROBE_PROMOTE_ENABLED", "false"))


def get_dns_confidence_penalty() -> float:
    """Confidence penalty applied to DNS-promoted signals.

    Canonical var DNS_PROBE_CONFIDENCE_PENALTY wins over deprecated
    DNS_PROMOTION_PENALTY. Invalid values fall back to default (0.03).
    """
    value, _source = get_effective_penalty_and_source()
    return value


def get_guardrail_override() -> tuple[bool, str]:
    """Return (override_flag, override_reason) for guardrail bypass."""
    flag = _parse_bool(os.environ.get("DNS_PHASE2_GUARDRAILS_OVERRIDE", "false"))
    reason = os.environ.get("DNS_PHASE2_GUARDRAILS_OVERRIDE_REASON", "")
    return flag, reason


def get_effective_penalty_and_source() -> tuple[float, str]:
    """Return (penalty_value, source_label) for audit/logging.

    Precedence:
      1. DNS_PROBE_CONFIDENCE_PENALTY (canonical)
      2. DNS_PROMOTION_PENALTY (deprecated alias)
      3. Default 0.03

    If both canonical and deprecated are set, canonical wins and a
    warning is logged once per process.
    """
    canonical_raw = os.environ.get("DNS_PROBE_CONFIDENCE_PENALTY")
    deprecated_raw = os.environ.get("DNS_PROMOTION_PENALTY")

    # Warn if both set
    if canonical_raw is not None and deprecated_raw is not None:
        logger.warning(
            "Both DNS_PROBE_CONFIDENCE_PENALTY and DNS_PROMOTION_PENALTY set; "
            "using canonical DNS_PROBE_CONFIDENCE_PENALTY=%s",
            canonical_raw,
        )

    # Try canonical first
    if canonical_raw is not None:
        try:
            return float(canonical_raw), "DNS_PROBE_CONFIDENCE_PENALTY"
        except (ValueError, TypeError):
            logger.warning(
                "Invalid DNS_PROBE_CONFIDENCE_PENALTY=%r, falling back to default",
                canonical_raw,
            )
            return _DEFAULT_PENALTY, "default"

    # Try deprecated alias
    if deprecated_raw is not None:
        try:
            return float(deprecated_raw), "DNS_PROMOTION_PENALTY"
        except (ValueError, TypeError):
            logger.warning(
                "Invalid DNS_PROMOTION_PENALTY=%r, falling back to default",
                deprecated_raw,
            )
            return _DEFAULT_PENALTY, "default"

    return _DEFAULT_PENALTY, "default"
