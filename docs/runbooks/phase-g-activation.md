# Phase G Entity Resolution Activation Runbook

## Overview

Phase G is the entity identity resolution system — the largest disabled feature in the
Discovery Engine (669 lines, 5 migrations, 4 tables). It resolves signals from
multiple sources into stable entity identities using strong keys (domains, registry IDs),
weak aliases (name variants), and fuzzy matching (blocking index + RapidFuzz).

This runbook covers safe activation with gates, dry-run tooling, and rollback procedures.

## Prerequisites

- Steps 1-2 of `feature-activation.md` already clean (shadow features + thin files active)
- All 40 migrations applied (`schema_migrations` table has version 40)
- Smoke suite passing (`pytest tests/smoke/ -q`)
- Phase G readiness gate passing (see Pre-check below)

## Pre-check: Phase G Readiness Gate

Before ANY Phase G activation, run the readiness gate:

```bash
# CLI (exit 0 = can proceed, exit 1 = blocked)
python run_pipeline.py phase-g-check

# API endpoint
curl http://localhost:8000/api/v1/health/phase-g-readiness
```

The gate checks 5 conditions:

| Check | Pass | Warn | Blocked |
|-------|------|------|---------|
| Entity tables present | All 4 tables exist | — | Any table missing |
| Blocking index populated | >0 rows | 0 rows (no shadow data) | — |
| Shadow merge quality | <10% rejection rate | — | >10% rejection rate |
| No orphaned entities | 0 orphaned signals | >0 orphaned signals | — |
| Claim facts consistent | No contradictions | Contradictions found | — |

**Verdict logic:** Any "blocked" check → overall blocked. Any "warn" → overall warn (can proceed with caution). All pass → ready.

---

## Phase 1: Shadow Pilot (48h minimum)

**Goal:** Observe entity resolution matches without applying merges.

### Set env vars

```bash
USE_SHADOW_ENTITY_RESOLUTION=true
# These should already be set from Step 1 activation:
# MERGE_WRITES_ENABLED=shadow
```

### What happens

- `PhaseGEntityResolver` runs on every pipeline execution
- Candidate merge pairs are computed and written to `merge_suggestions`
- Entity blocking index (`entity_blocking_index`) is populated
- Strong key bindings populate `entity_aliases`
- Weak alias bindings populate `entity_key_aliases`
- **No merges are applied.** Signals keep their original `company_id` values.

### Monitor

```bash
# Shadow report — see volumes and quality
python run_pipeline.py shadow-status --days 2

# Direct SQL checks
sqlite3 signals.db "SELECT COUNT(*) FROM entity_blocking_index"
sqlite3 signals.db "SELECT COUNT(*) FROM merge_suggestions WHERE status='proposed'"
sqlite3 signals.db "SELECT match_type, COUNT(*) FROM merge_suggestions GROUP BY match_type"
```

### What to look for

- Blocking index should grow with each pipeline run
- Merge suggestions should have reasonable confidence scores (>0.8)
- Match types should be diverse (not all `fuzzy_name`)
- Rejection rate should stay below 10%

### Rollback

```bash
USE_SHADOW_ENTITY_RESOLUTION=false
```

---

## Phase 2: Review Merge Candidates (after 48h shadow)

**Goal:** Validate merge quality before applying.

### Run readiness gate

```bash
python run_pipeline.py phase-g-check
# Must return "ready" or "warn" — not "blocked"
```

### Preview merges (read-only)

```bash
# Show top 10 proposed merges with impact preview
python run_pipeline.py entity-merge-preview --limit 10
```

This shows:
- Candidate merge pairs (from `merge_suggestions` with status=proposed)
- Winner entity_id (lexmin) and loser entity_id
- Signals that would be re-parented
- Review items affected
- Company files affected

### Evaluate quality

For each proposed merge, verify:
- [ ] The two entities genuinely represent the same company
- [ ] The match type makes sense (domain match vs fuzzy name)
- [ ] No false-positive merges (different companies with similar names)
- [ ] Confidence score reflects actual certainty

If rejection rate exceeds 10%, investigate and tune before proceeding.

---

## Phase 3: Activate Entity Resolution

**Goal:** Enable live entity merges.

### Set env vars

```bash
USE_PHASE_G_IDENTITY_RESOLUTION=true
# Keep shadow resolution on as well:
USE_SHADOW_ENTITY_RESOLUTION=true
# Ensure merge writes are active (Step 4 of feature-activation.md):
MERGE_WRITES_ENABLED=active
```

### What happens

- `EntityIdentityStore.merge_entities()` applies merges in production
- `cascade_merge()` reassigns signals, reviews, and company files
- `entity_migrations` records merge history
- Drift fingerprints computed post-merge
- Merge proposals track lifecycle: proposed → approved → applied

### Monitor (daily for first week)

```bash
# Entity audit — shows recent operations
python run_pipeline.py entity-audit --days 1

# Check for orphaned entities
python run_pipeline.py phase-g-check

# Verify merge cascade integrity
sqlite3 signals.db "
  SELECT COUNT(*) as total_merges,
         SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
         SUM(CASE WHEN status='rolled_back' THEN 1 ELSE 0 END) as rolled_back
  FROM merge_proposals
"

# Check no signals point to merged-away entities
sqlite3 signals.db "
  SELECT COUNT(DISTINCT s.company_id) FROM signals s
  WHERE s.company_id IS NOT NULL
    AND s.company_id IN (SELECT from_entity_id FROM entity_migrations)
    AND s.company_id NOT IN (SELECT to_entity_id FROM entity_migrations)
"
# Expected: 0
```

