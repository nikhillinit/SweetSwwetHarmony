from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "discovery-pipeline.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_daily_pipeline_requires_paired_database_restore() -> None:
    workflow = _workflow()

    assert "Restore persistent database state" in workflow
    assert "restored_db=false" in workflow
    assert "restored_watermark=false" in workflow
    assert "artifact-tmp/signals.db" in workflow
    assert "artifact-tmp/.omx/state/db_watermark.json" in workflow
    assert "Daily Pipeline requires paired restore" in workflow
    assert '[ "$RESTORED_DB" != "true" ] || [ "$RESTORED_WATERMARK" != "true" ]' in workflow
    assert "restore_accepted=true" in workflow
    assert workflow.index("Validate restored database state") < workflow.index(
        "Sync portfolio companies"
    )


def test_daily_pipeline_does_not_bootstrap_or_recreate_state() -> None:
    workflow = _workflow()

    assert "python run_pipeline.py init-watermark" not in workflow
    assert "Initializing fresh database" not in workflow
    assert "SignalStore('signals.db')" not in workflow
    assert "Database corrupted, reinitializing" not in workflow
    assert "rm -f signals.db signals.db-wal signals.db-shm" not in workflow


def test_daily_pipeline_validates_restored_state_before_running() -> None:
    workflow = _workflow()

    assert 'sqlite3 signals.db "PRAGMA integrity_check;" | grep -q "^ok$"' in workflow
    assert "Restored signals.db failed integrity check" in workflow
    assert "python -m json.tool .omx/state/db_watermark.json >/dev/null" in workflow


def test_daily_pipeline_artifact_uploads_are_fail_closed() -> None:
    workflow = _workflow()

    assert 'publish_artifacts=false" >> "$GITHUB_OUTPUT"' in workflow
    assert 'publish_artifacts=true" >> "$GITHUB_OUTPUT"' in workflow
    assert "Restore gate was not accepted; skipping artifact publication" in workflow

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
