# Ops Layer — Windows-first runbook (no Docker required)

This ops layer stores and curates “memory facts” (constraints, nuances, examples) derived from user actions/signals, and provides a CLI to review, approve, retire, and audit those facts.

You can run everything natively on Windows with a standard Python virtual environment. Treat YouTube subtitle ingestion as an optional add-on (it should not block core workflows).

## What’s in this doc set

- `docs/windows_quickstart.md` — end-to-end setup on Windows (PowerShell)
- `docs/env_and_config.md` — configuration via `.env` (with safe defaults)
- `docs/bootstrap.md` — native bootstrap script and what it validates
- `docs/ytdlp_maintenance.md` — yt-dlp update policy + smoke tests
- `docs/retries_and_resilience.md` — when to retry, and optional Tenacity usage

Supporting templates / scripts:

- `.env.example` — copy to `.env`
- `ops/bootstrap.py` — optional module you can add to run sanity checks + init
- `scripts/bootstrap.ps1` — convenience runner for Windows
- `scripts/ytdlp_smoke_test.py` — a single-purpose YouTube subtitle smoke test

## Expected repo layout

This doc set assumes you have an `ops/` package that contains:

- `ops/storage.py`
- `ops/utils.py`
- `ops/cli.py`
- `ops/memory/extractor.py`
- `ops/memory/briefing.py`
- `ops/trends/youtube.py`

(If your files currently live at repo root, move them into the package layout above; the imports already assume it.)

## First run (Windows)

Follow `docs/windows_quickstart.md`. In most cases, you’ll be up and running with:

1. A local venv (`.venv/`)
2. A SQLite database file (default: `signals.db`)
3. A working CLI (`python -m ops.cli stats`)

## Design principles

- **Windows-first**: no reliance on bash or containers for day-to-day dev.
- **Fail soft**: optional integrations (YouTube) should degrade gracefully.
- **Reproducible**: pin volatile dependencies when they matter (notably `yt-dlp`).
- **Operationally visible**: prefer clear logs + audit trails over cleverness.
