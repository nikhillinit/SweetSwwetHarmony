"""
Pre-flight checklist for production activation.

Validates ALL prerequisites before starting the 4-step activation sequence.
Pure Python implementation — no shell commands, no `gh` CLI.

Checks:
  1. DB exists + PRAGMA integrity_check
  2. Schema version == CURRENT_SCHEMA_VERSION
  3. Config validation clean
  4. Smoke suite passes (--mode full only)
  5. Activation gate Step 1 returns ready/warn
  6. Backup exists (< 24h old)
  7. Canary golden set defined (>0 items)
  8. Regression freshness (GitHub API)
  9. API server reachable (optional)

Usage:
    python scripts/preflight_check.py [--db signals.db] [--json] [--mode quick|full]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# Bootstrap project root so this script works from any CWD
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.db_path_helper import resolve_db_path_env

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Check result types
PASS = "pass"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


def _check_db_integrity(db_path: Path) -> dict[str, Any]:
    """Check 1: DB exists + PRAGMA integrity_check."""
    if not db_path.exists():
        return {"check": "db_integrity", "status": FAIL, "message": f"Database not found: {db_path}"}

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] == "ok":
                return {"check": "db_integrity", "status": PASS, "message": "Database integrity OK"}
            return {"check": "db_integrity", "status": FAIL, "message": f"Integrity check: {result[0]}"}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return {"check": "db_integrity", "status": FAIL, "message": f"Database error: {exc}"}


def _check_schema_version(db_path: Path) -> dict[str, Any]:
    """Check 2: Schema version matches CURRENT_SCHEMA_VERSION."""
    from storage.signal_store import CURRENT_SCHEMA_VERSION

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            if row is None or row[0] is None:
                return {
                    "check": "schema_version",
                    "status": FAIL,
                    "message": "No schema version found in schema_migrations",
                }
            version = row[0]
            if version < CURRENT_SCHEMA_VERSION:
                return {
                    "check": "schema_version",
                    "status": FAIL,
                    "message": f"Schema v{version} < expected v{CURRENT_SCHEMA_VERSION} (missing migrations)",
                }
            if version > CURRENT_SCHEMA_VERSION:
                return {
                    "check": "schema_version",
                    "status": WARN,
                    "message": f"Schema v{version} > expected v{CURRENT_SCHEMA_VERSION} (binary/schema mismatch?)",
                }
            return {
                "check": "schema_version",
                "status": PASS,
                "message": f"Schema version v{version} matches expected v{CURRENT_SCHEMA_VERSION}",
            }
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return {"check": "schema_version", "status": FAIL, "message": f"Schema check error: {exc}"}


def _check_config_validation() -> dict[str, Any]:
    """Check 3: Config validation clean."""
    from utils.config_validator import validate_config

    issues = validate_config()
    errors = [i for i in issues if i.level == "error"]
    if errors:
        msgs = [f"{e.key}: {e.message}" for e in errors]
        return {
            "check": "config_validation",
            "status": FAIL,
            "message": f"{len(errors)} config error(s): {'; '.join(msgs)}",
        }
    return {"check": "config_validation", "status": PASS, "message": "Config validation clean"}


def _check_smoke_suite() -> dict[str, Any]:
    """Check 4: Smoke suite passes (full mode only)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/smoke/", "-q", "--tb=line"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return {"check": "smoke_suite", "status": PASS, "message": "Smoke suite passed"}
        return {
            "check": "smoke_suite",
            "status": FAIL,
            "message": f"Smoke suite failed (exit {result.returncode})",
        }
    except subprocess.TimeoutExpired:
        return {"check": "smoke_suite", "status": FAIL, "message": "Smoke suite timed out (120s)"}
    except FileNotFoundError:
        return {"check": "smoke_suite", "status": WARN, "message": "pytest not found"}


def _check_activation_gate(db_path: Path) -> dict[str, Any]:
    """Check 5: Activation gate Step 1 returns ready/warn."""
    try:
        from storage.signal_store import SignalStore
        from monitoring.activation_gate import check_activation_readiness

        async def _run():
            store = SignalStore(str(db_path))
            await store.initialize()
            try:
                return await check_activation_readiness(store, step=1)
            finally:
                await store.close()

        gate_result = asyncio.run(_run())
        if gate_result.can_proceed:
            return {
                "check": "activation_gate",
                "status": PASS if gate_result.verdict == "ready" else WARN,
                "message": f"Step 1 gate: {gate_result.verdict} ({', '.join(gate_result.reasons) or 'all clear'})",
            }
        return {
            "check": "activation_gate",
            "status": FAIL,
            "message": f"Step 1 gate blocked: {', '.join(gate_result.reasons)}",
        }
    except Exception as exc:
        return {"check": "activation_gate", "status": FAIL, "message": f"Gate check error: {exc}"}


