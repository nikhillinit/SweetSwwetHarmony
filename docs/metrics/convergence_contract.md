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

## Reference Implementation

See `scripts/convergence_kpi.py` for the canonical implementation of these queries.
