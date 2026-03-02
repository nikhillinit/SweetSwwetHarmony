"""
Runtime Controls for Negative Keyword Policy v2, ML Thesis Model, and Cascade Routing.

Centralized parsing and normalization of environment variables and kwargs
for v2 policy enablement, ML model integration, and cascade routing. Handles:
- Normalization: empty/whitespace → unset, case normalization
- Membership validation: loader_mode, enablement values
- Invariant enforcement: shadow/live → strict, live → execution enabled
- Legacy mapping: enable_v2_policy → v2_enablement
- ML model enablement: disabled/shadow/live with model path
- Cascade routing: disabled/shadow/live with phase gate enforcement (ADR-4)

Bug hazards addressed:
- #4: env var casing/whitespace/empty string pitfalls
- #6: missing membership validation
- #11: empty env var treated as real value

Usage:
    controls = RuntimeControls.from_env(
        v2_enablement="shadow",
        policy_loader_mode=None,  # will derive from enablement
    )
    print(controls.v2_enablement)  # "shadow"
    print(controls.policy_loader_mode)  # "strict" (derived)

    # ML model controls
    controls = RuntimeControls.from_env(
        ml_enablement="shadow",
        ml_model_path="models/thesis_classifier.joblib",
    )
    print(controls.ml_enablement)  # "shadow"

    # Cascade routing
    controls = RuntimeControls.from_env(
        cascade_routing_enablement="shadow",
    )
    print(controls.cascade_routing_enablement)  # "shadow"
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Valid membership values
VALID_LOADER_MODES = frozenset({"permissive", "strict"})
VALID_ENABLEMENTS = frozenset({"disabled", "shadow", "live"})
VALID_ML_ENABLEMENTS = frozenset({"disabled", "shadow", "live"})
VALID_CASCADE_ENABLEMENTS = frozenset({"disabled", "shadow", "live"})

# Default phase gates file path (relative to project root)
_DEFAULT_PHASE_GATES_PATH = "config/phase_gates.yaml"

# Boolean parsing values
TRUTHY_VALUES = frozenset({"true", "1", "yes", "on"})
FALSY_VALUES = frozenset({"false", "0", "no", "off"})


def _normalize_string(value: Optional[str]) -> Optional[str]:
    """Normalize string value from env or args.

    Rules:
    - None → None (unset)
    - "" or whitespace-only → None (treat as unset)
    - Otherwise → stripped and lowercased

    Bug #4 mitigation: centralized normalization prevents casing/whitespace issues.
    Bug #11 mitigation: empty string → None, not empty string.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped.lower()


def _parse_bool_env(
    value: Optional[str],
    derive_from_enablement: str,
    env_var_name: str = "V2_EXECUTION_ENABLED",
) -> bool:
    """Parse boolean from environment variable with lenient handling.

    Rules:
    - Truthy: true, 1, yes, on
    - Falsy: false, 0, no, off
    - Unset/empty/whitespace: derive from enablement (shadow/live → True, disabled → False)
    - Unrecognized non-empty: log WARNING, derive default

    Args:
        value: Raw env var value
        derive_from_enablement: Resolved enablement to derive default from
        env_var_name: Name of env var for logging

    Returns:
        Parsed boolean value
    """
    normalized = _normalize_string(value)

    # Unset → derive from enablement
    if normalized is None:
        return derive_from_enablement in {"shadow", "live"}

    # Truthy values
    if normalized in TRUTHY_VALUES:
        return True

    # Falsy values
    if normalized in FALSY_VALUES:
        return False

    # Unrecognized → warn and derive
    logger.warning(
        "Unrecognized boolean value for %s: '%s'. "
        "Expected true/false/1/0/yes/no/on/off. "
        "Deriving default from enablement '%s'.",
        env_var_name,
        value,
        derive_from_enablement,
    )
    return derive_from_enablement in {"shadow", "live"}


