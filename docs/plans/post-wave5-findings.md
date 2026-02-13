# Findings: Post-Wave 5 Milestone Assessment

## Date: 2026-02-12
## Context: Wave 5 fully committed to main (856689f, 301 Wave-5 tests; ~6709 total project tests)

---

## Finding 1: Wave 5 is FULLY COMPLETE

All planned deliverables verified via file existence check:

| Component | File | Tests |
|-----------|------|-------|
| SPC Monitor | `monitoring/spc_monitor.py` | 15 |
| Alert Escalation | `monitoring/alert_escalation.py` | 13 |
| Drift Recommendations | `monitoring/drift_recommendations.py` | 5 |
| Drift Dashboard | `dashboard/views/drift_monitoring.py` | 8 |
| Daily Aggregator | `monitoring/daily_aggregator.py` | 12 |
| Drift CLI (9 commands) | `run_pipeline.py` lines 2319-6337 | CLI tests |
| Feature Guard | `workflows/feature_guards.py` | guard tests |
| v41 Migration | `storage/migrations/v41_drift_monitoring.py` | 8 downgrade |
| E2E Drift Workflow | `tests/e2e/test_drift_workflow.py` | 6 |
| Wave 5 SLOs | `tests/performance/test_wave5_slos.py` | 6 |
| 3 Runbooks | `docs/runbooks/drift-*.md`, `spc-*.md`, `canary-*.md` | - |

---

## Finding 2: 10+ Feature Systems Built But Disabled

The codebase has extensive "dark" functionality behind feature flags:

**Shadow-capable (3-mode: disabled/shadow/active):**
- `LLM_THESIS_MODE` — Gemini thesis classification
- `ML_ENABLEMENT` — supervised ML classifier
- `MERGE_WRITES_ENABLED` — entity merge writes
- `V2_ENABLEMENT` — YAML policy scoring

**Binary (disabled/active):**
- `BULK_TRIAGE_ENABLED`, `HUNTER_PROMOTE_ENABLED`, `DRIFT_MONITORING_ENABLED`

**Boolean flags:**
- `USE_PHASE_G_IDENTITY_RESOLUTION`, `USE_CLAIM_FACTS`, `USE_THIN_FILES`
- `USE_SHADOW_ENTITY_RESOLUTION`
- `ENABLE_EXIT_PREDICTOR`, `ENABLE_INVESTOR_MATCHING`
- `ENABLE_FUNCTIONAL_SCHEMA`, `ENABLE_WARM_INTRO_ENRICHMENT`

---

## Finding 3: Production Readiness = 6.5/10 (CORRECTED)

**Strong:** Deployment (8/10), Monitoring (7/10), Testing (7/10), Operations (7/10)
**Weak:** Dependencies (5/10), CI/CD (6/10), Security (6/10), Docs (6/10)

**Corrections from reviewer:**
- `docs/operator-guide.md` (159 lines) EXISTS — needs consolidation, not creation
- `docs/architecture-overview.md` (162 lines) EXISTS — needs gap-fill, not creation
- Total test count is 6709 (not 2009) with 1 collection error

**Revised top 5 blocking gaps:**
1. Config validator not wired into startup (silent misconfig risk)
2. No container support (Docker/compose)
3. No Prometheus/Grafana (external observability)
4. No startup health probes (systemd blind after launch)
5. Batch publish is dry-run only (deals never reach Notion CRM)

---

## Finding 4: Critical TODOs in Production Code

| Location | Issue | Impact |
|----------|-------|--------|
| `api/routers/batch.py:193` | NotionPusher not wired (dry-run only) | Batch publish broken |
| `api/routers/entities.py:260` | No count query for pagination | Incorrect totals |
| `api/routers/companies.py:143` | No count query for pagination | Incorrect totals |
| `discovery_engine/curated_scout.py:410` | Enrichment not wired | Missing enrichments |
| `utils/config_validator.py` | Not called at startup | Silent misconfig |

---

## Finding 5: Stub/Placeholder Implementations

These files exist but contain placeholder logic:
- `profilers/pdf_profiler.py` — "TODO: Real Gemini integration"
- `intelligence/health_classifier.py` — returns default result
- `enrichment/tech_stack.py`, `community_metrics.py`, `brand_sentiment.py` — placeholders
- `collectors/plugandplay.py`, `g2crowd.py`, `capterra.py` — stubs

---

## Finding 6: Phase G Entity Identity — NOT Dormant (CORRECTED)

669-line `entity_identity_store.py` is fully implemented and ACTIVELY WIRED in
`workflows/pipeline.py:575` behind `USE_PHASE_G_IDENTITY_RESOLUTION` flag. It has
977 lines of tests across 2 dedicated files:
- `tests/utils/test_phase_g_entity_resolver.py` (522 lines)
- `tests/storage/test_phase_g_identity_schema.py` (455 lines)

