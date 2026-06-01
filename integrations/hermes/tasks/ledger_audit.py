from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import CheckResult, HermesTask, TaskContext, TaskMode, TaskResult
from .ledger_audit_collector_promotion import (
    COLLECTOR_PROMOTION_SUBSYSTEM as _COLLECTOR_PROMOTION_SUBSYSTEM,
    audit_collector_promotion_subsystem as _audit_collector_promotion_subsystem,
    empty_collector_promotion_subsystem as _empty_collector_promotion_subsystem,
)
from .ledger_audit_governance_config import (
    GOVERNANCE_CONFIG_SUBSYSTEM as _GOVERNANCE_CONFIG_SUBSYSTEM,
    audit_governance_config_subsystem as _audit_governance_config_subsystem,
    empty_governance_config_subsystem as _empty_governance_config_subsystem,
)
from .ledger_audit_restore_sqlite import (
    RESTORE_SQLITE_SUBSYSTEM as _RESTORE_SQLITE_SUBSYSTEM,
    audit_restore_sqlite_subsystem as _audit_restore_sqlite_subsystem,
    empty_restore_sqlite_subsystem as _empty_restore_sqlite_subsystem,
)
from .ledger_audit_suppression_outbox import (
    SUPPRESSION_OUTBOX_SUBSYSTEM as _SUPPRESSION_OUTBOX_SUBSYSTEM,
    audit_suppression_outbox_subsystem as _audit_suppression_outbox_subsystem,
    empty_suppression_outbox_subsystem as _empty_suppression_outbox_subsystem,
)

LEDGER_AUDIT_REPORT_JSON = "ledger_audit_report.json"
LEDGER_AUDIT_REPORT_MD = "ledger_audit_report.md"

_ALL_CHECKS = ("index", "runs", "artifacts")
_FINDING_SEVERITIES = ("low", "medium", "high", "critical")
_FINDING_SEVERITY_RANK = {
    severity: index for index, severity in enumerate(_FINDING_SEVERITIES)
}
_DEFAULT_FINDING_SEVERITY_THRESHOLD = "critical"


