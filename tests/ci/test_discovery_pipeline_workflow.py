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


def _job_block(workflow: str, job: str) -> str:
    block = workflow.split(f"\n  {job}:", maxsplit=1)[1]
    for other in ("\n  run-pipeline:", "\n  notify-on-failure:"):
        cut = block.find(other)
        if cut != -1:
            block = block[:cut]
    return block


def _dispatch_inputs_block(workflow: str) -> str:
    return workflow.split("workflow_dispatch:", maxsplit=1)[1].split(
        "\nconcurrency:", maxsplit=1
    )[0]


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


# ---------------------------------------------------------------------------
# Q2+Q3 adjudicated changes (2026-07 operator queue)
# ---------------------------------------------------------------------------


def test_run_pipeline_job_runs_under_backup_environment() -> None:
    """T2-A: backup secrets are provisioned environment-scoped."""
    job = _job_block(_workflow(), "run-pipeline")
    assert "environment: sqlite-production-backups" in job


def test_signals_db_latest_retention_is_90_days() -> None:
    """1A: the recovery artifact must outlive a month of missed runs."""
    workflow = _workflow()

    upload_latest = workflow.split(
        "      - name: Upload signals database artifact", maxsplit=1
    )[1]
    upload_latest = upload_latest.split("      - name: Upload timestamped", maxsplit=1)[0]
    assert "retention-days: 90" in upload_latest
    assert "retention-days: 30" not in upload_latest

    # timestamped debugging artifact keeps its short retention
    upload_timestamped = _step_block(workflow, "Upload timestamped database artifact")
    assert "retention-days: 7" in upload_timestamped


def test_bootstrap_from_replica_is_a_boolean_dispatch_input() -> None:
    """3A: bootstrap-from-replica is manual-dispatch only, default off."""
    inputs = _dispatch_inputs_block(_workflow())
    assert "bootstrap_from_replica:" in inputs
    # existing legacy-artifact recovery input is kept as-is
    assert "recover_legacy_artifact:" in inputs

    bootstrap = inputs.split("bootstrap_from_replica:", maxsplit=1)[1]
    assert "type: boolean" in bootstrap
    assert "default: false" in bootstrap


def test_no_artifact_upload_dispatch_input_added() -> None:
    """3A: bootstrap goes through the replica, never an operator-uploaded artifact."""
    inputs = _dispatch_inputs_block(_workflow())
    for forbidden in ("upload_artifact", "artifact_upload", "seed_artifact", "upload_db"):
        assert forbidden not in inputs


def test_bootstrap_step_is_gated_and_fails_closed() -> None:
    """3A: replica bootstrap only on explicit dispatch, only when the artifact
    restore produced no DB, and fail closed on missing bucket / failed restore."""
    workflow = _workflow()
    step = _step_block(workflow, "Bootstrap database from Litestream replica")

    # gating: manual dispatch + input + artifact restore produced no DB
    assert "github.event_name == 'workflow_dispatch'" in step
    assert "github.event.inputs.bootstrap_from_replica == 'true'" in step
    assert "steps.restore-state.outputs.restored_db != 'true'" in step

    # fail closed on unset bucket, before any restore attempt
    assert '-z "$SQLITE_BACKUP_BUCKET"' in step
    assert "exit 1" in step

    # restores from the same replica URL the replicate step publishes to
    assert 's3://${SQLITE_BACKUP_BUCKET}/sweetswwetharmony/litestream/signals.db/' in step
    assert 'litestream restore' in step
    assert '"$DISCOVERY_DB_PATH"' in step

    # integrity is validated before the watermark is initialized
    assert 'sqlite3 "$DISCOVERY_DB_PATH" "PRAGMA integrity_check;" | grep -q "^ok$"' in step
    assert 'python run_pipeline.py init-watermark --db-path "$DISCOVERY_DB_PATH"' in step
    assert step.index("PRAGMA integrity_check") < step.index("init-watermark")


def test_bootstrap_feeds_existing_validation_and_anomaly_gates() -> None:
    """3A: the bootstrapped DB still goes through the unchanged gates."""
    workflow = _workflow()

    assert workflow.index("Bootstrap database from Litestream replica") < workflow.index(
        "Validate restored database state"
    )
    assert workflow.index("Validate restored database state") < workflow.index(
        "Anomaly check on restored database"
    )

    validate = _step_block(workflow, "Validate restored database state")
    assert "steps.bootstrap-replica.outputs.bootstrapped" in validate
    # scheduled-run fail-closed paths stay intact
    assert '[ "$RESTORED_DB" != "true" ]' in validate
    assert '[ "$RESTORED_WATERMARK" != "true" ]' in validate
    assert "Daily Pipeline requires paired restore" in validate


def test_notify_job_uses_failure_issue_composite_action() -> None:
    """2A: notifier adopts the shared composite action; 403 regression guard."""
    notify = _job_block(_workflow(), "notify-on-failure")

    assert "permissions:" in notify
    assert "contents: read" in notify
    assert "issues: write" in notify
    # local composite actions require a checkout first
    assert "actions/checkout@v4" in notify
    assert "uses: ./.github/actions/failure-issue" in notify
    assert "label: daily-pipeline-failure" in notify

    # existing Slack curl kept as best-effort secondary step
    assert "SLACK_WEBHOOK_URL" in notify
    assert notify.index("./.github/actions/failure-issue") < notify.index(
        "SLACK_WEBHOOK_URL"
    )
