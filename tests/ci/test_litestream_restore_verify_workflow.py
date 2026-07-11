"""Contract test for the Litestream Restore Verify Nightly workflow.

Locks the shape that proves S3/R2 cloud-restore durability (the Mode B
durability proof for restore_db.py): it runs nightly + on demand under the
backup environment, hard-checks the backup settings before restoring, resolves
the live schema version, restores the replica with that expected version, and
always uploads the restore summary.

Raw-text assertions (YAML-1.1 loaders mis-parse the ``on`` key).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "litestream-restore-verify-nightly.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists() -> None:
    assert WORKFLOW.exists()


def test_runs_nightly_and_on_demand() -> None:
    wf = _workflow()
    assert "name: Litestream Restore Verify Nightly" in wf
    assert "schedule:" in wf
    assert 'cron: "37 10 * * *"' in wf
    assert "workflow_dispatch:" in wf


def test_runs_under_backup_environment() -> None:
    wf = _workflow()
    assert "environment: sqlite-production-backups" in wf


def test_required_backup_settings_checked_before_restore() -> None:
    wf = _workflow()
    assert 'test -n "$SQLITE_BACKUP_BUCKET"' in wf
    assert 'test -n "$AWS_ACCESS_KEY_ID"' in wf
    assert 'test -n "$AWS_SECRET_ACCESS_KEY"' in wf
    # the settings guard must precede the restore step
    assert wf.index("Verify required backup settings") < wf.index(
        "Restore replica into temp DB and verify"
    )


def test_resolves_current_schema_version_from_signal_store() -> None:
    wf = _workflow()
    assert "storage/signal_store.py" in wf
    assert "CURRENT_SCHEMA_VERSION" in wf


def test_invokes_restore_verify_with_expected_schema_version() -> None:
    wf = _workflow()
    assert "python -m scripts.litestream_restore_verify" in wf
    assert '--expected-schema-version "$CURRENT_SCHEMA_VERSION"' in wf
    assert "--min-signals" in wf


def test_uploads_summary_artifact_always() -> None:
    wf = _workflow()
    assert "actions/upload-artifact@v4" in wf
    assert "litestream-restore-verify-summary" in wf
    upload = wf.split("Upload restore summary", maxsplit=1)[1]
    assert "if: always()" in upload


# ---------------------------------------------------------------------------
# Q2+Q3 adjudicated changes (2026-07 operator queue)
# ---------------------------------------------------------------------------


def test_min_signals_has_no_inline_fallback() -> None:
    """6A: an unset SQLITE_RESTORE_MIN_SIGNALS must fail closed, not
    silently degrade the row-count gate to 1."""
    wf = _workflow()
    assert "SQLITE_RESTORE_MIN_SIGNALS: ${{ vars.SQLITE_RESTORE_MIN_SIGNALS }}" in wf
    assert "|| '1'" not in wf


def test_required_settings_check_min_signals_non_empty() -> None:
    """6A: the required-settings guard covers the row-count threshold too."""
    wf = _workflow()
    settings = wf.split("Verify required backup settings", maxsplit=1)[1]
    settings = settings[: settings.find("\n      - name:")]
    assert 'test -n "$SQLITE_BACKUP_BUCKET"' in settings
    assert 'test -n "$SQLITE_RESTORE_MIN_SIGNALS"' in settings


def test_notifier_job_uses_failure_issue_composite_action() -> None:
    """2A: failures open/update ONE tracking issue via the shared composite
    action; issues: write is the 403 regression guard."""
    wf = _workflow()
    notify = wf.split("\n  notify-on-failure:", maxsplit=1)[1]

    assert "needs: restore-verify" in notify
    assert "if: failure()" in notify
    assert "permissions:" in notify
    assert "contents: read" in notify
    assert "issues: write" in notify
    # local composite actions require a checkout first
    assert "actions/checkout@v4" in notify
    assert "uses: ./.github/actions/failure-issue" in notify
    assert "label: litestream-verify-failure" in notify
