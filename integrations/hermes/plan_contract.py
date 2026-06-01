"""Canonical Hermes task-plan contract helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

CURRENT_CONTRACT_VERSION = 2
PLAN_HASH_DOMAIN = "hermes.task_plan"

_VOLATILE_KEYS = {
    "createdat",
    "duration",
    "durations",
    "error",
    "exception",
    "generatedat",
    "hostname",
    "ledgerappendposition",
    "pid",
    "planhash",
    "processid",
    "repairprompt",
    "rundir",
    "runid",
    "startedat",
    "status",
    "temporarypath",
    "tmppath",
    "updatedat",
}


def canonical_plan_hash_preimage(
    *,
    task: str,
    mode: str,
    risk_level: str,
    input_preimage: dict[str, Any] | None,
    resource_preimage: dict[str, Any] | None,
    output_contract_preimage: dict[str, Any] | None,
    contract_version: int = CURRENT_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Build the typed, sanitized preimage used for Hermes task-plan hashes."""

    return {
        "domain": PLAN_HASH_DOMAIN,
        "contractVersion": contract_version,
        "task": task,
        "mode": mode,
        "riskLevel": risk_level,
        "inputPreimage": strip_volatile_plan_fields(input_preimage or {}),
        "resourcePreimage": strip_volatile_plan_fields(resource_preimage or {}),
        "outputContractPreimage": strip_volatile_plan_fields(
            output_contract_preimage or {}
        ),
    }


def canonical_plan_hash(
    *,
    task: str,
    mode: str,
    risk_level: str,
    input_preimage: dict[str, Any] | None,
    resource_preimage: dict[str, Any] | None,
    output_contract_preimage: dict[str, Any] | None,
    contract_version: int = CURRENT_CONTRACT_VERSION,
) -> str:
    """Return the canonical `sha256:<hex>` digest for a Hermes task plan."""

    preimage = canonical_plan_hash_preimage(
        task=task,
        mode=mode,
        risk_level=risk_level,
        input_preimage=input_preimage,
        resource_preimage=resource_preimage,
        output_contract_preimage=output_contract_preimage,
        contract_version=contract_version,
    )
    digest = hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()
    return f"sha256:{digest}"


def canonical_plan_hash_from_plan(plan: dict[str, Any]) -> str:
    """Hash a concrete task plan while ignoring volatile/self-referential fields."""

    stable_plan = _stable_plan_payload(plan)
    return canonical_plan_hash(
        task=str(plan.get("task") or ""),
        mode=str(plan.get("mode") or ""),
        risk_level=str(plan.get("risk_level") or plan.get("riskLevel") or ""),
        input_preimage=stable_plan,
        resource_preimage=_resource_preimage(stable_plan),
        output_contract_preimage=_output_contract_preimage(stable_plan),
        contract_version=_contract_version_from_plan(plan),
    )


def attach_plan_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a task plan carrying the current contract version and plan hash."""

    contracted = dict(plan)
    contracted.pop("contract_version", None)
    contracted["contractVersion"] = _contract_version_from_plan(contracted)
    contracted["planHash"] = canonical_plan_hash_from_plan(contracted)
    return contracted


def run_record_contract_version(record: dict[str, Any]) -> int:
    """Return the record contract version, defaulting historical records to v1."""

    value = record.get("contract_version")
    if value is None:
        return 1
    return int(value)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def strip_volatile_plan_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): strip_volatile_plan_fields(item)
            for key, item in value.items()
            if _normalized_key(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [strip_volatile_plan_fields(item) for item in value]
    if isinstance(value, tuple):
        return [strip_volatile_plan_fields(item) for item in value]
    return value


def _stable_plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): strip_volatile_plan_fields(value)
        for key, value in plan.items()
        if _normalized_key(key) not in _VOLATILE_KEYS
        and key not in {"contractVersion", "contract_version"}
    }


def _resource_preimage(plan: dict[str, Any]) -> dict[str, Any]:
    resource_keys = (
        "backup",
        "candidates",
        "current_config",
        "database",
        "database_reads",
        "external_reads",
        "lock_scope",
        "mutation",
        "proposed_config",
        "sidecars",
        "target",
    )
    return {key: plan[key] for key in resource_keys if key in plan}


def _output_contract_preimage(plan: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(plan.get("artifacts") or {})
    artifacts.setdefault("task_plan", "task_plan.json")
    artifacts.setdefault("run_record", "run_record.json")
    return {"artifacts": artifacts}


def _contract_version_from_plan(plan: dict[str, Any]) -> int:
    value = plan.get("contractVersion", CURRENT_CONTRACT_VERSION)
    return int(value)


def _normalized_key(key: Any) -> str:
    return str(key).replace("_", "").replace("-", "").lower()