class LedgerAuditTask(HermesTask):
    name = "ledger-audit"
    description = "Read-only integrity audit for Hermes ledger runs and artifacts."
    risk_level = "low"
    supported_modes = ("plan-only", "preflight-only", "dry-run")
    required_locks = ()
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--check",
            default="all",
            type=_parse_check_arg,
            help="Comma-separated ledger audit scopes: all,index,runs,artifacts",
        )
        parser.add_argument(
            "--finding-severity-threshold",
            default=_DEFAULT_FINDING_SEVERITY_THRESHOLD,
            type=_parse_finding_severity_threshold,
            help="Minimum finding severity that fails postflight: low,medium,high,critical",
        )

    def run(
        self,
        args: argparse.Namespace,
        *,
        mode: TaskMode,
        config_path: str | Path | None = None,
        ack_risk: str | None = None,
    ) -> TaskResult:
        _requested_checks(getattr(args, "check", "all"))
        return super().run(
            args,
            mode=mode,
            config_path=config_path,
            ack_risk=ack_risk,
        )

    def plan(self, context: TaskContext) -> dict[str, Any]:
        checks = _requested_checks(getattr(context.args, "check", "all"))
        finding_severity_threshold = _finding_severity_threshold(context)
        ledger_root, index_path = _ledger_paths(context)
        audit = _audit_ledger(ledger_root, index_path, checks)

        plan = self._base_plan(context)
        plan.update(
            {
                "ledger": {
                    "root": str(ledger_root),
                    "index": str(index_path),
                    "index_entries": audit["index"]["valid_count"],
                    "index_malformed_entries": audit["index"]["malformed_count"],
                },
                "checks_requested": list(checks),
                "finding_severity_threshold": finding_severity_threshold,
                "artifacts": {
                    "audit_report_json": LEDGER_AUDIT_REPORT_JSON,
                    "audit_report_markdown": LEDGER_AUDIT_REPORT_MD,
                    "run_record": "run_record.json",
                    "task_plan": "task_plan.json",
                },
                "preflight_gates": _preflight_gate_names(checks),
                "postflight_gates": [
                    "ledger_audit_report_json_written",
                    "ledger_audit_report_markdown_written",
                    "no_ledger_audit_findings",
                ],
                "mutation": {
                    "allowed": False,
                    "affected_db": None,
                    "affected_files": [],
                    "affected_tables": [],
                    "external_systems": [],
                    "ledger_artifacts": [
                        "task_plan.json",
                        "run_record.json",
                        LEDGER_AUDIT_REPORT_JSON,
                        LEDGER_AUDIT_REPORT_MD,
                    ],
                },
                "external_reads": [],
                "database_reads": [],
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        checks = tuple(plan.get("checks_requested") or _ALL_CHECKS)
        ledger_root = Path(plan["ledger"]["root"])
        index_path = Path(plan["ledger"]["index"])
        audit = _audit_ledger(ledger_root, index_path, checks)
        results = [
            CheckResult(
                "ledger_root_readable",
                bool(audit["root"]["readable"]),
                audit["root"]["detail"],
                audit["root"],
            ),
            CheckResult(
                "ledger_index_readable",
                bool(audit["index"]["readable"]),
                audit["index"]["detail"],
                audit["index"],
            ),
            CheckResult(
                "ledger_index_jsonl_valid",
                audit["index"]["malformed_count"] == 0
                and audit["index"]["read_error"] is None,
                _index_jsonl_detail(audit),
                audit["index"],
            ),
        ]
        if "runs" in checks:
            results.append(
                CheckResult(
                    "ledger_run_dirs_present",
                    not audit["runs"]["missing_run_dirs"]
                    and not audit["runs"]["missing_run_dir_fields"],
                    _run_dirs_detail(audit),
                    audit["runs"],
                )
            )
        if "artifacts" in checks:
            results.append(
                CheckResult(
                    "ledger_artifact_refs_present",
                    not audit["artifacts"]["missing_artifacts"]
                    and not audit["artifacts"]["malformed_ledgers"],
                    _artifacts_detail(audit),
                    audit["artifacts"],
                )
            )
        return results

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        checks = tuple(plan.get("checks_requested") or _ALL_CHECKS)
        ledger_root = Path(plan["ledger"]["root"])
        index_path = Path(plan["ledger"]["index"])
        audit = _audit_ledger(ledger_root, index_path, checks)
        generated_at_dt = datetime.now(timezone.utc)
        report = {
            "auditId": f"ledger-audit-{generated_at_dt.strftime('%Y%m%dT%H%M%SZ')}",
            "generatedAt": generated_at_dt.isoformat(),
            "dryRun": True,
            "mutationCommitted": False,
            "task": self.name,
            "ledgerRoot": str(ledger_root),
            "ledgerIndex": str(index_path),
            "checksRun": list(checks),
            "summary": {
                "indexLines": audit["index"]["line_count"],
                "validIndexEntries": audit["index"]["valid_count"],
                "malformedIndexEntries": audit["index"]["malformed_count"],
                "rawIndexRows": audit["runs"]["raw_index_rows"],
                "uniqueRunDirsChecked": audit["runs"]["unique_run_dirs_checked"],
                "missingRunDirs": len(audit["runs"]["missing_run_dirs"]),
                "missingArtifacts": len(audit["artifacts"]["missing_artifacts"]),
            },
            "findings": audit["findings"],
            "subsystems": audit["subsystems"],
            "reportArtifacts": {
                "json": LEDGER_AUDIT_REPORT_JSON,
                "markdown": LEDGER_AUDIT_REPORT_MD,
            },
        }
        context.write_json(LEDGER_AUDIT_REPORT_JSON, report)
        context.write_text(LEDGER_AUDIT_REPORT_MD, _report_markdown(report))
        return report

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        json_path = context.run_dir / LEDGER_AUDIT_REPORT_JSON
        md_path = context.run_dir / LEDGER_AUDIT_REPORT_MD
        severity_threshold = _finding_severity_threshold(context)
        findings = _findings_list(outputs.get("findings", []))
        blocking_findings = [
            finding
            for finding in findings
            if _finding_meets_threshold(finding, severity_threshold)
        ]
        return [
            CheckResult(
                "ledger_audit_report_json_written",
                json_path.exists(),
                LEDGER_AUDIT_REPORT_JSON if json_path.exists() else "missing",
                {"path": str(json_path)},
            ),
            CheckResult(
                "ledger_audit_report_markdown_written",
                md_path.exists(),
                LEDGER_AUDIT_REPORT_MD if md_path.exists() else "missing",
                {"path": str(md_path)},
            ),
            CheckResult(
                "no_ledger_audit_findings",
                len(blocking_findings) == 0,
                (
                    f"threshold={severity_threshold}, "
                    f"blocking_findings={len(blocking_findings)}, "
                    f"findings={len(findings)}"
                ),
                {
                    "severityThreshold": severity_threshold,
                    "blockingFindings": blocking_findings,
                    "findings": findings,
                },
            ),
        ]


def _ledger_paths(context: TaskContext) -> tuple[Path, Path]:
    if context.ledger is None:
        raise RuntimeError("ledger-audit requires a ledger-backed context")
    return context.ledger.root, context.ledger.index_path


def _requested_checks(value: object) -> tuple[str, ...]:
    raw = str(value or "all")
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    valid = {*_ALL_CHECKS, "all"}
    invalid = [item for item in items if item not in valid]
    if invalid:
        expected = ", ".join(("all", *_ALL_CHECKS))
        raise ValueError(
            f"unknown ledger-audit check: {', '.join(invalid)}; expected one of {expected}"
        )
    if not items or "all" in items:
        return _ALL_CHECKS

    requested: list[str] = []
    for item in items:
        if item not in _ALL_CHECKS:
            continue
        if item not in requested:
            requested.append(item)
    if not requested:
        return ("index",)
    if ("runs" in requested or "artifacts" in requested) and "index" not in requested:
        requested.insert(0, "index")
    if "artifacts" in requested and "runs" not in requested:
        artifact_index = requested.index("artifacts")
        requested.insert(artifact_index, "runs")
    return tuple(requested)


def _parse_check_arg(value: str) -> str:
    try:
        _requested_checks(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _parse_finding_severity_threshold(value: str) -> str:
    try:
        return _normalise_finding_severity_threshold(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _finding_severity_threshold(context: TaskContext) -> str:
    return _normalise_finding_severity_threshold(
        getattr(
            getattr(context, "args", None),
            "finding_severity_threshold",
            _DEFAULT_FINDING_SEVERITY_THRESHOLD,
        )
    )


def _normalise_finding_severity_threshold(value: object) -> str:
    severity = str(value or _DEFAULT_FINDING_SEVERITY_THRESHOLD).strip().lower()
    if severity not in _FINDING_SEVERITY_RANK:
        expected = ", ".join(_FINDING_SEVERITIES)
        raise ValueError(
            f"unknown ledger-audit finding severity threshold: {severity}; "
            f"expected one of {expected}"
        )
    return severity


def _findings_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return [
            {
                "code": "malformed_findings_payload",
                "severity": "critical",
                "detail": "findings must be a list",
            }
        ]
    findings: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            findings.append(item)
        else:
            findings.append(
                {
                    "code": "malformed_finding",
                    "severity": "critical",
                    "detail": "finding must be a JSON object",
                }
            )
    return findings


def _finding_meets_threshold(
    finding: dict[str, Any],
    severity_threshold: str,
) -> bool:
    severity = str(finding.get("severity") or "critical").strip().lower()
    severity_rank = _FINDING_SEVERITY_RANK.get(
        severity,
        _FINDING_SEVERITY_RANK["critical"],
    )
    threshold_rank = _FINDING_SEVERITY_RANK[severity_threshold]
    return severity_rank >= threshold_rank


def _preflight_gate_names(checks: tuple[str, ...]) -> list[str]:
    gates = [
        "ledger_root_readable",
        "ledger_index_readable",
        "ledger_index_jsonl_valid",
    ]
    if "runs" in checks:
        gates.append("ledger_run_dirs_present")
    if "artifacts" in checks:
        gates.append("ledger_artifact_refs_present")
    return gates


def _audit_ledger(
    ledger_root: Path,
    index_path: Path,
    checks: tuple[str, ...],
) -> dict[str, Any]:
    index = _read_index(index_path)
    findings: list[dict[str, Any]] = []
    root_state = _root_state(ledger_root)
    subsystems = {
        _RESTORE_SQLITE_SUBSYSTEM: _empty_restore_sqlite_subsystem(
            enabled="artifacts" in checks
        ),
        _GOVERNANCE_CONFIG_SUBSYSTEM: _empty_governance_config_subsystem(
            enabled="artifacts" in checks
        ),
        _COLLECTOR_PROMOTION_SUBSYSTEM: _empty_collector_promotion_subsystem(
            enabled="artifacts" in checks
        ),
        _SUPPRESSION_OUTBOX_SUBSYSTEM: _empty_suppression_outbox_subsystem(
            enabled="artifacts" in checks
        ),
    }

    for malformed in index["malformed_rows"]:
        findings.append(
            {
                "code": "malformed_index_row",
                "severity": "high",
                "line": malformed["line"],
                "detail": malformed["detail"],
            }
        )

    runs_state = {
        "raw_index_rows": index["valid_count"],
        "unique_run_dirs_checked": 0,
        "missing_run_dirs": [],
        "missing_run_dir_fields": [],
    }
    artifacts_state = {
        "checked_count": 0,
        "missing_artifacts": [],
        "malformed_ledgers": [],
    }

    if index["read_error"] is not None:
        findings.append(
            {
                "code": "ledger_index_unreadable",
                "severity": "critical",
                "path": str(index_path),
                "detail": index["read_error"],
            }
        )

    if "runs" in checks or "artifacts" in checks:
        runs_state, run_findings = _audit_run_dirs(index["entries"])
        findings.extend(run_findings)

    if "artifacts" in checks:
        artifacts_state, artifact_findings = _audit_artifacts(index["entries"])
        findings.extend(artifact_findings)
        restore_sqlite_state = _audit_restore_sqlite_subsystem(index["entries"])
        subsystems[_RESTORE_SQLITE_SUBSYSTEM] = restore_sqlite_state
        findings.extend(restore_sqlite_state["findings"])
        governance_config_state = _audit_governance_config_subsystem(
            index["entries"]
        )
        subsystems[_GOVERNANCE_CONFIG_SUBSYSTEM] = governance_config_state
        findings.extend(governance_config_state["findings"])
        collector_promotion_state = _audit_collector_promotion_subsystem(
            index["entries"]
        )
        subsystems[_COLLECTOR_PROMOTION_SUBSYSTEM] = collector_promotion_state
        findings.extend(collector_promotion_state["findings"])
        suppression_outbox_state = _audit_suppression_outbox_subsystem(
            index["entries"]
        )
        subsystems[_SUPPRESSION_OUTBOX_SUBSYSTEM] = suppression_outbox_state
        findings.extend(suppression_outbox_state["findings"])

    return {
        "root": root_state,
        "index": index,
        "runs": runs_state,
        "artifacts": artifacts_state,
        "subsystems": subsystems,
        "findings": findings,
    }


def _root_state(ledger_root: Path) -> dict[str, Any]:
    exists = ledger_root.exists()
    is_dir = ledger_root.is_dir()
    detail = "ok" if exists and is_dir else "missing"
    readable = exists and is_dir
    if readable:
        try:
            next(ledger_root.iterdir(), None)
        except OSError as exc:
            readable = False
            detail = str(exc)
    return {
        "path": str(ledger_root),
        "exists": exists,
        "is_dir": is_dir,
        "readable": readable,
        "detail": detail,
    }


def _read_index(index_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "path": str(index_path),
        "exists": index_path.exists(),
        "is_file": index_path.is_file(),
        "readable": False,
        "read_error": None,
        "line_count": 0,
        "valid_count": 0,
        "malformed_count": 0,
        "malformed_rows": [],
        "entries": [],
        "detail": "missing",
    }
    if not index_path.exists() or not index_path.is_file():
        return state

    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        state["read_error"] = str(exc)
        state["detail"] = str(exc)
        return state

    state["readable"] = True
    state["line_count"] = len(lines)
    state["detail"] = "ok"
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            state["malformed_rows"].append(
                {
                    "line": line_number,
                    "detail": str(exc),
                    "raw": line[:200],
                }
            )
            continue
        if not isinstance(row, dict):
            state["malformed_rows"].append(
                {
                    "line": line_number,
                    "detail": "index row must be a JSON object",
                    "raw": line[:200],
                }
            )
            continue
        state["entries"].append(row)
    state["valid_count"] = len(state["entries"])
    state["malformed_count"] = len(state["malformed_rows"])
    return state


def _audit_run_dirs(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    missing_dirs: list[str] = []
    missing_fields: list[str] = []
    findings: list[dict[str, Any]] = []
    seen_run_dirs: set[str] = set()
    for row in rows:
        run_id = str(row.get("runId") or "unknown")
        run_dir_value = row.get("runDir")
        if not run_dir_value:
            missing_fields.append(run_id)
            findings.append(
                {
                    "code": "missing_run_dir_field",
                    "severity": "high",
                    "runId": run_id,
                    "detail": "index row does not declare runDir",
                }
            )
            continue
        run_dir = Path(str(run_dir_value))
        run_dir_key = str(run_dir)
        if run_dir_key in seen_run_dirs:
            continue
        seen_run_dirs.add(run_dir_key)
        if not run_dir.exists() or not run_dir.is_dir():
            missing_dirs.append(run_dir_key)
            findings.append(
                {
                    "code": "missing_run_dir",
                    "severity": "high",
                    "runId": run_id,
                    "path": str(run_dir),
                }
            )
    return (
        {
            "raw_index_rows": len(rows),
            "unique_run_dirs_checked": len(seen_run_dirs),
            "missing_run_dirs": missing_dirs,
            "missing_run_dir_fields": missing_fields,
        },
        findings,
    )


def _audit_artifacts(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    missing_artifacts: list[str] = []
    malformed_ledgers: list[str] = []
    findings: list[dict[str, Any]] = []
    checked_count = 0
    seen_run_dirs: set[str] = set()
    for row in rows:
        run_id = str(row.get("runId") or "unknown")
        run_dir_value = row.get("runDir")
        if not run_dir_value:
            continue
        run_dir = Path(str(run_dir_value))
        run_dir_key = str(run_dir)
        if run_dir_key in seen_run_dirs:
            continue
        seen_run_dirs.add(run_dir_key)
        if not run_dir.exists() or not run_dir.is_dir():
            continue

        ledger_path = run_dir / "ledger.json"
        if not ledger_path.exists():
            missing_artifacts.append(str(ledger_path))
            findings.append(
                {
                    "code": "missing_ledger_artifact",
                    "severity": "high",
                    "runId": run_id,
                    "path": str(ledger_path),
                }
            )
            continue

        try:
            ledger_payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            malformed_ledgers.append(str(ledger_path))
            findings.append(
                {
                    "code": "malformed_ledger_artifact",
                    "severity": "high",
                    "runId": run_id,
                    "path": str(ledger_path),
                    "detail": str(exc),
                }
            )
            continue

        artifacts = ledger_payload.get("artifacts", {})
        if not isinstance(artifacts, dict):
            malformed_ledgers.append(str(ledger_path))
            findings.append(
                {
                    "code": "malformed_ledger_artifact_refs",
                    "severity": "high",
                    "runId": run_id,
                    "path": str(ledger_path),
                }
            )
            continue

        for relative_path in artifacts.values():
            if not isinstance(relative_path, str):
                continue
            checked_count += 1
            artifact_path = run_dir / relative_path
            if artifact_path.exists():
                continue
            missing_artifacts.append(str(artifact_path))
            findings.append(
                {
                    "code": "missing_ledger_artifact_ref",
                    "severity": "high",
                    "runId": run_id,
                    "path": str(artifact_path),
                }
            )

    return (
        {
            "checked_count": checked_count,
            "missing_artifacts": missing_artifacts,
            "malformed_ledgers": malformed_ledgers,
        },
        findings,
    )


def _index_jsonl_detail(audit: dict[str, Any]) -> str:
    index = audit["index"]
    if index["read_error"]:
        return str(index["read_error"])
    return f"malformed={index['malformed_count']}, valid={index['valid_count']}"


def _run_dirs_detail(audit: dict[str, Any]) -> str:
    runs = audit["runs"]
    return (
        f"raw_rows={runs['raw_index_rows']}, "
        f"unique_run_dirs={runs['unique_run_dirs_checked']}, "
        f"missing_dirs={len(runs['missing_run_dirs'])}, "
        f"missing_fields={len(runs['missing_run_dir_fields'])}"
    )


def _artifacts_detail(audit: dict[str, Any]) -> str:
    artifacts = audit["artifacts"]
    return (
        f"missing_artifacts={len(artifacts['missing_artifacts'])}, "
        f"malformed_ledgers={len(artifacts['malformed_ledgers'])}"
    )


def _report_markdown(report: dict[str, Any]) -> str:
    findings = report.get("findings", [])
    subsystems = report.get("subsystems", {})
    lines = [
        "# Hermes Ledger Audit Report",
        "",
        f"- Audit ID: {report.get('auditId')}",
        f"- Generated at: {report.get('generatedAt')}",
        f"- Ledger root: {report.get('ledgerRoot')}",
        f"- Ledger index: {report.get('ledgerIndex')}",
        f"- Checks: {', '.join(report.get('checksRun', []))}",
        f"- Findings: {len(findings)}",
        "",
    ]
    if findings:
        lines.append("## Findings")
        lines.append("")
        for finding in findings:
            code = finding.get("code", "unknown")
            path = finding.get("path")
            detail = finding.get("detail")
            suffix = f" ({path})" if path else ""
            if detail:
                suffix = f"{suffix}: {detail}"
            lines.append(f"- {code}{suffix}")
        lines.append("")
    if subsystems:
        lines.append("## Subsystems")
        lines.append("")
        for name, subsystem in subsystems.items():
            subsystem_findings = subsystem.get("findings", [])
            lines.append(
                f"- {name}: runs={subsystem.get('runsChecked', 0)}, "
                f"findings={len(subsystem_findings)}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"