def _check_backup_freshness(backup_dir: Path) -> dict[str, Any]:
    """Check 6: At least 1 backup < 24h old."""
    if not backup_dir.exists():
        return {"check": "backup_freshness", "status": WARN, "message": f"Backup directory not found: {backup_dir}"}

    from scripts.backup_db import BACKUP_PREFIX, BACKUP_SUFFIX

    backups = list(backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
    if not backups:
        return {"check": "backup_freshness", "status": WARN, "message": "No backups found"}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    for backup in sorted(backups, key=lambda p: p.name, reverse=True):
        mtime = datetime.fromtimestamp(backup.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            return {
                "check": "backup_freshness",
                "status": PASS,
                "message": f"Recent backup: {backup.name} ({mtime.isoformat()})",
            }

    oldest_name = sorted(backups, key=lambda p: p.name)[-1].name
    return {
        "check": "backup_freshness",
        "status": WARN,
        "message": f"No backup < 24h old (newest: {oldest_name})",
    }


def _check_canary_items(db_path: Path) -> dict[str, Any]:
    """Check 7: Canary golden set has >0 items."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM canary_runs").fetchone()
            count = row[0] if row else 0
            if count > 0:
                return {"check": "canary_items", "status": PASS, "message": f"{count} canary run(s) found"}
            return {"check": "canary_items", "status": WARN, "message": "No canary runs found (0 items)"}
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return {"check": "canary_items", "status": WARN, "message": "canary_runs table not found"}


def _check_regression_freshness() -> dict[str, Any]:
    """Check 8: Regression freshness via GitHub API (pure Python, no gh CLI)."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"check": "regression_freshness", "status": WARN, "message": "No GITHUB_TOKEN set"}

    # Determine commit SHA: try origin/main first, then HEAD
    sha = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not sha:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not sha:
        return {"check": "regression_freshness", "status": WARN, "message": "Could not determine commit SHA"}

    # Detect owner/repo from git remote
    owner_repo = _detect_owner_repo()
    if not owner_repo:
        return {"check": "regression_freshness", "status": WARN, "message": "Could not detect GitHub owner/repo"}

    # Query GitHub check runs API
    if _httpx is None:
        return {"check": "regression_freshness", "status": WARN, "message": "httpx not installed (pip install httpx)"}

    url = f"https://api.github.com/repos/{owner_repo}/commits/{sha}/check-runs"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    for attempt in range(2):
        try:
            resp = _httpx.get(url, headers=headers, timeout=_httpx.Timeout(10.0, read=15.0))
            if resp.status_code == 422:
                return {
                    "check": "regression_freshness",
                    "status": WARN,
                    "message": f"Commit {sha[:8]} not found on remote (unpushed?)",
                }
            if resp.status_code >= 500:
                if attempt == 0:
                    import time
                    time.sleep(2)
                    continue
                return {
                    "check": "regression_freshness",
                    "status": WARN,
                    "message": f"GitHub API returned {resp.status_code}",
                }
            if resp.status_code != 200:
                return {
                    "check": "regression_freshness",
                    "status": WARN,
                    "message": f"GitHub API returned {resp.status_code}",
                }

            data = resp.json()
            check_runs = data.get("check_runs", [])

            for run in check_runs:
                if run.get("name") == "Core Regression Suite":
                    conclusion = run.get("conclusion")
                    if conclusion == "success":
                        return {
                            "check": "regression_freshness",
                            "status": PASS,
                            "message": f"Core Regression Suite passed on {sha[:8]}",
                        }
                    if conclusion == "failure":
                        return {
                            "check": "regression_freshness",
                            "status": FAIL,
                            "message": f"Core Regression Suite FAILED on {sha[:8]}",
                        }
                    return {
                        "check": "regression_freshness",
                        "status": WARN,
                        "message": f"Core Regression Suite status: {conclusion} on {sha[:8]}",
                    }

            return {
                "check": "regression_freshness",
                "status": WARN,
                "message": f"No 'Core Regression Suite' check run found for {sha[:8]}",
            }
        except (_httpx.ConnectError, _httpx.TimeoutException, OSError):
            if attempt == 0:
                import time
                time.sleep(2)
                continue
            return {
                "check": "regression_freshness",
                "status": WARN,
                "message": "GitHub API unreachable after retry",
            }

    return {"check": "regression_freshness", "status": WARN, "message": "Unexpected state"}


def _detect_owner_repo() -> str | None:
    """Detect GitHub owner/repo from git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        # Handle SSH: git@github.com:owner/repo.git
        if ":" in url and "@" in url:
            path = url.split(":")[-1]
        # Handle HTTPS: https://github.com/owner/repo.git
        elif "github.com/" in url:
            path = url.split("github.com/")[-1]
        else:
            return None
        path = path.removesuffix(".git")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _check_api_health() -> dict[str, Any]:
    """Check 9: API server reachable (optional)."""
    if _httpx is not None:
        try:
            resp = _httpx.get("http://localhost:8000/api/v1/health", timeout=5.0)
            if resp.status_code == 200:
                return {"check": "api_health", "status": PASS, "message": "API server healthy"}
            return {"check": "api_health", "status": WARN, "message": f"API returned {resp.status_code}"}
        except Exception:
            return {"check": "api_health", "status": SKIP, "message": "API server not running (OK for pre-flight)"}
    else:
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:8000/api/v1/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return {"check": "api_health", "status": PASS, "message": "API server healthy"}
                return {"check": "api_health", "status": WARN, "message": f"API returned {resp.status}"}
        except Exception:
            return {"check": "api_health", "status": SKIP, "message": "API server not running (OK for pre-flight)"}


def run_preflight(
    db_path: str | Path | None = None,
    mode: str = "quick",
    backup_dir: str | Path = "backups",
) -> dict[str, Any]:
    """Run all pre-flight checks and return a report.

    Args:
        db_path: Path to the database.
        mode: "quick" (skip smoke suite) or "full" (include smoke suite).
        backup_dir: Directory containing backups.

    Returns:
        Dict with checks list and overall verdict.
    """
    db_path = Path(resolve_db_path_env(db_path))
    backup_dir = Path(backup_dir)
    checks: list[dict[str, Any]] = []

    # Checks 1-3 (always run)
    checks.append(_check_db_integrity(db_path))
    if checks[-1]["status"] != FAIL:
        checks.append(_check_schema_version(db_path))
    else:
        checks.append({"check": "schema_version", "status": SKIP, "message": "Skipped (DB check failed)"})
    checks.append(_check_config_validation())

    # Check 4 (full mode only)
    if mode == "full":
        checks.append(_check_smoke_suite())
    else:
        checks.append({"check": "smoke_suite", "status": SKIP, "message": "Skipped (quick mode)"})

    # Check 5 (requires DB)
    if checks[0]["status"] != FAIL:
        checks.append(_check_activation_gate(db_path))
    else:
        checks.append({"check": "activation_gate", "status": SKIP, "message": "Skipped (DB check failed)"})

    # Checks 6-9
    checks.append(_check_backup_freshness(backup_dir))
    if checks[0]["status"] != FAIL:
        checks.append(_check_canary_items(db_path))
    else:
        checks.append({"check": "canary_items", "status": SKIP, "message": "Skipped (DB check failed)"})
    checks.append(_check_regression_freshness())
    checks.append(_check_api_health())

    # Overall verdict
    statuses = [c["status"] for c in checks]
    if FAIL in statuses:
        overall = FAIL
    elif WARN in statuses:
        overall = WARN
    else:
        overall = PASS

    return {
        "overall": overall,
        "mode": mode,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-flight checklist for production activation")
    parser.add_argument("--db", default=None, help="Database path")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick",
                        help="Check mode: quick (default, ~5s) or full (includes smoke suite)")
    parser.add_argument("--backup-dir", default="backups", help="Backup directory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    report = run_preflight(args.db, args.mode, args.backup_dir)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status_icons = {PASS: "[PASS]", WARN: "[WARN]", FAIL: "[FAIL]", SKIP: "[SKIP]"}
        print(f"Pre-flight Check ({args.mode} mode)")
        print("=" * 60)
        for check in report["checks"]:
            icon = status_icons.get(check["status"], "[???]")
            print(f"  {icon} {check['check']}: {check['message']}")
        print("=" * 60)
        print(f"Overall: {report['overall'].upper()}")

    return 0 if report["overall"] != FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
