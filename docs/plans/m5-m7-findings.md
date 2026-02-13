# Findings: M5-M7 Milestone Planning

## Date: 2026-02-13
## Context: Post-M4 (commit dd2b665 on main; 6861+ tests; 551+ regression gate)

---

## Finding 1: Containerization Is the #1 Infrastructure Gap

The system runs on a bare Systemd VM. No Docker, Compose, or container config exists.
The M2 plan explicitly deferred this: "containerization is M5, not this sprint."

**Impact:** Manual deployments, no reproducibility, no staging environment parity.
**Files needed:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`
**Constraint:** Must support SQLite WAL mode (single-writer) -- not designed for multi-replica.

---

## Finding 2: Shadow Features Have No Observability Tooling

Step 1 of the activation runbook enables 4 shadow features (LLM, ML, merge writes,
entity resolution). But there is **no reporting tool** to see shadow results:

- LLM shadow classifications are logged but not aggregated
- ML shadow predictions go to `thesis_classifications` but no summary CLI
- Merge suggestions are proposed but not surfaced
- Entity resolution shadow matches are not reported

**Impact:** Operators can't evaluate shadow results without manual SQL queries.
**Gap:** Need a `shadow-report` CLI command or dashboard view.

---

## Finding 3: Phase G Entity Identity Is Fully Coded But Has No Activation Gate

Phase G (`entity_identity_store.py`, 669 lines) is the largest disabled feature system.
It has:
- Full merge logic (lexmin winner, transitive resolution)
- 5 migrations (19-21 + claim_facts)
- 977 lines of tests
- Shadow pilot mode via `USE_SHADOW_ENTITY_RESOLUTION`

But it lacks:
- A dedicated activation gate (M4's `activation_gate.py` doesn't check entity-specific health)
- Dry-run merge CLI (can't preview what would merge before enabling)
- Merge audit reporting (no CLI to inspect `entity_migrations` table)
- Integration test for full lifecycle: create -> alias -> merge -> resolve -> verify

---

## Finding 4: Feature Flag Interaction Matrix Is Undocumented

20 feature flags exist across 6 categories. Some interactions are risky:
- Phase G identity + V2 scoring: merges may change scoring inputs mid-batch
- Drift monitoring + shadow: drift flags written even in shadow, may confuse operators
- ML + LLM: ML is post-processing rescue, but both scoring in parallel is untested at scale

**No integration test validates flag combinations.**

---

## Finding 5: OpenMetrics Endpoint Exists But Is Home-Grown

`api/routers/health.py:516-580` serves `/api/v1/health/metrics` in OpenMetrics text format.
Built from `utils/instrumentation.py` (thread-safe counters + timers) + OpsMetricsCollector.

**Gaps:**
- No histogram bucketing (only gauge-style min/max/avg)
- No rate calculations (cumulative counters only)
- Per-process metrics (not shared across workers -- fine for single-worker deploy)
- Not using `prometheus_client` library (manual text construction)

**Sufficient for M5:** The existing endpoint works for Prometheus scraping. Adding
`prometheus_client` is an optimization, not a blocker.

---

## Finding 6: JWT Auth Is Internal-Only (No OAuth/OIDC)

`api/auth/rbac.py` implements real JWT-based RBAC with 11 permissions and 3 roles
(GP/ANALYST/READONLY). Default users seeded in dev mode. Sufficient for 1-3 person
internal team on VPC.

**Not a blocker for M5-M7.** OAuth2 is a future enhancement if external users need access.

---

## Finding 7: CI/CD Has Regression Gate But No Docker Build

`.github/workflows/regression-gate.yml` runs 551+ tests on PR. But no:
- Docker build step
- Image tagging/push
- Deployment trigger

**M5 should add** a Docker build step to the CI workflow (build + tag, no push until M7).

---

## Finding 8: Production Stubs Remain in Code

| Stub | Impact | Effort to Fix |
|------|--------|---------------|
| `profilers/pdf_profiler.py` Tier 2-3 | Scanned PDFs fail silently | 4-6h (Gemini API) |
| `intelligence/health_classifier.py` | Consumer health vertical non-functional | 4-6h |
| `collectors/capterra.py` `_parse_products()` | Returns empty list | 2-3h |
| `collectors/g2crowd.py`, `plugandplay.py` | Likely similar stubs | 2-3h each |
| `distribution/digest_builder.py` | No unsubscribe/preferences URLs | 2h |

**Decision:** These are enhancement-scope, not activation-scope. Phase G and pipeline
activation are higher priority than filling stubs.

---

## Finding 9: E2E Pipeline Test Coverage Is Thin

No single test exercises the full pipeline: collect -> store -> dedupe -> verify -> push.
Individual workflow tests exist (pipeline_wiring, batch_publisher, etc.) but the
end-to-end integration is untested.

**Risk:** Silent failures at stage boundaries (e.g., canonical key format mismatch
between collector output and Notion pusher input).

---

## Finding 10: Performance SLOs Are Defined But Not Benchmarked at Scale

`tests/performance/test_phase1a_slos.py` and `test_wave5_slos.py` exist but test
small datasets. No load testing baseline exists for:
- Pipeline throughput (signals/minute)
- API response time under concurrent load
- SQLite write contention under batch operations
- Entity resolution merge latency with large entity graphs

---

## Finding 11: .env.example Needs STRICT_CONFIG_VALIDATION

The `.env.example` (173 lines) covers most env vars but is missing:
- `STRICT_CONFIG_VALIDATION` (added in M1)
- Docker-specific vars (ports, volume paths)
- Container health check config

---

## Finding 12: Existing Runbooks Are Comprehensive (6 docs)

| Runbook | Coverage |
|---------|----------|
| `feature-activation.md` | 4-step activation sequence with gates |
| `migration-rollback.md` | v35-v38 rollback procedures |
| `drift-escalation.md` | Alert handling procedures |
| `canary-failure.md` | Golden set regression response |
| `spc-out-of-control.md` | Statistical process control alerts |
| `ci-regression-gate.md` | CI setup and check names |

**Gap:** No Phase G-specific runbook, no Docker deployment runbook, no E2E validation runbook.
