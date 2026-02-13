# Operator Guide

## Daily Operations

### Scheduled Tasks

| Task | Schedule | Command |
|------|----------|---------|
| Daily metric aggregation | 01:30 UTC | `DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift aggregate` |
| SPC check | 02:00 UTC | `DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift check` |
| Snooze auto-reopen | Every 15 min | Automatic (alerts with expired `snoozed_until` reopen) |
| Full pipeline run | As needed | `python run_pipeline.py full --collectors github,sec_edgar` |
| Suppression sync | Every 6 hours | `python run_pipeline.py sync` |

### Cron Setup (Fallback)

If the in-repo scheduler is not deployed:

```bash
# Daily aggregation at 01:30 UTC
30 1 * * * cd /path/to/Harmonic && DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift aggregate --db signals.db

# SPC check at 02:00 UTC
0 2 * * * cd /path/to/Harmonic && DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift check --db signals.db
```

## Alert Triage Workflow

### 1. Check open alerts

```bash
python run_pipeline.py drift alerts --status open
```

### 2. Acknowledge + investigate

```bash
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift ack <id> --reason "Investigating"
```

### 3. Resolve or snooze

```bash
# Resolve
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift resolve <id> --reason "Fixed: adjusted threshold"

# Snooze (1-168 hours)
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift snooze <id> --hours 24
```

### 4. Check recommendations

```bash
python run_pipeline.py drift recommend
```

## Quality Monitoring

### View quality stats

```bash
python -m ops.cli quality stats --db signals.db --days 30
```

### Run canary check

```bash
python run_pipeline.py canary --db signals.db
```

### View calibration

```bash
python run_pipeline.py drift calibrate
```

## Database Maintenance

### Health check

```bash
python run_pipeline.py health --json
```

### Export metrics (before GC)

```bash
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift export-metrics --days 365 --format csv --out metrics_backup.csv
```

### Garbage collection

```bash
DRIFT_MONITORING_ENABLED=active python run_pipeline.py drift gc --metrics-days 365 --alerts-days 180
```

### Database backup

```bash
cp signals.db signals.db.backup-$(date +%Y%m%d-%H%M%S)
sqlite3 signals.db "PRAGMA integrity_check"
```

## Feature Flags

| Flag | Values | Purpose |
|------|--------|---------|
| `DRIFT_MONITORING_ENABLED` | `disabled` (default) / `active` | Gates all drift monitoring mutations |
| `MERGE_WRITES_ENABLED` | `disabled` / `shadow` / `active` | Controls merge proposal writes |
| `BULK_TRIAGE_ENABLED` | `disabled` / `active` | Controls bulk triage operations |
| `HUNTER_PROMOTE_ENABLED` | `disabled` / `active` | Controls hunter signal promotion |
| `DELIVERY_MODE` | `staging_only` / `manual_publish` / `batch_publish` / `auto_publish` | Controls Notion push behavior |
| `LLM_THESIS_MODE` | `off` / `shadow` / `active` | Controls LLM classification |

## RBAC Roles

| Role | Permissions | Typical User |
|------|-------------|-------------|
| READONLY (viewer) | View, search, export | External stakeholders |
| ANALYST (operator) | + triage, hunter, canary, alert triage | Deal team analysts |
| GP (admin) | + batch publish, entity merge, bulk triage, manage users | General partners |

## Dashboard Access

The Streamlit dashboard provides visual monitoring:

```bash
streamlit run dashboard/app.py
```

Key views:
- **Ops Health**: Pipeline metrics, collector status, anomaly detection
- **Triage Fast Pass**: Quick signal triage with cursor pagination
- **Triage Deep Review**: Detailed signal review with ACH matrix
- **Batch Publish**: Batch publishing workflow
- **Hunter Sandbox**: Active hunter results and promotion
- **ACH Matrix**: Analysis of competing hypotheses
- **Drift Monitoring**: SPC charts, canary status, alert timeline, recommendations

## Troubleshooting

### Pipeline stuck

1. Check for lock files: `ls *.lock`
2. Check running processes: `ps aux | grep run_pipeline`
3. Check DB WAL size: `ls -la signals.db-wal`
4. If WAL is large: `sqlite3 signals.db "PRAGMA wal_checkpoint(TRUNCATE)"`

### API errors

1. Check health: `python run_pipeline.py health --json`
2. Verify API keys in `.env`
3. Check rate limits (GitHub: 5000/hr, GNews: 100/day)

### No new signals

1. Run collectors manually: `python run_pipeline.py collect --collectors github`
2. Check suppression cache: signals may be suppressed from previous runs
3. Verify collector API keys: see `CLAUDE.md` API Key Coverage section
