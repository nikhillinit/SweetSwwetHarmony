"""
Policy Loader for Negative Keyword Policy v2.

Handles loading YAML policy files with permissive/strict modes.
Implements marker-based directory discovery for configuration files.

Bug hazards addressed:
- #1: permissive mode crashes on YAML parse errors → try/except + load_errors
- #2: missing files conflated with load/parse errors → separate fields
- #7: strict mode fails early → aggregate all, raise once at end
- #8: explicit path not validated → validate before I/O
- #9: silent fallback when no marker → raise if marker missing
- #10: env paths not validated symmetrically → same validation for all sources
- #11: generator exhaustion → tuple(specs) at entry

Usage:
    from utils.policy_loader import (
        resolve_policy_dir,
        load_policy_bundle,
        DEFAULT_V2_SPECS,
    )

    # Resolve policy directory
    policy_dir = resolve_policy_dir()  # Auto-discovery

    # Load policies
    bundle = load_policy_bundle(
        base_dir=policy_dir,
        specs=DEFAULT_V2_SPECS,
        loader_mode="strict",  # or "permissive"
    )

    # Access loaded policies
    if "negative_keyword_policy" in bundle.policies:
        policy = bundle.policies["negative_keyword_policy"]
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicySpec:
    """Specification for a policy file to load.

    Immutable (frozen=True) to prevent accidental modification.

    Attributes:
        name: Logical name for the policy (used as key in PolicyBundle.policies)
        filename: Filename to load from the policy directory
        required: If True, missing file is an error; if False, it's optional
    """

    name: str
    filename: str
    required: bool


@dataclass
class PolicyBundle:
    """Container for loaded policies and any errors encountered.

    Mutable to allow aggregation during loading.

    Attributes:
        base_dir: Directory policies were loaded from
        policies: Successfully loaded policies (name → parsed YAML dict)
        missing_required: Names of required policies that were not found
        load_errors: Names of policies that failed to load (name → error message)
        loaded_at: Timestamp when bundle was loaded
    """

    base_dir: Path
    policies: dict[str, dict] = field(default_factory=dict)
    missing_required: set[str] = field(default_factory=set)
    load_errors: dict[str, str] = field(default_factory=dict)
    loaded_at: datetime | None = None


# Default specs for v2 policy loading
DEFAULT_V2_SPECS: tuple[PolicySpec, ...] = (
    PolicySpec(
        name="negative_keyword_policy",
        filename="negative_keyword_policy.yaml",
        required=True,
    ),
)


def _validate_directory(path: Path, source: str) -> None:
    """Validate that path exists and is a directory.

    Args:
        path: Path to validate
        source: Description of where the path came from (for error messages)

    Raises:
        FileNotFoundError: If path does not exist
        NotADirectoryError: If path exists but is not a directory
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Policy directory from {source} ('{path}') does not exist."
        )
    if not path.is_dir():
        raise NotADirectoryError(
            f"Policy directory from {source} ('{path}') is not a directory."
        )


def resolve_policy_dir(
    explicit: str | None = None,
    *,
    search_root: Path | None = None,
) -> Path:
    """Resolve the policy directory using explicit path, env vars, or discovery.

    Resolution order:
    1. Explicit path (if provided) → validate exists + is_dir
    2. V2_POLICY_DIR env var → validate exists + is_dir
    3. HARMONIC_POLICY_DIR env var → validate exists + is_dir
    4. Default discovery anchored to search_root (or repo root):
       - Check config/v2/{marker} first
       - Fall back to config/{marker}
       - Raise if marker not found in either location

    The marker file is the required policy filename (negative_keyword_policy.yaml).

    Args:
        explicit: Explicit path to use (highest priority)
        search_root: Root directory for discovery (defaults to repo root)

    Returns:
        Resolved Path to the policy directory

    Raises:
        FileNotFoundError: If no valid policy directory found
        NotADirectoryError: If path exists but is not a directory
    """
    marker_filename = "negative_keyword_policy.yaml"

    # 1. Explicit path (highest priority)
    # Bug #8: Validate before any I/O
    if explicit is not None:
        explicit_path = Path(explicit)
        _validate_directory(explicit_path, "explicit path")
        logger.debug("Using explicit policy directory: %s", explicit_path)
        return explicit_path

    # 2. V2_POLICY_DIR env var
    # Bug #10: Same validation as explicit path
    env_v2 = os.environ.get("V2_POLICY_DIR")
    if env_v2:
        v2_path = Path(env_v2)
        _validate_directory(v2_path, "V2_POLICY_DIR env var")
        logger.debug("Using V2_POLICY_DIR: %s", v2_path)
        return v2_path

    # 3. HARMONIC_POLICY_DIR env var
    # Bug #10: Same validation as explicit path
    env_harmonic = os.environ.get("HARMONIC_POLICY_DIR")
    if env_harmonic:
        harmonic_path = Path(env_harmonic)
        _validate_directory(harmonic_path, "HARMONIC_POLICY_DIR env var")
        logger.debug("Using HARMONIC_POLICY_DIR: %s", harmonic_path)
        return harmonic_path

    # 4. Default discovery (code-location anchored)
    if search_root is None:
        # Default to repo root (parent of utils/)
        search_root = Path(__file__).resolve().parents[1]

    candidate_v2 = search_root / "config" / "v2"
    candidate_base = search_root / "config"

    # Prefer v2 if marker exists there
    if (candidate_v2 / marker_filename).exists():
        logger.debug("Discovered policy directory (v2): %s", candidate_v2)
        return candidate_v2

    # Fall back to config/ if marker exists there
    if (candidate_base / marker_filename).exists():
        logger.debug("Discovered policy directory (config): %s", candidate_base)
        return candidate_base

    # Bug #9: No silent fallback - raise if marker not found
    raise FileNotFoundError(
        f"No policy directory found: expected marker '{marker_filename}' "
        f"in '{candidate_v2}' or '{candidate_base}'."
    )


