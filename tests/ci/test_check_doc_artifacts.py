from __future__ import annotations

from pathlib import Path

import yaml

from scripts.ci.check_doc_artifacts import main, validate_file


def _write_artifact(root: Path, relative_path: str, front_matter: dict[str, object]) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.safe_dump(front_matter, sort_keys=False)
        + "---\n"
        + "# Artifact\n\n"
        + "Body.\n",
        encoding="utf-8",
    )
    return path


def _valid_front_matter(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "type": "spec",
        "status": "draft",
        "owner": "codex",
        "created_at": "2026-05-27",
        "related_prs": [],
        "related_files": [],
    }
    data.update(overrides)
    return data


def test_valid_artifact_directory_passes(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "docs/specs/good.md", _valid_front_matter())

    assert main(str(tmp_path / "docs")) == 0


def test_missing_type_is_rejected(tmp_path: Path) -> None:
    data = _valid_front_matter()
    del data["type"]
    path = _write_artifact(tmp_path, "docs/specs/missing-type.md", data)

    errors = validate_file(path, tmp_path / "docs")

    assert any("missing required key: type" in error for error in errors)


def test_invalid_status_is_rejected(tmp_path: Path) -> None:
    path = _write_artifact(
        tmp_path,
        "docs/incidents/bad-status.md",
        _valid_front_matter(type="incident", status="todo"),
    )

    errors = validate_file(path, tmp_path / "docs")

    assert any("invalid status" in error for error in errors)


def test_invalid_owner_is_rejected(tmp_path: Path) -> None:
    path = _write_artifact(
        tmp_path,
        "docs/evals/bad-owner.md",
        _valid_front_matter(type="eval_update", owner="bad owner"),
    )

    errors = validate_file(path, tmp_path / "docs")

    assert any("invalid owner" in error for error in errors)


def test_invalid_approval_front_matter_is_rejected(tmp_path: Path) -> None:
    path = _write_artifact(
        tmp_path,
        "docs/approvals/bad-approval.md",
        _valid_front_matter(type="approval"),
    )

    errors = validate_file(path, tmp_path / "docs")

    assert any("approval_required_for" in error for error in errors)
    assert any("approval_log" in error for error in errors)


def test_malformed_markdown_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "docs/specs/malformed.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Missing front matter\n", encoding="utf-8")

    errors = validate_file(path, tmp_path / "docs")

    assert any("missing YAML front matter" in error for error in errors)
