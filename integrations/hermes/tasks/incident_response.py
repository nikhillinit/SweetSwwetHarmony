from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ops.maintenance import incident as incident_capsules
from ops.maintenance.incident import MaintenanceIncident

from .base import CheckResult, HermesTask, TaskContext

INCIDENT_PHASES = ("freeze", "analyze", "repair-plan", "verify")
INCIDENT_RESPONSE_STATUS = "investigating"
PACKET_JSON = "hermes_response_packet.json"
PACKET_MARKDOWN = "hermes_response_packet.md"


class IncidentResponseTask(HermesTask):
    name = "incident"
    description = "Locked, ledger-backed wrapper for maintenance incident capsules."
    risk_level = "medium"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("incident-response",)
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--incident-id")
        parser.add_argument(
            "--phase-name",
            dest="incident_phase",
            choices=INCIDENT_PHASES,
            default="freeze",
        )
        parser.add_argument(
            "--artifact-root",
            default="ops/artifacts/maintenance",
            help="Maintenance incident capsule root.",
        )

    def plan(self, context: TaskContext) -> dict[str, Any]:
        incident_id = _arg(context, "incident_id")
        phase = _phase(context)
        artifact_root = _artifact_root(context)
        incident_state = _inspect_incident(artifact_root, incident_id)
        artifact_dir = (
            artifact_root / incident_id
            if incident_id
            else artifact_root / "incident-unspecified"
        )

        plan = self._base_plan(context)
        plan.update(
            {
                "incident_id": incident_id,
                "phase": phase,
                "artifact_root": str(artifact_root),
                "incident": incident_state,
                "capsule_contract": {
                    "module": "ops.maintenance.incident",
                    "operations": ["load_incident", "update_incident_status"],
                },
                "packet": {
                    "artifact_dir": str(artifact_dir),
                    "json_path": str(artifact_dir / PACKET_JSON),
                    "markdown_path": str(artifact_dir / PACKET_MARKDOWN),
                },
                "expected_capsule_state": {
                    "status_after": INCIDENT_RESPONSE_STATUS,
                    "response_phase": phase,
                },
                "locks_required": list(self.required_locks),
                "ack_risk_required": False,
                "ack_risk_token": None,
                "preflight_gates": [
                    "incident_id_declared",
                    "phase_valid",
                    "artifact_root_exists",
                    "incident_capsule_exists",
                    "incident_capsule_json_valid",
                ],
                "postflight_gates": [
                    "incident_packet_written",
                    "incident_packet_matches_plan",
                    "incident_status_matches_expected",
                    "ledger_written",
                ],
                "rollback": {
                    "available": True,
                    "recipe": (
                        "Use the previous incident.json from source control or backup "
                        "and remove Hermes response packet files if this run must be undone."
                    ),
                },
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_files": [str(artifact_dir)],
                    "affected_tables": [],
                    "external_systems": [],
                },
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        incident_id = plan.get("incident_id")
        phase = plan.get("phase")
        artifact_root = Path(str(plan.get("artifact_root")))
        incident_state = _inspect_incident(
            artifact_root,
            str(incident_id) if incident_id else None,
        )

        return [
            CheckResult(
                "incident_id_declared",
                bool(incident_id),
                str(incident_id or "missing --incident-id"),
            ),
            CheckResult(
                "phase_valid",
                phase in INCIDENT_PHASES,
                str(phase or "missing phase"),
                {"allowed": list(INCIDENT_PHASES), "phase": phase},
            ),
            CheckResult(
                "artifact_root_exists",
                artifact_root.exists() and artifact_root.is_dir(),
                str(artifact_root),
            ),
            CheckResult(
                "incident_capsule_exists",
                bool(incident_state.get("exists")),
                str(incident_state.get("path")),
                incident_state,
            ),
            CheckResult(
                "incident_capsule_json_valid",
                bool(incident_state.get("valid")),
                str(incident_state.get("detail")),
                incident_state,
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        packet = plan.get("packet", {})
        outputs = {
            "dryRun": True,
            "mutationCommitted": False,
            "incidentId": plan.get("incident_id"),
            "phase": plan.get("phase"),
            "wouldUpdateStatus": plan.get("expected_capsule_state", {}).get("status_after"),
            "wouldWriteFiles": [
                str(packet.get("json_path")),
                str(packet.get("markdown_path")),
            ],
            "capsuleContract": plan.get("capsule_contract", {}),
        }
        context.write_json("incident_response_dry_run.json", outputs)
        return outputs

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(plan.get("incident_id") or "")
        artifact_root = Path(str(plan.get("artifact_root")))
        phase = str(plan.get("phase") or "")
        expected_status = str(
            plan.get("expected_capsule_state", {}).get(
                "status_after",
                INCIDENT_RESPONSE_STATUS,
            )
        )

        incident_before = _load_incident(artifact_root, incident_id)
        status_before = incident_before.status if incident_before else None

        _update_incident_status(
            artifact_root,
            incident_id,
            expected_status,
            f"Hermes incident response phase {phase} recorded by run {context.run.run_id if context.run else 'unknown'}.",
        )
        incident_after = _load_incident(artifact_root, incident_id) or incident_before
        status_after = incident_after.status if incident_after else None

        packet = _write_response_packet(
            context,
            plan,
            incident=incident_after,
            status_before=status_before,
            status_after=status_after,
        )
        outputs = {
            "mutationCommitted": True,
            "incidentId": incident_id,
            "phase": phase,
            "statusBefore": status_before,
            "statusAfter": status_after,
            "packet": packet,
            "capsuleContract": plan.get("capsule_contract", {}),
        }
        context.write_json("incident_response_artifacts.json", outputs)
        return outputs

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        if context.mode == "dry-run":
            return [
                CheckResult(
                    "incident_response_dry_run_recorded",
                    (context.run_dir / "incident_response_dry_run.json").exists(),
                    "incident_response_dry_run.json",
                ),
                CheckResult(
                    "ledger_written",
                    (context.run_dir / "run_record.json").exists(),
                    "run_record.json",
                ),
            ]

        packet = plan.get("packet", {})
        packet_json = Path(str(packet.get("json_path")))
        packet_markdown = Path(str(packet.get("markdown_path")))
        packet_state = _read_packet(packet_json)
        artifact_root = Path(str(plan.get("artifact_root")))
        incident_id = str(plan.get("incident_id") or "")
        incident_state = _inspect_incident(artifact_root, incident_id)
        actual_status = incident_state.get("status")
        expected_status = plan.get("expected_capsule_state", {}).get("status_after")
        packet_matches = (
            packet_state.get("readable")
            and packet_state.get("incidentId") == incident_id
            and packet_state.get("phase") == plan.get("phase")
            and packet_state.get("statusAfter") == actual_status
        )

        return [
            CheckResult(
                "incident_packet_written",
                packet_json.exists() and packet_markdown.exists(),
                f"{packet_json}; {packet_markdown}",
                {"json": str(packet_json), "markdown": str(packet_markdown)},
            ),
            CheckResult(
                "incident_packet_matches_plan",
                bool(packet_matches),
                packet_state.get("detail", "packet checked"),
                packet_state,
            ),
            CheckResult(
                "incident_status_matches_expected",
                actual_status == expected_status,
                f"actual={actual_status} expected={expected_status}",
                incident_state,
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


def _arg(context: TaskContext, name: str) -> str | None:
    value = getattr(context.args, name, None)
    return str(value) if value not in (None, "") else None


def _phase(context: TaskContext) -> str:
    return _arg(context, "incident_phase") or "freeze"


def _artifact_root(context: TaskContext) -> Path:
    return (
        context.resolve(getattr(context.args, "artifact_root", None))
        or context.root / "ops" / "artifacts" / "maintenance"
    )


@contextmanager
def _capsule_root(root: Path) -> Iterator[None]:
    previous = incident_capsules.ARTIFACTS_DIR
    incident_capsules.ARTIFACTS_DIR = root
    try:
        yield
    finally:
        incident_capsules.ARTIFACTS_DIR = previous


def _load_incident(root: Path, incident_id: str | None) -> MaintenanceIncident | None:
    if not incident_id:
        return None
    try:
        with _capsule_root(root):
            return incident_capsules.load_incident(incident_id)
    except Exception:
        return None


def _update_incident_status(
    root: Path,
    incident_id: str,
    status: str,
    notes: str,
) -> MaintenanceIncident | None:
    with _capsule_root(root):
        return incident_capsules.update_incident_status(incident_id, status, notes)


def _inspect_incident(root: Path, incident_id: str | None) -> dict[str, Any]:
    artifact_dir = root / incident_id if incident_id else root / "incident-unspecified"
    incident_path = artifact_dir / "incident.json"
    evidence: dict[str, Any] = {
        "artifact_root": str(root),
        "artifact_dir": str(artifact_dir),
        "path": str(incident_path),
        "exists": incident_path.exists(),
        "valid": False,
        "status": None,
        "component": None,
        "detail": "incident id missing" if not incident_id else "incident capsule missing",
    }
    if not incident_id or not incident_path.exists():
        return evidence

    try:
        incident = _load_incident(root, incident_id)
    except Exception as exc:
        evidence["detail"] = str(exc)
        return evidence

    if incident is None:
        return evidence
    evidence.update(
        {
            "valid": True,
            "status": incident.status,
            "component": incident.component,
            "detail": "incident capsule readable",
        }
    )
    return evidence


def _write_response_packet(
    context: TaskContext,
    plan: dict[str, Any],
    *,
    incident: MaintenanceIncident | None,
    status_before: str | None,
    status_after: str | None,
) -> dict[str, Any]:
    packet = plan.get("packet", {})
    packet_json = Path(str(packet.get("json_path")))
    packet_markdown = Path(str(packet.get("markdown_path")))
    packet_json.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "incidentId": plan.get("incident_id"),
        "phase": plan.get("phase"),
        "generatedAt": generated_at,
        "runId": context.run.run_id if context.run else None,
        "statusBefore": status_before,
        "statusAfter": status_after,
        "capsule": asdict(incident) if incident else None,
        "packetFiles": [str(packet_json), str(packet_markdown)],
    }
    packet_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    packet_markdown.write_text(_packet_markdown(payload), encoding="utf-8")
    return {
        "jsonPath": str(packet_json),
        "markdownPath": str(packet_markdown),
        "generatedAt": generated_at,
    }


def _packet_markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Incident Response: {payload.get('incidentId')}",
            "",
            f"- Phase: {payload.get('phase')}",
            f"- Status before: {payload.get('statusBefore')}",
            f"- Status after: {payload.get('statusAfter')}",
            f"- Hermes run: {payload.get('runId')}",
            "",
        ]
    )


def _read_packet(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"readable": False, "detail": "packet missing", "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "readable": False,
            "detail": str(exc),
            "path": str(path),
        }
    if not isinstance(data, dict):
        return {"readable": False, "detail": "packet is not an object", "path": str(path)}
    data["readable"] = True
    data["detail"] = "packet readable"
    data["path"] = str(path)
    return data