def load_policy_bundle(
    base_dir: Path,
    specs: Sequence[PolicySpec],
    loader_mode: str,
) -> PolicyBundle:
    """Load policy files according to specs.

    Bug #11: Converts specs to tuple at entry to prevent generator exhaustion.

    Args:
        base_dir: Directory containing policy files
        specs: Sequence of PolicySpec defining what to load
        loader_mode: "permissive" (collect errors) or "strict" (raise on errors)

    Returns:
        PolicyBundle with loaded policies and any errors

    Raises:
        RuntimeError: In strict mode, if any required policies are missing or errored
    """
    # Bug #11: Convert to tuple immediately to prevent generator exhaustion
    specs = tuple(specs)

    bundle = PolicyBundle(base_dir=base_dir)

    # Process all specs, never raise inside the loop (Bug #7)
    for spec in specs:
        policy_path = base_dir / spec.filename

        # Check if file exists
        if not policy_path.exists():
            if spec.required:
                bundle.missing_required.add(spec.name)
                logger.warning(
                    "Required policy '%s' not found at '%s'",
                    spec.name,
                    policy_path,
                )
            else:
                logger.debug(
                    "Optional policy '%s' not found at '%s'",
                    spec.name,
                    policy_path,
                )
            continue

        # Try to load and parse YAML
        # Bug #1: Catch all errors in permissive mode
        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)

            # Validate that content is a mapping (dict)
            if not isinstance(content, dict):
                error_msg = (
                    f"Failed to load policy '{spec.name}' from '{policy_path}': "
                    f"Expected YAML mapping (dict), got {type(content).__name__}"
                )
                bundle.load_errors[spec.name] = error_msg
                logger.warning(error_msg)
                continue

            # Success
            bundle.policies[spec.name] = content
            logger.debug("Loaded policy '%s' from '%s'", spec.name, policy_path)

        except yaml.YAMLError as e:
            error_msg = (
                f"Failed to load policy '{spec.name}' from '{policy_path}': "
                f"YAMLError: {e}"
            )
            bundle.load_errors[spec.name] = error_msg
            logger.warning(error_msg)

        except OSError as e:
            error_msg = (
                f"Failed to load policy '{spec.name}' from '{policy_path}': "
                f"OSError: {e}"
            )
            bundle.load_errors[spec.name] = error_msg
            logger.warning(error_msg)

    # Set loaded timestamp
    bundle.loaded_at = datetime.now()

    # Bug #7: In strict mode, aggregate all errors and raise ONCE at the end
    if loader_mode == "strict":
        # Only consider errors for required policies
        required_names = {spec.name for spec in specs if spec.required}

        # Filter missing_required and load_errors to only required policies
        missing = bundle.missing_required & required_names
        errors_on_required = {
            name: msg
            for name, msg in bundle.load_errors.items()
            if name in required_names
        }

        if missing or errors_on_required:
            raise RuntimeError(
                f"Strict policy load failed in '{base_dir}': "
                f"missing_required={sorted(missing)}; "
                f"load_errors={sorted(errors_on_required.keys())}"
            )

    return bundle
