from __future__ import annotations

import argparse
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskFailure,
    resolve_task_db_path,
    run_async_blocking,
)

COLLECTOR_PROMOTE_ACK = "COLLECTOR_PROMOTE"
COLLECTOR_DEMOTE_ACK = "COLLECTOR_DEMOTE"
COLLECTOR_PROMOTION_ARTIFACT_VERSION = 1
COLLECTOR_PROMOTION_ARTIFACT = "collector_promotion.json"
DRY_RUN_DRIFT_ARTIFACT = "dry_run_drift.json"

PROMOTE_TARGETS = {"active", "promoted"}
DEMOTE_TARGETS = {"shadow", "disabled", "off", "not_relevant", "not-relevant"}

PROMOTION_TABLES = ["hunter_results", "signals", "audit_events", "idempotency_keys"]
DEMOTION_TABLES = ["hunter_results", "audit_events"]


class CollectorPromoteTask(HermesTask):
    name = "collector-promote"
    description = "Ledger-backed wrapper for hunter result promotion decisions."
    risk_level = "high"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("signals.db", "collector-promotion")
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--collector")
        parser.add_argument("--result-id", type=int)
        parser.add_argument("--collector-state", default="state/collectors.json")
        parser.add_argument("--collector-config", default=None)
        parser.add_argument("--idempotency-key", default=None)
        parser.add_argument("--allow-collision-as-known", action="store_true")

    def plan(self, context: TaskContext) -> dict[str, Any]:
        collector = _arg(context, "collector")
        result_id = _result_id(context)
        target_state = _arg(context, "target_state")
        db_path = _db_path(context)
        transition = _transition(target_state)
        database = _inspect_hunter_result(db_path, result_id)
        collector_state = _inspect_collector_state(context, collector)

        plan = self._base_plan(context)
        plan.update(
            {
                "collector": collector,
                "result_id": result_id,
                "target_state": target_state,
                "result_target_state": transition.get("result_target_state"),
                "transition": transition,
                "database": database,
                "planned_result_updated_at": database.get("updated_at"),
                "collector_state": collector_state,
                "workflow": {
                    "module": "workflows.hunter_promotion",
                    "promotion_contract": (
                        "async promote_hunter_result(store, result_id, "
                        "actor='system', idempotency_key=None, "
                        "expected_updated_at=None)"
                    ),
                    "demotion_contract": (
                        "async storage.hunter_result_store.update_result_status("
                        "store, result_id, 'not_relevant', "
                        "expected_updated_at=None, ...)"
                    ),
                },
                "artifacts": {
                    "record": COLLECTOR_PROMOTION_ARTIFACT,
                    "run_record": "run_record.json",
                },
                "locks_required": list(self.required_locks),
                "ack_risk_required": bool(transition.get("ack_risk_token")),
                "ack_risk_token": transition.get("ack_risk_token"),
                "preflight_gates": [
                    "collector_declared",
                    "result_id_declared",
                    "target_state_supported",
                    "collector_state_readable",
                    "collector_known",
                    "hunter_promotion_bridge_available",
                    "database_openable",
                    "hunter_result_found",
                    "result_collector_matches",
                    "result_status_eligible",
                ],
                "postflight_gates": [
                    "collector_promotion_artifact_written",
                    "no_dry_run_input_drift",
                    "promotion_result_success",
                    "desired_outcome_satisfied",
                    "ledger_written",
                ],
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_db": str(db_path) if context.mode == "execute" else None,
                    "affected_files": [str(db_path)] if context.mode == "execute" else [],
                    "affected_tables": _affected_tables(transition),
                    "external_systems": [],
                },
                "external_reads": [],
                "rollback": _rollback_recipe(transition, collector, result_id),
            }
        )
        return plan

    def required_ack_token(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> str | None:
        token = plan.get("transition", {}).get("ack_risk_token")
        return str(token) if token else None

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        collector = str(plan.get("collector") or "")
        result_id = plan.get("result_id")
        transition = plan.get("transition", {})
        collector_state = _inspect_collector_state(context, plan.get("collector"))
        hunter_result = _inspect_hunter_result(_db_path(context), result_id)
        bridge = _promotion_bridge_import_check()

        return [
            CheckResult("collector_declared", bool(collector), collector or "missing"),
            CheckResult(
                "result_id_declared",
                result_id is not None,
                str(result_id) if result_id is not None else "missing",
            ),
            CheckResult(
                "target_state_supported",
                bool(transition.get("valid")),
                str(transition.get("detail") or transition.get("requested") or "missing"),
                transition,
            ),
            CheckResult(
                "collector_state_readable",
                bool(collector_state.get("readable")),
                str(collector_state.get("detail") or ""),
                collector_state,
            ),
            CheckResult(
                "collector_known",
                bool(collector_state.get("collector_known")),
                str(collector_state.get("detail") or ""),
                collector_state,
            ),
            CheckResult(
                "hunter_promotion_bridge_available",
                bool(bridge.get("available")),
                str(bridge.get("detail") or ""),
                bridge,
            ),
            CheckResult(
                "database_openable",
                bool(hunter_result.get("openable")),
                str(hunter_result.get("detail") or ""),
                hunter_result,
            ),
            CheckResult(
                "hunter_result_found",
                bool(hunter_result.get("found")),
                str(hunter_result.get("detail") or ""),
                hunter_result,
            ),
            CheckResult(
                "result_collector_matches",
                _collector_matches(collector, hunter_result),
                _collector_match_detail(collector, hunter_result),
                hunter_result,
            ),
            CheckResult(
                "result_status_eligible",
                _status_eligible(transition, hunter_result),
                _status_detail(transition, hunter_result),
                {
                    "transition": transition,
                    "hunter_result": hunter_result,
                },
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        payload = _collector_payload(
            context,
            plan,
            dry_run=True,
            mutation_committed=False,
            promotion_result=None,
        )
        observed = _inspect_hunter_result(_db_path(context), plan.get("result_id"))
        drifts = _collector_dry_run_drifts(plan, observed)
        if drifts:
            drift_payload = {
                "artifactVersion": COLLECTOR_PROMOTION_ARTIFACT_VERSION,
                "task": CollectorPromoteTask.name,
                "mode": "dry-run",
                "dryRun": True,
                "mutationCommitted": False,
                "driftDetected": True,
                "drifts": drifts,
                "stalePreview": payload,
                "observed": {"hunterResult": observed},
                "dryRunDriftArtifact": DRY_RUN_DRIFT_ARTIFACT,
                "nextAction": (
                    "Refresh the collector promotion plan against current "
                    "hunter result state before rerunning dry-run."
                ),
            }
            context.write_json(DRY_RUN_DRIFT_ARTIFACT, drift_payload)
            return drift_payload

        _write_collector_artifact(context, payload)
        return payload

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        payload = _run_transition(context, plan)
        _write_collector_artifact(context, payload)
        return payload

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        artifact_path = context.run_dir / COLLECTOR_PROMOTION_ARTIFACT
        result = outputs.get("promotionResult")
        dry_run_success = bool(outputs.get("dryRun")) and not bool(
            outputs.get("mutationCommitted")
        ) and not bool(outputs.get("driftDetected"))
        result_success = dry_run_success or (
            isinstance(result, dict) and bool(result.get("success"))
        )
        desired_outcome_satisfied = dry_run_success or bool(
            outputs.get("desiredOutcomeSatisfied")
        )
        return [
            CheckResult(
                "collector_promotion_artifact_written",
                artifact_path.exists(),
                COLLECTOR_PROMOTION_ARTIFACT if artifact_path.exists() else "missing",
                {"path": str(artifact_path)},
            ),
            CheckResult(
                "no_dry_run_input_drift",
                not bool(outputs.get("driftDetected")),
                _dry_run_drift_detail(outputs),
                {
                    "drifts": outputs.get("drifts", []),
                    "artifact": (
                        str(context.run_dir / DRY_RUN_DRIFT_ARTIFACT)
                        if outputs.get("driftDetected")
                        else None
                    ),
                },
            ),
            CheckResult(
                "promotion_result_success",
                result_success,
                "dry-run" if dry_run_success else str(result or "missing"),
                result if isinstance(result, dict) else {},
            ),
            CheckResult(
                "desired_outcome_satisfied",
                desired_outcome_satisfied,
                "dry-run"
                if dry_run_success
                else (
                    "desired outcome satisfied"
                    if desired_outcome_satisfied
                    else "requested target not reached"
                ),
                {
                    "requestedResultStatus": outputs.get("requestedResultStatus"),
                    "actualResultStatus": outputs.get("actualResultStatus"),
                    "requestedTargetReached": outputs.get("requestedTargetReached"),
                    "desiredOutcomeSatisfied": outputs.get("desiredOutcomeSatisfied"),
                    "allowCollisionAsKnown": bool(
                        getattr(context.args, "allow_collision_as_known", False)
                    ),
                    "collision": outputs.get("collision"),
                },
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


def _result_id(context: TaskContext) -> int | None:
    value = getattr(context.args, "result_id", None)
    return int(value) if value is not None else None


def _db_path(context: TaskContext) -> Path:
    return resolve_task_db_path(context, getattr(context.args, "db_path", None))


def _collector_state_path(context: TaskContext) -> Path:
    return (
        context.resolve(getattr(context.args, "collector_state", None))
        or context.root / "state" / "collectors.json"
    )


def _collector_config_path(context: TaskContext) -> Path | None:
    return context.resolve(getattr(context.args, "collector_config", None))


def _transition(target_state: str | None) -> dict[str, Any]:
    requested = str(target_state or "").strip()
    normalized = requested.lower()
    if not requested:
        return {
            "valid": False,
            "requested": None,
            "detail": "missing target state",
            "action_type": None,
            "ack_risk_token": None,
            "result_target_state": None,
        }
    if normalized in PROMOTE_TARGETS:
        return {
            "valid": True,
            "requested": requested,
            "detail": "promote hunter result",
            "action_type": "hunter_promote",
            "ack_risk_token": COLLECTOR_PROMOTE_ACK,
            "result_target_state": "promoted",
        }
    if normalized in DEMOTE_TARGETS:
        return {
            "valid": True,
            "requested": requested,
            "detail": "demote hunter result",
            "action_type": "hunter_demote",
            "ack_risk_token": COLLECTOR_DEMOTE_ACK,
            "result_target_state": "not_relevant",
        }
    return {
        "valid": False,
        "requested": requested,
        "detail": f"unsupported target state: {requested}",
        "action_type": None,
        "ack_risk_token": None,
        "result_target_state": None,
    }


def _affected_tables(transition: dict[str, Any]) -> list[str]:
    if transition.get("action_type") == "hunter_promote":
        return list(PROMOTION_TABLES)
    if transition.get("action_type") == "hunter_demote":
        return list(DEMOTION_TABLES)
    return []


def _inspect_collector_state(
    context: TaskContext,
    collector: Any,
) -> dict[str, Any]:
    path = _collector_state_path(context)
    evidence: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "readable": False,
        "collector": collector,
        "collector_known": False,
        "entry": None,
        "detail": "collector missing",
    }
    if not collector:
        evidence["detail"] = "missing collector"
        return evidence

    try:
        from ops.collector_heartbeat import load_collector_state

        config_path = _collector_config_path(context)
        state = load_collector_state(
            path,
            config_path=str(config_path) if config_path else None,
        )
    except Exception as exc:
        evidence["detail"] = str(exc)
        return evidence

    collectors = state.get("collectors") if isinstance(state, dict) else {}
    entry = collectors.get(str(collector)) if isinstance(collectors, dict) else None
    evidence.update(
        {
            "readable": True,
            "collector_known": isinstance(entry, dict),
            "entry": entry if isinstance(entry, dict) else None,
            "detail": "collector known" if isinstance(entry, dict) else "collector not found",
            "schema_version": state.get("schema_version") if isinstance(state, dict) else None,
            "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        }
    )
    return evidence


def _inspect_hunter_result(db_path: Path, result_id: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "openable": False,
        "detail": "result id missing",
        "found": False,
        "result_id": result_id,
        "status": None,
        "source_api": None,
        "query_collector": None,
        "canonical_key": None,
        "promoted_signal_id": None,
        "updated_at": None,
    }
    if result_id is None:
        return evidence
    if not db_path.exists():
        evidence["detail"] = "database missing"
        return evidence

    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            evidence["openable"] = bool(integrity and integrity[0] == "ok")
            evidence["detail"] = str(integrity[0] if integrity else "missing")
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            evidence["tables"] = sorted(tables)
            if "hunter_results" not in tables:
                evidence["detail"] = "hunter_results table missing"
                return evidence

            if "hunter_queries" in tables:
                row = conn.execute(
                    """
                    SELECT r.id, r.status, r.source_api, r.canonical_key,
                           r.promoted_signal_id, q.collector, r.updated_at
                    FROM hunter_results r
                    LEFT JOIN hunter_queries q ON q.id = r.query_id
                    WHERE r.id = ?
                    """,
                    (int(result_id),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, status, source_api, canonical_key,
                           promoted_signal_id, NULL, updated_at
                    FROM hunter_results
                    WHERE id = ?
                    """,
                    (int(result_id),),
                ).fetchone()

            if not row:
                evidence["detail"] = f"hunter result {result_id} not found"
                return evidence

            evidence.update(
                {
                    "found": True,
                    "detail": "hunter result found",
                    "result_id": row[0],
                    "status": row[1],
                    "source_api": row[2],
                    "canonical_key": row[3],
                    "promoted_signal_id": row[4],
                    "query_collector": row[5],
                    "updated_at": row[6],
                }
            )
        finally:
            conn.close()
    except (sqlite3.DatabaseError, OSError, ValueError) as exc:
        evidence["detail"] = str(exc)
    return evidence


def _promotion_bridge_import_check() -> dict[str, Any]:
    try:
        _load_promote_hunter_result()
        _load_update_result_status()
    except Exception as exc:
        return {
            "available": False,
            "detail": str(exc),
            "module": "workflows.hunter_promotion/storage.hunter_result_store",
        }
    return {
        "available": True,
        "detail": "hunter promotion and status transition bridges importable",
        "module": "workflows.hunter_promotion/storage.hunter_result_store",
    }


def _collector_matches(collector: str, hunter_result: dict[str, Any]) -> bool:
    if not collector or not hunter_result.get("found"):
        return False
    expected = collector.lower()
    candidates = {
        str(value).lower()
        for value in (
            hunter_result.get("query_collector"),
            hunter_result.get("source_api"),
        )
        if value
    }
    return expected in candidates


def _collector_match_detail(collector: str, hunter_result: dict[str, Any]) -> str:
    if not collector:
        return "missing collector"
    if not hunter_result.get("found"):
        return "hunter result missing"
    return (
        f"collector={collector} query_collector={hunter_result.get('query_collector')} "
        f"source_api={hunter_result.get('source_api')}"
    )


def _status_eligible(
    transition: dict[str, Any],
    hunter_result: dict[str, Any],
) -> bool:
    if not transition.get("valid") or not hunter_result.get("found"):
        return False
    status = str(hunter_result.get("status") or "")
    if transition.get("action_type") == "hunter_promote":
        return status in {"relevant", "promoted"}
    if transition.get("action_type") == "hunter_demote":
        return status in {"pending", "relevant"}
    return False


def _status_detail(
    transition: dict[str, Any],
    hunter_result: dict[str, Any],
) -> str:
    status = str(hunter_result.get("status") or "missing")
    target = str(transition.get("result_target_state") or "unsupported")
    return f"status={status} target={target}"


def _rollback_recipe(
    transition: dict[str, Any],
    collector: str | None,
    result_id: int | None,
) -> dict[str, Any]:
    if transition.get("action_type") != "hunter_promote" or not collector or result_id is None:
        return {
            "available": False,
            "recipe": "No automatic rollback recipe is available for this transition.",
        }
    return {
        "available": False,
        "recipe": (
            "Promotion writes through workflows.hunter_promotion and the live "
            "hunter state machine treats promoted results as terminal; rollback "
            "requires operator review of the promoted signal, audit trail, and "
            "database backup rather than an automatic demotion command."
        ),
    }


def _run_transition(context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
    try:
        return run_async_blocking(_run_transition_async(context, plan))
    except TaskFailure:
        raise
    except Exception as exc:
        raise TaskFailure(str(exc)) from exc


async def _run_transition_async(
    context: TaskContext,
    plan: dict[str, Any],
) -> dict[str, Any]:
    transition = plan.get("transition", {})
    db_path = _db_path(context)
    result_id = int(plan.get("result_id"))
    actor = _actor(context)
    expected_updated_at = _planned_result_updated_at(plan)

    async with _open_signal_store(db_path, writable=True) as store:
        try:
            if transition.get("action_type") == "hunter_promote":
                promote = _load_promote_hunter_result()
                result = await promote(
                    store,
                    result_id,
                    actor=actor,
                    idempotency_key=getattr(context.args, "idempotency_key", None),
                    expected_updated_at=expected_updated_at,
                )
            elif transition.get("action_type") == "hunter_demote":
                update_status = _load_update_result_status()
                await update_status(
                    store,
                    result_id,
                    "not_relevant",
                    operator_feedback=_arg(context, "reason"),
                    actor=actor,
                    expected_updated_at=expected_updated_at,
                )
                result = {
                    "success": True,
                    "result_id": result_id,
                    "status": "not_relevant",
                    "message": "Hunter result demoted to not_relevant",
                }
            else:
                raise TaskFailure("unsupported collector promotion transition")
        except Exception as exc:
            if _is_stale_update_error(exc):
                raise _stale_update_failure(context, plan, exc) from exc
            raise

    promotion_result = _promotion_result_to_dict(result)
    writes_committed = _writes_committed(dry_run=False, promotion_result=promotion_result)
    return _collector_payload(
        context,
        plan,
        dry_run=False,
        mutation_committed=writes_committed,
        promotion_result=promotion_result,
    )


def _collector_payload(
    context: TaskContext,
    plan: dict[str, Any],
    *,
    dry_run: bool,
    mutation_committed: bool,
    promotion_result: dict[str, Any] | None,
) -> dict[str, Any]:
    requested_status = _requested_result_status(plan)
    actual_status = _actual_result_status(
        plan,
        dry_run=dry_run,
        promotion_result=promotion_result,
    )
    idempotent = _idempotent(promotion_result)
    collision = bool(promotion_result and promotion_result.get("collision"))
    requested_target_reached = bool(
        requested_status and actual_status and requested_status == actual_status
    )
    desired_outcome_satisfied = requested_target_reached or (
        collision
        and bool(getattr(context.args, "allow_collision_as_known", False))
        and requested_status == "promoted"
        and actual_status == "already_known"
    )
    return {
        "artifactVersion": COLLECTOR_PROMOTION_ARTIFACT_VERSION,
        "task": CollectorPromoteTask.name,
        "mode": context.mode,
        "dryRun": dry_run,
        "mutationCommitted": mutation_committed,
        "collector": plan.get("collector"),
        "resultId": plan.get("result_id"),
        "targetState": plan.get("target_state"),
        "resultTargetState": plan.get("result_target_state"),
        "requestedResultStatus": requested_status,
        "actualResultStatus": actual_status,
        "requestedTargetReached": requested_target_reached,
        "desiredOutcomeSatisfied": desired_outcome_satisfied,
        "idempotent": idempotent,
        "collision": collision,
        "writesCommitted": mutation_committed,
        "transition": plan.get("transition", {}),
        "promotionResult": promotion_result,
        "artifactCommit": {
            "ledgerOnly": dry_run,
            "runtimeState": mutation_committed,
            "externalSystems": False,
        },
        "persistence": {
            "persisted": mutation_committed,
            "affectedDb": plan.get("mutation", {}).get("affected_db"),
            "affectedTables": plan.get("mutation", {}).get("affected_tables", []),
            "externalSystems": [],
        },
        "auditEvidence": _collector_audit_evidence(plan),
    }


def _collector_audit_evidence(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "planHash": plan.get("planHash"),
        "plannedResultUpdatedAt": _planned_result_updated_at(plan),
        "database": plan.get("database") if isinstance(plan.get("database"), dict) else {},
        "collectorState": (
            plan.get("collector_state")
            if isinstance(plan.get("collector_state"), dict)
            else {}
        ),
        "ackRiskToken": plan.get("transition", {}).get("ack_risk_token"),
    }


def _collector_dry_run_drifts(
    plan: dict[str, Any],
    observed: dict[str, Any],
) -> list[dict[str, Any]]:
    database = plan.get("database")
    planned = database if isinstance(database, dict) else {}
    drifts: list[dict[str, Any]] = []
    for field in ("status", "updated_at"):
        planned_value = planned.get(field)
        observed_value = observed.get(field)
        if planned_value == observed_value:
            continue
        drifts.append(
            {
                "field": field,
                "planned": planned_value,
                "observed": observed_value,
                "resultId": plan.get("result_id"),
            }
        )
    return drifts


def _dry_run_drift_detail(outputs: dict[str, Any]) -> str:
    if not outputs.get("driftDetected"):
        return "no dry-run input drift"
    return f"drifts={len(outputs.get('drifts', []))}"


def _write_collector_artifact(context: TaskContext, payload: dict[str, Any]) -> Path:
    return context.write_json(COLLECTOR_PROMOTION_ARTIFACT, payload)


def _promotion_result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    return {
        "success": bool(getattr(result, "success", False)),
        "signal_id": getattr(result, "signal_id", None),
        "result_id": getattr(result, "result_id", None),
        "status": getattr(result, "status", ""),
        "message": getattr(result, "message", ""),
        "collision": bool(getattr(result, "collision", False)),
    }


def _planned_result_updated_at(plan: dict[str, Any]) -> str | None:
    value = plan.get("planned_result_updated_at")
    if value:
        return str(value)
    database = plan.get("database")
    if isinstance(database, dict) and database.get("updated_at"):
        return str(database["updated_at"])
    return None


def _requested_result_status(plan: dict[str, Any]) -> str | None:
    value = plan.get("result_target_state")
    return str(value) if value else None


def _actual_result_status(
    plan: dict[str, Any],
    *,
    dry_run: bool,
    promotion_result: dict[str, Any] | None,
) -> str | None:
    if dry_run or not promotion_result:
        database = plan.get("database")
        if isinstance(database, dict) and database.get("status"):
            return str(database["status"])
        return None

    status = str(promotion_result.get("status") or "")
    if status == "already_promoted":
        return "promoted"
    return status or None


def _idempotent(promotion_result: dict[str, Any] | None) -> bool:
    return bool(promotion_result and promotion_result.get("status") == "already_promoted")


def _writes_committed(
    *,
    dry_run: bool,
    promotion_result: dict[str, Any] | None,
) -> bool:
    if dry_run or not promotion_result or not bool(promotion_result.get("success")):
        return False
    return str(promotion_result.get("status") or "") != "already_promoted"


def _is_stale_update_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "StaleUpdateError"


def _stale_update_failure(
    context: TaskContext,
    plan: dict[str, Any],
    exc: Exception,
) -> TaskFailure:
    observed = _inspect_hunter_result(_db_path(context), plan.get("result_id"))
    planned = plan.get("database") if isinstance(plan.get("database"), dict) else {}
    planned_updated_at = _planned_result_updated_at(plan)
    observed_updated_at = (
        str(observed.get("updated_at")) if observed.get("updated_at") else None
    )
    return TaskFailure(
        (
            f"stale hunter result version for result {plan.get('result_id')}: "
            f"planned updated_at={planned_updated_at}, "
            f"observed updated_at={observed_updated_at}"
        ),
        evidence={
            "result_id": plan.get("result_id"),
            "planned": {
                "updated_at": planned_updated_at,
                "hunter_result": planned,
            },
            "observed": {
                "updated_at": observed_updated_at,
                "hunter_result": observed,
            },
            "error": str(exc),
        },
    )


def _actor(context: TaskContext) -> str:
    actor_type = getattr(context.args, "actor_type", None) or "operator"
    actor_id = getattr(context.args, "actor_id", None) or "unknown"
    return f"{actor_type}:{actor_id}"


@asynccontextmanager
async def _open_signal_store(
    db_path: Path,
    *,
    writable: bool,
) -> AsyncIterator[Any]:
    from storage.signal_store import SignalStore

    store = SignalStore(db_path=db_path, read_only=not writable)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


def _load_promote_hunter_result() -> Callable[..., Awaitable[Any]]:
    from workflows.hunter_promotion import promote_hunter_result

    return promote_hunter_result


def _load_update_result_status() -> Callable[..., Awaitable[Any]]:
    from storage.hunter_result_store import update_result_status

    return update_result_status
