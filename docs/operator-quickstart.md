# Operator Quickstart

Day-1 guide for running the Discovery Engine in production.

## 1. System Requirements

- Python 3.11+
- 2GB+ disk space for database and backups
- Network access to GitHub, Notion, and Google APIs (as configured)
- Git (for CI regression checks)

## 2. Initial Setup

```bash
# Clone and install
git clone <repo-url> && cd Harmonic
pip install -r requirements.txt

# Create production env from template
cp .env.production.template .env
# Edit .env with your API keys and settings
```

## 3. Validate Configuration

```bash
python scripts/validate_env.py --env-file .env
```

All checks should pass with no errors. Warnings about missing optional keys
(Crunchbase, Product Hunt, etc.) are expected if you haven't configured them.

## 4. Pre-flight Check

```bash
# Quick check (~5s) — DB, schema, config, gates, backup
python scripts/preflight_check.py --json

# Full check (~60s) — includes smoke test suite
python scripts/preflight_check.py --mode full --json
```

Overall verdict must be `pass` or `warn` before proceeding.

## 5. First Backup

```bash
python scripts/backup_db.py --db signals.db --out-dir backups/ --retain 7
```

Verify backup was created:

```bash
ls backups/
# signals-YYYYMMDD-HHMMSS.db
```

## 6. Start API Server

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Verify health:

```bash
curl http://localhost:8000/api/v1/health
```

## 7. Begin Activation

Follow the 4-step progressive activation in
[docs/runbooks/feature-activation.md](runbooks/feature-activation.md).

**Before Step 1:**

```bash
python scripts/preflight_check.py --json
python scripts/backup_db.py
```

**Step 1 flags** (shadow — observe only, no mutations):

```bash
LLM_THESIS_MODE=shadow
ML_ENABLEMENT=shadow
MERGE_WRITES_ENABLED=shadow
USE_SHADOW_ENTITY_RESOLUTION=true
```

Run for 48h, monitor logs and canary, then check the gate:

```bash
python run_pipeline.py activation-check --step 2
```

## 8. Daily Operations

| Task | Command | Frequency |
|------|---------|-----------|
| Pipeline run | `python run_pipeline.py full --collectors github,sec_edgar` | Daily |
| Backup | `python scripts/backup_db.py` | Daily |
| Health check | `python run_pipeline.py health --json` | Daily |
| Pre-flight | `python scripts/preflight_check.py --json` | Before step changes |
| Canary check | `python run_pipeline.py canary` | After pipeline runs |

## 9. Emergency Procedures

**Full rollback** — disable all features:

```bash
LLM_THESIS_MODE=off
ML_ENABLEMENT=disabled
MERGE_WRITES_ENABLED=disabled
USE_SHADOW_ENTITY_RESOLUTION=false
DRIFT_MONITORING_ENABLED=disabled
USE_THIN_FILES=false
V2_ENABLEMENT=shadow
DELIVERY_MODE=staging_only
BULK_TRIAGE_ENABLED=disabled
HUNTER_PROMOTE_ENABLED=disabled
```

Restart the API server and verify smoke suite:

```bash
python -m pytest tests/smoke/ -q
```

**Restore from backup:**

```bash
# Stop the API server first, then:
python scripts/restore_db.py backups/signals-YYYYMMDD-HHMMSS.db --db signals.db
```

**Detailed runbooks:**
- [Feature Activation](runbooks/feature-activation.md)
- [Migration Rollback](runbooks/migration-rollback.md)
- [Phase G Activation](runbooks/phase-g-activation.md)

## Key CLI Commands

| Command | Purpose |
|---------|---------|
| `python run_pipeline.py full --dry-run` | Dry-run pipeline |
| `python run_pipeline.py health --json` | Health check |
| `python run_pipeline.py activation-check --step N` | Gate check |
| `python scripts/preflight_check.py --json` | Pre-flight |
| `python scripts/backup_db.py` | Create backup |
| `python scripts/restore_db.py <file>` | Restore backup |
| `python scripts/validate_env.py` | Validate env |
| `python -m ops.cli quality stats --db signals.db` | Quality stats |
