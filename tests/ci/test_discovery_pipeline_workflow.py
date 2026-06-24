from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "discovery-pipeline.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _step_block(workflow: str, name: str) -> str:
    block = workflow.split(f"      - name: {name}", maxsplit=1)[1]
    next_step = block.find("\n      - name:")
    return block if next_step == -1 else block[:next_step]


def test_daily_pipeline_requires_paired_database_restore() -> None:
    workflow = _workflow()

    assert "Restore persistent database state" in workflow
    assert "restored_db=false" in workflow
    assert "restored_watermark=false" in workflow
    assert "artifact-tmp/signals.db" in workflow
    assert "artifact-tmp/.omx/state/db_watermark.json" in workflow
    assert "Daily Pipeline requires paired restore" in workflow
    assert '[ "$RESTORED_DB" != "true" ]' in workflow
    assert '[ "$RESTORED_WATERMARK" != "true" ]' in workflow
    assert "restore_accepted=true" in workflow
    assert workflow.index("Validate restored database state") < workflow.index(
        "Sync portfolio companies"
    )


def test_daily_pipeline_legacy_recovery_requires_manual_operator_approval() -> None:
    workflow = _workflow()

    assert "recover_legacy_artifact" in workflow
    assert "One-time operator-approved migration" in workflow
    assert "GITHUB_EVENT_NAME" in workflow
    assert 'RECOVER_LEGACY_ARTIFACT" != "true"' in workflow
    assert "manually dispatch with recover_legacy_artifact=true" in workflow
    assert "python run_pipeline.py init-watermark --db-path" in workflow
    assert "legacy_watermark_migrated=true" in workflow
    assert workflow.index("Validating database integrity") < workflow.index(
        "python run_pipeline.py init-watermark --db-path"
    )


def test_daily_pipeline_does_not_silently_bootstrap_or_recreate_state() -> None:
    workflow = _workflow()

    assert "python run_pipeline.py init-watermark" in workflow
    assert 'RECOVER_LEGACY_ARTIFACT" != "true"' in workflow
    assert "Initializing fresh database" not in workflow
    assert "SignalStore('signals.db')" not in workflow
    assert "Database corrupted, reinitializing" not in workflow
    assert "rm -f signals.db signals.db-wal signals.db-shm" not in workflow


def test_daily_pipeline_validates_restored_state_before_running() -> None:
    workflow = _workflow()

    assert 'sqlite3 "$DISCOVERY_DB_PATH" "PRAGMA integrity_check;" | grep -q "^ok$"' in workflow
    assert "Restored signals.db failed integrity check" in workflow
    assert "python -m json.tool .omx/state/db_watermark.json >/dev/null" in workflow


def test_daily_pipeline_artifact_publication_requires_anomaly_success() -> None:
    workflow = _workflow()

    anomaly = _step_block(workflow, "Anomaly check on restored database")
    finalize = _step_block(workflow, "Finalize database")

    assert "id: anomaly-check" in anomaly
    assert "python scripts/db_anomaly.py" in anomaly
    assert "steps.anomaly-check.outcome" in finalize
    assert '"success"' in finalize
    assert "Restore/anomaly gate was not accepted; skipping artifact publication" in finalize
    assert "steps.validate-restore.outputs.restore_accepted" not in finalize

    assert workflow.index("python scripts/db_anomaly.py") < workflow.index(
        "steps.anomaly-check.outcome"
    )


def test_daily_pipeline_operates_database_out_of_tree() -> None:
    """#149: the canonical DB must operate outside the git working tree.

    The pipeline resolves DISCOVERY_DB_PATH to $RUNNER_TEMP and never writes
    signals.db into the checked-out workspace (which the in-tree guard in
    storage/db_paths.py also rejects).
    """
    workflow = _workflow()

    # Canonical path resolved out-of-tree, set once for every later step.
    assert 'echo "DISCOVERY_DB_PATH=$RUNNER_TEMP/signals.db" >> "$GITHUB_ENV"' in workflow
    # Restore lands the DB out-of-tree, not at the workspace root.
    assert 'mv artifact-tmp/signals.db "$DISCOVERY_DB_PATH"' in workflow
    assert "mv artifact-tmp/signals.db signals.db" not in workflow
    # No step pins the in-tree default any more.
    assert "DISCOVERY_DB_PATH: signals.db" not in workflow
    # Finalization stages artifact contents out-of-tree before upload.
    assert 'STAGING="$RUNNER_TEMP/artifact-out"' in workflow
    assert "${{ runner.temp }}/artifact-out/signals.db" in workflow


def test_daily_pipeline_artifact_uploads_are_fail_closed() -> None:
    workflow = _workflow()

    assert 'publish_artifacts=false" >> "$GITHUB_OUTPUT"' in workflow
    assert 'publish_artifacts=true" >> "$GITHUB_OUTPUT"' in workflow
    assert "Restore/anomaly gate was not accepted; skipping artifact publication" in workflow

    upload_latest = workflow.split(
        "      - name: Upload signals database artifact", maxsplit=1
    )[1]
    upload_latest = upload_latest.split("      - name: Upload timestamped", maxsplit=1)[0]
    assert "steps.finalize-db.outputs.publish_artifacts == 'true'" in upload_latest
    assert ".omx/state/db_watermark.json" in upload_latest
    assert "include-hidden-files: true" in upload_latest

    upload_timestamped = workflow.split(
        "      - name: Upload timestamped database artifact", maxsplit=1
    )[1]
    assert "steps.finalize-db.outputs.publish_artifacts == 'true'" in upload_timestamped
    assert ".omx/state/db_watermark.json" in upload_timestamped
    assert "include-hidden-files: true" in upload_timestamped
