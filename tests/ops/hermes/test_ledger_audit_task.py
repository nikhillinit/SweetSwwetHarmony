from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from integrations.hermes.tasks.base import EXIT_GATE_FAILURE, EXIT_INVALID
from integrations.hermes.tasks.ledger_audit import (
    LEDGER_AUDIT_REPORT_JSON,
    LEDGER_AUDIT_REPORT_MD,
    LedgerAuditTask,
)
from integrations.hermes.tasks.registry import run_registered_task

from .conftest import minimal_config_dict


def _config_path(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(
    tmp_path: Path,
    *,
    mode: str = "plan-only",
    check: str = "all",
    finding_severity_threshold: str = "critical",
) -> argparse.Namespace:
    return argparse.Namespace(
        task_name="ledger-audit",
        config=str(_config_path(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="test",
        json_output=False,
        check=check,
        finding_severity_threshold=finding_severity_threshold,
    )


def _ledger_root(tmp_path: Path) -> Path:
    return tmp_path / "ai-logs" / "hermes"


def _append_index(root: Path, row: dict[str, Any] | str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = row if isinstance(row, str) else json.dumps(row, separators=(",", ":"))
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(payload + "\n")


def _write_ledger_run(
    root: Path,
    run_id: str,
    *,
    artifacts: dict[str, str],
    missing_artifacts: set[str] | None = None,
) -> Path:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    missing = missing_artifacts or set()
    for relative_path in artifacts.values():
        if relative_path in missing:
            continue
        (run_dir / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / relative_path).write_text("{}", encoding="utf-8")
    (run_dir / "ledger.json").write_text(
        json.dumps({"runId": run_id, "artifacts": artifacts}),
        encoding="utf-8",
    )
    return run_dir


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _restore_plan(
    *,
    plan_hash: str = "sha256:restore123",
    target_path: str = "signals.db",
    target_class: str = "live",
    backup_sha256: str = "sha256:backup123",
    min_row_count: int = 4,
    expected_schema_version: int | None = 53,
    contract_version: int = 2,
) -> dict[str, Any]:
    return {
        "contractVersion": contract_version,
        "task": "restore-db",
        "mode": "execute",
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
            "allowed": True,
            "affected_databases": [target_path],
        },
    }


def _restore_readiness(
    *,
    plan_hash: str = "sha256:restore123",
    target_path: str = "signals.db",
    target_class: str = "live",
    backup_sha256: str | None = "sha256:backup123",
    min_row_count: int | None = 4,
    expected_schema_version: int | None = 53,
) -> dict[str, Any]:
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


def _restore_run_record(
    run_id: str,
    *,
    contract_version: int = 2,
    plan_hash: str | None = None,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if plan_hash is not None:
        inputs["planHash"] = plan_hash
    return {
        "run_id": run_id,
        "contract_version": contract_version,
        "task": "restore-db",
        "mode": "execute",
        "risk_level": "critical",
        "status": "approval_required",
        "inputs": inputs,
        "outputs": {},
        "plan_ref": "task_plan.json",
    }


def _write_restore_ledger_run(
    root: Path,
    run_id: str,
    *,
    plan: dict[str, Any] | str | None = None,
    record: dict[str, Any] | str | None = None,
    readiness: dict[str, Any] | str | None = None,
    missing: set[str] | None = None,
) -> Path:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    missing_artifacts = missing or set()
    artifact_payloads: dict[str, dict[str, Any] | str | None] = {
        "task_plan.json": _restore_plan() if plan is None else plan,
        "run_record.json": _restore_run_record(run_id) if record is None else record,
        "restore_readiness.json": _restore_readiness()
        if readiness is None
        else readiness,
    }
    for relative_path, payload in artifact_payloads.items():
        if relative_path in missing_artifacts:
            continue
        artifact_path = run_dir / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            artifact_path.write_text(payload, encoding="utf-8")
        else:
            artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    ledger_artifacts = {
        "ledger": "ledger.json",
        **{
            artifact_name.removesuffix(".json"): artifact_name
            for artifact_name in artifact_payloads
            if artifact_name not in missing_artifacts
        },
    }
    _write_json(
        run_dir / "ledger.json",
        {
            "runId": run_id,
            "artifacts": ledger_artifacts,
        },
    )
    _append_index(
        root,
        {
            "runId": run_id,
            "createdAt": "2026-06-01T00:00:00Z",
            "task": "restore-db",
            "mode": "execute",
            "status": "approval_required",
            "runDir": str(run_dir),
        },
    )
    return run_dir


def _finding_codes(report: dict[str, Any]) -> set[str]:
    return {str(finding.get("code")) for finding in report.get("findings", [])}


def _restore_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    subsystem = report["subsystems"]["restore_sqlite"]
    return subsystem["findings"]


def test_plan_only_writes_ledger_artifacts_and_stays_non_mutating(
    tmp_path: Path,
) -> None:
    result = run_registered_task(_args(tmp_path, mode="plan-only"))

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["mutation"]["allowed"] is False
    assert result.plan["mutation"]["affected_db"] is None
    assert result.plan["mutation"]["affected_tables"] == []
    assert result.plan["mutation"]["external_systems"] == []
    assert result.plan["mutation"]["ledger_artifacts"] == [
        "task_plan.json",
        "run_record.json",
        "ledger_audit_report.json",
        "ledger_audit_report.md",
    ]
    run_dir = Path(result.run_dir or "")
    assert (run_dir / "task_plan.json").exists()
    assert (run_dir / "run_record.json").exists()
    assert (run_dir / "plan.md").exists()


def test_dry_run_writes_audit_reports_and_does_not_touch_db_or_config(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path, mode="dry-run")
    config_path = Path(args.config)
    before_config = config_path.read_text(encoding="utf-8")

    result = run_registered_task(args)

    assert result.exit_code == 0
    assert result.status == "dry_run_passed"
    assert result.outputs["dryRun"] is True
    assert result.outputs["mutationCommitted"] is False
    assert result.outputs["findings"] == []
    assert config_path.read_text(encoding="utf-8") == before_config
    assert not (tmp_path / "signals.db").exists()
    run_dir = Path(result.run_dir or "")
    report_json = run_dir / "ledger_audit_report.json"
    report_md = run_dir / "ledger_audit_report.md"
    assert report_json.exists()
    assert report_md.exists()
    assert json.loads(report_json.read_text(encoding="utf-8"))["findings"] == []
    assert "Hermes Ledger Audit Report" in report_md.read_text(encoding="utf-8")


def test_dry_run_summary_distinguishes_raw_index_rows_from_unique_run_dirs(
    tmp_path: Path,
) -> None:
    root = _ledger_root(tmp_path)
    run_dir = _write_ledger_run(
        root,
        "existing",
        artifacts={
            "plan": "task_plan.json",
            "record": "run_record.json",
            "ledger": "ledger.json",
        },
    )
    row = {
        "runId": "existing",
        "createdAt": "2026-05-29T00:00:00Z",
        "runDir": str(run_dir),
    }
    _append_index(root, row)
    _append_index(root, row)

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    summary = result.outputs["summary"]
    assert summary["rawIndexRows"] > summary["uniqueRunDirsChecked"]
    assert summary["rawIndexRows"] == summary["validIndexEntries"]
    assert "checkedRunDirs" not in summary


def test_dry_run_reports_restore_sqlite_subsystem_without_findings(
    tmp_path: Path,
) -> None:
    _write_restore_ledger_run(_ledger_root(tmp_path), "restore-ok")

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == 0
    restore_sqlite = result.outputs["subsystems"]["restore_sqlite"]
    assert restore_sqlite["subsystem"] == "restore_sqlite"
    assert restore_sqlite["ownerTask"] == "restore-db"
    assert restore_sqlite["runsChecked"] == 1
    assert restore_sqlite["findings"] == []
    assert result.outputs["findings"] == []


def test_dry_run_fails_closed_on_missing_restore_required_artifact(
    tmp_path: Path,
) -> None:
    run_dir = _write_restore_ledger_run(
        _ledger_root(tmp_path),
        "restore-missing-readiness",
        missing={"restore_readiness.json"},
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "dry_run_failed"
    assert "missing_required_artifact" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _restore_findings(result.outputs)
        if item["code"] == "missing_required_artifact"
    )
    assert finding["severity"] == "critical"
    assert finding["subsystem"] == "restore_sqlite"
    assert finding["resourceId"] == "restore_readiness"
    assert finding["evidencePath"] == str(run_dir / "restore_readiness.json")
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_dry_run_fails_closed_on_malformed_restore_json(
    tmp_path: Path,
) -> None:
    _write_restore_ledger_run(
        _ledger_root(tmp_path),
        "restore-malformed-readiness",
        readiness="{not-json",
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "malformed_json" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _restore_findings(result.outputs)
        if item["code"] == "malformed_json"
    )
    assert finding["subsystem"] == "restore_sqlite"
    assert finding["resourceId"] == "restore_readiness"


def test_dry_run_fails_closed_on_restore_plan_hash_mismatch(
    tmp_path: Path,
) -> None:
    _write_restore_ledger_run(
        _ledger_root(tmp_path),
        "restore-plan-hash-mismatch",
        readiness=_restore_readiness(plan_hash="sha256:other"),
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "plan_hash_mismatch" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _restore_findings(result.outputs)
        if item["code"] == "plan_hash_mismatch"
    )
    assert finding["resourceId"] == "restore_sqlite.plan_hash"
    assert finding["subsystem"] == "restore_sqlite"


def test_dry_run_fails_closed_on_restore_target_binding_mismatch(
    tmp_path: Path,
) -> None:
    _write_restore_ledger_run(
        _ledger_root(tmp_path),
        "restore-target-mismatch",
        readiness=_restore_readiness(
            target_path="signals.db.canary",
            target_class="canary",
        ),
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "dry_run_binding_mismatch" in _finding_codes(result.outputs)
    finding = next(
        item
        for item in _restore_findings(result.outputs)
        if item["code"] == "dry_run_binding_mismatch"
    )
    assert finding["resourceId"] == "restore_sqlite.target"
    assert finding["subsystem"] == "restore_sqlite"


def test_dry_run_fails_closed_on_restore_readiness_execution_semantics(
    tmp_path: Path,
) -> None:
    readiness = _restore_readiness()
    readiness["task"] = "shadow-validate"
    readiness["mode"] = "dry-run"
    readiness["executeEligible"] = False
    _write_restore_ledger_run(
        _ledger_root(tmp_path),
        "restore-readiness-semantics-mismatch",
        readiness=readiness,
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert "dry_run_binding_mismatch" in _finding_codes(result.outputs)
    resource_ids = {
        finding["resourceId"] for finding in _restore_findings(result.outputs)
    }
    assert {
        "restore_sqlite.readiness.task",
        "restore_sqlite.readiness.mode",
        "restore_sqlite.readiness.execute_eligible",
    }.issubset(resource_ids)


def test_dry_run_fails_closed_on_restore_schema_and_row_contract_mismatch(
    tmp_path: Path,
) -> None:
    _write_restore_ledger_run(
        _ledger_root(tmp_path),
        "restore-contract-mismatch",
        readiness=_restore_readiness(
            min_row_count=2,
            expected_schema_version=26,
        ),
    )

    result = run_registered_task(_args(tmp_path, mode="dry-run"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert {"resource_digest_mismatch", "schema_version_mismatch"}.issubset(
        _finding_codes(result.outputs)
    )
    resource_ids = {
        finding["resourceId"] for finding in _restore_findings(result.outputs)
    }
    assert "restore_sqlite.postflight.min_row_count" in resource_ids
    assert "restore_sqlite.postflight.expected_schema_version" in resource_ids


def test_postflight_default_threshold_ignores_non_critical_findings(
    tmp_path: Path,
) -> None:
    (tmp_path / LEDGER_AUDIT_REPORT_JSON).write_text("{}", encoding="utf-8")
    (tmp_path / LEDGER_AUDIT_REPORT_MD).write_text("# report\n", encoding="utf-8")
    context = SimpleNamespace(
        run_dir=tmp_path,
        args=argparse.Namespace(finding_severity_threshold="critical"),
    )
    outputs = {
        "findings": [
            {
                "code": "missing_run_dir",
                "severity": "high",
                "path": str(tmp_path / "missing"),
            }
        ]
    }

    checks = LedgerAuditTask().postflight(context, {}, outputs)

    finding_check = next(
        check for check in checks if check.name == "no_ledger_audit_findings"
    )
    assert finding_check.passed is True
    assert finding_check.evidence["severityThreshold"] == "critical"
    assert finding_check.evidence["blockingFindings"] == []


def test_postflight_threshold_can_fail_on_high_findings(tmp_path: Path) -> None:
    (tmp_path / LEDGER_AUDIT_REPORT_JSON).write_text("{}", encoding="utf-8")
    (tmp_path / LEDGER_AUDIT_REPORT_MD).write_text("# report\n", encoding="utf-8")
    context = SimpleNamespace(
        run_dir=tmp_path,
        args=argparse.Namespace(finding_severity_threshold="high"),
    )
    outputs = {
        "findings": [
            {
                "code": "missing_run_dir",
                "severity": "high",
                "path": str(tmp_path / "missing"),
            }
        ]
    }

    checks = LedgerAuditTask().postflight(context, {}, outputs)

    finding_check = next(
        check for check in checks if check.name == "no_ledger_audit_findings"
    )
    assert finding_check.passed is False
    assert finding_check.evidence["severityThreshold"] == "high"
    assert finding_check.evidence["blockingFindings"] == outputs["findings"]


def test_preflight_rejects_malformed_index_line_and_emits_repair_prompt(
    tmp_path: Path,
) -> None:
    _append_index(_ledger_root(tmp_path), "{not-json")

    result = run_registered_task(_args(tmp_path, mode="preflight-only"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "ledger_index_jsonl_valid")
    assert check.passed is False
    assert check.evidence["malformed_count"] == 1
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_preflight_rejects_missing_indexed_run_directory(
    tmp_path: Path,
) -> None:
    missing_run = _ledger_root(tmp_path) / "runs" / "missing"
    _append_index(
        _ledger_root(tmp_path),
        {"runId": "missing", "createdAt": "2026-05-28T00:00:00Z", "runDir": str(missing_run)},
    )

    result = run_registered_task(_args(tmp_path, mode="preflight-only"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "ledger_run_dirs_present")
    assert check.passed is False
    assert check.evidence["missing_run_dirs"] == [str(missing_run)]
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_preflight_rejects_missing_referenced_artifact(
    tmp_path: Path,
) -> None:
    root = _ledger_root(tmp_path)
    run_dir = _write_ledger_run(
        root,
        "old",
        artifacts={
            "plan": "plan.json",
            "prompt": "prompt.txt",
            "ledger": "ledger.json",
            "summary": "summary.json",
        },
        missing_artifacts={"summary.json"},
    )
    _append_index(
        root,
        {"runId": "old", "createdAt": "2026-05-28T00:00:00Z", "runDir": str(run_dir)},
    )

    result = run_registered_task(_args(tmp_path, mode="preflight-only"))

    assert result.exit_code == EXIT_GATE_FAILURE
    assert result.status == "preflight_failed"
    check = next(check for check in result.checks if check.name == "ledger_artifact_refs_present")
    assert check.passed is False
    assert check.evidence["missing_artifacts"] == [str(run_dir / "summary.json")]
    assert (Path(result.run_dir or "") / "repair_prompt.md").exists()


def test_check_index_scope_skips_run_directory_and_artifact_checks(
    tmp_path: Path,
) -> None:
    missing_run = _ledger_root(tmp_path) / "runs" / "missing"
    _append_index(
        _ledger_root(tmp_path),
        {"runId": "missing", "createdAt": "2026-05-28T00:00:00Z", "runDir": str(missing_run)},
    )

    result = run_registered_task(
        _args(tmp_path, mode="preflight-only", check="index"),
    )

    assert result.exit_code == 0
    assert result.status == "preflight_passed"
    assert [check.name for check in result.checks] == [
        "ledger_root_readable",
        "ledger_index_readable",
        "ledger_index_jsonl_valid",
    ]


def test_invalid_check_scope_fails_before_ledger_run_is_created(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown ledger-audit check"):
        run_registered_task(
            _args(tmp_path, mode="preflight-only", check="artifactz"),
        )

    assert not _ledger_root(tmp_path).exists()


def test_execute_mode_is_unsupported_and_writes_no_ledger_run(
    tmp_path: Path,
) -> None:
    result = run_registered_task(_args(tmp_path, mode="execute"))

    assert result.exit_code == EXIT_INVALID
    assert result.status == "unsupported_mode"
    assert result.run_dir is None
    assert not _ledger_root(tmp_path).exists()
