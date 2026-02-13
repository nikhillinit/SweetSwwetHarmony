"""
Standalone env file validator.

Loads a .env file into the environment, runs validate_config(), and reports
errors/warnings. Exit 0 = clean, exit 1 = errors found.

Usage:
    python scripts/validate_env.py [--env-file .env.production]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a .env file and return key-value pairs.

    Handles:
    - KEY=VALUE (simple)
    - KEY="VALUE" or KEY='VALUE' (quoted)
    - # comments and blank lines (skipped)
    - Inline comments after unquoted values
    """
    pairs: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Remove surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        elif "#" in value:
            # Strip inline comment for unquoted values
            value = value.split("#")[0].strip()

        pairs[key] = value
    return pairs


def validate_env(env_path: str | Path) -> int:
    """Load env file, inject into os.environ, run validate_config.

    Returns:
        0 if no errors, 1 if errors found.
    """
    env_path = Path(env_path)
    if not env_path.exists():
        print(f"ERROR: Env file not found: {env_path}", file=sys.stderr)
        return 1

    # Load and inject
    pairs = load_env_file(env_path)
    for key, value in pairs.items():
        os.environ[key] = value

    # Run validation
    from utils.config_validator import validate_config, print_config_report

    issues = validate_config()
    has_errors = print_config_report(issues)
    return 1 if has_errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a production .env file"
    )
    parser.add_argument(
        "--env-file",
        default=".env.production",
        help="Path to the .env file to validate (default: .env.production)",
    )
    args = parser.parse_args(argv)
    return validate_env(args.env_file)


if __name__ == "__main__":
    sys.exit(main())
