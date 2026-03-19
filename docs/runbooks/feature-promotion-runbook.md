# Feature Promotion Runbook

Operational guide for recording feature governance events.

## Prerequisites

- `audit_events` table exists (migration v35)
- `signals.db` must exist and be readable (governance CLI never creates a new DB)

## CLI (Canonical)

The governance CLI is the primary interface. It validates transitions,
computes config snapshots, and writes audit events atomically.

### Promote an env-backed flag

```bash
DISCOVERY_DB_PATH=signals.db python -m governance feature promote DELIVERY_MODE \
  --from manual_publish --to batch_publish \
  --reason "Step 4A promotion — canary stable for 48h"
```

### Promote a feature-registry flag

```bash
DISCOVERY_DB_PATH=signals.db python -m governance feature promote boilerplate_defense \
  --from shadow --to active \
  --reason "Canary stable for 48h, lift demonstrated"
```

### Record a regret check

```bash
DISCOVERY_DB_PATH=signals.db python -m governance feature regret-check DELIVERY_MODE \
  --verdict pass --canary-verdict pass --drift-status in_control \
  --reason "No regressions in 14-day window"
```

### Demote a flag

```bash
DISCOVERY_DB_PATH=signals.db python -m governance feature demote DELIVERY_MODE \
  --from batch_publish --to manual_publish \
  --reason "Canary degraded — reverting to manual_publish"
```

### Break-glass: direct DB path

Use `--direct-db` to bypass `DISCOVERY_API_URL` and write directly to a
specific database file:

```bash
python -m governance feature promote DELIVERY_MODE \
  --from manual_publish --to batch_publish \
  --reason "Emergency break-glass" \
  --direct-db /path/to/signals.db
```

### Custom regret check date

```bash
DISCOVERY_DB_PATH=signals.db python -m governance feature promote boilerplate_defense \
  --from shadow --to active \
  --regret-check-date 2026-04-15 \
  --reason "Extended observation window"
```

## Validation Rules

The CLI and API enforce these rules via `governance/state_policies.py`:

| Rule | Env-backed (UPPER_CASE) | Feature-registry (lower_case) |
|------|------------------------|-------------------------------|
| Direction | Promote=up, Demote=down | Promote=up, Demote=down |
| Skip-level | Rejected | Allowed |
| No-op | Rejected | Rejected |
| Unknown flag | Rejected (with case hint) | Rejected (with case hint) |

### Common error messages

- `Did you mean 'DELIVERY_MODE'?` — used lowercase `delivery_mode`
- `Did you mean 'boilerplate_defense'?` — used UPPER_CASE or FEATURE_ prefix
- `wrong direction` — tried to promote downward or demote upward
- `Skip-level promotion not allowed` — skipped intermediate state (env-backed only)

## Querying Governance State

### All feature decisions (via convenience view)

```sql
SELECT * FROM feature_decisions ORDER BY created_at DESC LIMIT 20;
```

### Overdue regret checks

```bash
python -m monitoring.feature_gate overdue --db signals.db --json
```

## Automated Monitoring

The canary-monitor scheduler runs regret checks every 6 hours. See
[canary-monitor-scheduler.md](canary-monitor-scheduler.md) for the `ok/error`
payload contract.

---

## Appendix: Emergency SQL (break-glass only)

**Use only for backfill or emergency when the CLI is unavailable.**
These INSERTs bypass semantic validation (no directional checks, no
snapshot computation, no flag registration check).

### Feature promotion

```sql
INSERT INTO audit_events (
    action_type, entity_type, entity_id, actor_id, reason,
    metadata, created_at
) VALUES (
    'feature_promote',
    'feature_flag',
    'DELIVERY_MODE',
    'operator:alice',
    'Emergency: Step 4A promotion',
    '{"action_type": "feature_promote", "feature_name": "DELIVERY_MODE",
      "from_state": "manual_publish", "to_state": "batch_publish",
      "regret_due_at": "2026-04-01",
      "config_snapshot_hash": "MANUAL-BACKFILL",
      "config_snapshot_flags": null}',
    '2026-03-16T19:00:00+00:00'
);
```

### Regret check

```sql
INSERT INTO audit_events (
    action_type, entity_type, entity_id, actor_id, reason,
    metadata, created_at
) VALUES (
    'regret_check',
    'feature_flag',
    'DELIVERY_MODE',
    'operator:alice',
    'No regressions observed in 14-day window',
    '{"action_type": "regret_check", "verdict": "pass",
      "canary_verdict": "pass", "drift_status": "in_control",
      "window_days": 14}',
    '2026-03-30T12:00:00+00:00'
);
```

**Critical:** `actor_id` is `NOT NULL`. Omitting it will cause an INSERT failure.
