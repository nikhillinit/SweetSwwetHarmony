# DB Ops Policy

## Purpose

Define what the repo's DB safety surfaces can prove, what they cannot prove, and the minimum operator rules for manual or external SQLite work.

ADR-043 accepts this policy as the Phase 5 Tranche-1 DB-tooling discipline: repo-supported DB mutators use explicit lock ownership, ledger rows, typed partial-evidence errors, and init-time `PRAGMA data_version` evidence.

## Repo-Local Guarantees

The repo can enforce or verify:

1. shared DB-path handling for targeted scripts
2. targeted script confirmation/exclusivity rules for destructive tools
3. restore refusal or safe handling when `-wal` / `-shm` sidecars are present
4. targeted CI/test guardrails against hard-coded production DB access patterns

The repo cannot fully prove:

1. that no external/manual SQLite tooling touched the DB
2. that no operator copied files outside the repo's scripted paths
3. universal attribution for all DB mutations

## Tranche-2 Repo-Local Evidence

When enabled for destructive repo-local tools, the DB-ops ledger is:

1. a repo-local JSONL evidence trail outside `signals.db`
2. useful for inclusion/exclusion of repo-local tool activity
3. not proof that external/manual SQLite operations did or did not happen

Hardened DB mutators also record a `PRAGMA data_version` preflight read at script init. This is a cheap SQLite state marker for the connection's view of the database before the tool starts its lock/transaction path. It is forensic evidence, not a substitute for the repo-local lock, explicit transactions, or the ledger.

## Manual / External SQLite Rules

When using external or manual DB operations:

1. use an explicit maintenance window
2. stop writers before restore or file replacement
3. checkpoint or clear WAL/SHM sidecars before treating a main DB file as authoritative
4. prefer restore into a fresh target path when writer ambiguity remains unresolved
5. document the operation outside the DB if the repo-local tooling is bypassed

`signals.db` must be hosted on a local filesystem. Do not place the live SQLite database on NFS, SMB, CIFS, cloud-drive sync folders, or a remote mount; SQLite WAL locking assumptions can break on network filesystems.

If you intentionally bypass repo-local tooling, record the action in the repo-local ledger with:

```text
python scripts/db_ops_note.py --db-path <db> --tool-name <tool> --action <action> --note "<what happened>"
```

## Prioritized Script Class

Tranche-1 hardening applies to:

- `scripts/backup_db.py`
- `scripts/restore_db.py`
- `scripts/db_maintenance.py`
- `scripts/e2e_batch_approve.py`
- `scripts/run_backfill.py`

Read-only scripts from the original priority list are exempt from lock/ledger by design:

- `scripts/e2e_batch_check.py`
- `scripts/export_labeling_review.py`

Meta-ledger tooling is exempt from `DBToolLock` because it records manual or external operations and does not mutate the target DB:

- `scripts/db_ops_note.py`

Scratch-only decision helpers are not Tier-E live mutators, even if they mutate temp copies:

- `scripts/spc_override_decision.py`

This does not imply a repo-wide ban on every `"signals.db"` mention in docs, help text, or lower-risk parser defaults.

## Tranche-2 / F.3 Live-Mutator Slices

F.3 hardening proceeded by live-data blast radius and is complete as of PR #162:

