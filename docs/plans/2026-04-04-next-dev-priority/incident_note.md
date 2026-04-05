# Incident Note - signals.db Truncation and Restore

Date: 2026-04-04
Execution mode: Ralph

## Summary

- Live `signals.db` was truncated to `4` signals with `0` audit events, `0` suppression entries, and `0` thesis rows.
- Backup `signals.db.pre-step4b-promotion-20260404` was intact with `612` signals, `20` audit events, `588` suppression entries, `2593` thesis rows, and `48` pipeline runs.
- Both DB files passed `PRAGMA integrity_check`.
- Live `signals.db` had active `signals.db-wal` and `signals.db-shm` sidecars; the backup did not.
- The approved branch was staged restore because WAL/SHM ambiguity was real and stale `run_pipeline.py health --json` processes were still holding the sidecars open.

## Repo-Local Suspect Paths

- [scripts/e2e_batch_check.py](/C:/dev/Harmonic/scripts/e2e_batch_check.py#L8) opens `signals.db` directly.
- [scripts/e2e_batch_approve.py](/C:/dev/Harmonic/scripts/e2e_batch_approve.py#L11) opens `signals.db` directly and enables WAL mode.
- [scripts/export_labeling_review.py](/C:/dev/Harmonic/scripts/export_labeling_review.py#L117) hard-codes `C:/dev/Harmonic/signals.db`.
- [scripts/run_backfill.py](/C:/dev/Harmonic/scripts/run_backfill.py#L11) opens `signals.db` directly.
- [run_pipeline.py](/C:/dev/Harmonic/run_pipeline.py) defaults many CLI paths to `signals.db` / `DISCOVERY_DB_PATH`.
- [storage/signal_store.py](/C:/dev/Harmonic/storage/signal_store.py#L2026) forces WAL mode on startup.

## Findings

1. Unsafe-default `signals.db` writers exist in the repo, but no single culprit is proven from repo-local evidence alone.
2. Live WAL/SHM sidecars were materially relevant:
   - they blocked clean cutover,
   - they caused a malformed mixed-state DB after the main file was replaced while the stale sidecars were still present,
   - they were tied to two stale `run_pipeline.py health --json` processes started at the same time as the WAL/SHM timestamps.
3. [scripts/restore_db.py](/C:/dev/Harmonic/scripts/restore_db.py#L137) copies the main DB file only and does not explicitly handle WAL/SHM sidecars.

## Residual Uncertainty

- I cannot prove which process or script originally truncated the DB without external process/file audit.
- Repo-local evidence cannot exclude manual file replacement or external SQLite tooling outside the repo.
- The suspect list is evidentiary, not causal.

## Restore Branch Decision

Staged restore was justified because:

1. live `signals.db-wal` / `signals.db-shm` sidecars existed while the backup had none,
2. stale Python health processes were actively holding those sidecars open,
3. the existing restore helper only copies the main DB file.

## Restore Execution Notes

1. Restored the backup into `signals.db.restore-stage-20260404T195300Z`.
2. Verified staged counts matched the backup snapshot.
3. Copied the broken live DB plus sidecars into `artifacts/incident-db-restore-20260404T195300Z/`.
4. Replaced the live `signals.db` main file with the staged file.
5. Initial sidecar removal failed because two stale `run_pipeline.py health --json` processes still held the files open.
6. Stopped those stale health processes and removed the stale sidecars.
7. Re-verified live `signals.db` successfully read back the expected `612`-signal dataset.