Plus coverage in `test_pipeline_shadow.py`. This is NOT dormant code — it's a fully
tested system behind a feature flag. Activation is a shadow pilot, not an audit.

Orphaned `canonical_key_aliases` cleanup should be DEFERRED until post-activation stability.

---

## Finding 7: Config Validator Gap

`utils/config_validator.py` (302 lines) validates 4 delivery modes, 4 write features,
12 thresholds, and 2 Notion keys. But it has no `main()` entry point and is never called
at pipeline startup. Silent misconfiguration is a real risk in production.

**Validator API (confirmed):**
```python
from utils.config_validator import validate_config, print_config_report
issues = validate_config()        # Returns List[ConfigIssue]
has_errors = print_config_report(issues)  # Prints + returns bool
```

**ConfigIssue:** `dataclass(level: str, key: str, message: str)` where level = error|warning|info

**Existing tests:** 27 unit tests + 6 integration tests — all passing

**Insertion points:**
- API: `api/main.py` line 55-57 (after existing `startup_check()`, before store init)
- CLI: `run_pipeline.py` line ~6096 (after `setup_logging()`, before command dispatch)

**Existing `startup_check()`** in `utils/logging_config.py:118-128` only checks DB file
existence. Must be preserved — config validation is additive, not a replacement.

---

## Finding 8: Batch Publish Wire-up — Complete Dependency Map (NEW)

**The problem:** `api/routers/batch.py:193` always passes `pusher=None` to `commit_batch()`.
When `dry_run=False`, `batch_publisher.py:369` raises `BatchError("NotionPusher required")`.

**NotionPusher constructor:**
```python
NotionPusher(
    signal_store=app.state.store,                    # Already available
    notion_connector=create_connector_from_env(),    # Needs NOTION_API_KEY + NOTION_DATABASE_ID
    verification_gate=VerificationGate(),            # No deps, uses defaults
    dry_run=body.dry_run,                            # From request
)
```

**Factory:** `connectors.notion_connector_v2.create_connector_from_env()` reads
`NOTION_API_KEY` and `NOTION_DATABASE_ID` from env, raises `ValueError` if missing.

**Delivery policy guard:** `commit_batch()` already calls
`assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)` at line 367.
Requires `DELIVERY_MODE=batch_publish` or higher.

**TOCTOU guard:** SHA256[:16] hash of sorted `review_ids`, computed at preview,
validated at commit (batch.py lines 180-190). Must be preserved.

**REVISED approach (v2):** App-scoped NotionConnector/Transport in lifespan (not per-request).
See Finding 10 for rationale.

**Test pattern:** Mock `pusher.process_single_prospect` as `AsyncMock` returning
`PushResult` with `pushed=True, notion_page_id="page-123"`. Patch
`DELIVERY_MODE=batch_publish` via `@patch.dict(os.environ, ...)`.

---

## Finding 9: Baseline Test State (NEW)

Snapshot (2026-02-13, commit 856689f):
- pytest collection reports ~6709 tests, 1 collection error
- Command: `pytest --collect-only -q`
- Known pre-existing failures: tests in `test_confidence_routing.py` (DELIVERY_MODE=staging_only)

G0 baseline gate must:
1. Fix the collection error(s)
2. Generate baseline artifact with commit hash + date + failing node IDs (not hardcoded counts)
3. Establish smoke suite: API startup + /health + batch dry-run + drift check

---

## Finding 10: v2 Reviewer Findings -- Critical Evaluation (NEW)

### Evaluated 2026-02-13

Nine findings from a second reviewer, evaluated against the codebase.

### F10.1 HIGH: NotionTransport lifecycle leak -- CONFIRMED, PLAN CHANGED

**Claim:** Per-request NotionPusher creates a NotionTransport that owns an
`httpx.AsyncClient` with `shutdown()` semantics. Abandoning it leaks connections.

**Verified:** `connectors/notion_transport.py` creates `httpx.AsyncClient` in `start()`,
requires explicit `await shutdown()` to call `aclose()`. `NotionConnector` does NOT
manage transport shutdown. `create_connector_from_env()` creates a transport internally
with no shutdown path exposed.

**Correct usage (pipeline.py):** Owns transport, calls `start()` at init, `shutdown()` at close.

**Decision change:** M3.1 now uses app-scoped connector/transport initialized in
`api/main.py` lifespan, shut down in lifespan teardown. Matches existing SignalStore
pattern. Per-request approach REJECTED.

