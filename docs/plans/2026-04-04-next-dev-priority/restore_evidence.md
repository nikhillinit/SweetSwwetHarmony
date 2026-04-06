# Restore Evidence - 2026-04-04

## Snapshot Before Restore

Current live `signals.db`:

```json
{
  "signals": 4,
  "schema_version": 53,
  "integrity": "ok",
  "processing": {
    "held": 4
  },
  "audit_events": 0,
  "suppression_cache": 0,
  "thesis_classifications": 0,
  "pipeline_runs": 1
}
```

Restore candidate `signals.db.pre-step4b-promotion-20260404`:

```json
{
  "signals": 612,
  "schema_version": 53,
  "integrity": "ok",
  "processing": {
    "held": 464,
    "pending": 32,
    "pushed": 15,
    "queued": 2,
    "rejected": 98
  },
  "audit_events": 20,
  "suppression_cache": 588,
  "thesis_classifications": 2593,
  "pipeline_runs": 48
}
```

## Staged Restore Snapshot

`signals.db.restore-stage-20260404T195300Z`:

```json
{
  "signals": 612,
  "schema_version": 53,
  "integrity": "ok",
  "processing": {
    "held": 464,
    "pending": 32,
    "pushed": 15,
    "queued": 2,
    "rejected": 98
  },
  "audit_events": 20,
  "suppression_cache": 588,
  "thesis_classifications": 2593,
  "pipeline_runs": 48
}
```

## Live Snapshot After Sidecar Cleanup

`signals.db`:

```json
{
  "signals": 612,
  "schema_version": 53,
  "integrity": "ok",
  "processing": {
    "held": 464,
    "pending": 32,
    "pushed": 15,
    "queued": 2,
    "rejected": 98
  },
  "audit_events": 20,
  "suppression_cache": 588,
  "thesis_classifications": 2593,
  "pipeline_runs": 48
}
```

## Forensics Copy

Captured broken live files:

- `artifacts/incident-db-restore-20260404T195300Z/signals.db.truncated`
- `artifacts/incident-db-restore-20260404T195300Z/signals.db-wal`
- `artifacts/incident-db-restore-20260404T195300Z/signals.db-shm`
