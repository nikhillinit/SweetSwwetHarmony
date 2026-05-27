from __future__ import annotations

import argparse
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

DEFAULT_TEMPLATE_BY_TYPE = {
    "spec": "# {title}\n\n## Context\n\n## Decision\n\n## Verification\n",
    "incident": "# {title}\n\n## Impact\n\n## Timeline\n\n## Resolution\n",
    "eval_update": "# {title}\n\n## Change\n\n## Evidence\n\n## Follow-Up\n",
    "approval": "# {title}\n\n## Request\n\n## Approval Notes\n",
    "adr": "# {title}\n\n## Status\n\n## Context\n\n## Decision\n",
    "runbook": "# {title}\n\n## Purpose\n\n## Procedure\n\n## Rollback\n",
}


def slugify(value: str) -> str:
    lowered = value.lower()
    replaced = re.sub(r"[^a-z0-9]+", "-", lowered)
    return replaced.strip("-") or "artifact"


def _front_matter(artifact_type: str, owner: str, status: str, created_at: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": artifact_type,
        "status": status,
        "owner": owner,
        "created_at": created_at,
        "related_prs": [],
        "related_files": [],
    }
    if artifact_type == "approval":
        data["approval_required_for"] = []
        data["approval_log"] = []
    return data


def _load_template(root: Path, artifact_type: str) -> str:
    template_path = root / "docs" / "templates" / f"{artifact_type}.md"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return DEFAULT_TEMPLATE_BY_TYPE[artifact_type]


def _unique_path(directory: Path, created_at: str, title: str) -> Path:
    base_name = f"{created_at}-{slugify(title)}"
    candidate = directory / f"{base_name}.md"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{base_name}-{suffix}.md"
        suffix += 1
    return candidate


def create_artifact(
    artifact_type: str,
    title: str,
    *,
    root: Path,
    owner: str = "codex",
    status: str = "draft",
    created_at: str | None = None,
) -> Path:
    if artifact_type not in TARGET_DIR_BY_TYPE:
        valid = ", ".join(sorted(TARGET_DIR_BY_TYPE))
        raise ValueError(f"unknown artifact type {artifact_type!r}; valid types: {valid}")

    created_at = created_at or date.today().isoformat()
    target_dir = root / TARGET_DIR_BY_TYPE[artifact_type]
    target_dir.mkdir(parents=True, exist_ok=True)

    front_matter = _front_matter(artifact_type, owner, status, created_at)
    template = _load_template(root, artifact_type).format(title=title)
    output_path = _unique_path(target_dir, created_at, title)
    output_path.write_text(
        "---\n"
        + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=False)
        + "---\n"
        + template.rstrip()
        + "\n",
        encoding="utf-8",
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a local-first docs artifact.")
    parser.add_argument("artifact_type", choices=sorted(TARGET_DIR_BY_TYPE))
    parser.add_argument("title")
    parser.add_argument("--root", default=".", help="Repository root to write under.")
    parser.add_argument("--owner", default="codex")
    parser.add_argument("--status", default="draft")
    parser.add_argument("--created-at", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_path = create_artifact(
            args.artifact_type,
            args.title,
            root=Path(args.root),
            owner=args.owner,
            status=args.status,
            created_at=args.created_at,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