Implementation:
```python
# In lifespan() startup:
try:
    transport = NotionTransport(api_key=os.environ["NOTION_API_KEY"])
    await transport.start()
    connector = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
        transport=transport,
    )
    app.state.notion_transport = transport
    app.state.notion_connector = connector
except (KeyError, ValueError):
    app.state.notion_transport = None
    app.state.notion_connector = None
    logger.warning("Notion not configured - batch commit unavailable")

# In lifespan() shutdown:
if app.state.notion_transport:
    await app.state.notion_transport.shutdown()
```

New test requirement: resource lifecycle test proving no leaked clients after
repeated batch commits.

### F10.2 HIGH: Hardcoded known failure count -- ACCEPTED

**Claim:** "6 pre-existing failures" is brittle and will drift.
**Verdict:** Correct. Replace with generated baseline artifact containing:
- Commit hash + date
- Exact failing test node IDs (e.g., `tests/workflows/test_confidence_routing.py::test_xxx`)
- Owner/reason for each exemption
- Delta review policy: any NEW failures vs baseline = gate failure

### F10.3 HIGH: tail -1 is shell-fragile on Windows -- ACCEPTED

**Claim:** Windows/PowerShell environment; `tail -1` won't work.
**Verdict:** Correct. This is a Windows-first project. Use:
- `pytest --collect-only -q 2>&1 | Select-Object -Last 1` (PowerShell)
- Or better: parse pytest JSON report (`--report-log`) for shell-agnostic approach
- Or: Python one-liner wrapper in smoke suite

### F10.4 MEDIUM: "Read-only activation" misnaming -- ACCEPTED

**Claim:** `USE_THIN_FILES=true` writes to `company_files` table; `V2_ENABLEMENT=live`
changes scoring behavior. "Read-only" is misleading.
**Verdict:** Correct. Rename to "Low-risk activation" in the activation runbook.
Add explicit note: "These flags may change persisted/runtime behavior and must be
canary-gated."

### F10.5 MEDIUM: Error contract underspecified for missing Notion config -- ACCEPTED

**Claim:** Error response for missing Notion env vars needs exact contract.
**Verdict:** Correct. Define:
- Status: `503 Service Unavailable` (not 500 -- Notion is an external dependency)
- Code: `NOTION_NOT_CONFIGURED`
- Detail: "Batch commit requires NOTION_API_KEY and NOTION_DATABASE_ID"
- Retry guidance: non-retryable (config issue, not transient)
- Test assertions: verify `status_code`, `detail.code`, and `detail.message` shape

**Note:** Changed from 500 to 503 -- missing external service config is a service
unavailability, not an internal error.

### F10.6 MEDIUM: Test ownership boundaries blurred -- ACCEPTED

**Claim:** M1.4 tests cover both API and CLI but live in one file.
**Verdict:** Correct. Split into:
- `tests/api/test_config_validation_wiring.py` -- API lifespan validation tests
- `tests/cli/test_config_validation_wiring.py` -- CLI startup validation tests
Each file tests: validator invoked, strict mode failure, existing checks preserved.

### F10.7 LOW: Volatile numeric claims -- ACCEPTED

**Claim:** "6709 tests" and "977 lines" will age quickly.
**Verdict:** Fair. Prefix with snapshot context:
"Snapshot (2026-02-13, 856689f): ~6709 tests" and link to command for live count.

### F10.8 LOW: Encoding artifacts -- ACCEPTED

**Claim:** Em-dashes and special characters may render as mojibake on some terminals.
**Verdict:** Fair for Windows-first project. Normalize to ASCII-safe: use `--` and `->`.

### F10.9 LOW: Line-number references are brittle -- ACCEPTED

**Claim:** Line numbers change with every edit; use function anchors.
**Verdict:** Correct. Replace:
- "api/main.py line 55-57" -> "api/main.py `lifespan()` function"
- "run_pipeline.py line ~6096" -> "run_pipeline.py `main()` function"
- "batch.py line 193" -> "batch.py `commit_batch_endpoint()` function"

---

## Finding 11: Architectural Decision Change -- App-Scoped Notion (NEW)

**Previous decision:** Per-request NotionPusher construction
**New decision:** App-scoped NotionConnector/Transport in lifespan

**Rationale:**
1. NotionTransport owns `httpx.AsyncClient` requiring explicit shutdown
2. Pipeline already uses this pattern correctly (own -> start -> use -> shutdown)
3. Matches how SignalStore is managed in API lifespan
4. Single connection pool shared across requests (better performance)
5. Graceful degradation: if Notion env vars missing, `app.state.notion_connector = None`

**Implications for batch.py:**
- No longer needs lazy imports or try/except for env vars
- Reads `request.app.state.notion_connector`
- If None and `dry_run=False`: return 503 NOTION_NOT_CONFIGURED
- NotionPusher constructed per-request but reuses the shared connector/transport

**New test:** Resource lifecycle test -- repeated batch commits don't leak clients.
