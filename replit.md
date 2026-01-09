# Discovery Engine

Automated deal sourcing system for Press On Ventures (early-stage VC). Modern Next.js + FastAPI architecture.

## Overview

**Purpose:** Automated deal sourcing and signal collection for venture capital deal flow.

**Fund Focus:** Consumer | Pre-Seed to Series A | US/UK

**Current State:** Migrating from Streamlit to Next.js + FastAPI web application.

## Architecture

- **Frontend:** Next.js 16 on port 5000 (dark theme, sidebar navigation)
- **Backend:** FastAPI on port 8000 with API proxy rewrites
- **Database:** SQLite (signals.db) for signals, founders; app.db planned for user metadata
- **Language:** Python 3.11 + TypeScript

### Key Directories
- `web/` - Next.js frontend (TypeScript, Tailwind CSS)
- `api/` - FastAPI backend
- `storage/` - SQLite storage layer (SignalStore, FounderStore)
- `collectors/` - Signal collectors (GitHub, SEC, Companies House, etc.)
- `workflows/` - Pipeline orchestration, founder sync
- `consumer/` - Signal processing and filtering
- `utils/` - Helper utilities
- `dashboard/` - Legacy Streamlit dashboard (deprecated)

## Running the Application

Two workflows configured:
- **Web App:** `cd web && npm run dev` (port 5000)
- **API Server:** `python -m uvicorn api.main:app --reload --host localhost --port 8000`

## Environment Variables Needed

```
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx
GITHUB_TOKEN=ghp_xxx
COMPANIES_HOUSE_API_KEY=xxx
PH_API_KEY=xxx
GOOGLE_API_KEY=xxx
DISCOVERY_DB_PATH=signals.db
```

## Technical Notes

### SQLite Configuration
All SQLite stores (SignalStore, FounderStore) use shared pragmas via `storage/sqlite_pragmas.py`:
- `journal_mode=WAL` for concurrent read/write
- `busy_timeout=5000` to prevent lock errors
- `foreign_keys=ON` for referential integrity

### Founder Schema
- Uses `linkedin:username` format for `founder_key` (unique identifier)
- One founder → one company constraint (skip conflicts in sync)
- Join-table refactor deferred until: conflict rate >3% for 3 runs, or >25 conflicts/run, or explicit network view need

## Recent Changes

- 2026-01-09: SQLite hardening - WAL mode, busy_timeout, portable ordering, transaction consistency
- 2026-01-09: Next.js + FastAPI architecture with sidebar navigation
- 2026-01-09: Initial Replit setup - Python 3.11, dependencies installed
