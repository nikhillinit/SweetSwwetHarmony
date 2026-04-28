# DB Guard Operator Runbook

**Status:** canonical (Phase 2 hotfix Day 2.5)
**Owner:** Phase 2 instrumentation track
**Scope:** `utils/db_guard.py` watermark contract and the operator commands that interact with it.

## What the guard protects against

After the 2026-04-04 incident — where `signals.db` truncated to a fraction of its
expected size and downstream writes proceeded against the truncated DB — we
persist a last-known-good signal count *outside* `signals.db` at
`.omx/state/db_watermark.json`. `utils.db_guard.check_db_health` compares the
live count against the watermark and returns `catastrophic_drop_detected` when
the DB has dropped below 50% of its watermark baseline.

## Strict explicit-init contract (Day 2.5)

The guard **does not auto-create** the watermark. A missing watermark is a real
operational state, not a soft-init opportunity:

| Command type | Watermark state           | Guard outcome                                       |
|--------------|---------------------------|-----------------------------------------------------|
| `read`       | missing                   | allowed; emits `watermark_missing` warning          |
| `read`       | catastrophic drop         | allowed; emits warning                              |
| `write`      | missing                   | **blocked**; operator must run `init-watermark`     |
| `write`      | catastrophic drop         | blocked unless `--recovery-override` is passed      |
| `write`      | healthy                   | allowed                                             |

The guard never mutates `.omx/state/db_watermark.json`. Only the
`init-watermark` CLI does.

## Canonical commands

### Initialize watermark (post-restore, post-bootstrap)

```bash
python run_pipeline.py init-watermark
```

Optional explicit DB path:

```bash
python run_pipeline.py init-watermark --db-path /path/to/signals.db
```

Reads `signals.signals` count, persists to `.omx/state/db_watermark.json` with
the current `CURRENT_SCHEMA_VERSION` and a UTC timestamp. Defined at
`run_pipeline.py:4473-4483` and `run_pipeline.py:8117-8132`.

### Verify watermark

```bash
cat .omx/state/db_watermark.json
```

Expected fields: `signal_count`, `schema_version`, `timestamp`.

## When to run `init-watermark`

1. **Post-restore.** After replacing `signals.db` from a verified backup, run
   the verification gate (see
   `docs/plans/2026-04-04-next-dev-priority/post_restore_verification.md`)
   then re-init the watermark to the post-restore signal count.
2. **First-time bootstrap on a fresh checkout** before any `write` pipeline
   command. Without this, every guarded write fails with
   `DB guard blocked (watermark_missing)`.
3. **After deliberate large reductions** (e.g., archival cleanup) where the
   reduced count is the new ground truth.

Do **not** run it as a routine fix for guard-blocked writes. A blocked write
on `catastrophic_drop_detected` is a signal to investigate, not to re-anchor.

## Recovery override (catastrophic drop)

If a write must proceed despite a tripped guard (e.g., during a controlled
incident response), use the explicit override path. The audit log will reflect
the override at `WARNING` level.

The override does **not** auto-init a missing watermark — only an existing
watermark with a tripped baseline. Missing-watermark writes always require
`init-watermark` first.

## What changed in Day 2.5

- Removed silent watermark auto-init from `check_db_health`.
- `check_db_health` now returns `(False, "watermark_missing")` when the file
  is absent, rather than `(True, "watermark_missing")` after writing one.
- `guard_command` blocks writes on missing watermark (consistent with how it
  treats catastrophic drops).
- Tests at `tests/utils/test_db_guard.py` updated to assert the new contract.
