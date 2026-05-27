---
type: incident
status: active
owner: codex
created_at: 2026-05-27
related_prs: []
related_files:
  - docs/runbooks/sqlite-durability.md
  - utils/db_guard.py
  - scripts/restore_db.py
  - scripts/sqlite_snapshot.py
  - scripts/litestream_restore_verify.py
---
# DB Reversion Control Plane

## Impact

The production SQLite database needs a control plane that catches reversion or truncation before new writes compound the damage. The existing external watermark guard is the first protection; this incident record tracks the additional durability layer for deterministic snapshots and temp restore verification.

## Response

Task 1 adds:

- Deterministic `VACUUM INTO` snapshot artifacts with hashes and row-count manifests.
- A Litestream restore verifier that restores into a temporary DB and validates SQLite invariants.
- A read-only durability check in `utils/db_guard.py` and `run_pipeline.py`.
- PR smoke coverage without production secrets.
- Nightly restore verification through the protected `sqlite-production-backups` GitHub environment.

## Operator Rules

Do not weaken `watermark_missing`; it remains a blocked write state until `python run_pipeline.py init-watermark` is run against a known-good database. Do not restore directly from a replica into production. Restore to a temporary DB first, preserve the verification summary, then use `scripts/restore_db.py` for production restore only after live writers are stopped and `db-restore-approved` is present.

## Status

Active until the durability smoke workflow, nightly restore workflow, and runbook are landed and exercised on `main`.
