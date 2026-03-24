"""Governance state policies — single semantic authority for flag transitions.

Two explicit lanes:

Lane 1 — Env-backed rollout controls (UPPER_CASE):
    States sourced from upstream constants in config_validator / runtime_controls.
    Skip-level transitions are rejected.

Lane 2 — Feature-registry experiments (lower_case):
    Names resolved from utils/feature_states.py DEFAULT_FEATURES.
    States: off < shadow < active. Skip-level allowed.

Unknown names are rejected with case-aware hints.
"""

from __future__ import annotations

from utils.config_validator import VALID_DELIVERY_MODES, WRITE_FEATURE_ENV_VARS
from utils.runtime_controls import VALID_ENABLEMENTS, VALID_ML_ENABLEMENTS
from utils.feature_states import DEFAULT_FEATURES


class GovernanceStatePolicyError(ValueError):
    """Raised when a governance transition violates policy."""


# ── Lane 1: env-backed rollout controls ──────────────────────────────────

_ENV_BACKED_FLAGS: dict[str, tuple[str, ...]] = {
    "DELIVERY_MODE": (
        "staging_only", "manual_publish", "batch_publish", "auto_publish",
    ),
    "ML_ENABLEMENT": ("disabled", "shadow", "live"),
    "V2_ENABLEMENT": ("disabled", "shadow", "live"),
    "MERGE_WRITES_ENABLED": ("disabled", "shadow", "active"),
    "BULK_TRIAGE_ENABLED": ("disabled", "active"),
    "HUNTER_PROMOTE_ENABLED": ("disabled", "active"),
    "DRIFT_MONITORING_ENABLED": ("disabled", "active"),
    "LLM_THESIS_MODE": ("off", "shadow", "active"),
}

# ── Lane 2: feature-registry experiments ─────────────────────────────────

_FEATURE_REGISTRY_STATES: tuple[str, ...] = ("off", "shadow", "active")

_FEATURE_REGISTRY_FLAGS: dict[str, tuple[str, ...]] = {
    name: _FEATURE_REGISTRY_STATES for name in DEFAULT_FEATURES.keys()
}

# ── Combined exports ────────────────────────────────────────────────────

ALL_GOVERNANCE_STATES: frozenset[str] = frozenset(
    state
    for states in list(_ENV_BACKED_FLAGS.values()) + [_FEATURE_REGISTRY_STATES]
    for state in states
)


def is_registered_flag(feature_name: str) -> bool:
    """Check if a flag is registered in either lane."""
    return feature_name in _ENV_BACKED_FLAGS or feature_name in _FEATURE_REGISTRY_FLAGS


def allowed_states_for_flag(feature_name: str) -> tuple[str, ...]:
    """Return the ordered states for a registered flag.

    Raises GovernanceStatePolicyError if the flag is not registered.
    """
    ensure_registered_flag(feature_name)
    if feature_name in _ENV_BACKED_FLAGS:
        return _ENV_BACKED_FLAGS[feature_name]
    return _FEATURE_REGISTRY_FLAGS[feature_name]


def ensure_registered_flag(feature_name: str) -> None:
    """Raise GovernanceStatePolicyError if the flag is not registered.

    Provides case-aware hints for common mistakes.
    """
    if feature_name in _ENV_BACKED_FLAGS or feature_name in _FEATURE_REGISTRY_FLAGS:
        return

    hint = _suggest_hint(feature_name)
    raise GovernanceStatePolicyError(
        f"Flag '{feature_name}' not registered for governance.{hint}"
    )


def validate_transition(
    action_type: str,
    feature_name: str,
    from_state: str,
    to_state: str,
) -> None:
    """Validate a governance state transition.

    Args:
        action_type: 'feature_promote' or 'feature_demote'
        feature_name: The flag name
        from_state: Current state
        to_state: Target state

    Raises:
        GovernanceStatePolicyError on policy violation
    """
    ensure_registered_flag(feature_name)
    states = allowed_states_for_flag(feature_name)

    if from_state not in states:
        raise GovernanceStatePolicyError(
            f"Invalid from_state '{from_state}' for {feature_name}. "
            f"Allowed: {list(states)}"
        )
    if to_state not in states:
        raise GovernanceStatePolicyError(
            f"Invalid to_state '{to_state}' for {feature_name}. "
            f"Allowed: {list(states)}"
        )

    from_idx = states.index(from_state)
    to_idx = states.index(to_state)

    if from_idx == to_idx:
        raise GovernanceStatePolicyError(
            f"No-op transition: {feature_name} is already '{from_state}'."
        )

    is_env_backed = feature_name in _ENV_BACKED_FLAGS

    if action_type == "feature_promote":
        if to_idx < from_idx:
            raise GovernanceStatePolicyError(
                f"Cannot promote {feature_name} from '{from_state}' to "
                f"'{to_state}' (wrong direction). Allowed: {list(states)}"
            )
        if is_env_backed and to_idx - from_idx > 1:
            raise GovernanceStatePolicyError(
                f"Skip-level promotion not allowed for {feature_name}: "
                f"'{from_state}' → '{to_state}'. "
                f"Next allowed state: '{states[from_idx + 1]}'."
            )

    elif action_type == "feature_demote":
        if to_idx > from_idx:
            raise GovernanceStatePolicyError(
                f"Cannot demote {feature_name} from '{from_state}' to "
                f"'{to_state}' (wrong direction). Allowed: {list(states)}"
            )
        if is_env_backed and from_idx - to_idx > 1:
            raise GovernanceStatePolicyError(
                f"Skip-level demotion not allowed for {feature_name}: "
                f"'{from_state}' → '{to_state}'. "
                f"Next allowed state: '{states[from_idx - 1]}'."
            )


# ── Hint logic ───────────────────────────────────────────────────────────

def _suggest_hint(name: str) -> str:
    """Generate a case-aware hint for unregistered flag names."""
    # lowercase env-backed name? e.g. "delivery_mode" → "DELIVERY_MODE"
    upper = name.upper()
    if upper in _ENV_BACKED_FLAGS:
        return f" Did you mean '{upper}'?"

    # UPPER_CASE feature-registry name? e.g. "BOILERPLATE_DEFENSE"
    lower = name.lower()
    if lower in _FEATURE_REGISTRY_FLAGS:
        return f" Did you mean '{lower}'?"

    # FEATURE_ prefix? e.g. "FEATURE_BOILERPLATE_DEFENSE"
    if name.startswith("FEATURE_"):
        stripped = name[len("FEATURE_"):].lower()
        if stripped in _FEATURE_REGISTRY_FLAGS:
            return f" Did you mean '{stripped}'?"

    return ""
