# DB Ops Policy

## Purpose

Define what the repo's DB safety surfaces can prove, what they cannot prove, and the minimum operator rules for manual or external SQLite work.

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

## Manual / External SQLite Rules

When using external or manual DB operations:

1. use an explicit maintenance window
2. stop writers before restore or file replacement
3. checkpoint or clear WAL/SHM sidecars before treating a main DB file as authoritative
4. prefer restore into a fresh target path when writer ambiguity remains unresolved
5. document the operation outside the DB if the repo-local tooling is bypassed

If you intentionally bypass repo-local tooling, record the action in the repo-local ledger with:

```text
python scripts/db_ops_note.py --db-path <db> --tool-name <tool> --action <action> --note "<what happened>"
```

## Prioritized Script Class

Tranche-1 hardening applies to:

- `scripts/e2e_batch_check.py`
- `scripts/e2e_batch_approve.py`
- `scripts/export_labeling_review.py`
- `scripts/run_backfill.py`
- `scripts/restore_db.py`

This does not imply a repo-wide ban on every `"signals.db"` mention in docs, help text, or lower-risk parser defaults.

## Restore Sidecar Contract

When `-wal` / `-shm` sidecars are present for the target DB, `scripts/restore_db.py` must either:

1. checkpoint/clear/swap them safely after exclusivity passes, or
2. refuse with an explicit operator-facing error

Main-file-only overwrite while sidecars remain live is not an acceptable restore path.
