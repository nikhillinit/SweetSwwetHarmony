from __future__ import annotations

import json
import os
import time
from pathlib import Path

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


def test_deliberation_passed_accepts_live_record_shape(
    tmp_path: Path,
    capsys,
) -> None:
    run_dir = tmp_path / "runs" / "deliberate-1"
    _write_json(
        run_dir / "deliberation_record.json",
        {
            "input": {"planHash": "sha256:abc123"},
            "consensus": {
                "status": "approved",
                "blockers": [],
                "dissent": {"present": False},
            },
            "freshnessTtlSeconds": 86400,
        },
    )

    exit_code = deliberation_passed.main(
        ["--run-dir", str(run_dir), "--plan-hash", "abc123"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["detail"] == "deliberation passed"
    assert payload["evidence"]["consensus"]["status"] == "approved"


def test_deliberation_passed_rejects_stale_records(
    tmp_path: Path,
    capsys,
) -> None:
    record_path = _write_json(
        tmp_path / "run" / "deliberation_record.json",
        {
            "input": {"planHash": "sha256:abc123"},
            "consensus": {
                "status": "approved",
                "blockers": [],
                "dissent": {"present": False},
            },
            "freshnessTtlSeconds": 86400,
        },
    )
    stale_time = time.time() - 120
    os.utime(record_path, (stale_time, stale_time))

    exit_code = deliberation_passed.main(
        ["--run-dir", str(record_path.parent), "--max-age-seconds", "1"]
    )

    payload = _stdout_payload(capsys)
    assert exit_code == 4
    assert payload["ok"] is False
    assert payload["detail"] == "deliberation failed"
    assert payload["evidence"]["ageOk"] is False


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
