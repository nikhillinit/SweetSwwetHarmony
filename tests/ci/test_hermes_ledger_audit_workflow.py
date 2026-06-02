from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "hermes-ledger-audit.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_hermes_ledger_audit_workflow_has_ci_manual_and_nightly_triggers() -> None:
    workflow = _workflow()

    assert "name: Hermes Ledger Audit" in workflow
    assert "pull_request:" in workflow
    assert "branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert 'cron: "23 11 * * *"' in workflow


def test_hermes_ledger_audit_workflow_runs_read_only_local_audit() -> None:
    workflow = _workflow()

    assert "Initialize local Hermes ledger scaffold" in workflow
    assert "mkdir -p ai-logs/hermes/runs" in workflow
    assert ": > ai-logs/hermes/index.jsonl" in workflow
    assert "Run Hermes ledger audit" in workflow
    assert "set -o pipefail" in workflow
    assert "python -m ops.cli hermes task ledger-audit" in workflow
    assert "--dry-run" in workflow
    assert "--json" in workflow
    assert "--check all" in workflow
    assert "--finding-severity-threshold low" in workflow
    assert "task-result.json" in workflow
    assert workflow.index("Initialize local Hermes ledger scaffold") < workflow.index(
        "Run Hermes ledger audit"
    )


def test_hermes_ledger_audit_workflow_uploads_operator_reports() -> None:
    workflow = _workflow()

    assert "actions/upload-artifact@v4" in workflow
    assert "hermes-ledger-audit-report" in workflow
    assert "Collect Hermes ledger audit reports" in workflow
    collect_step = workflow.split("      - name: Collect Hermes ledger audit reports", maxsplit=1)[1]
    collect_step = collect_step.split("      - name: Upload Hermes ledger audit report", maxsplit=1)[0]
    assert "if: always()" in collect_step
    assert "ledger_audit_report.json" in workflow
    assert "ledger_audit_report.md" in workflow
    assert "task_plan.json" in workflow
    assert "run_record.json" in workflow
    assert "repair_prompt.md" in workflow


def test_hermes_ledger_audit_workflow_avoids_live_mutation_surfaces() -> None:
    workflow = _workflow().lower()

    assert "signals.db" not in workflow
    assert "signals.db.canary" not in workflow
    assert "state/collectors.json" not in workflow
    assert "restore-db" not in workflow
    assert "run_pipeline.py" not in workflow
    assert "notion" not in workflow
