# Feature Promotion Runbook

Operational guide for recording feature governance events in `audit_events`.

## Prerequisites

- `audit_events` table exists (migration v35)
- Schema: `actor_id TEXT NOT NULL` — every event must have an actor

## Recording a Feature Promotion

When promoting a feature flag (e.g., advancing from `shadow` to `active`):

```sql
INSERT INTO audit_events (
    action_type, entity_type, entity_id, actor_id, reason, created_at
) VALUES (
    'feature_promote',
    'feature_flag',
    'DELIVERY_MODE',
    'operator:alice',
    'Step 3 activation — canary stable for 48h',
    '2026-02-28T12:00:00+00:00'
);
```

**Critical:** `actor_id` is `NOT NULL`. Omitting it will cause an INSERT failure.

## Recording a Regret Check

After the 14-day regret window, record the check outcome:

```sql
INSERT INTO audit_events (
    action_type, entity_type, entity_id, actor_id, reason, metadata, created_at
) VALUES (
    'regret_check',
    'feature_flag',
    'DELIVERY_MODE',
    'operator:alice',
    'No regressions observed in 14-day window',
    '{"canary_verdict": "pass", "drift_status": "in_control"}',
    '2026-03-14T12:00:00+00:00'
);
```

## Recording a Feature Demotion (Rollback)

```sql
INSERT INTO audit_events (
    action_type, entity_type, entity_id, actor_id, reason, created_at
) VALUES (
    'feature_demote',
    'feature_flag',
    'DELIVERY_MODE',
    'operator:alice',
    'Canary degraded — reverting to manual_publish',
    '2026-03-01T08:00:00+00:00'
);
```

## Querying Governance State

### All feature decisions (via convenience view)

```sql
SELECT * FROM feature_decisions ORDER BY created_at DESC LIMIT 20;
```

The `feature_decisions` view is auto-created by `monitoring.feature_gate` and
includes all `audit_events` rows where `action_type LIKE 'feature_%'`.

### Overdue regret checks

```bash
python -m monitoring.feature_gate overdue --db signals.db --json
```

Output:
```json
{"count": 1, "overdue": [{"entity_id": "DELIVERY_MODE", "promoted_at": "2026-02-01T00:00:00+00:00", "due_at": "2026-02-15T00:00:00+00:00"}]}
```

## Automated Monitoring

The canary-monitor scheduler runs regret checks every 6 hours. See
[canary-monitor-scheduler.md](canary-monitor-scheduler.md) for the `ok/error`
payload contract.
