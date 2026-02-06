# Native bootstrap (no Docker)

A “bootstrap” step reduces onboarding friction and catches common environment issues before someone wastes time debugging.

## What bootstrap should do

1. Verify Python version and key dependencies
2. Verify SQLite has FTS5 enabled (or provide a clear error)
3. Create expected directories (`ops/artifacts`, etc.)
4. Initialize migrations by constructing `OpsStorage`
5. Optionally run a lightweight YouTube subtitle smoke test (non-blocking)
6. Print next-step commands for the operator

## Provided scripts

- `ops/bootstrap.py` — run via `python -m ops.bootstrap`
- `scripts/bootstrap.ps1` — helper wrapper for PowerShell

## Typical usage

```powershell
python -m ops.bootstrap --db signals.db
python -m ops.cli stats --db signals.db
```

## Philosophy: fail early on “core”, fail soft on “optional”

- FTS5 missing? That’s core → stop with a crisp error.
- YouTube fetch fails? That’s optional → warn, but continue.
