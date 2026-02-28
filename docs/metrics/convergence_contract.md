# Convergence KPI Contract

## KPI Definitions

### Primary KPI: Multi-Family Convergence (`keys_with_2plus_families`)

How many `canonical_key_v2` values have signals from 2+ distinct evidence families?

```sql
SELECT canonical_key_v2, COUNT(DISTINCT evidence_family) as families
FROM signals
WHERE canonical_key_v2 IS NOT NULL
  AND evidence_family IS NOT NULL AND evidence_family <> 'unknown'
  AND detected_at >= datetime('now', '-30 days')
  AND canonical_key_v2 NOT LIKE 'name_loc:unlinked_buzz_%'
GROUP BY canonical_key_v2
HAVING families >= 2
```

### Secondary KPI: Multi-API Convergence (`keys_with_2plus_source_apis`)

How many `canonical_key_v2` values have signals from 2+ distinct `source_api` values?

```sql
SELECT canonical_key_v2, COUNT(DISTINCT source_api) as apis
FROM signals
WHERE canonical_key_v2 IS NOT NULL
  AND detected_at >= datetime('now', '-30 days')
  AND canonical_key_v2 NOT LIKE 'name_loc:unlinked_buzz_%'
GROUP BY canonical_key_v2
HAVING apis >= 2
```

### Unknown Family Rate

Percentage of signals with `evidence_family IS NULL OR evidence_family = 'unknown'`.

```sql
SELECT COUNT(*) FROM signals
WHERE detected_at >= datetime('now', '-30 days')
  AND (evidence_family IS NULL OR evidence_family = 'unknown')
```

### Unlinked Buzz Rate

Percentage of signals with synthetic unlinked-buzz canonical keys.

```sql
SELECT COUNT(*) FROM signals
WHERE detected_at >= datetime('now', '-30 days')
  AND canonical_key_v2 LIKE 'name_loc:unlinked_buzz_%'
```

## Field Mapping

| Field | Table | Description |
|-------|-------|-------------|
| `canonical_key_v2` | `signals` | Domain-first key (v43 schema). Prefix: `domain:`, `name_loc:`, `hash:` |
| `evidence_family` | `signals` | Signal category (v42 schema). Values: `developer`, `regulatory`, `web_presence`, `hiring`, `public_buzz`, `unknown` |
| `source_api` | `signals` | Collector that produced the signal (e.g. `github`, `news_api`, `sec_edgar`) |
| `detected_at` | `signals` | When the signal was detected (determines KPI window inclusion) |
| `created_at` | `signals` | When the row was inserted (NOT used for KPI window) |

## Window Semantics

- KPI queries filter by `detected_at`, NOT `created_at`
- Default window: 30 days (`datetime('now', '-30 days')`)
- Backfilled signals use their original `detected_at`, so they appear in the correct window
- Before reading KPI verdicts, always verify recent signals exist within window by `detected_at`

## Synthetic vs Organic Discrimination

| Category | Criterion | Example |
|----------|-----------|---------|
| **Synthetic** | `source_api = 'manual_seed_buzz'` | Forced-overlap signals from PR #70 |
| **Organic** | All other `source_api` values | `github`, `news_api`, `sec_edgar`, etc. |

Organic KPI = the convergence KPI computed after excluding `source_api = 'manual_seed_buzz'` signals.

## Provenance

No schema expansion for provenance fields. Synthetic vs organic discrimination is purely query-time filtering on `source_api`.

## Diagnostic Scoping Semantics

The convergence diagnostic (`scripts/convergence_diagnostic.py`) supports scoping sections to a pipeline run or time window. Scoping and synthetic/organic filtering are independent dimensions.

### Scoping Matrix

| Scope | Sections | Filter field |
|-------|----------|--------------|
| All-time | 1, 2, 6, 7, 8, 9, 10 | N/A |
| `--run-id` | 3, 4 | `signals.created_at` within run window |
| `--run-id` | 5 | `collector_metrics.run_id` (direct equality) |
| `--since` | 3, 4 | `signals.created_at >= cutoff` |
| Section 5 default | 5 (no run_id) | Latest `run_id` via CTE |

**Important distinction:** Diagnostic scoping filters by `created_at` (when the row was inserted). KPI windows filter by `detected_at` (when the signal was first observed). These are different fields with different semantics.

### Precedence

```
explicit --run-id > --latest-run (resolved run_id) > --since > all_time
```

- `--latest-run` is a run_id resolver: sets `run_id` from the latest `pipeline_runs` row. If no row exists, falls back to `all_time` with a warning.
- If both `--run-id` and `--latest-run` are provided, the explicit `--run-id` wins.
- `--since` must be >= 1; `--since 0` is rejected at CLI parse time.

### `active_scope` JSON Shape

Every report includes an `active_scope` object:

```json
{
  "mode": "run_id | since_days | all_time",
  "run_id": "abc123 | null",
  "since_days": "14 | null",
  "resolved_from_latest_run": true,
  "applies_to_sections": [3, 4, 5],
  "section_5_default": "latest_run | null",
  "description": "Scoped to latest pipeline run: abc123"
}
```

- `applies_to_sections`: `[3,4,5]` for `run_id`, `[3,4]` for `since_days`, `[]` for `all_time`
- `section_5_default`: `"latest_run"` when no run_id provided, `null` otherwise
- `resolved_from_latest_run`: `true` only when `--latest-run` resolved a run_id
- The legacy `scoped_run_id` top-level field is retained for backward compatibility

## Reference Implementation

See `scripts/convergence_kpi.py` for the canonical implementation of these queries.
