from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from integrations.hermes.gate_runners import (
    deliberation_passed,
    shadow_agreement,
    tribunal_clean,
)


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stdout_payload(capsys) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def _iso_timestamp(*, seconds_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _approved_deliberation_record(
    *,
    plan_hash: str | None = "sha256:abc123",
    created_at: str | None = None,
    deliberation_id: str = "deliberate-20260529T040000Z",
    include_policy: bool = True,
    panel: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    panel = panel or [
        {
            "executor": "codex",
            "verdict": "approve",
            "parsed": True,
            "success": True,
        },
        {
            "executor": "kimi",
            "verdict": "approve",
            "parsed": True,
            "success": True,
        },
    ]
    record: dict[str, object] = {
        "contractVersion": 2,
        "deliberationId": deliberation_id,
        "createdAt": created_at if created_at is not None else _iso_timestamp(),
        "input": {"planHash": plan_hash},
        "panel": panel,
        "consensus": {
            "status": "approved",
            "blockers": [],
            "dissent": {"present": False, "summary": ""},
            "quorum": {
                "status": "satisfied",
                "required": 2,
                "countedApprovals": ["codex", "kimi"],
                "trustedReviewers": ["codex", "gemini", "kimi"],
                "untrustedApprovals": [],
                "nonCompliantApprovals": [],
                "malformedReviewers": [],
            },
        },
        "freshnessTtlSeconds": 86400,
    }
    if include_policy:
        record["reviewerPolicy"] = {
            "policyVersion": 1,
            "task": "deliberate",
            "riskLevel": "high",
            "trustedReviewers": ["codex", "gemini", "kimi"],
            "requiredQuorum": 2,
            "approvalCriteria": {
                "verdict": "approve",
                "success": True,
                "parsed": True,
                "schema": "structured_json_v1",
            },
        }
    return record


def _restore_plan(
    *,
    plan_hash: str = "sha256:live123",
    mode: str = "execute",
    target_path: str = "signals.db",
    target_class: str = "live",
    backup_sha256: str = "backup123",
    min_row_count: int = 612,
    expected_schema_version: int | None = 53,
) -> dict[str, object]:
    return {
        "contractVersion": 2,
        "task": "restore-db",
        "mode": mode,
        "risk_level": "critical",
        "planHash": plan_hash,
        "target": {
            "path": target_path,
            "target_class": target_class,
        },
        "backup": {
            "sha256": backup_sha256,
        },
        "postflight_gate_contracts": {
            "row_count_above_watermark": {
                "min_row_count": min_row_count,
            },
            "schema_version_matches_if_declared": {
                "expected_schema_version": expected_schema_version,
            },
        },
        "mutation": {
            "allowed": mode == "execute",
            "affected_databases": [target_path],
        },
    }


def _restore_readiness(
    *,
    plan_hash: str = "sha256:live123",
    target_path: str = "signals.db",
    target_class: str = "live",
    backup_sha256: str | None = "backup123",
    min_row_count: int | None = 612,
    expected_schema_version: int | None = 53,
) -> dict[str, object]:
    return {
        "artifactVersion": 1,
        "task": "restore-db",
        "mode": "execute",
        "executeEligible": True,
        "executePlanHash": plan_hash,
        "target": {
            "path": target_path,
            "identity": Path(target_path).name,
            "class": target_class,
            "exists": True,
        },
        "backup": {
            "path": "backup.db",
            "sha256": backup_sha256,
        },
        "postflight": {
            "minRowCount": min_row_count,
            "expectedSchemaVersion": expected_schema_version,
        },
    }


def test_deliberation_passed_accepts_live_record_shape(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "deliberate-1"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(),
    )

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["detail"] == "deliberation passed"
    assert payload["evidence"]["consensus"]["status"] == "approved"
    assert payload["evidence"]["reviewerPolicy"]["status"] == "satisfied"
    assert payload["evidence"]["reviewerPolicy"]["countedApprovals"] == [
        "codex",
        "kimi",
    ]
    assert payload["evidence"]["reviewerPolicy"]["required"] == 2


def test_deliberation_passed_rejects_missing_reviewer_policy_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "missing-policy"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(include_policy=False),
    )

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "deliberation failed"
    assert payload["evidence"]["reviewerPolicy"]["status"] == "missing_policy"


def test_deliberation_passed_rejects_missing_recorded_quorum_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "missing-quorum"
    record = _approved_deliberation_record()
    del record["consensus"]["quorum"]
    _write_json(run_dir / "deliberation_record.json", record)

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "deliberation failed"
    assert payload["evidence"]["reviewerPolicy"]["status"] == "missing_quorum_evidence"
    assert payload["evidence"]["reviewerPolicy"]["quorumEvidencePresent"] is False


def test_deliberation_passed_rejects_stale_recorded_quorum_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "stale-quorum"
    record = _approved_deliberation_record()
    record["consensus"]["quorum"]["countedApprovals"] = ["codex", "claude"]
    record["consensus"]["quorum"]["untrustedApprovals"] = []
    _write_json(run_dir / "deliberation_record.json", record)

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "deliberation failed"
    assert payload["evidence"]["reviewerPolicy"]["status"] == "quorum_evidence_mismatch"
    assert payload["evidence"]["reviewerPolicy"]["quorumEvidenceMatches"] is False
    assert payload["evidence"]["reviewerPolicy"]["countedApprovals"] == [
        "codex",
        "kimi",
    ]
    assert payload["evidence"]["reviewerPolicy"]["untrustedApprovals"] == []


def test_deliberation_passed_rejects_recorded_quorum_status_mismatch(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "quorum-status-mismatch"
    record = _approved_deliberation_record()
    record["consensus"]["quorum"]["status"] = "insufficient_quorum"
    _write_json(run_dir / "deliberation_record.json", record)

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["evidence"]["reviewerPolicy"]["status"] == "quorum_evidence_mismatch"
    assert payload["evidence"]["reviewerPolicy"]["quorumEvidenceMatches"] is False


def test_deliberation_passed_rejects_untrusted_reviewer_approval(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "untrusted-reviewer"
    record = _approved_deliberation_record(
        panel=[
            {
                "executor": "codex",
                "verdict": "approve",
                "parsed": True,
                "success": True,
            },
            {
                "executor": "claude",
                "verdict": "approve",
                "parsed": True,
                "success": True,
            },
        ],
    )
    record["consensus"]["quorum"] = {
        "status": "insufficient_quorum",
        "required": 2,
        "countedApprovals": ["codex"],
        "trustedReviewers": ["codex", "gemini", "kimi"],
        "untrustedApprovals": ["claude"],
        "nonCompliantApprovals": [
            {"executor": "claude", "reasons": ["untrusted_reviewer"]}
        ],
        "malformedReviewers": [],
    }
    _write_json(
        run_dir / "deliberation_record.json",
        record,
    )

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["evidence"]["reviewerPolicy"]["status"] == "insufficient_quorum"
    assert payload["evidence"]["reviewerPolicy"]["countedApprovals"] == ["codex"]
    assert payload["evidence"]["reviewerPolicy"]["untrustedApprovals"] == ["claude"]


def test_deliberation_passed_requires_plan_hash_by_default(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "deliberate-1"
    _write_json(run_dir / "deliberation_record.json", _approved_deliberation_record())

    exit_code = deliberation_passed.main(["--run-dir", str(run_dir)])

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "plan hash required"
    assert payload["evidence"]["planBinding"]["required"] is True


def test_deliberation_passed_allow_unbound_emits_unsafe_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "deliberate-1"
    _write_json(run_dir / "deliberation_record.json", _approved_deliberation_record())

    exit_code = deliberation_passed.main(["--run-dir", str(run_dir), "--allow-unbound"])

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["evidence"]["planBinding"] == {
        "mode": "unbound",
        "unsafe": True,
        "required": False,
    }


def test_deliberation_passed_rejects_wrong_or_missing_record_plan_hash(
    tmp_path: Path,
    capsys,
) -> None:
    wrong_hash_dir = tmp_path / "runs" / "wrong-hash"
    _write_json(
        wrong_hash_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:def456"),
    )
    missing_hash_dir = tmp_path / "runs" / "missing-hash"
    _write_json(
        missing_hash_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash=None),
    )

    wrong_exit_code = deliberation_passed.main(
        ["--run-dir", str(wrong_hash_dir), "--plan-hash", "abc123"]
    )
    wrong_payload = _stdout_payload(capsys)
    missing_exit_code = deliberation_passed.main(
        ["--run-dir", str(missing_hash_dir), "--plan-hash", "abc123"]
    )
    missing_payload = _stdout_payload(capsys)

    assert wrong_exit_code == 4
    assert wrong_payload["evidence"]["planHashOk"] is False
    assert missing_exit_code == 4
    assert missing_payload["evidence"]["planHashOk"] is False


def test_deliberation_passed_requires_restore_readiness_for_restore_plan(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "restore-readiness-missing"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:live123"),
    )
    plan_path = _write_json(tmp_path / "task_plan.json", _restore_plan())

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "sha256:live123",
            "--restore-plan",
            str(plan_path),
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["evidence"]["restoreReadiness"]["status"] == "missing_readiness"


def test_deliberation_passed_accepts_matching_restore_readiness(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "restore-readiness-matching"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:live123"),
    )
    target = str(tmp_path / "signals.db")
    plan_path = _write_json(
        tmp_path / "task_plan.json",
        _restore_plan(target_path=target),
    )
    readiness_path = _write_json(
        tmp_path / "restore_readiness.json",
        _restore_readiness(target_path=target),
    )

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "sha256:live123",
            "--restore-plan",
            str(plan_path),
            "--restore-readiness",
            str(readiness_path),
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["evidence"]["restoreReadiness"]["status"] == "satisfied"


def test_deliberation_passed_rejects_canary_readiness_for_live_restore_plan(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "restore-readiness-target-mismatch"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:live123"),
    )
    live_target = str(tmp_path / "signals.db")
    canary_target = str(tmp_path / "signals.db.canary")
    plan_path = _write_json(
        tmp_path / "live_task_plan.json",
        _restore_plan(target_path=live_target, target_class="live"),
    )
    readiness_path = _write_json(
        tmp_path / "canary_restore_readiness.json",
        _restore_readiness(target_path=canary_target, target_class="canary"),
    )

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "sha256:live123",
            "--restore-plan",
            str(plan_path),
            "--restore-readiness",
            str(readiness_path),
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["evidence"]["restoreReadiness"]["ok"] is False
    assert "target_path_mismatch" in payload["evidence"]["restoreReadiness"]["reasons"]
    assert "target_class_mismatch" in payload["evidence"]["restoreReadiness"]["reasons"]


def test_deliberation_passed_rejects_non_execute_restore_plan(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "restore-readiness-plan-only"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:planonly"),
    )
    plan_path = _write_json(
        tmp_path / "plan_only_task_plan.json",
        _restore_plan(plan_hash="sha256:planonly", mode="plan-only"),
    )
    readiness_path = _write_json(
        tmp_path / "restore_readiness.json",
        _restore_readiness(plan_hash="sha256:planonly"),
    )

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "sha256:planonly",
            "--restore-plan",
            str(plan_path),
            "--restore-readiness",
            str(readiness_path),
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["evidence"]["restoreReadiness"]["ok"] is False
    assert "restore_plan_not_execute_mode" in payload["evidence"]["restoreReadiness"][
        "reasons"
    ]


def test_deliberation_passed_rejects_mismatched_restore_readiness_fields(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "restore-readiness-field-mismatch"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:live123"),
    )
    plan_path = _write_json(
        tmp_path / "task_plan.json",
        _restore_plan(
            backup_sha256="backup-expected",
            min_row_count=612,
            expected_schema_version=53,
        ),
    )
    readiness_path = _write_json(
        tmp_path / "restore_readiness.json",
        _restore_readiness(
            backup_sha256="backup-actual",
            min_row_count=4,
            expected_schema_version=26,
        ),
    )

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "sha256:live123",
            "--restore-plan",
            str(plan_path),
            "--restore-readiness",
            str(readiness_path),
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["evidence"]["restoreReadiness"]["ok"] is False
    assert {
        "backup_hash_mismatch",
        "min_row_count_mismatch",
        "expected_schema_version_mismatch",
    }.issubset(set(payload["evidence"]["restoreReadiness"]["reasons"]))


def test_deliberation_passed_rejects_missing_restore_readiness_fields(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "restore-readiness-field-missing"
    _write_json(
        run_dir / "deliberation_record.json",
        _approved_deliberation_record(plan_hash="sha256:live123"),
    )
    plan_path = _write_json(tmp_path / "task_plan.json", _restore_plan())
    readiness_path = _write_json(
        tmp_path / "restore_readiness.json",
        _restore_readiness(backup_sha256=None, min_row_count=None),
    )

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "sha256:live123",
            "--restore-plan",
            str(plan_path),
            "--restore-readiness",
            str(readiness_path),
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert {
        "backup_hash_missing",
        "min_row_count_missing",
    }.issubset(set(payload["evidence"]["restoreReadiness"]["reasons"]))


def test_deliberation_passed_rejects_stale_created_at_records(
    tmp_path: Path,
    capsys,
) -> None:
    record_path = _write_json(
        tmp_path / "run" / "deliberation_record.json",
        _approved_deliberation_record(created_at=_iso_timestamp(seconds_ago=120)),
    )
    stale_time = time.time() - 120
    os.utime(record_path, (stale_time, stale_time))

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(record_path.parent),
            "--plan-hash",
            "abc123",
            "--max-age-seconds",
            "1",
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "deliberation failed"
    assert payload["evidence"]["ageOk"] is False
    assert payload["evidence"]["freshnessSource"] == "createdAt"


def test_deliberation_passed_ignores_mtime_freshness_by_default(
    tmp_path: Path,
    capsys,
) -> None:
    record_path = _write_json(
        tmp_path / "run" / "deliberation_record.json",
        _approved_deliberation_record(created_at=_iso_timestamp(seconds_ago=120)),
    )
    fresh_time = time.time()
    os.utime(record_path, (fresh_time, fresh_time))

    exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(record_path.parent),
            "--plan-hash",
            "abc123",
            "--max-age-seconds",
            "1",
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["evidence"]["ageOk"] is False
    assert payload["evidence"]["freshnessSource"] == "createdAt"


def test_deliberation_passed_uses_deliberation_id_timestamp_as_fallback(
    tmp_path: Path,
    capsys,
) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = _approved_deliberation_record(
        created_at=None,
        deliberation_id=f"deliberate-{stamp}",
    )
    record.pop("createdAt")
    run_dir = tmp_path / "run"
    _write_json(run_dir / "deliberation_record.json", record)

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["evidence"]["freshnessSource"] == "deliberationId"


def test_deliberation_passed_mtime_freshness_requires_explicit_escape_hatch(
    tmp_path: Path,
    capsys,
) -> None:
    record = _approved_deliberation_record(
        created_at=None,
        deliberation_id="deliberate-not-a-timestamp",
    )
    record.pop("createdAt")
    run_dir = tmp_path / "run"
    _write_json(run_dir / "deliberation_record.json", record)

    default_exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )
    default_payload = _stdout_payload(capsys)
    allowed_exit_code = deliberation_passed.main(
        [
            "--run-dir",
            str(run_dir),
            "--plan-hash",
            "abc123",
            "--allow-mtime-freshness",
        ]
    )
    allowed_payload = _stdout_payload(capsys)

    assert default_exit_code == 4
    assert default_payload["evidence"]["freshnessSource"] is None
    assert allowed_exit_code == 0
    assert allowed_payload["evidence"]["freshnessSource"] == "mtime"


def test_shadow_agreement_accepts_live_shadow_validation_artifact(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "shadow-1"
    _write_json(
        run_dir / "shadow_validation.json",
        {
            "shadowRun": {
                "status": "completed",
                "agreementRate": 0.98,
            }
        },
    )

    exit_code = shadow_agreement.main(["--run-dir", str(run_dir), "--min-rate", "0.95"])

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["detail"] == "shadow agreement passed"
    assert payload["evidence"]["agreementRate"] == 0.98
    assert payload["evidence"]["statusCompatibility"]["deprecated"] is False


@pytest.mark.parametrize("legacy_status", ["passed", "pass"])
def test_shadow_agreement_accepts_legacy_status_with_deprecation_evidence(
    legacy_status: str,
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "shadow-legacy"
    _write_json(
        run_dir / "shadow_validation.json",
        {
            "shadowRun": {
                "status": legacy_status,
                "agreementRate": 0.98,
            }
        },
    )

    exit_code = shadow_agreement.main(["--run-dir", str(run_dir), "--min-rate", "0.95"])

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["detail"] == "shadow agreement passed with deprecated status"
    assert payload["evidence"]["statusCompatibility"] == {
        "accepted": True,
        "canonicalStatus": "completed",
        "deprecated": True,
        "deprecationDetail": (
            f"status {legacy_status!r} is deprecated; emit 'completed'"
        ),
        "strictStatus": False,
    }


def test_shadow_agreement_strict_status_rejects_legacy_success(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "shadow-legacy"
    _write_json(
        run_dir / "shadow_validation.json",
        {
            "shadowRun": {
                "status": "passed",
                "agreementRate": 0.98,
            }
        },
    )

    exit_code = shadow_agreement.main(
        ["--run-dir", str(run_dir), "--min-rate", "0.95", "--strict-status"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "shadow agreement failed"
    assert payload["evidence"]["statusCompatibility"]["accepted"] is False
    assert payload["evidence"]["statusCompatibility"]["deprecated"] is True
    assert payload["evidence"]["statusCompatibility"]["strictStatus"] is True


def test_shadow_agreement_rejects_low_agreement_rate(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "shadow-1"
    _write_json(
        run_dir / "shadow_validation.json",
        {
            "shadowRun": {
                "status": "completed",
                "agreementRate": 0.5,
            }
        },
    )

    exit_code = shadow_agreement.main(["--run-dir", str(run_dir), "--min-rate", "0.95"])

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "shadow agreement failed"


def test_tribunal_clean_fails_closed_without_matrix(capsys) -> None:
    exit_code = tribunal_clean.main([])

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "ACH matrix unavailable; tribunal gate fails closed"


def test_tribunal_clean_accepts_score_and_differentiator_summary(
    tmp_path: Path,
    capsys,
) -> None:
    matrix_path = _write_json(
        tmp_path / "ach-matrix.json",
        {
            "top_score": 0.8,
            "differentiator_count": 2,
            "top_hypothesis": "H1",
        },
    )

    exit_code = tribunal_clean.main(
        [
            "--matrix",
            str(matrix_path),
            "--min-score",
            "0.7",
            "--max-differentiators",
            "3",
        ]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["detail"] == "tribunal clean"
    assert payload["evidence"]["topScore"] == 0.8
    assert payload["evidence"]["differentiatorCount"] == 2
