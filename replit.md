# Discovery Engine

Automated deal sourcing system for Press On Ventures (early-stage VC). This is a Python application using Streamlit for the dashboard.

## Overview

**Purpose:** Automated deal sourcing and signal collection for venture capital deal flow.

**Fund Focus:** Consumer | Pre-Seed to Series A | US/UK

**Current State:** Imported from GitHub, configured for Replit environment.

## Architecture

- **Frontend:** Streamlit dashboard on port 5000
- **Database:** SQLite (signals.db) for signal storage
- **Language:** Python 3.11

### Key Directories
- `dashboard/` - Streamlit web interface
- `collectors/` - Signal collectors (GitHub, SEC, Companies House, etc.)
- `storage/` - SQLite storage layer
- `workflows/` - Pipeline orchestration
- `consumer/` - Signal processing and filtering
- `utils/` - Helper utilities

## Running the Application

The Streamlit dashboard runs automatically via the configured workflow:
```bash
streamlit run dashboard/app.py --server.port 5000 --server.address 0.0.0.0 --server.headless true
```

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

## Recent Changes

- 2026-01-09: Initial Replit setup - Python 3.11, dependencies installed, Streamlit workflow configured