1. F.3.0: `scripts/cleanup_publisher_keys.py` (complete)
2. F.3.1 PR1: `run_pipeline.py backfill-evidence-family` and `run_pipeline.py rehydrate-canonical-keys-v2` wrappers (complete via PR #157)
3. F.3.1 PR2: `scripts/backfill_company_extraction.py`, `scripts/backfill_evidence_keys.py`, `scripts/backfill_thesis_provenance.py` (complete via PR #158)
4. F.3.2: `scripts/backfill_company_files.py`, `scripts/build_case_law_corpus.py`, `scripts/build_exemplar_library.py` (complete via PR #159)
5. F.3.3: `scripts/seed_tier_c_domains.py` (complete via PR #160)
6. F.3.4A: `scripts/gc_thin_files.py` (complete via PR #161)
7. F.3.4B: `scripts/backfill_hunter_company_names.py` (complete via PR #162)

Each mutating script in these slices should default to dry-run or otherwise require explicit commit/apply confirmation, acquire `DBToolLock` in commit mode, write success and error rows to `db_ops_ledger.jsonl`, preserve structured partial evidence on failures, and include focused subprocess tests for dry-run, success, lock-blocked, and rollback/error paths.

The standard init marker in reports and ledger details is `preflight_data_version`, captured from `PRAGMA data_version` before the lock/transaction path. Do not use alternate names such as `data_version_at_init` in new DB-tool reports.

For F.3.2, `build_case_law_corpus.py` and `build_exemplar_library.py` are deliberately non-mutating on bare invocation; use `--commit` to write. `build_case_law_corpus.py` writes SQLite rows plus vectorizer artifacts, so its reports and ledger rows record staged artifact paths and cleanup evidence rather than claiming DB-plus-filesystem atomicity.

For F.3.3, `scripts/seed_job_posting_domains.py` is a read-only selector/export helper. It reads `signals` or `company_files`, emits `JOB_POSTING_DOMAINS` or list/csv output, and is intentionally lock-free and ledger-silent while it has no write path. Future write-capable job-posting-domain seeding should be planned as a new mutator contract.

For F.3.4, both scripts use `--apply` rather than `--commit` for their mutating path. `gc_thin_files.py` records explicit partial-commit evidence when delete work succeeds but the later audit-log insert fails. `backfill_hunter_company_names.py` records per-table attempted update counts for mid-transaction rollback evidence.

## Assertion Contract Variants

DB-tool tests should use the smallest contract that fits the script's mutation surface:

1. Standard mutator: dry-run or commit/apply success, lock-blocked, preflight error, rollback/error evidence, success report envelope, error report envelope.
2. Mixed read/write tool: prove the non-mutating path is ledger-silent and does not require a lock; use a read-only connection when practical.
3. Destructive cleanup tool: add rollback delete failure and audit/report failure evidence so the ledger never overclaims global atomicity.
4. Multi-table identity rewrite: include per-table attempted counts in `DBToolError.partial_evidence` for rollback correlation.

## Phase 5.2 Cloud Durability Direction

Cloud durability for `signals.db` should use Litestream-style WAL streaming and restore workflows, not remote-mounted SQLite. ADR-043 accepts this direction, but Phase 5.2 restore objectives are provisional until backed by host sizing, storage throughput, and restore-drill benchmarks. Do not combine a small-runner assumption with a 100GB restore-time target unless the chosen host and storage tier have measured support for it.

Phase 5.2 archival outputs should be Arrow- or Parquet-readable so future analysis can consume cold artifacts without binding to the live SQLite file format. Off-host ledger retention should be append-only where possible.

Runner scheduling and liveness governance is deliberately outside this DB durability policy. The runner-liveness ADR and runbook own scheduler registration, monitor delivery, alert recipients, host-mode gates, and re-enable criteria. Phase 5.2 consumes only DB durability evidence: local host storage characteristics, WAL/restore behavior, archive readability, ledger retention, sidecar handling, and restore-drill results.

## Support Tooling Quarantine

The following scripts are currently local-only and are not repo-supported until a separate promotion plan lands:

- `scripts/e2e_batch_verify.py`
- `check_keys.py`
- `view_signals.py`
- `view_signals_detail.py`
- `view_unlabeled.py`

`scripts/e2e_batch_verify.py` is publish-capable and high-risk. Do not execute it against `signals.db` under repo-guided workflows.

Promotion from quarantine requires all of the following:

1. explicit DB-path handling
2. dry-run or safe-default behavior
3. tests
4. docs
5. guardrails

## Restore Sidecar Contract

When `-wal` / `-shm` sidecars are present for the target DB, `scripts/restore_db.py` must either:

1. checkpoint/clear/swap them safely after exclusivity passes, or
2. refuse with an explicit operator-facing error

Main-file-only overwrite while sidecars remain live is not an acceptable restore path.
