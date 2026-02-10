"""Feature States Module for SHADOW Experimentation Infrastructure.

Implements the ACTIVE/SHADOW/OFF feature state model from the founder_intel spec.

Key Concepts:
- ACTIVE: Feature affects ranking/routing/alerts
- SHADOW: Feature is computed and logged, but has 0 weight (learning mode)
- OFF: Feature is not computed (requires explicit enable + owner)

This enables the "build wide, activate narrow" experimentation pattern:
1. Deploy features in SHADOW mode
2. Log predictions without affecting outputs
3. Measure correlation with outcomes over 2-3 weeks
4. Promote to ACTIVE only if lift is demonstrated
5. Max 2 promotions/month to prevent instability

Usage:
    from utils.feature_states import FeatureRegistry, FeatureState

    registry = FeatureRegistry()

    # Check if feature should be computed
    if registry.is_enabled("boilerplate_defense"):
        result = compute_boilerplate_match(signal)

        # Only affect output if ACTIVE
        if registry.is_active("boilerplate_defense"):
            apply_penalty(signal, result)
        else:
            # SHADOW mode: log only
            await store.log_shadow_computation("boilerplate_defense", key, result)

Environment Overrides:
    FEATURE_<NAME>=active|shadow|off

    Example: FEATURE_BOILERPLATE_DEFENSE=active
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class FeatureState(Enum):
    """Feature state for experimentation.

    ACTIVE: Affects ranking/routing (has weight)
    SHADOW: Computed + logged, but 0 weight (learning mode)
    OFF: Not computed (disabled)
    """

    ACTIVE = "active"
    SHADOW = "shadow"
    OFF = "off"


@dataclass
class FeatureConfig:
    """Configuration for a single feature.

    Attributes:
        name: Feature identifier (snake_case)
        state: Current state (ACTIVE/SHADOW/OFF)
        owner: Accountable person (e.g., "@nikhi")
        description: What this feature does
    """

    name: str
    state: FeatureState
    description: str
    owner: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "name": self.name,
            "state": self.state.value,
            "owner": self.owner,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> FeatureConfig:
        """Deserialize from dict."""
        return cls(
            name=d["name"],
            state=FeatureState(d["state"]),
            owner=d.get("owner"),
            description=d.get("description", ""),
        )


# Default feature configurations from founder_intel spec
DEFAULT_FEATURES: Dict[str, FeatureConfig] = {
    "boilerplate_defense": FeatureConfig(
        name="boilerplate_defense",
        state=FeatureState.SHADOW,
        description="Token-based fingerprinting to filter starter kit noise (10 templates)",
    ),
    "team_shape": FeatureConfig(
        name="team_shape",
        state=FeatureState.SHADOW,
        description="2-5 contributor analysis, concentration scores, sustained activity",
    ),
    "founder_surfaces": FeatureConfig(
        name="founder_surfaces",
        state=FeatureState.SHADOW,
        description="Profile README + gist scanning for founder intent markers",
    ),
    "smart_money": FeatureConfig(
        name="smart_money",
        state=FeatureState.OFF,
        owner=None,
        description="GitHub handle watchlist for signal boost (requires manual curation)",
    ),
    "stargazer_expansion": FeatureConfig(
        name="stargazer_expansion",
        state=FeatureState.OFF,
        owner=None,
        description="KILLED per spec: High API cost, unclear value",
    ),
    "workflow_maturity": FeatureConfig(
        name="workflow_maturity",
        state=FeatureState.SHADOW,
        description="CI/tests/releases/security configs scoring",
    ),
    "commercial_intent": FeatureConfig(
        name="commercial_intent",
        state=FeatureState.SHADOW,
        description="Homepage, README markers, commercial deps, legal maturity",
    ),
    # Wave 2: Shadow entity resolution
    "shadow_entity_resolution": FeatureConfig(
        name="shadow_entity_resolution",
        state=FeatureState.OFF,
        description="Shadow mode Phase G entity resolution alongside Phase 1a identity",
    ),
    # Phase B: Thesis matching SHADOW logging
    "thesis_match": FeatureConfig(
        name="thesis_match",
        state=FeatureState.SHADOW,
        description="Log thesis match details (intent phrases, domain match, keywords) for analysis",
    ),
}


class FeatureRegistry:
    """Registry for managing feature states.

    Supports:
    - Default configurations from spec
    - Environment variable overrides (FEATURE_<NAME>=state)
    - File-based persistence for promotions/demotions
    """

    def __init__(self, features: Optional[Dict[str, FeatureConfig]] = None):
        """Initialize registry with defaults or provided features.

        Args:
            features: Optional custom features (defaults to DEFAULT_FEATURES)
        """
        # Start with defaults
        self._features: Dict[str, FeatureConfig] = {}
        for name, config in DEFAULT_FEATURES.items():
            self._features[name] = FeatureConfig(
                name=config.name,
                state=config.state,
                owner=config.owner,
                description=config.description,
            )

        # Overlay provided features
        if features:
            for name, config in features.items():
                self._features[name] = config

        # Apply environment overrides
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides.

        Format: FEATURE_<NAME>=active|shadow|off
        Example: FEATURE_BOILERPLATE_DEFENSE=active
        """
        for name in list(self._features.keys()):
            env_key = f"FEATURE_{name.upper()}"
            env_value = os.environ.get(env_key, "").lower()

            if env_value in ("active", "shadow", "off"):
                try:
                    new_state = FeatureState(env_value)
                    logger.info(f"Feature {name} overridden to {new_state.value} via {env_key}")
                    self._features[name] = FeatureConfig(
                        name=name,
                        state=new_state,
                        owner=self._features[name].owner,
                        description=self._features[name].description,
                    )
                except ValueError:
                    logger.warning(f"Invalid feature state in {env_key}: {env_value}")

    def get_state(self, feature_name: str) -> FeatureState:
        """Get current state of a feature.

        Args:
            feature_name: Feature identifier

        Returns:
            FeatureState (defaults to OFF for unknown features)
        """
        # Check env override first (for dynamic changes)
        env_key = f"FEATURE_{feature_name.upper()}"
        env_value = os.environ.get(env_key, "").lower()
        if env_value in ("active", "shadow", "off"):
            try:
                return FeatureState(env_value)
            except ValueError:
                pass

        config = self._features.get(feature_name)
        if config:
            return config.state
        return FeatureState.OFF  # Safe default for unknown features

    def set_state(
        self,
        feature_name: str,
        state: FeatureState,
        owner: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """Set feature state.

        Args:
            feature_name: Feature identifier
            state: New state
            owner: Optional owner for accountability
            description: Optional description (for new features)
        """
        existing = self._features.get(feature_name)
        self._features[feature_name] = FeatureConfig(
            name=feature_name,
            state=state,
            owner=owner if owner else (existing.owner if existing else None),
            description=description if description else (existing.description if existing else ""),
        )
        logger.info(f"Feature {feature_name} state changed to {state.value}")

    def is_active(self, feature_name: str) -> bool:
        """Check if feature is ACTIVE (affects output).

        Args:
            feature_name: Feature identifier

        Returns:
            True only if feature is in ACTIVE state
        """
        return self.get_state(feature_name) == FeatureState.ACTIVE

    def is_shadow(self, feature_name: str) -> bool:
        """Check if feature is in SHADOW mode (logged but 0 weight).

        Args:
            feature_name: Feature identifier

        Returns:
            True only if feature is in SHADOW state
        """
        return self.get_state(feature_name) == FeatureState.SHADOW

    def is_enabled(self, feature_name: str) -> bool:
        """Check if feature should be computed (ACTIVE or SHADOW).

        Args:
            feature_name: Feature identifier

        Returns:
            True if feature is ACTIVE or SHADOW (computed)
        """
        state = self.get_state(feature_name)
        return state in (FeatureState.ACTIVE, FeatureState.SHADOW)

    def get_config(self, feature_name: str) -> Optional[FeatureConfig]:
        """Get full configuration for a feature.

        Args:
            feature_name: Feature identifier

        Returns:
            FeatureConfig or None if unknown
        """
        return self._features.get(feature_name)

    def list_features(self, state: Optional[FeatureState] = None) -> List[str]:
        """List all registered features, optionally filtered by state.

        Args:
            state: Optional state filter

        Returns:
            List of feature names
        """
        if state is None:
            return list(self._features.keys())
        return [name for name, config in self._features.items() if config.state == state]

    def save_to_file(self, path: Path) -> None:
        """Save registry state to JSON file.

        Args:
            path: File path for config
        """
        data = {name: config.to_dict() for name, config in self._features.items()}
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Feature registry saved to {path}")

    @classmethod
    def load_from_file(cls, path: Path) -> FeatureRegistry:
        """Load registry from JSON file, merging with defaults.

        Args:
            path: File path for config

        Returns:
            FeatureRegistry with loaded + default features
        """
        features: Dict[str, FeatureConfig] = {}

        # Try to load from file
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                for name, config_dict in data.items():
                    features[name] = FeatureConfig.from_dict(config_dict)
                logger.info(f"Loaded feature registry from {path}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load feature registry from {path}: {e}")
        else:
            logger.debug(f"Feature registry file not found: {path}, using defaults")

        # Create registry (will merge with defaults in __init__)
        registry = cls()

        # Overlay loaded features (file takes precedence)
        for name, config in features.items():
            registry._features[name] = config

        return registry
