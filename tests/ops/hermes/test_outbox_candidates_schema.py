from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from integrations.hermes.config import PROJECT_ROOT
from integrations.hermes.tasks import outbox_purge
from integrations.hermes.tasks.registry import run_registered_task

from .test_outbox_purge_task import _args, _insert_outbox_row, _write_outbox_db


STALE_OVERLAY_KEYS = {
    "audit_id",
    "checks_run",
    "ledger_entries",
    "drifts",
    "failure_type",
    "next_action",
    "blast_radius",
    "required_gates",
    "rollback",
}


def _schema_path() -> Path:
    return (
        PROJECT_ROOT
        / "integrations"
        / "hermes"
        / "schemas"
        / "outbox_candidates.schema.json"
    )


def _load_schema() -> dict[str, Any]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def _live_outbox_candidates(tmp_path: Path) -> dict[str, Any]:
    db_path = tmp_path / "signals.db"
    _write_outbox_db(db_path)
    _insert_outbox_row(db_path, key="stale")

    result = run_registered_task(
        _args(tmp_path, db_path=db_path, mode="dry-run"),
    )

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    run_dir = Path(result.run_dir or "")
    return json.loads(
        (run_dir / outbox_purge.OUTBOX_CANDIDATES_ARTIFACT).read_text(
            encoding="utf-8"
        )
    )


def test_outbox_candidates_schema_matches_live_candidate_snapshot_keys(
    tmp_path: Path,
) -> None:
    artifact = _live_outbox_candidates(tmp_path)
    schema = _load_schema()
    properties = schema["properties"]

    assert set(artifact) == set(properties) - {"error"}
    assert schema["required"] == [
        "artifactVersion",
        "candidateCount",
        "candidateIds",
        "candidateIdHash",
        "candidateHash",
        "rows",
    ]
    assert all(key in artifact for key in schema["required"])

    row_schema = properties["rows"]["items"]
    assert set(artifact["rows"][0]) == set(row_schema["properties"])
    assert row_schema["required"] == [
        "id",
        "idempotency_key",
        "payload_json",
        "status",
        "attempts",
        "next_attempt_at",
        "last_error",
        "created_at",
        "updated_at",
        "event_type",
        "max_attempts",
    ]


def test_outbox_candidates_schema_tracks_live_shape_not_overlay_stubs() -> None:
    schema = _load_schema()
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert properties["artifactVersion"] == {"const": 1}
    assert properties["candidateCount"] == {"type": "integer", "minimum": 0}
    assert properties["candidateIds"]["items"] == {"type": "integer", "minimum": 1}
    assert properties["candidateIdHash"] == {"type": "string", "minLength": 1}
    assert properties["candidateHash"] == {"type": "string", "minLength": 1}
    assert properties["error"] == {"type": "string", "minLength": 1}
    assert STALE_OVERLAY_KEYS.isdisjoint(properties)
    assert "repairPromptTemplate" not in properties
    assert "governancePlanTemplate" not in properties
    assert "driftReportTemplate" not in properties
