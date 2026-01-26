"""
Configuration Management for Monitoring Subsystem

Loads config from config/monitoring.json and provides typed access.
Computes config hash for run linkage.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from monitoring.gating import GatingConfig
from monitoring.failure_classifier import FailureCategoryConfig, FailureCategory

logger = logging.getLogger(__name__)

# Default config file location
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "monitoring.json"


@dataclass
class OutboxConfig:
    """Outbox processing configuration."""
    max_attempts: int = 5
    stale_processing_ttl_minutes: int = 30
    default_backoff_seconds: float = 60.0


@dataclass
class RetentionConfig:
    """Data retention configuration."""
    max_snapshots_per_watch: int = 10
    max_diff_age_days: int = 90
    max_events_per_watch: int = 100


@dataclass
class SweepConfig:
    """Sweep execution configuration."""
    max_watches_per_run: int = 100
    advisory_lock_timeout_seconds: int = 30
    recent_hash_window_minutes: int = 5


@dataclass
class MonitoringConfigV2:
    """
    Complete monitoring configuration (v2.4).

    Loaded from config/monitoring.json and cached.
    """
    version: str = "2.4"

    # Gating rules
    gating: GatingConfig = field(default_factory=GatingConfig)

    # Severity weights (also in gating for convenience)
    severity_weights: Dict[str, float] = field(default_factory=lambda: {
        "content_delta": 0.30,
        "redirect_change": 0.25,
        "state_change": 0.35,
        "semantic_drift": 0.10,
    })

    # Failure handling per category
    failure_configs: Dict[FailureCategory, FailureCategoryConfig] = field(
        default_factory=dict
    )

    # Outbox settings
    outbox: OutboxConfig = field(default_factory=OutboxConfig)

    # Retention settings
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    # Sweep settings
    sweep: SweepConfig = field(default_factory=SweepConfig)

    # Computed hash for run linkage
    _config_hash: Optional[str] = field(default=None, repr=False)
    _raw_json: Optional[str] = field(default=None, repr=False)

    @property
    def config_hash(self) -> str:
        """Get SHA256 hash of the raw config JSON."""
        if self._config_hash is None:
            content = self._raw_json or json.dumps(self.to_dict(), sort_keys=True)
            self._config_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        return self._config_hash

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "version": self.version,
            "gating": {
                "alert_threshold": self.gating.alert_threshold,
                "profile_update_threshold": self.gating.profile_update_threshold,
                "critical_threshold": self.gating.critical_threshold,
                "low_sev_cooldown_threshold": self.gating.low_sev_cooldown_threshold,
                "cooldown_hours": self.gating.cooldown_hours,
                "post_alert_cooldown_minutes": self.gating.post_alert_cooldown_minutes,
            },
            "severity_weights": self.severity_weights,
            "outbox": {
                "max_attempts": self.outbox.max_attempts,
                "stale_processing_ttl_minutes": self.outbox.stale_processing_ttl_minutes,
                "default_backoff_seconds": self.outbox.default_backoff_seconds,
            },
            "retention": {
                "max_snapshots_per_watch": self.retention.max_snapshots_per_watch,
                "max_diff_age_days": self.retention.max_diff_age_days,
                "max_events_per_watch": self.retention.max_events_per_watch,
            },
            "sweep": {
                "max_watches_per_run": self.sweep.max_watches_per_run,
                "advisory_lock_timeout_seconds": self.sweep.advisory_lock_timeout_seconds,
                "recent_hash_window_minutes": self.sweep.recent_hash_window_minutes,
            },
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


class ConfigLoader:
    """
    Loads and caches monitoring configuration.

    Usage:
        loader = ConfigLoader()
        config = loader.load()  # From default path
        config = loader.load("/path/to/config.json")  # Custom path

        # Get config hash for run linkage
        print(config.config_hash)
    """

    def __init__(self, default_path: Optional[Path] = None):
        """
        Initialize loader.

        Args:
            default_path: Default config file path (uses DEFAULT_CONFIG_PATH if not provided)
        """
        self.default_path = default_path or DEFAULT_CONFIG_PATH
        self._cache: Optional[MonitoringConfigV2] = None
        self._cache_path: Optional[Path] = None

    def load(self, path: Optional[Path] = None, force_reload: bool = False) -> MonitoringConfigV2:
        """
        Load configuration from file.

        Args:
            path: Config file path (uses default if not provided)
            force_reload: Force reload even if cached

        Returns:
            MonitoringConfigV2 instance
        """
        path = Path(path) if path else self.default_path

        # Check cache
        if not force_reload and self._cache and self._cache_path == path:
            return self._cache

        # Load from file
        if not path.exists():
            logger.warning(f"Config file not found: {path}, using defaults")
            config = MonitoringConfigV2()
            self._cache = config
            self._cache_path = path
            return config

        try:
            with open(path, "r") as f:
                raw_json = f.read()
                data = json.loads(raw_json)

            config = self._parse_config(data, raw_json)
            self._cache = config
            self._cache_path = path

            logger.info(f"Loaded monitoring config from {path} (hash: {config.config_hash})")
            return config

        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
            raise

    def _parse_config(self, data: Dict[str, Any], raw_json: str) -> MonitoringConfigV2:
        """Parse config dict into MonitoringConfigV2."""
        # Parse gating config
        gating_data = data.get("gating", {})
        gating = GatingConfig(
            alert_threshold=gating_data.get("alert_threshold", 0.30),
            profile_update_threshold=gating_data.get("profile_update_threshold", 0.60),
            critical_threshold=gating_data.get("critical_threshold", 0.90),
            low_sev_cooldown_threshold=gating_data.get("low_sev_cooldown_threshold", 5),
            cooldown_hours=gating_data.get("cooldown_hours", 24),
            post_alert_cooldown_minutes=gating_data.get("post_alert_cooldown_minutes", 60),
        )

        # Parse severity weights
        severity_weights = data.get("severity_weights", {
            "content_delta": 0.30,
            "redirect_change": 0.25,
            "state_change": 0.35,
            "semantic_drift": 0.10,
        })

        # Update gating with severity weights
        gating.weight_content_delta = severity_weights.get("content_delta", 0.30)
        gating.weight_redirect_change = severity_weights.get("redirect_change", 0.25)
        gating.weight_state_change = severity_weights.get("state_change", 0.35)
        gating.weight_semantic_drift = severity_weights.get("semantic_drift", 0.10)

        # Parse failure handling configs
        failure_configs = {}
        failure_data = data.get("failure_handling", {})
        for category_name, cfg in failure_data.items():
            try:
                category = FailureCategory(category_name)
                failure_configs[category] = FailureCategoryConfig(
                    max_consecutive_failures=cfg.get("max_failures", 5),
                    backoff_type="exponential" if len(cfg.get("backoff_minutes", [60])) > 1 else "fixed",
                    backoff_values_minutes=cfg.get("backoff_minutes", [60]),
                )
            except ValueError:
                logger.warning(f"Unknown failure category: {category_name}")

        # Parse outbox config
        outbox_data = data.get("outbox", {})
        outbox = OutboxConfig(
            max_attempts=outbox_data.get("max_attempts", 5),
            stale_processing_ttl_minutes=outbox_data.get("stale_processing_ttl_minutes", 30),
            default_backoff_seconds=outbox_data.get("default_backoff_seconds", 60.0),
        )

        # Parse retention config
        retention_data = data.get("retention", {})
        retention = RetentionConfig(
            max_snapshots_per_watch=retention_data.get("max_snapshots_per_watch", 10),
            max_diff_age_days=retention_data.get("max_diff_age_days", 90),
            max_events_per_watch=retention_data.get("max_events_per_watch", 100),
        )

        # Parse sweep config
        sweep_data = data.get("sweep", {})
        sweep = SweepConfig(
            max_watches_per_run=sweep_data.get("max_watches_per_run", 100),
            advisory_lock_timeout_seconds=sweep_data.get("advisory_lock_timeout_seconds", 30),
            recent_hash_window_minutes=sweep_data.get("recent_hash_window_minutes", 5),
        )

        return MonitoringConfigV2(
            version=data.get("version", "2.4"),
            gating=gating,
            severity_weights=severity_weights,
            failure_configs=failure_configs,
            outbox=outbox,
            retention=retention,
            sweep=sweep,
            _raw_json=raw_json,
        )


# Global loader instance
_loader = ConfigLoader()


def load_config(path: Optional[Path] = None, force_reload: bool = False) -> MonitoringConfigV2:
    """
    Load monitoring configuration (convenience function).

    Args:
        path: Config file path (uses default if not provided)
        force_reload: Force reload even if cached

    Returns:
        MonitoringConfigV2 instance
    """
    return _loader.load(path, force_reload)


def get_config_hash(path: Optional[Path] = None) -> str:
    """Get the config hash for run linkage."""
    config = load_config(path)
    return config.config_hash
