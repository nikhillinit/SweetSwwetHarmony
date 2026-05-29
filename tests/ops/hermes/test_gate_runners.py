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
) -> dict[str, object]:
    return {
        "deliberationId": deliberation_id,
        "createdAt": created_at if created_at is not None else _iso_timestamp(),
        "input": {"planHash": plan_hash},
        "consensus": {
            "status": "approved",
            "blockers": [],
            "dissent": {"present": False},
        },
        "freshnessTtlSeconds": 86400,
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
