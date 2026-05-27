---
type: runbook
status: active
owner: codex
created_at: 2026-05-27
related_prs: []
related_files:
  - .litestream.yml
  - scripts/sqlite_snapshot.py
  - scripts/litestream_restore_verify.py
  - scripts/restore_db.py
  - utils/db_guard.py
  - run_pipeline.py
---
# SQLite Durability

## Context

This runbook covers the control plane for preventing another production SQLite reversion incident. The existing external DB watermark remains the write-side guard: a missing `.omx/state/db_watermark.json` is not auto-created and write commands stay blocked until an operator runs the explicit bootstrap command.

Use this runbook for snapshot creation, temp restore verification, and the final production restore path. Litestream replica data and daily/monthly snapshot artifacts are separate object prefixes:

```text
s3://$SQLITE_BACKUP_BUCKET/sweetswwetharmony/litestream/signals.db/
s3://$SQLITE_BACKUP_BUCKET/sweetswwetharmony/snapshots/daily/
s3://$SQLITE_BACKUP_BUCKET/sweetswwetharmony/snapshots/monthly/
```

Do not apply generic lifecycle pruning to Litestream WAL or LTX paths. Retention and deletion policy must be explicit for this database.

## Watermark Bootstrap

Run this only after confirming the current production `signals.db` is the intended baseline:

```powershell
python run_pipeline.py init-watermark --db-path signals.db
```

The command records signal count, schema version, and timestamp outside the DB. It does not repair the DB and it must not be used to bless a suspected reverted database.

## Snapshot

Create a deterministic local snapshot:

```powershell
python -m scripts.sqlite_snapshot --db-path signals.db --out-dir artifacts/sqlite-snapshots/daily --manifest-out artifacts/sqlite-snapshots/latest.manifest.json
```

The script uses `VACUUM INTO`, writes stable gzip bytes, and emits a manifest with source hash, compressed hash, row counts, schema version, and creation time.

## Temp Restore Verification

Restore only into a temporary path, then let SQLite checks prove the result:

```powershell
python -m scripts.litestream_restore_verify --replica-url "s3://$env:SQLITE_BACKUP_BUCKET/sweetswwetharmony/litestream/signals.db/" --restore-path "$env:TEMP\restored-signals.db" --min-signals 1 --expected-schema-version 53 --summary-out artifacts/litestream-restore-verify/summary.json
```

Do not use Litestream's verify subcommand for this control. The accepted proof is restore-to-temp plus `PRAGMA integrity_check`, `schema_migrations`, and signal lower-bound checks.

## Production Restore

Production restore still goes through the existing guarded script:

```powershell
python scripts/restore_db.py path\to\verified-backup.db --db-path signals.db
```

Before restoring into production:

1. Confirm no API server, scheduled task, keepalive job, collector, or local agent is writing to `signals.db`.
2. Confirm the PR or incident has the `db-restore-approved` label.
3. Preserve the restore verification summary artifact.
4. Let `scripts/restore_db.py` create the pre-restore backup and run post-restore integrity checks.
5. Re-run `python run_pipeline.py sqlite-durability-check --db-path signals.db --require-watermark --min-signals <expected-floor>`.

## Rollback

Disable the durability workflows and Litestream service first. Keep existing replica and snapshot objects. Do not delete backup objects as part of rollback.
