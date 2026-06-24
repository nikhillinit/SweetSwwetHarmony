# Phase 5 — Litestream lifecycle mode decision

**Date:** 2026-06-24
**Branch:** `claude/kind-archimedes-pzvqq3`
**Selected mode: B (orchestration out of scope).**

## 0.5.2 capability smoke test (decision input)

```
$ command -v litestream
(absent)
$ litestream version
LITESTREAM NOT INSTALLED on this dev host
```

The plan requires defaulting to **Mode B unless _every_ 0.5.2 command in the
smoke test passes**. Litestream is not installed on the dev host, so the
capability proof cannot pass. Mode B is therefore the only defensible choice and
Mode A (Litestream-managed restore) is **not wired**.

## What Mode B means (implemented)

- `scripts/restore_db.py` performs artifact / local-file restore only and now
  records `litestream_mode="off"` in the restore result and in **every**
  `db_ops_ledger` row (`SUPPORTED_LITESTREAM_MODES = ("off",)`; passing
  `"required"` is rejected with a `RestoreError`).
- A `--litestream-mode` CLI flag (choices `off`, default `off`) makes the mode
  explicit for non-interactive runs.
- `scripts/litestream_ctrl.py` is **quarantined**: constructing `LitestreamCtrl`
  raises `LitestreamUnsupportedError`. The previous 0.5.2 commands
  (`stop`, `replicate` under a 10s subprocess timeout, `generations`-as-reset,
  missing `status`, `reset` being 0.5.7-only) are removed so they cannot be
  invoked as if supported.
- S3/R2 cloud-restore durability is proven independently by
  `.github/workflows/litestream-restore-verify-nightly.yml`.

## Pre-existing on main (verified, not re-implemented)

- The restore-path maintenance lock-timeout defect was **already fixed** on main
  by PR #290 (`baa1f29`): `restore_backup_with_lock_and_ledger` defaults
  `lock_timeout_seconds=MAINTENANCE_LOCK_TIMEOUT_SECONDS` (180s), and the CLI
  `--lock-timeout-seconds` defaults to 180s. Regression tests
  (`test_restore_helper_defaults_to_maintenance_lock_timeout`,
  `..._acquires_lock_with_maintenance_timeout`,
  `test_cli_main_threads_lock_timeout_flag`) are present in
  `tests/scripts/test_restore_db.py`.
- M1A (`scripts/db_anomaly.py`) is already on main.

## Tests

`tests/scripts/test_restore_db.py`, `tests/scripts/test_restore_litestream.py`,
`tests/ci/test_restore_db_cli_contract.py` — 24 passed.
