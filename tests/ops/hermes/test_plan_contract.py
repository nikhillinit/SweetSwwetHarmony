from __future__ import annotations

import re

from integrations.hermes.plan_contract import (
    CURRENT_CONTRACT_VERSION,
    canonical_plan_hash,
    canonical_plan_hash_preimage,
    run_record_contract_version,
)


def test_canonical_plan_hash_is_stable_under_key_order_changes() -> None:
    first = canonical_plan_hash(
        task="restore-db",
        mode="dry-run",
        risk_level="critical",
        input_preimage={"b": 2, "a": 1},
        resource_preimage={"target": {"path": "signals.db", "sha256": "abc"}},
        output_contract_preimage={"artifacts": ["task_plan.json", "run_record.json"]},
    )
    second = canonical_plan_hash(
        task="restore-db",
        mode="dry-run",
        risk_level="critical",
        input_preimage={"a": 1, "b": 2},
        resource_preimage={"target": {"sha256": "abc", "path": "signals.db"}},
        output_contract_preimage={"artifacts": ["task_plan.json", "run_record.json"]},
    )

    assert first == second


def test_canonical_plan_hash_uses_sha256_string_form() -> None:
    value = canonical_plan_hash(
        task="restore-db",
        mode="execute",
        risk_level="critical",
        input_preimage={},
        resource_preimage={},
        output_contract_preimage={},
    )

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", value)


def test_canonical_plan_hash_preimage_includes_domain_and_contract_version() -> None:
    preimage = canonical_plan_hash_preimage(
        task="restore-db",
        mode="plan-only",
        risk_level="critical",
        input_preimage={},
        resource_preimage={},
        output_contract_preimage={},
    )

    assert preimage["domain"] == "hermes.task_plan"
    assert preimage["contractVersion"] == CURRENT_CONTRACT_VERSION


def test_canonical_plan_hash_excludes_volatile_run_fields() -> None:
    base = canonical_plan_hash(
        task="restore-db",
        mode="dry-run",
        risk_level="critical",
        input_preimage={"target": "signals.db", "run_id": "first"},
        resource_preimage={"runDir": "ai-logs/hermes/runs/first"},
        output_contract_preimage={"createdAt": "2026-06-01T00:00:00Z"},
    )
    changed_volatile = canonical_plan_hash(
        task="restore-db",
        mode="dry-run",
        risk_level="critical",
        input_preimage={"target": "signals.db", "run_id": "second"},
        resource_preimage={"runDir": "ai-logs/hermes/runs/second"},
        output_contract_preimage={"createdAt": "2026-06-01T01:00:00Z"},
    )

    assert base == changed_volatile


def test_canonical_plan_hash_distinguishes_preimage_changes() -> None:
    kwargs = {
        "task": "restore-db",
        "mode": "dry-run",
        "risk_level": "critical",
        "input_preimage": {"target": "signals.db"},
        "resource_preimage": {"target": {"sha256": "abc"}},
        "output_contract_preimage": {"artifacts": ["task_plan.json"]},
    }
    baseline = canonical_plan_hash(**kwargs)

    assert canonical_plan_hash(**{**kwargs, "input_preimage": {"target": "other.db"}}) != baseline
    assert canonical_plan_hash(**{**kwargs, "resource_preimage": {"target": {"sha256": "def"}}}) != baseline
    assert canonical_plan_hash(**{**kwargs, "output_contract_preimage": {"artifacts": ["execute.json"]}}) != baseline


def test_historical_run_record_without_contract_version_defaults_to_v1() -> None:
    assert run_record_contract_version({"task": "restore-db"}) == 1
