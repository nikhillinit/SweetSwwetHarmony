"""Contract test for the Dry-Run Immutability Canary workflow.

Locks the shape that proves ``process`` dry-runs are read-only on PRs touching
the pipeline/storage surface: the in-tree-DB allowance and staging-only delivery
are scoped to the canary job (never global), the read-only baseline lane runs,
and snapshot artifacts upload only on failure.

Raw-text assertions (YAML-1.1 loaders mis-parse the ``on`` key).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "process-dry-run-canary.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists() -> None:
    assert WORKFLOW.exists()


def test_protects_pipeline_and_storage_paths_on_pull_request() -> None:
    wf = _workflow()
    assert "name: Dry-Run Immutability Canary" in wf
    assert "pull_request:" in wf
    assert "paths:" in wf
    assert "workflows/pipeline.py" in wf
    assert "workflows/run_manager.py" in wf
    assert "storage/**" in wf


def test_in_tree_db_and_staging_only_scoped_to_canary_job() -> None:
    wf = _workflow()
    assert 'HARMONIC_ALLOW_IN_TREE_DB: "true"' in wf
    assert "DELIVERY_MODE: staging_only" in wf
    # Scoped under the job, not a workflow-level env block.
    assert wf.index("jobs:") < wf.index("HARMONIC_ALLOW_IN_TREE_DB")
    assert wf.index("jobs:") < wf.index("DELIVERY_MODE: staging_only")


def test_runs_dry_run_readonly_baseline_lane() -> None:
    wf = _workflow()
    assert "tests/integration/test_process_dry_run_readonly.py" in wf
    assert '-k "baseline"' in wf


def test_uploads_snapshots_only_on_failure() -> None:
    wf = _workflow()
    assert "dry-run-canary-snapshots" in wf
    upload = wf.split("Upload snapshot artifacts on failure", maxsplit=1)[1]
    assert "if: failure()" in upload
    assert "if: always()" not in upload
