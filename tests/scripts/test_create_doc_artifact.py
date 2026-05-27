from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "create_doc_artifact.py"

TARGET_DIR_BY_TYPE = {
    "spec": "docs/specs",
    "incident": "docs/incidents",
    "eval_update": "docs/evals",
    "approval": "docs/approvals",
}


def _front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, front_matter, body = text.split("---\n", 2)
    assert body.strip()
    return yaml.safe_load(front_matter)


def _as_created_at(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _run_generator(root: Path, artifact_type: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            artifact_type,
            "Task Zero Artifact",
            "--root",
            str(root),
            "--owner",
            "codex",
            "--status",
            "draft",
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


@pytest.mark.parametrize("artifact_type", sorted(TARGET_DIR_BY_TYPE))
def test_generator_creates_required_front_matter_for_artifact_types(
    tmp_path: Path, artifact_type: str
) -> None:
    result = _run_generator(tmp_path, artifact_type)

    assert result.returncode == 0, result.stderr
    generated = list((tmp_path / TARGET_DIR_BY_TYPE[artifact_type]).glob("*.md"))
    assert len(generated) == 1

    text = generated[0].read_text(encoding="utf-8")
    assert all(ord(char) < 128 for char in text)
    assert "[[" not in text
    assert "obsidian://" not in text

    data = _front_matter(generated[0])
    assert data["type"] == artifact_type
    assert data["status"] == "draft"
    assert data["owner"] == "codex"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", _as_created_at(data["created_at"]))
    assert data["related_prs"] == []
    assert data["related_files"] == []


def test_generator_adds_approval_specific_front_matter(tmp_path: Path) -> None:
    result = _run_generator(tmp_path, "approval")

    assert result.returncode == 0, result.stderr
    generated = next((tmp_path / "docs/approvals").glob("*.md"))
    data = _front_matter(generated)
    assert data["approval_required_for"] == []
    assert data["approval_log"] == []
