"""CI enforcement: no raw INSERT INTO audit_events outside approved modules.

The ONLY files allowed to contain 'INSERT INTO audit_events' are:
1. storage/audit_events.py — the canonical writer
2. storage/migrations/*.py — DDL and migration scripts
3. tests/ — test fixtures

Scans full file content (not line-by-line) to catch multi-line SQL strings
where INSERT and INTO audit_events may span lines.
"""

import os
import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ALLOWED_PREFIXES = (
    os.path.join("storage", "audit_events.py"),
    os.path.join("storage", "migrations") + os.sep,
    "tests" + os.sep,
)

# Match INSERT INTO audit_events across possible whitespace/newlines
_RAW_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+audit_events", re.IGNORECASE | re.DOTALL
)


def _is_allowed(rel_path: str) -> bool:
    for prefix in _ALLOWED_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    return False


def _scan_for_raw_inserts() -> list[str]:
    violations = []

    for root, _dirs, files in os.walk(_PROJECT_ROOT):
        rel_root = os.path.relpath(root, _PROJECT_ROOT)
        if any(
            part.startswith(".") or part == "__pycache__" or part == ".worktrees"
            for part in rel_root.split(os.sep)
        ):
            continue

        for fname in files:
            if not fname.endswith(".py"):
                continue

            rel_path = os.path.relpath(
                os.path.join(root, fname), _PROJECT_ROOT
            )

            if _is_allowed(rel_path):
                continue

            full_path = os.path.join(root, fname)
            try:
                content = open(
                    full_path, "r", encoding="utf-8", errors="replace"
                ).read()
                matches = list(_RAW_INSERT_RE.finditer(content))
                if matches:
                    # Find line numbers for reporting
                    for match in matches:
                        line_no = content[:match.start()].count("\n") + 1
                        violations.append(
                            f"{rel_path}:{line_no}: raw INSERT INTO "
                            f"audit_events (use insert_event() or "
                            f"governance/writer.py)"
                        )
            except OSError:
                continue

    return violations


class TestNoRawAuditInserts:
    def test_no_raw_inserts_outside_approved_modules(self):
        violations = _scan_for_raw_inserts()

        if violations:
            violation_list = "\n".join(f"  - {v}" for v in violations)
            pytest.fail(
                f"Found {len(violations)} raw INSERT INTO audit_events "
                f"outside approved modules:\n{violation_list}\n\n"
                f"Use storage.audit_events.insert_event() for tx-aware "
                f"writes or governance.writer for governance events."
            )
