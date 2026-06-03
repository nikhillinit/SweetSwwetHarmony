---
name: signals-db-checkpoint
description: Create a safe, SHA-256-hashed, ledgered snapshot of the live signals.db before any risky database operation. Use BEFORE restores, schema migrations, git reset --hard, bulk DELETE/UPDATE, or any command that could overwrite or truncate signals.db. Also use when the user asks to "checkpoint the db", "back up signals.db", or "snapshot the database".
---

# signals.db checkpoint

Takes a verified, timestamped snapshot of the live `signals.db` and records it in
the db-ops ledger. This is the cheap insurance that the 2026-05-05 909→4-row
reversion (Issue #149 / Known Issue #10) showed was missing: a truncated backup was
copied onto the live DB with no fresh, hashed snapshot to fall back to.

## When to run

Run this **before** doing anything that writes, overwrites, restores, migrates, or
bulk-mutates `signals.db`:

- restoring from any backup (`scripts/restore_db.py`, manual copy)
- running a schema migration (`storage/migrations/`)
- `git reset --hard`, `git clean -x`, or branch switches that might touch gitignored state
- bulk `DELETE FROM` / `UPDATE` / `VACUUM INTO`
- any operation the **signals.db write-guard** hook flagged with an "ask" prompt

It is non-destructive (read-only on the live DB), so running it "just in case" is
always safe.

## How to run it

Execute the helper via the PowerShell tool from the repo root. Pass a short reason:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".claude\skills\signals-db-checkpoint\checkpoint.ps1" -Reason "<why you are checkpointing>"
```

If `$ARGUMENTS` was provided to this skill, use it as the `-Reason`. Otherwise infer
a concise reason from the current task (e.g. "pre-restore", "before v52 migration").

## What it does

1. Resolves the live DB (`DISCOVERY_DB_PATH`, default `<repo>\signals.db`).
2. Computes SHA-256 + byte size, and the `signals` row count (via `sqlite3` if present).
3. Copies the DB to `backups\signals-checkpoint-<UTC>.db` (the repo's global `*.db`
   `.gitignore` rule keeps it out of version control — same convention as `restore_db`).
4. Re-hashes the copy and verifies it matches the source.
5. Appends one JSON line to `.omx\logs\db_ops_ledger.jsonl` using the same schema as
   `restore_db` (`timestamp`/`pid`/`tool_name`/`db_path`/`action: "checkpoint"`/`status`/
   `details` with both hashes, size, row count, and reason).
6. Prints a summary. Exit code is non-zero only if the copy hash mismatches.

If a non-empty `signals.db-wal` sidecar is present, the snapshot reflects the committed
DB only; the helper warns and recommends checkpointing when the pipeline is idle.

## After running

- Surface the printed summary to the user — especially the **sha256** and **row count**.
- If `verify` reports a MISMATCH, **stop** and investigate (do not proceed with the
  risky operation — the live DB may already be unstable).
- The checkpoint path is what you (or the operator) restore from if the subsequent
  operation goes wrong.

## Notes

- Backups never get committed: the repo's global `*.db` `.gitignore` rule covers
  `backups\signals-checkpoint-*.db`. They are local recovery assets, not repo history.
- This complements, but does not replace, the outstanding **cloud-backup hardening**
  item — all local checkpoints are co-resident with the writer that could corrupt them.
