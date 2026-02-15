# Task Plan: M5 -> M6 -> M7 Sprint (Post-M4)

## Goal
Containerize the engine for reproducible deploys (M5), safely activate Phase G entity
resolution (M6), then validate the full pipeline end-to-end for production readiness (M7).

## Current Phase
Planning -- awaiting user approval

---

## Context

**Completed:** G0 + M1 + M2 + M3 + M4 (commit dd2b665 on main)
**State:** 6861+ tests, 551+ regression gate, 41 migrations, 20 feature flags all disabled
**Deployment target:** Systemd VM today; Docker-ready after M5
**Activation runbook:** `docs/runbooks/feature-activation.md` (4-step sequence documented)
**Pre-merge gate:** `pytest tests/api/ tests/integration/ tests/workflows/test_batch_publisher.py tests/smoke/ --tb=short -q`

**Supporting docs:**
- `docs/plans/m5-m7-findings.md` -- 12 findings from gap audit
- `docs/plans/m5-m7-progress.md` -- session log

---

## Phase M5: Containerization & Shadow Observability
**Estimated effort:** 3-4 days
**Purpose:** Package the engine for reproducible deployment; add tooling to observe
shadow feature behavior before activating them in production.

### Task M5.1: Dockerfile (multi-stage, production-ready)
**File:** `Dockerfile`

Multi-stage build:
- **Stage 1 (builder):** Python 3.11 slim, install requirements, copy source
- **Stage 2 (runtime):** Python 3.11 slim, copy installed packages + source, non-root user

```dockerfile
# Stage 1: Build
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
COPY . .

# Stage 2: Runtime
FROM python:3.11-slim
RUN groupadd -r harmonic && useradd -r -g harmonic harmonic
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --from=builder /app .
RUN chown -R harmonic:harmonic /app
USER harmonic
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python scripts/healthcheck_startup.py || exit 1
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Constraints:**
- SQLite WAL mode requires single-writer (no replicas sharing DB file)
- `signals.db` must be on a persistent volume (not baked into image)
- `.env` file bind-mounted at runtime (not in image)

### Task M5.2: Docker Compose for development
**File:** `docker-compose.yml`

```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./signals.db:/app/signals.db
      - ./.env:/app/.env:ro
    env_file: .env
    healthcheck:
      test: ["CMD", "python", "scripts/healthcheck_startup.py"]
      interval: 30s
      timeout: 5s
      retries: 3