@dataclass
class RuntimeControls:
    """Runtime controls for v2 policy behavior, ML model, and cascade routing.

    Fields:
        policy_loader_mode: "permissive" or "strict"
        v2_enablement: "disabled", "shadow", or "live"
        v2_execution_enabled: Whether v2 scoring is active
        ml_enablement: "disabled", "shadow", or "live" (ML thesis model)
        ml_model_path: Path to trained ML model file (joblib)
        cascade_routing_enablement: "disabled", "shadow", or "live" (cascade routing)

    Invariants (enforced at construction):
        - enablement in {shadow, live} → loader_mode must be "strict"
        - enablement == live → v2_execution_enabled must be True
        - cascade_routing_enablement in VALID_CASCADE_ENABLEMENTS
    """

    policy_loader_mode: str
    v2_enablement: str
    v2_execution_enabled: bool
    ml_enablement: str = "disabled"
    ml_model_path: Optional[str] = None
    cascade_routing_enablement: str = "disabled"

    def __post_init__(self):
        """Validate membership after initialization."""
        # Bug #6 mitigation: explicit membership validation
        if self.policy_loader_mode not in VALID_LOADER_MODES:
            raise ValueError(
                f"Invalid policy_loader_mode: '{self.policy_loader_mode}'. "
                f"Must be one of: {sorted(VALID_LOADER_MODES)}"
            )
        if self.v2_enablement not in VALID_ENABLEMENTS:
            raise ValueError(
                f"Invalid v2_enablement: '{self.v2_enablement}'. "
                f"Must be one of: {sorted(VALID_ENABLEMENTS)}"
            )
        if self.ml_enablement not in VALID_ML_ENABLEMENTS:
            raise ValueError(
                f"Invalid ml_enablement: '{self.ml_enablement}'. "
                f"Must be one of: {sorted(VALID_ML_ENABLEMENTS)}"
            )
        if self.cascade_routing_enablement not in VALID_CASCADE_ENABLEMENTS:
            raise ValueError(
                f"Invalid cascade_routing_enablement: '{self.cascade_routing_enablement}'. "
                f"Must be one of: {sorted(VALID_CASCADE_ENABLEMENTS)}"
            )

    @classmethod
    def from_env(
        cls,
        *,
        v2_enablement: Optional[str] = None,
        policy_loader_mode: Optional[str] = None,
        v2_execution_enabled: Optional[bool] = None,
        enable_v2_policy: Optional[bool] = None,
        ml_enablement: Optional[str] = None,
        ml_model_path: Optional[str] = None,
        cascade_routing_enablement: Optional[str] = None,
        phase_gates_path: Optional[str] = None,
    ) -> "RuntimeControls":
        """Create RuntimeControls from kwargs and environment variables.

        Precedence (highest to lowest):
        1. Explicit kwargs (v2_enablement, policy_loader_mode, v2_execution_enabled)
        2. Legacy kwarg (enable_v2_policy) - only if modern not provided
        3. Environment variables (V2_ENABLEMENT, POLICY_LOADER_MODE, V2_EXECUTION_ENABLED)
        4. Defaults (disabled, permissive/strict derived, derived from enablement)

        ML controls:
        1. Explicit kwargs (ml_enablement, ml_model_path)
        2. Environment variables (ML_ENABLEMENT, ML_MODEL_PATH)
        3. Defaults (disabled, None)

        Cascade routing controls:
        1. Explicit kwargs (cascade_routing_enablement)
        2. Environment variables (CASCADE_ROUTING_ENABLEMENT)
        3. Default: disabled
        4. Phase gate enforcement: live requires web3_ambiguity_gate=passed

        Legacy mapping:
        - enable_v2_policy=True → v2_enablement="shadow"
        - enable_v2_policy=False → v2_enablement="disabled"

        Invariants (auto-corrected with WARNING for env misconfig):
        - shadow/live → loader_mode must be "strict"
        - live → v2_execution_enabled must be True

        Invalid explicit args raise ValueError (programmer error).
        Invalid env values log WARNING and use defaults (config error).
        """
        # Step 1: Resolve enablement
        resolved_enablement = cls._resolve_enablement(
            v2_enablement=v2_enablement,
            enable_v2_policy=enable_v2_policy,
        )

        # Step 2: Resolve loader mode (may depend on enablement)
        resolved_loader_mode = cls._resolve_loader_mode(
            policy_loader_mode=policy_loader_mode,
            enablement=resolved_enablement,
        )

        # Step 3: Resolve execution enabled (depends on enablement)
        resolved_execution = cls._resolve_execution_enabled(
            v2_execution_enabled=v2_execution_enabled,
            enablement=resolved_enablement,
        )

        # Step 4: Enforce invariants with corrections
        resolved_loader_mode, resolved_execution = cls._enforce_invariants(
            enablement=resolved_enablement,
            loader_mode=resolved_loader_mode,
            execution_enabled=resolved_execution,
        )

        # Step 5: Resolve ML controls
        resolved_ml_enablement = cls._resolve_ml_enablement(ml_enablement)
        resolved_ml_model_path = cls._resolve_ml_model_path(ml_model_path)

        # Step 6: Resolve cascade routing (with phase gate enforcement)
        resolved_cascade = cls._resolve_cascade_routing(
            cascade_routing_enablement=cascade_routing_enablement,
            phase_gates_path=phase_gates_path,
        )

        # Log resolved values at DEBUG level
        logger.debug(
            "RuntimeControls resolved: enablement=%s, loader_mode=%s, execution=%s, "
            "ml_enablement=%s, ml_model_path=%s, cascade=%s",
            resolved_enablement,
            resolved_loader_mode,
            resolved_execution,
            resolved_ml_enablement,
            resolved_ml_model_path,
            resolved_cascade,
        )

        return cls(
            policy_loader_mode=resolved_loader_mode,
            v2_enablement=resolved_enablement,
            v2_execution_enabled=resolved_execution,
            ml_enablement=resolved_ml_enablement,
            ml_model_path=resolved_ml_model_path,
            cascade_routing_enablement=resolved_cascade,
        )

    @classmethod
    def _resolve_enablement(
        cls,
        v2_enablement: Optional[str],
        enable_v2_policy: Optional[bool],
    ) -> str:
        """Resolve v2_enablement from args and env.

        Precedence:
        1. Explicit v2_enablement kwarg
        2. Legacy enable_v2_policy mapped
        3. Env V2_ENABLEMENT
        4. Default "disabled"
        """
        # 1. Explicit v2_enablement (highest priority)
        if v2_enablement is not None:
            normalized = _normalize_string(v2_enablement)
            if normalized is None:
                # Explicit empty string → treat as unset, fall through
                pass
            elif normalized not in VALID_ENABLEMENTS:
                # Invalid explicit arg → programmer error
                raise ValueError(
                    f"Invalid v2_enablement: '{v2_enablement}'. "
                    f"Must be one of: {sorted(VALID_ENABLEMENTS)}"
                )
            else:
                return normalized

        # 2. Legacy enable_v2_policy mapping (only if modern not provided)
        if enable_v2_policy is not None:
            if enable_v2_policy:
                return "shadow"
            else:
                return "disabled"

        # 3. Environment variable
        env_value = os.environ.get("V2_ENABLEMENT")
        normalized_env = _normalize_string(env_value)
        if normalized_env is not None:
            if normalized_env not in VALID_ENABLEMENTS:
                # Invalid env value → warn and treat as unset
                logger.warning(
                    "Invalid V2_ENABLEMENT env value: '%s'. "
                    "Expected one of: %s. Using default 'disabled'.",
                    env_value,
                    sorted(VALID_ENABLEMENTS),
                )
            else:
                return normalized_env

        # 4. Default
        return "disabled"

    @classmethod
    def _resolve_loader_mode(
        cls,
        policy_loader_mode: Optional[str],
        enablement: str,
    ) -> str:
        """Resolve policy_loader_mode from args and env.

        Precedence:
        1. Explicit policy_loader_mode kwarg
        2. Env POLICY_LOADER_MODE
        3. Derived: shadow/live → "strict", else "permissive"
        """
        # 1. Explicit kwarg
        if policy_loader_mode is not None:
            normalized = _normalize_string(policy_loader_mode)
            if normalized is None:
                # Explicit empty → treat as unset
                pass
            elif normalized not in VALID_LOADER_MODES:
                # Invalid explicit arg → programmer error
                raise ValueError(
                    f"Invalid policy_loader_mode: '{policy_loader_mode}'. "
                    f"Must be one of: {sorted(VALID_LOADER_MODES)}"
                )
            else:
                return normalized

        # 2. Environment variable
        env_value = os.environ.get("POLICY_LOADER_MODE")
        normalized_env = _normalize_string(env_value)
        if normalized_env is not None:
            if normalized_env not in VALID_LOADER_MODES:
                # Invalid env value → warn and treat as unset
                logger.warning(
                    "Invalid POLICY_LOADER_MODE env value: '%s'. "
                    "Expected one of: %s. Deriving from enablement.",
                    env_value,
                    sorted(VALID_LOADER_MODES),
                )
            else:
                return normalized_env

        # 3. Derive from enablement
        if enablement in {"shadow", "live"}:
            return "strict"
        return "permissive"

    @classmethod
    def _resolve_execution_enabled(
        cls,
        v2_execution_enabled: Optional[bool],
        enablement: str,
    ) -> bool:
        """Resolve v2_execution_enabled from args and env.

        Precedence:
        1. Explicit v2_execution_enabled kwarg
        2. Env V2_EXECUTION_ENABLED (with lenient parsing)
        3. Derived: shadow/live → True, disabled → False
        """
        # 1. Explicit kwarg
        if v2_execution_enabled is not None:
            return v2_execution_enabled

        # 2. Environment variable with lenient parsing
        env_value = os.environ.get("V2_EXECUTION_ENABLED")
        return _parse_bool_env(env_value, enablement)

    @classmethod
    def _enforce_invariants(
        cls,
        enablement: str,
        loader_mode: str,
        execution_enabled: bool,
    ) -> tuple[str, bool]:
        """Enforce invariants, auto-correcting with warnings.

        Invariants:
        - shadow/live → loader_mode must be "strict"
        - live → execution_enabled must be True

        Returns corrected (loader_mode, execution_enabled).
        """
        corrected_loader_mode = loader_mode
        corrected_execution = execution_enabled

        # Invariant 1: shadow/live requires strict
        if enablement in {"shadow", "live"} and loader_mode != "strict":
            logger.warning(
                "Invariant violation: v2_enablement='%s' requires "
                "policy_loader_mode='strict', but got '%s'. Auto-correcting to 'strict'.",
                enablement,
                loader_mode,
            )
            corrected_loader_mode = "strict"

        # Invariant 2: live requires execution enabled
        if enablement == "live" and not execution_enabled:
            logger.warning(
                "Invariant violation: v2_enablement='live' requires "
                "v2_execution_enabled=True, but got False. Auto-correcting to True.",
            )
            corrected_execution = True

        return corrected_loader_mode, corrected_execution

    @classmethod
    def _resolve_ml_enablement(cls, ml_enablement: Optional[str]) -> str:
        """Resolve ml_enablement from args and env.

        Precedence:
        1. Explicit ml_enablement kwarg
        2. Env ML_ENABLEMENT
        3. Default "disabled"
        """
        # 1. Explicit kwarg
        if ml_enablement is not None:
            normalized = _normalize_string(ml_enablement)
            if normalized is None:
                pass  # Empty → fall through
            elif normalized not in VALID_ML_ENABLEMENTS:
                raise ValueError(
                    f"Invalid ml_enablement: '{ml_enablement}'. "
                    f"Must be one of: {sorted(VALID_ML_ENABLEMENTS)}"
                )
            else:
                return normalized

        # 2. Environment variable
        env_value = os.environ.get("ML_ENABLEMENT")
        normalized_env = _normalize_string(env_value)
        if normalized_env is not None:
            if normalized_env not in VALID_ML_ENABLEMENTS:
                logger.warning(
                    "Invalid ML_ENABLEMENT env value: '%s'. "
                    "Expected one of: %s. Using default 'disabled'.",
                    env_value,
                    sorted(VALID_ML_ENABLEMENTS),
                )
            else:
                return normalized_env

        # 3. Default
        return "disabled"

    @classmethod
    def _resolve_ml_model_path(cls, ml_model_path: Optional[str]) -> Optional[str]:
        """Resolve ml_model_path from args and env.

        Precedence:
        1. Explicit ml_model_path kwarg
        2. Env ML_MODEL_PATH
        3. Default None (model loader will use default path)
        """
        if ml_model_path is not None:
            return ml_model_path

        env_value = os.environ.get("ML_MODEL_PATH")
        if env_value and env_value.strip():
            return env_value.strip()

        return None

    @classmethod
    def _resolve_cascade_routing(
        cls,
        cascade_routing_enablement: Optional[str],
        phase_gates_path: Optional[str] = None,
    ) -> str:
        """Resolve cascade_routing_enablement from args, env, and phase gates.

        Precedence:
        1. Explicit kwarg
        2. Env CASCADE_ROUTING_ENABLEMENT
        3. Default "disabled"

        Phase gate enforcement (ADR-4):
        - If resolved to "live", check web3_ambiguity_gate in phase_gates.yaml
        - If gate not "passed" → downgrade to "disabled" with warning
        - Shadow mode is NOT blocked by gates (for instrumentation)
        """
        # 1. Explicit kwarg
        resolved = None
        if cascade_routing_enablement is not None:
            normalized = _normalize_string(cascade_routing_enablement)
            if normalized is None:
                pass  # Fall through
            elif normalized not in VALID_CASCADE_ENABLEMENTS:
                raise ValueError(
                    f"Invalid cascade_routing_enablement: '{cascade_routing_enablement}'. "
                    f"Must be one of: {sorted(VALID_CASCADE_ENABLEMENTS)}"
                )
            else:
                resolved = normalized

        # 2. Env var
        if resolved is None:
            env_value = os.environ.get("CASCADE_ROUTING_ENABLEMENT")
            normalized_env = _normalize_string(env_value)
            if normalized_env is not None:
                if normalized_env not in VALID_CASCADE_ENABLEMENTS:
                    logger.warning(
                        "Invalid CASCADE_ROUTING_ENABLEMENT env value: '%s'. "
                        "Expected one of: %s. Using default 'disabled'.",
                        env_value,
                        sorted(VALID_CASCADE_ENABLEMENTS),
                    )
                else:
                    resolved = normalized_env

        # 3. Default
        if resolved is None:
            resolved = "disabled"

        # Phase gate enforcement (only for live)
        if resolved == "live":
            resolved = cls._enforce_phase_gates(resolved, phase_gates_path)

        return resolved

    @classmethod
    def _enforce_phase_gates(
        cls,
        cascade_mode: str,
        phase_gates_path: Optional[str] = None,
    ) -> str:
        """Check phase gates; downgrade if gates not passed.

        ADR-4: cascade=live requires web3_ambiguity_gate=passed.
        """
        import yaml

        gates_path = phase_gates_path
        if gates_path is None:
            # Look for default path relative to project root
            project_root = Path(__file__).resolve().parent.parent
            gates_path = str(project_root / _DEFAULT_PHASE_GATES_PATH)

        try:
            gates_file = Path(gates_path)
            if not gates_file.exists():
                logger.warning(
                    "event=config_load_failed, applied=cascade_disabled, "
                    "reason=phase_gates_file_not_found, path=%s",
                    gates_path,
                )
                return "disabled"

            with open(gates_file) as f:
                gates = yaml.safe_load(f) or {}

            web3_gate = gates.get("web3_ambiguity_gate", {})
            gate_status = web3_gate.get("status", "pending")

            if gate_status != "passed":
                logger.warning(
                    "event=gate_blocked, applied=cascade_disabled, "
                    "reason=web3_ambiguity_gate_%s, "
                    "cascade_requested=live",
                    gate_status,
                )
                return "disabled"

            return cascade_mode

        except Exception as e:
            logger.warning(
                "event=config_load_failed, applied=cascade_disabled, "
                "reason=phase_gates_parse_error, error=%s",
                str(e),
            )
            return "disabled"

    @property
    def is_ml_active(self) -> bool:
        """Check if ML model is active (not disabled)."""
        return self.ml_enablement != "disabled"

    @property
    def is_ml_shadow(self) -> bool:
        """Check if ML is in shadow mode."""
        return self.ml_enablement == "shadow"

    @property
    def is_ml_live(self) -> bool:
        """Check if ML is in live mode."""
        return self.ml_enablement == "live"

    @property
    def is_v2_active(self) -> bool:
        """Check if v2 policy is active (not disabled)."""
        return self.v2_enablement != "disabled"

    @property
    def is_shadow_mode(self) -> bool:
        """Check if running in shadow mode."""
        return self.v2_enablement == "shadow"

    @property
    def is_live_mode(self) -> bool:
        """Check if running in live mode."""
        return self.v2_enablement == "live"

    @property
    def is_cascade_active(self) -> bool:
        """Check if cascade routing is active (shadow or live)."""
        return self.cascade_routing_enablement != "disabled"

    @property
    def is_cascade_shadow(self) -> bool:
        """Check if cascade routing is in shadow mode."""
        return self.cascade_routing_enablement == "shadow"

    @property
    def is_cascade_live(self) -> bool:
        """Check if cascade routing is in live mode."""
        return self.cascade_routing_enablement == "live"