### What to look for

- Entity audit should show clean LIFO chain (no broken chains)
- No orphaned entity IDs (signals pointing to merged-away entities)
- Drift fingerprints change only for merged entities
- Merge cascade reports `signals_reassigned > 0` for genuine merges
- Review items correctly consolidated (no duplicate active reviews)

### Rollback

```bash
USE_PHASE_G_IDENTITY_RESOLUTION=false
# Merge writes back to shadow:
MERGE_WRITES_ENABLED=shadow
```

Applied merges are NOT automatically reversed on rollback. To reverse specific merges:

```bash
# Check merge proposals that can be rolled back (within TTL, LIFO order)
sqlite3 signals.db "
  SELECT id, suggestion_id, status, created_at
  FROM merge_proposals
  WHERE status = 'applied'
  ORDER BY created_at DESC
"

# Rollback via API (requires admin role)
curl -X POST http://localhost:8000/api/v1/merge/{proposal_id}/rollback \
  -H "Content-Type: application/json" \
  -d '{"actor": "operator@example.com", "reason": "Phase G rollback"}'
```

**Rollback eligibility gates:**
- TTL: Within `MERGE_ROLLBACK_TTL_HOURS` (default 24h) of merge
- LIFO: Most recent merge must be rolled back first
- Drift: Entity fingerprint must match post-merge snapshot (no manual edits)

---

## Phase 4: Activate Claim Facts (optional, after 1 week clean)

**Goal:** Enable bi-temporal fact tracking for richer entity profiles.

### Set env vars

```bash
USE_CLAIM_FACTS=true
```

### What happens

- `ClaimFactStore` begins recording structured facts (company_name, founding_date, etc.)
- SCD-2 logic manages fact supersession by source authority tier
- Facts from higher authority sources override lower authority
- Contradictions are flagged in readiness gate

### Monitor

```bash
# Check for contradictions
python run_pipeline.py phase-g-check
# Look for claim_fact_contradictions metric

# Direct SQL
sqlite3 signals.db "
  SELECT entity_id, predicate, COUNT(*) as active_facts
  FROM claim_facts
  WHERE valid_until IS NULL AND is_retracted = 0
  GROUP BY entity_id, predicate
  HAVING active_facts > 1
"
# Expected: 0 (no contradictions)
```

### Rollback

```bash
USE_CLAIM_FACTS=false
```

Existing facts remain in the table but stop being updated.

---

## Emergency Full Phase G Rollback

Disable all Phase G features:

```bash
USE_PHASE_G_IDENTITY_RESOLUTION=false
USE_SHADOW_ENTITY_RESOLUTION=false
USE_CLAIM_FACTS=false
MERGE_WRITES_ENABLED=shadow
```

Restart the API server. Verify:

```bash
python run_pipeline.py phase-g-check
# Should show "blocked" (which is expected — tables exist but feature disabled)

pytest tests/smoke/ -q
# Smoke suite should pass
```

---

## Feature Flag Reference

| Flag | Values | Default | Phase |
|------|--------|---------|-------|
| `USE_SHADOW_ENTITY_RESOLUTION` | true / false | false | 1 (Shadow) |
| `USE_PHASE_G_IDENTITY_RESOLUTION` | true / false | false | 3 (Activate) |
| `MERGE_WRITES_ENABLED` | disabled / shadow / active | disabled | 3 (requires active) |
| `USE_CLAIM_FACTS` | true / false | false | 4 (Optional) |

## Key Files

| File | Purpose |
|------|---------|
| `storage/entity_identity_store.py` | Core entity identity CRUD (669 lines) |
| `utils/phase_g_entity_resolver.py` | 4-stage resolution pipeline |
| `storage/claim_fact_store.py` | Bi-temporal fact store (SCD-2) |
| `storage/merge_cascade.py` | Cross-table cascade merge |
| `storage/merge_rollback.py` | Reverse cascade for rollback |
| `monitoring/phase_g_readiness.py` | Readiness gate (5 checks) |
| `storage/migrations/v19-v21` | Entity tables DDL |

## Troubleshooting

### "Blocking index empty" warning
Run the pipeline once with `USE_SHADOW_ENTITY_RESOLUTION=true` to populate.

### "Merge suggestion rejection rate exceeds threshold"
Review the rejected suggestions. Common causes:
- Fuzzy matching too aggressive (lower `FUZZY_THRESHOLD` from 90)
- Similar company names across different industries
- Inconsistent canonical keys across collectors

### "Signals point to merged-away entity_ids"
This indicates cascade_merge didn't fully re-parent signals. Fix:
```sql
UPDATE signals SET company_id = (
  SELECT to_entity_id FROM entity_migrations
  WHERE from_entity_id = signals.company_id
) WHERE company_id IN (
  SELECT from_entity_id FROM entity_migrations
);
```

### Merge rollback fails with "drift detected"
The entity was modified after merge (e.g., new signals added). Manual intervention required:
1. Check what changed: `python run_pipeline.py entity-audit --days 1`
2. Either accept the current state or manually reverse the specific changes