```

No external services (Prometheus/Grafana deferred -- existing OpenMetrics endpoint
is scrape-ready but no need to deploy scrapers until operator demand exists).

### Task M5.3: .dockerignore
**File:** `.dockerignore`

Exclude: `.git`, `*.db`, `.env`, `models/`, `*.pyc`, `__pycache__`, `.worktrees/`,
`tests/`, `docs/`, `*.csv`, `*.jsonl`, `*.json` (data files), `NUL`, `*.backup*`.

### Task M5.4: CI Docker build step
**File:** `.github/workflows/regression-gate.yml` (modify existing)

Add a `docker-build` job that runs after the `regression` job:
- `docker build -t harmonic:${{ github.sha }} .`
- No push (push deferred to M7 deployment automation)
- Validates the image builds cleanly on every PR

### Task M5.5: Shadow observability CLI command
**File:** `run_pipeline.py` (add `shadow-report` subcommand)

Purpose: Let operators evaluate shadow feature behavior without raw SQL.

```
python run_pipeline.py shadow-report --days 7
```

Output sections:
1. **LLM Classification Shadow** (if `LLM_THESIS_MODE=shadow`):
   - Total signals classified, agreement rate with keyword scorer
   - Top 5 disagreements (keyword>=0.7 AND llm<0.4, or vice versa)
   - Rate limit headroom (RPM/RPD vs limits)

2. **ML Classification Shadow** (if `ML_ENABLEMENT=shadow`):
   - Total ML predictions, rescue count (ML overrode keyword FP)
   - Precision/recall vs keyword baseline

3. **Entity Resolution Shadow** (if `USE_SHADOW_ENTITY_RESOLUTION=true`):
   - Candidate merges found, match types (fuzzy_name, shared_domain, etc.)
   - Top 5 proposed merges with confidence scores

4. **Merge Writes Shadow** (if `MERGE_WRITES_ENABLED=shadow`):
   - Proposals created, auto-approved count
   - Pending manual review count

**Data sources:** `thesis_classifications`, `merge_suggestions`, `entity_aliases`,
`entity_blocking_index` tables (all already populated in shadow mode).

### Task M5.6: Feature flag interaction smoke tests
**File:** `tests/smoke/test_feature_flag_interactions.py`

Test key flag combinations don't crash on startup or basic operations:
- [ ] All flags at defaults (disabled) -- smoke suite passes
- [ ] Step 1 flags (shadow quartet) -- API starts, /health returns 200
- [ ] Step 2 flags (low-risk trio) -- API starts, /health returns 200
- [ ] Step 3 flags (write trio) -- API starts, /health returns 200
- [ ] Step 4 flags (batch pair) -- API starts, /health returns 200
- [ ] All flags active simultaneously -- API starts, /health returns 200
- [ ] Conflicting flag: `DELIVERY_MODE=staging_only` + `BULK_TRIAGE_ENABLED=active` -- 423 on triage write

### Task M5.7: Update .env.example
**File:** `.env.example` (modify existing)

Add:
- `STRICT_CONFIG_VALIDATION=false` (with comment explaining M1 addition)
- Docker-specific section: `API_PORT=8000`, `DB_VOLUME_PATH=./signals.db`
- Shadow reporting section: comment block explaining shadow-report usage

### Task M5.8: Tests for Docker health check
**File:** `tests/smoke/test_docker_healthcheck.py`

- [ ] `scripts/healthcheck_startup.py` exits 0 when API is running
- [ ] `scripts/healthcheck_startup.py` exits 1 after max retries when API is down
- [ ] Respects HEALTHCHECK_RETRIES and HEALTHCHECK_DELAY env vars

### Task M5.9: Deployment runbook
**File:** `docs/runbooks/docker-deployment.md`

Cover:
- Building the image locally
- Running with docker-compose (dev)
- Systemd + Docker (production VM)
- Volume management for signals.db
- Upgrading (pull new image, stop, migrate, start)
- Rollback (tag-based, keep previous image)

**M5 Gate:** Docker image builds cleanly in CI; `docker-compose up` starts API with
passing health check; shadow-report CLI returns formatted output; feature flag
interaction tests green.

---

## Phase M6: Phase G Entity Resolution Activation
**Estimated effort:** 3-4 days
**Purpose:** Safely activate the largest disabled feature system (entity identity
resolution) with proper gates, dry-run tooling, and audit capabilities.

### Task M6.1: Phase G activation gate
**File:** `monitoring/activation_gate.py` (extend existing)

Add Phase G-specific readiness checks to the existing activation gate:

```python
PHASE_G_CHECKS = {
    "entity_tables_exist": "entity_aliases, entity_migrations, entity_key_aliases tables present",
    "blocking_index_populated": "entity_blocking_index has >0 rows (shadow data)",
    "shadow_merge_quality": "shadow merge suggestions have <10% rejection rate",
    "no_orphaned_entities": "all entity_ids in signals resolve to valid roots",
    "claim_facts_consistent": "claim_facts table has no contradictions",
}
```

Expose via:
- `GET /health/activation-readiness?step=phase-g`
- `python run_pipeline.py activation-check --step phase-g`

### Task M6.2: Entity merge dry-run CLI
**File:** `run_pipeline.py` (add `entity-merge-preview` subcommand)

```
python run_pipeline.py entity-merge-preview --limit 10
```

Shows what would happen if Phase G merges were applied:
- Candidate merge pairs (from `merge_suggestions` with status=proposed)
- Winner entity_id (lexmin) and loser entity_id
- Signals that would be re-parented
- Review items affected
- Company files affected

**No mutations.** Read-only preview using existing `cascade_merge()` logic in dry-run mode.

### Task M6.3: Phase G integration tests
**File:** `tests/integration/test_phase_g_lifecycle.py`

Full entity lifecycle with real SQLite:
- [ ] Create entity via `entity_id_for_seed()` with domain key
- [ ] Add aliases via `register_alias()`
- [ ] Create second entity with overlapping alias
- [ ] Trigger merge via `merge_entities()` -- verify lexmin winner
- [ ] Verify `entity_migrations` row created
- [ ] Verify transitive resolution: `resolve_entity_root()` returns winner
- [ ] Verify signals re-parented (company_id updated)
- [ ] Verify review_items re-parented
- [ ] Verify company_files re-parented
- [ ] Verify drift fingerprint computed post-merge
- [ ] Rollback: verify `rollback_merge()` restores original state
- [ ] LIFO chain: multiple merges respect undo ordering

### Task M6.4: Merge audit report CLI
**File:** `run_pipeline.py` (add `entity-audit` subcommand)

```
python run_pipeline.py entity-audit --days 7
```

Shows recent entity operations:
- Entity migrations (from_entity_id -> to_entity_id, merge_reason, timestamp)
- LIFO chain integrity (any broken chains?)
- Orphaned entity_ids (signals pointing to merged-away entities)
- Drift fingerprint changes post-merge

### Task M6.5: Phase G activation runbook
**File:** `docs/runbooks/phase-g-activation.md`

Sequence:
1. **Pre-check:** Run `activation-check --step phase-g` -- must be "ready"
2. **Shadow pilot (48h):** `USE_SHADOW_ENTITY_RESOLUTION=true` -- observe matches
3. **Review:** Run `shadow-report` + `entity-merge-preview` -- validate merge quality
4. **Activate:** `USE_PHASE_G_IDENTITY_RESOLUTION=true` -- merges applied
5. **Monitor:** Run `entity-audit --days 1` daily for first week
6. **Claim facts (optional):** `USE_CLAIM_FACTS=true` after 1 week clean
7. **Rollback:** Set flags to false; run `entity-audit` to verify no corruption

### Task M6.6: Canonical key aliases cleanup
**File:** `storage/signal_store.py`

Remove orphaned `canonical_key_aliases` table creation (early Phase G attempt,
superseded by `entity_key_aliases` in migrations 19-21).

**Prerequisite:** Confirm no code references `canonical_key_aliases` except the
CREATE TABLE statement. Use grep to verify.

### Task M6.7: Phase G unit tests for activation gate
**File:** `tests/monitoring/test_phase_g_activation_gate.py`

- [ ] Tables missing -> blocked
- [ ] Blocking index empty -> warn (no shadow data yet)
- [ ] Shadow merge rejection rate >10% -> blocked
- [ ] All checks pass -> ready
- [ ] API endpoint returns correct JSON for phase-g step
- [ ] CLI exit code 0 on ready, 1 on blocked

### Task M6.8: Feature flag interaction tests (Phase G specific)
**File:** `tests/integration/test_phase_g_flag_interactions.py`

- [ ] Phase G + V2 scoring: merge doesn't corrupt confidence scores
- [ ] Phase G + drift monitoring: drift fingerprint updates after merge
- [ ] Phase G + thin files: merged entity's company_files consolidated
- [ ] Phase G + batch publish: merged signals use winner's canonical key in Notion

**M6 Gate:** Phase G integration tests green; activation gate reports "ready" with
shadow data populated; dry-run merge preview shows correct candidates; merge audit
CLI shows clean LIFO chain; no orphaned entity references.

---

## Phase M7: Production Readiness Gate
**Estimated effort:** 3-4 days
**Purpose:** Validate the full pipeline end-to-end and establish production readiness
criteria before executing Steps 3-4 of the activation runbook.

### Task M7.1: E2E pipeline integration test
**File:** `tests/e2e/test_full_pipeline_roundtrip.py`

Full pipeline round-trip with mock collectors and mock Notion:
- [ ] Collect: 3 mock signals from 2 collectors (1 overlapping for multi-source)
- [ ] Store: signals written to DB with canonical keys
- [ ] Dedupe: overlapping signal merged, suppression cache updated
- [ ] Verify: multi-source signal gets HIGH confidence, single-source gets MEDIUM
- [ ] Identity: Phase G resolves entities (if enabled)
- [ ] Push: HIGH -> "Source" status in Notion, MEDIUM -> "Tracking"
- [ ] Verify: `notion_page_id` persisted, audit_events recorded
- [ ] Suppress: re-running pipeline doesn't re-push same signals

### Task M7.2: Batch publish canary tool
**File:** `run_pipeline.py` (add `canary-publish` subcommand)

```
python run_pipeline.py canary-publish --items 3 --confirm
```

Controlled publish of N approved signals to Notion:
1. Select top N approved items from review queue (by confidence, descending)
2. Preview: show company name, canonical key, confidence, signal types
3. Require `--confirm` flag (no accidental publishes)
4. Push to Notion, report success/failure per item
5. Log to audit_events with `action_type=canary_publish`

### Task M7.3: Performance SLO benchmarks
**File:** `tests/performance/test_production_slos.py`

Benchmark with realistic data volumes:
- [ ] Pipeline throughput: process 100 signals in < 60s
- [ ] API response time: GET /health < 200ms p95
- [ ] API response time: GET /batch/{id}/preview < 500ms p95
- [ ] SQLite write: 50 concurrent signal inserts complete without SQLITE_BUSY
- [ ] Entity resolution: merge 10 entity pairs in < 5s
- [ ] Batch commit: 20-item batch commits in < 30s (with mock Notion)

### Task M7.4: Production readiness checklist (automated)
**File:** `monitoring/production_readiness.py`

Automated checklist that can run as CLI or API endpoint:

```python
CHECKS = [
    ("config_validation", "validate_config() returns no errors"),
    ("smoke_suite", "smoke tests pass"),
    ("activation_gate", "all activation steps report ready or warn"),
    ("phase_g_gate", "Phase G activation gate reports ready"),
    ("db_integrity", "PRAGMA integrity_check = ok"),
    ("migration_current", "schema version matches latest migration"),
    ("notion_reachable", "Notion API responds (if DELIVERY_MODE != staging_only)"),
    ("canary_fresh", "latest canary run < 24h old"),
    ("no_critical_alerts", "no open critical drift alerts"),
    ("regression_green", "regression gate last passed < 24h ago"),
]
```

Expose via:
- `python run_pipeline.py production-readiness`
- `GET /health/production-readiness`

### Task M7.5: Collector resilience tests
**File:** `tests/integration/test_collector_resilience.py`

Test graceful degradation when collectors fail:
- [ ] Network timeout: collector returns empty results, pipeline continues
- [ ] Rate limit (429): collector backs off, logs warning, returns partial results
- [ ] Malformed response: collector logs error, returns empty, pipeline continues
- [ ] API key invalid (401/403): collector disabled, pipeline continues with others
- [ ] All collectors fail: pipeline completes with 0 signals, no crash

### Task M7.6: Error recovery tests
**File:** `tests/integration/test_error_recovery.py`

Test recovery from partial failures:
- [ ] Batch commit: 1 of 5 items fails Notion push -> batch status `committed_with_errors`
- [ ] Entity merge: merge fails mid-cascade -> transaction rolls back, no corruption
- [ ] Pipeline: signal store write fails -> signal skipped, pipeline continues
- [ ] API: request during DB migration -> 503 (not 500)
- [ ] Notion transport: connection drops mid-push -> retry or clean error

### Task M7.7: Production deployment guide
**File:** `docs/runbooks/production-deployment.md`

Cover both deployment paths:
1. **Systemd (current):** Updated service file, health probe, log rotation
2. **Docker (new):** Build, run, volume management, upgrades, rollback

Include:
- Pre-deployment checklist (run production-readiness check)
- Migration procedure (backup DB, run migrations, verify)
- Post-deployment verification (smoke suite, canary publish)
- Rollback procedure (tag-based for Docker, git-based for Systemd)

### Task M7.8: Operator training documentation
**File:** `docs/operator-guide.md` (extend existing, 159 lines)

Add sections for:
- Daily operations (run pipeline, check health, review shadow report)
- Weekly operations (canary publish, entity audit, drift review)
- Activation progression (when/how to advance activation steps)
- Troubleshooting guide (common errors, resolution steps)

**M7 Gate:** E2E pipeline test green with full collect->push round-trip; canary
publish tool successfully pushes to Notion staging; performance SLOs pass;
production readiness checker reports all green; collector resilience tests green;
error recovery tests green; deployment guide reviewed by operator.

---

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| M5 before M6 | Docker enables consistent dev/staging environments for Phase G testing |
| No Prometheus deployment in M5 | OpenMetrics endpoint already exists; scraper deployment is operator-driven |
| Phase G gets its own milestone (M6) | Largest disabled system; needs dedicated gates and dry-run tooling |
| E2E test in M7 (not M5) | Requires Phase G activation (M6) to test full pipeline path |
| Shadow-report CLI over dashboard | CLI is faster to implement and works in all environments |
| Production readiness as automated check | Manual checklists drift; code enforces consistency |
| No new Prometheus/Grafana infra | Existing OpenMetrics + CLI reporting is sufficient for 1-3 person team |
| canonical_key_aliases cleanup in M6 | Natural time to clean up -- Phase G activation is the trigger |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet -- planning phase) | | |

## Files to Create
| File | Purpose | Milestone |
|------|---------|-----------|
| `Dockerfile` | Multi-stage production image | M5.1 |
| `docker-compose.yml` | Dev environment | M5.2 |
| `.dockerignore` | Build exclusions | M5.3 |
| `tests/smoke/test_feature_flag_interactions.py` | Flag combo smoke tests | M5.6 |
| `tests/smoke/test_docker_healthcheck.py` | Health probe unit tests | M5.8 |
| `docs/runbooks/docker-deployment.md` | Docker deployment guide | M5.9 |
| `tests/integration/test_phase_g_lifecycle.py` | Phase G full lifecycle tests | M6.3 |
| `tests/monitoring/test_phase_g_activation_gate.py` | Phase G gate tests | M6.7 |
| `tests/integration/test_phase_g_flag_interactions.py` | Phase G + other flags | M6.8 |
| `docs/runbooks/phase-g-activation.md` | Phase G activation sequence | M6.5 |
| `tests/e2e/test_full_pipeline_roundtrip.py` | E2E pipeline test | M7.1 |
| `monitoring/production_readiness.py` | Automated readiness checklist | M7.4 |
| `tests/performance/test_production_slos.py` | Production-scale SLO benchmarks | M7.3 |
| `tests/integration/test_collector_resilience.py` | Collector failure handling | M7.5 |
| `tests/integration/test_error_recovery.py` | Partial failure recovery | M7.6 |
| `docs/runbooks/production-deployment.md` | Full deployment guide | M7.7 |

## Files to Modify
| File | Change | Milestone |
|------|--------|-----------|
| `.github/workflows/regression-gate.yml` | Add Docker build job | M5.4 |
| `run_pipeline.py` | Add `shadow-report` subcommand | M5.5 |
| `.env.example` | Add STRICT_CONFIG_VALIDATION + Docker vars | M5.7 |
| `monitoring/activation_gate.py` | Add Phase G readiness checks | M6.1 |
| `run_pipeline.py` | Add `entity-merge-preview` + `entity-audit` subcommands | M6.2, M6.4 |
| `storage/signal_store.py` | Remove orphaned `canonical_key_aliases` | M6.6 |
| `run_pipeline.py` | Add `canary-publish` + `production-readiness` subcommands | M7.2, M7.4 |
| `docs/operator-guide.md` | Add daily/weekly ops + troubleshooting sections | M7.8 |

## Verification Plan
1. **M5:** `docker build` succeeds in CI; `docker-compose up` starts healthy API;
   `shadow-report` CLI returns formatted output; flag interaction tests green;
   regression gate still 551+ passing
2. **M6:** Phase G integration tests green; activation gate "ready" with shadow data;
   dry-run merge preview correct; entity audit shows clean state; canonical_key_aliases
   table removed without regressions
3. **M7:** E2E pipeline test green; canary publish succeeds to Notion staging;
   SLO benchmarks pass; production readiness check all-green; collector resilience
   and error recovery tests green
4. **Full sprint:** All new tests pass; existing tests still collect cleanly; smoke
   suite green; pre-merge regression gate 551+ passing throughout
