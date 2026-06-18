from __future__ import annotations

import argparse
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Type

from .base import (
    CheckResult,
    HermesTask,
    TaskContext,
    TaskFailure,
    resolve_task_db_path,
    run_async_blocking,
)

SHADOW_TABLES = ("shadow_entity_runs", "shadow_disagreements")
SHADOW_ARTIFACT = "shadow_validation.json"


@dataclass(frozen=True)
class ShadowEvaluatorBindings:
    ShadowRunConfig: Type[Any]
    run_shadow_comparison: Callable[[Any, Any, Any], Awaitable[Any]]
    store_shadow_run: Callable[[Any, Any], Awaitable[int]]
    store_skipped_shadow_run: Callable[[Any, str], Awaitable[int]]


class ShadowValidateTask(HermesTask):
    name = "shadow-validate"
    description = "Ledger-backed wrapper for intelligence.shadow_entity_evaluator."
    risk_level = "medium"
    supported_modes = ("plan-only", "preflight-only", "dry-run", "execute")
    required_locks = ("signals.db", "shadow-entity-evaluator")
    ledger_backed = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--max-signals", type=int, default=500)
        parser.add_argument("--sample-rate", type=float, default=1.0)
        parser.add_argument("--timeout-seconds", type=float, default=30.0)
        parser.add_argument("--max-disagreements", type=int, default=1000)
        parser.add_argument("--min-similarity-threshold", type=float, default=0.85)
        parser.add_argument("--max-suggestions", type=int, default=100)
        parser.add_argument("--min-agreement-rate", type=float, default=0.95)

    def plan(self, context: TaskContext) -> dict[str, Any]:
        db_path = _db_path(context)
        config = _shadow_config_from_args(context)
        plan = self._base_plan(context)
        plan.update(
            {
                "database": _inspect_shadow_database(db_path),
                "evaluator": {
                    "module": "intelligence.shadow_entity_evaluator",
                    "run_contract": "async run_shadow_comparison(store, ro_identity_store, config=None)",
                    "persistence_contract": "async store_shadow_run(store, result)",
                },
                "shadow_config": config,
                "artifacts": {
                    "record": SHADOW_ARTIFACT,
                },
                "preflight_gates": [
                    "shadow_evaluator_importable",
                    "shadow_runtime_importable",
                    "database_openable",
                    "signals_schema_supports_shadow",
                ],
                "postflight_gates": [
                    "shadow_status_completed",
                    "agreement_rate_above_threshold",
                    "shadow_validation_artifact_written",
                    "ledger_written",
                ],
                "locks_required": list(self.required_locks),
                "mutation": {
                    "allowed": context.mode == "execute",
                    "affected_files": [str(db_path)] if context.mode == "execute" else [],
                    "affected_tables": list(SHADOW_TABLES),
                    "external_systems": [],
                    "ledger_artifacts": [SHADOW_ARTIFACT, "run_record.json"],
                },
            }
        )
        return plan

    def preflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
    ) -> list[CheckResult]:
        evaluator = _evaluator_import_check()
        runtime = _runtime_import_check()
        database = _inspect_shadow_database(_db_path(context))
        return [
            CheckResult(
                "shadow_evaluator_importable",
                evaluator["available"],
                evaluator["detail"],
                evaluator,
            ),
            CheckResult(
                "shadow_runtime_importable",
                runtime["available"],
                runtime["detail"],
                runtime,
            ),
            CheckResult(
                "database_openable",
                bool(database["openable"]),
                database["detail"],
                database,
            ),
            CheckResult(
                "signals_schema_supports_shadow",
                bool(database["signals_company_id_column"]),
                database["signals_detail"],
                database,
            ),
        ]

    def dry_run(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        return _run_shadow(context, plan, persist=False)

    def execute(self, context: TaskContext, plan: dict[str, Any]) -> dict[str, Any]:
        return _run_shadow(context, plan, persist=True)

    def postflight(
        self,
        context: TaskContext,
        plan: dict[str, Any],
        outputs: dict[str, Any],
    ) -> list[CheckResult]:
        status = str(outputs.get("shadowRun", {}).get("status") or "")
        rate = _float_value(outputs.get("shadowRun", {}).get("agreementRate"))
        threshold = float(plan.get("shadow_config", {}).get("min_agreement_rate", 0.95))
        artifact_path = context.run_dir / SHADOW_ARTIFACT
        return [
            CheckResult(
                "shadow_status_completed",
                status == "completed",
                status or "missing",
                outputs.get("shadowRun", {}),
            ),
            CheckResult(
                "agreement_rate_above_threshold",
                rate is not None and rate >= threshold,
                (
                    f"{rate:.3f} >= {threshold:.3f}"
                    if rate is not None
                    else f"missing >= {threshold:.3f}"
                ),
                {
                    "agreementRate": rate,
                    "threshold": threshold,
                },
            ),
            CheckResult(
                "shadow_validation_artifact_written",
                artifact_path.exists(),
                SHADOW_ARTIFACT if artifact_path.exists() else "missing",
                {"path": str(artifact_path)},
            ),
            CheckResult(
                "ledger_written",
                (context.run_dir / "run_record.json").exists(),
                "run_record.json",
            ),
        ]


def _db_path(context: TaskContext) -> Path:
    return resolve_task_db_path(context, getattr(context.args, "db_path", None))


def _shadow_config_from_args(context: TaskContext) -> dict[str, Any]:
    return {
        "max_signals_per_run": int(getattr(context.args, "max_signals", 500) or 500),
        "sample_rate": float(getattr(context.args, "sample_rate", 1.0) or 1.0),
        "timeout_seconds": float(getattr(context.args, "timeout_seconds", 30.0) or 30.0),
        "max_disagreements_stored": int(
            getattr(context.args, "max_disagreements", 1000) or 1000
        ),
        "min_similarity_threshold": float(
            getattr(context.args, "min_similarity_threshold", 0.85) or 0.85
        ),
        "max_suggestions_per_run": int(
            getattr(context.args, "max_suggestions", 100) or 100
        ),
        "min_agreement_rate": float(
            getattr(context.args, "min_agreement_rate", 0.95) or 0.95
        ),
    }


def _evaluator_import_check() -> dict[str, Any]:
    try:
        _load_shadow_evaluator()
    except Exception as exc:
        return {
            "available": False,
            "detail": str(exc),
            "module": "intelligence.shadow_entity_evaluator",
        }
    return {
        "available": True,
        "detail": "intelligence.shadow_entity_evaluator importable",
        "module": "intelligence.shadow_entity_evaluator",
    }


def _runtime_import_check() -> dict[str, Any]:
    try:
        from storage.entity_identity_store import EntityIdentityStore  # noqa: F401
        from storage.readonly_identity_store import ReadOnlyIdentityStore  # noqa: F401
        from storage.signal_store import SignalStore  # noqa: F401
    except Exception as exc:
        return {
            "available": False,
            "detail": str(exc),
        }
    return {
        "available": True,
        "detail": "SignalStore and read-only identity runtime importable",
    }


def _inspect_shadow_database(db_path: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "openable": False,
        "detail": "database missing",
        "signals_table": False,
        "signals_company_id_column": False,
        "signals_detail": "signals table missing",
        "shadow_tables": {},
    }
    if not db_path.exists():
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
            evidence["signals_table"] = "signals" in tables
            if "signals" in tables:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(signals)").fetchall()
                }
                evidence["signals_company_id_column"] = "company_id" in columns
                evidence["signals_detail"] = (
                    "signals.company_id present"
                    if evidence["signals_company_id_column"]
                    else "signals.company_id missing"
                )
            evidence["shadow_tables"] = {
                table: table in tables for table in SHADOW_TABLES
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        evidence["detail"] = str(exc)
        evidence["signals_detail"] = str(exc)
    return evidence


def _load_shadow_evaluator() -> ShadowEvaluatorBindings:
    from intelligence.shadow_entity_evaluator import (
        ShadowRunConfig,
        run_shadow_comparison,
        store_shadow_run,
        store_skipped_shadow_run,
    )

    return ShadowEvaluatorBindings(
        ShadowRunConfig=ShadowRunConfig,
        run_shadow_comparison=run_shadow_comparison,
        store_shadow_run=store_shadow_run,
        store_skipped_shadow_run=store_skipped_shadow_run,
    )


@asynccontextmanager
async def _open_shadow_runtime(
    context: TaskContext,
    db_path: Path,
    *,
    writable: bool,
):
    from storage.entity_identity_store import EntityIdentityStore
    from storage.readonly_identity_store import ReadOnlyIdentityStore
    from storage.signal_store import SignalStore

    store = SignalStore(db_path=db_path, read_only=not writable)
    ro_identity_store = None
    await store.initialize()
    try:
        identity_store = EntityIdentityStore(store)
        ro_identity_store = ReadOnlyIdentityStore(identity_store, db_path=str(db_path))
        await ro_identity_store.initialize()
        yield store, ro_identity_store
    finally:
        if ro_identity_store is not None:
            await ro_identity_store.close()
        await store.close()


def _run_shadow(context: TaskContext, plan: dict[str, Any], *, persist: bool) -> dict[str, Any]:
    try:
        return run_async_blocking(_run_shadow_async(context, plan, persist=persist))
    except Exception as exc:
        raise TaskFailure(str(exc)) from exc


async def _run_shadow_async(
    context: TaskContext,
    plan: dict[str, Any],
    *,
    persist: bool,
) -> dict[str, Any]:
    bindings = _load_shadow_evaluator()
    db_path = _db_path(context)
    config = _build_shadow_run_config(bindings, plan)
    async with _open_shadow_runtime(context, db_path, writable=persist) as (
        store,
        ro_identity_store,
    ):
        result = await bindings.run_shadow_comparison(store, ro_identity_store, config)
        shadow_run_id = None
        if persist:
            shadow_run_id = await bindings.store_shadow_run(store, result)

    payload = _shadow_payload(
        context,
        plan,
        result,
        persist=persist,
        shadow_run_id=shadow_run_id,
    )
    _write_shadow_artifact(context, payload)
    return payload


def _build_shadow_run_config(
    bindings: ShadowEvaluatorBindings,
    plan: dict[str, Any],
) -> Any:
    config = dict(plan.get("shadow_config") or {})
    return bindings.ShadowRunConfig(
        max_signals_per_run=int(config.get("max_signals_per_run", 500)),
        sample_rate=float(config.get("sample_rate", 1.0)),
        timeout_seconds=float(config.get("timeout_seconds", 30.0)),
        max_disagreements_stored=int(config.get("max_disagreements_stored", 1000)),
        min_similarity_threshold=float(config.get("min_similarity_threshold", 0.85)),
        max_suggestions_per_run=int(config.get("max_suggestions_per_run", 100)),
    )


def _shadow_payload(
    context: TaskContext,
    plan: dict[str, Any],
    result: Any,
    *,
    persist: bool,
    shadow_run_id: int | None,
) -> dict[str, Any]:
    data = _result_dict(result)
    shadow_run = {
        "id": shadow_run_id,
        "runId": data.get("run_id"),
        "status": data.get("status"),
        "totalSignals": data.get("total_signals", 0),
        "phase1aGroups": data.get("phase1a_groups", 0),
        "phaseGGroups": data.get("phase_g_groups", 0),
        "agreements": data.get("agreements", 0),
        "disagreements": data.get("disagreements_count", 0),
        "agreementRate": data.get("agreement_rate"),
        "durationMs": data.get("duration_ms", 0.0),
        "inputsHash": data.get("inputs_hash"),
        "truncated": bool(data.get("truncated", False)),
        "errorSummary": data.get("error_summary"),
    }
    return {
        "task": ShadowValidateTask.name,
        "mode": context.mode,
        "dryRun": context.mode == "dry-run",
        "mutationCommitted": persist,
        "artifactCommit": {
            "ledgerOnly": not persist,
            "runtimeState": persist,
            "externalSystems": False,
        },
        "persistence": {
            "persisted": persist,
            "shadowRunId": shadow_run_id,
            "affectedTables": list(SHADOW_TABLES) if persist else [],
            "externalSystems": [],
        },
        "database": {
            "path": str(_db_path(context)),
        },
        "shadowConfig": plan.get("shadow_config", {}),
        "shadowRun": shadow_run,
        "rawResult": data,
    }


def _result_dict(result: Any) -> dict[str, Any]:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return dict(result)
    payload: dict[str, Any] = {}
    for key in (
        "run_id",
        "status",
        "total_signals",
        "phase1a_groups",
        "phase_g_groups",
        "agreements",
        "disagreements_count",
        "agreement_rate",
        "duration_ms",
        "inputs_hash",
        "truncated",
        "error_summary",
    ):
        if hasattr(result, key):
            payload[key] = getattr(result, key)
    return payload


def _write_shadow_artifact(context: TaskContext, payload: dict[str, Any]) -> Path:
    return context.write_json(SHADOW_ARTIFACT, payload)


def _float_value(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
