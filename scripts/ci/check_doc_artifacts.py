from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml


TARGET_DIR_BY_TYPE = {
    "spec": "docs/specs",
    "incident": "docs/incidents",
    "eval_update": "docs/evals",
    "approval": "docs/approvals",
    "adr": "docs/decisions",
    "runbook": "docs/runbooks",
}

TYPE_BY_ARTIFACT_DIR = {
    "specs": "spec",
    "incidents": "incident",
    "evals": "eval_update",
    "approvals": "approval",
}

OPT_IN_ARTIFACT_DIRS = {
    "decisions": "adr",
    "runbooks": "runbook",
}

REQUIRED_KEYS = (
    "type",
    "status",
    "owner",
    "created_at",
    "related_prs",
    "related_files",
)

VALID_STATUSES = {
    "draft",
    "active",
    "accepted",
    "resolved",
    "superseded",
    "archived",
}

OWNER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _relative_parts(path: Path, docs_root: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(docs_root).parts
    except ValueError:
        return ()


def _has_front_matter(path: Path) -> bool:
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
    except (IndexError, UnicodeDecodeError):
        return False
    return first_line.strip() == "---"


def is_artifact_candidate(path: Path, docs_root: Path) -> bool:
    if path.suffix != ".md" or path.name.lower() == "readme.md":
        return False

    parts = _relative_parts(path, docs_root)
    if not parts:
        return False

    top_level = parts[0]
    if top_level == "templates":
        return False
    if top_level in TYPE_BY_ARTIFACT_DIR:
        return True
    if top_level in OPT_IN_ARTIFACT_DIRS:
        return _has_front_matter(path)
    return False


def _parse_front_matter(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        return None, [f"{path}: UTF-8 decode error: {exc}"]

    if not lines or lines[0].strip() != "---":
        return None, [f"{path}: missing YAML front matter"]

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return None, [f"{path}: unterminated YAML front matter"]

    raw_front_matter = "\n".join(lines[1:closing_index])
    try:
        data = yaml.safe_load(raw_front_matter)
    except yaml.YAMLError as exc:
        return None, [f"{path}: invalid YAML front matter: {exc}"]

    if not isinstance(data, dict):
        return None, [f"{path}: YAML front matter must be a mapping"]
    return data, []


def _created_at_text(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def validate_file(path: Path, docs_root: Path) -> list[str]:
    errors: list[str] = []
    data, parse_errors = _parse_front_matter(path)
    if parse_errors:
        return parse_errors
    assert data is not None

    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"{path}: missing required key: {key}")

    artifact_type = data.get("type")
    if artifact_type not in TARGET_DIR_BY_TYPE:
        errors.append(f"{path}: invalid type: {artifact_type!r}")

    parts = _relative_parts(path, docs_root)
    if parts:
        expected_type = TYPE_BY_ARTIFACT_DIR.get(parts[0]) or OPT_IN_ARTIFACT_DIRS.get(parts[0])
        if expected_type and artifact_type != expected_type:
            errors.append(
                f"{path}: type {artifact_type!r} does not match docs/{parts[0]} "
                f"(expected {expected_type!r})"
            )

    status = data.get("status")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        errors.append(f"{path}: invalid status: {status!r}")

    owner = data.get("owner")
    if not isinstance(owner, str) or not OWNER_PATTERN.fullmatch(owner):
        errors.append(f"{path}: invalid owner: {owner!r}")

    created_at = data.get("created_at")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _created_at_text(created_at)):
        errors.append(f"{path}: created_at must be YYYY-MM-DD")

    for list_key in ("related_prs", "related_files"):
        if list_key in data and not isinstance(data[list_key], list):
            errors.append(f"{path}: {list_key} must be a list")

    if artifact_type == "approval":
        for approval_key in ("approval_required_for", "approval_log"):
            if approval_key not in data:
                errors.append(f"{path}: missing required key: {approval_key}")
            elif not isinstance(data[approval_key], list):
                errors.append(f"{path}: {approval_key} must be a list")

    return errors


def main(docs_dir: str = "docs") -> int:
    docs_root = Path(docs_dir)
    if not docs_root.exists():
        print(f"WARNING: {docs_dir} directory not found, skipping artifact validation")
        return 0

    errors: list[str] = []
    checked = 0
    for path in sorted(docs_root.rglob("*.md")):
        if not is_artifact_candidate(path, docs_root):
            continue
        checked += 1
        errors.extend(validate_file(path, docs_root))

    if errors:
        print(f"Doc artifact validation FAILED: {len(errors)} issues in {checked} files")
        for error in errors:
            print(f"  {error}")
        return 1

    print(f"Doc artifact validation passed: {checked} files checked")
    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "docs"
    raise SystemExit(main(path))
