# KG Post-Window Validation Runbook

**Earliest execution:** 2026-03-24 (after observation window closes)
**Status:** EXECUTION-FREE until then — this is a plan only

---

## Prerequisites

1. Step 4A observation window closed (Mar 23 end-of-day)
2. Working DB snapshot copy (**never the live `signals.db`**)
3. `graph etl` writes KG runs, nodes, and edges — it is a write operation, not read-only

```bash
# MANDATORY: Create snapshot copy BEFORE any KG operations
# All commands below use this copy, never the live DB
cp signals.db signals.db.kg-validation-snapshot
```

**Hard constraint:** Every command in this runbook targets `signals.db.kg-validation-snapshot`. If any step accidentally targets `signals.db`, stop immediately.

---

## Phase A: Dry-Run Acceptance (read-only)

### A1. ETL dry-run

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot etl --mode full --dry-run --json
```

**Expected output fields:**
- `company_nodes` > 0 (should approximate company_files count)
- `signal_nodes` > 0 (should approximate signals count)
- `detected_by_edges` > 0
- `status` = "dry_run"
- `warnings` = [] (empty)

**Failure criteria:** Any non-zero warning count or zero company/signal nodes.

### A2. ETL status (before build)

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot etl-status --json
```

**Expected:** `node_counts` and `edge_counts` empty, `last_run` null, `source_tables` populated.

---

## Phase B: Full ETL on Snapshot

### B1. Run full ETL

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot etl --mode full --json
```

**Capture:** Full JSON output. Save to `artifacts/kg-validation/etl-report.json`.

### B2. Graph stats

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot stats --json
```

**Do not use hardcoded expected ranges.** The plan's original estimates (657 signals, ~380 companies, ~790 nodes, ~1700 edges) are stale. The DB has 612 signals and 503 company_files as of 2026-03-20.

Instead, capture actual counts and validate:
- `live_nodes` > `company_files count` (companies + signals + locations + ontology seeds)
- `live_edges` > `signals count` (detected_by should approximate signals-with-matching-companies)
- `nodes_by_type` includes company, signal, and at least one location
- `edges_by_type` includes detected_by, has_evidence; in_sector and located_in may be zero if no extractor yields those

### B3. Validation checks

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot validate --json
```

**All 7 checks must pass:** orphan_edges, tombstone_edges, schema_conformance, referential_fk, symmetry, cycle_safety, view_liveness.

### B4. ETL status (after build)

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot etl-status --json
```

**Expected:** Non-empty node/edge counts, last_run with status="completed".

---

## Phase C: Query Sanity Checks

### C1. Evidence chain — pick a known company

```bash
# Pick a company_id from company_files
python -m ops.cli graph --db signals.db.kg-validation-snapshot evidence <company_id> --json
```

**Check:** signals list non-empty, source_count > 0, weighted_score in [0, 1].

### C2. Data gaps

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot gaps --min-evidence 2 --json
```

**Check:** Results are companies with < 2 unique source_apis. Cross-reference a few against `signals` table.

### C3. Conflicts

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot conflicts --json
```

**Check:** If non-empty, verify the `field` is "stage" or "sector" and `values` dict has > 1 distinct value.

### C4. Evidence ranking

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot rank --min-sources 2 --limit 20 --json
```

**Check:** Results sorted by `evidence_strength` descending. Multi-source companies rank higher than single-source.

### C5. Ego graph

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot ego <company_id> --depth 2 --out artifacts/kg-validation/ego-sample.json
```

**Check:** `node_count` > 1, `edge_count` > 0. JSON is valid and contains nodes/edges arrays.

---

## Phase D: Idempotency Check

### D1. Run ETL a second time

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot etl --mode full --json
```

**Expected:** `nodes_tombstoned` = 0, `edges_expired` = 0 (deterministic edge IDs should prevent churn).

### D2. Validate again

```bash
python -m ops.cli graph --db signals.db.kg-validation-snapshot validate --json
```

**All 7 checks must still pass.**

---

## Acceptance Criteria

| Check | Pass condition |
|---|---|
| A1 dry-run | Non-zero counts, no warnings |
| B1 full ETL | status="completed" |
| B3 validation | 7/7 pass |
| C1-C5 queries | Non-empty results, reasonable values |
| D1 idempotency | Zero tombstoned/expired on re-run |
| D2 post-idempotency validation | 7/7 pass |

**If any check fails:** Do not proceed. File a bug against the specific phase, fix on a branch, re-run from Phase A.

---

## Post-Acceptance

- Delete `signals.db.kg-validation-snapshot`
- Save `artifacts/kg-validation/` outputs as evidence
- Update MEMORY.md with KG validation status
- Decide on operator playbook scope (item 9 from review queue)
