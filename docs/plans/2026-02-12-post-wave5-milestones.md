# Task Plan: G0 -> M1 -> M3 Sprint (Post-Wave 5) -- v2

## Goal
Establish a clean test baseline (G0), wire config validation into startup and create
an activation runbook (M1), then wire real NotionPusher into batch publish for actual
deal delivery to Notion CRM (M3).

## Current Phase
Phase 1 -- G0 Baseline Integrity Gate

---

## Context

Snapshot (2026-02-13, commit 856689f):
- pytest collection reports ~6709 tests, 1 collection error
- Command: `pytest --collect-only -q`

**Completed:** Waves 0-5 of v1.1.3 (41 migrations, commit 856689f on main)
**State:** All features built but DISABLED by default (10+ feature flags)
**Sprint scope:** G0 -> M1 -> M3 (~7-9 days)
**Deployment target:** Systemd VM (containerization is M5, not this sprint)
**Existing API contracts:** remain backward-compatible except M3 error-contract additions

**v1 corrections applied:**
- Phase G is actively wired (`pipeline.py` `_apply_phase_g_identity_resolution()`) with ~977 lines of tests
- OPS docs exist (`docs/operator-guide.md`, `docs/architecture-overview.md`)

**v2 refinements applied:**
- App-scoped Notion connector (not per-request) -- transport lifecycle safety
- Generated baseline artifact (not hardcoded failure counts)
- Shell-agnostic baseline commands (not `tail -1`)
- Activation stage renamed "Low-risk" (not "Read-only")
- Split API/CLI test ownership
- Tightened error contracts (503 for missing Notion config)
- Function anchors (not line numbers)

---

## Phase G0: Baseline Integrity Gate
**Time-box:** 1-2 days max
**Purpose:** Establish what "green" looks like before changing anything

### Tasks
- [ ] **G0.1** Fix collection error(s) -- run `pytest --collect-only -q`, identify and fix any collection errors to reach zero
- [ ] **G0.2** Generate baseline artifact -- create `tests/KNOWN_FAILURES.md` containing:
  - Commit hash and date
  - Exact failing test node IDs (e.g., `tests/workflows/test_confidence_routing.py::TestClassName::test_method`)
  - Owner/reason for each exemption (e.g., "DELIVERY_MODE=staging_only env dependency")
  - Delta review policy: any NEW failure vs this baseline = gate failure
- [ ] **G0.3** Create smoke suite -- `tests/smoke/test_smoke_suite.py`:
  - API startup + `/health` endpoint
  - Batch create + preview (dry-run only)
  - Drift check (read-only)
  - Config validation call (`validate_config()` returns results)
- [ ] **G0.4** Record baseline snapshot -- Python-parsed output from pytest collection (shell-agnostic, not `tail -1`)

**Gate:** `pytest --collect-only -q` exits 0 with zero collection errors; smoke suite green; baseline artifact committed
**Status:** pending

---

## Phase M1: Production Activation
**Estimated effort:** 3-4 days
**Purpose:** Wire config validation into startup, create activation runbook, prepare shadow-first rollout

### Task M1.0: Upgrade Notion key validation to delivery-mode-aware
**File:** `utils/config_validator.py`, `_validate_notion_keys()` function

Current behavior: missing Notion keys always emit `level="warning"`.
Problem: `STRICT_CONFIG_VALIDATION=true` only aborts on `level="error"`, so a
`DELIVERY_MODE=batch_publish` deployment with no Notion keys would start successfully
and fail at runtime.

**Fix:** When `DELIVERY_MODE` is `manual_publish`, `batch_publish`, or `auto_publish`,
missing `NOTION_API_KEY` or `NOTION_DATABASE_ID` must emit `level="error"` (not warning).

```python
def _validate_notion_keys() -> List[ConfigIssue]:
    issues: List[ConfigIssue] = []
    delivery_mode = os.environ.get("DELIVERY_MODE", "staging_only").strip().lower()
    needs_notion = delivery_mode in ("manual_publish", "batch_publish", "auto_publish")

    notion_key = os.environ.get("NOTION_API_KEY", "").strip()
    if not notion_key:
        level = "error" if needs_notion else "warning"
        issues.append(ConfigIssue(
            level=level,
            key="NOTION_API_KEY",
            message=f"Not configured (required for {delivery_mode})"
                    if needs_notion else "Not configured (Notion push will fail)",
        ))
    else:
        issues.append(ConfigIssue(level="info", key="NOTION_API_KEY", message="Configured"))

    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not notion_db:
        level = "error" if needs_notion else "warning"
        issues.append(ConfigIssue(
            level=level,
            key="NOTION_DATABASE_ID",
            message=f"Not configured (required for {delivery_mode})"
                    if needs_notion else "Not configured (Notion push will fail)",
        ))
    else:
        issues.append(ConfigIssue(level="info", key="NOTION_DATABASE_ID", message="Configured"))

    return issues
```

**Tests (add to existing `tests/utils/test_config_validator.py`):**
- [ ] `DELIVERY_MODE=staging_only` + no Notion keys -> warnings (not errors)
- [ ] `DELIVERY_MODE=batch_publish` + no Notion keys -> errors
- [ ] `DELIVERY_MODE=batch_publish` + Notion keys present -> info (no error)
- [ ] `DELIVERY_MODE=auto_publish` + no NOTION_DATABASE_ID -> error on that key

### Task M1.1: Wire config validator into API startup
**File:** `api/main.py`, `lifespan()` function (after existing `startup_check()`)

```python
# After existing startup_check()
from utils.config_validator import validate_config
config_issues = validate_config()
for issue in config_issues:
    log_level = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO}
    logger.log(log_level.get(issue.level, logging.INFO), "Config %s: %s", issue.key, issue.message)

if os.getenv("STRICT_CONFIG_VALIDATION", "false").lower() == "true":
    if any(i.level == "error" for i in config_issues):
        raise RuntimeError("Config validation failed -- see logs above")
```

**Preserve:** Existing `startup_check()` (DB file existence) is orthogonal -- keep as-is.

### Task M1.2: Wire config validator into CLI startup
**File:** `run_pipeline.py`, `main()` function (after `setup_logging()`)

```python
# After setup_logging()
from utils.config_validator import validate_config, print_config_report
config_issues = validate_config()
has_errors = print_config_report(config_issues)
if os.getenv("STRICT_CONFIG_VALIDATION", "false").lower() == "true" and has_errors:
    sys.exit(1)
```

### Task M1.3: Add STRICT_CONFIG_VALIDATION env var

**Behavior matrix:**
| STRICT_CONFIG_VALIDATION | Config errors present | Result |
|--------------------------|----------------------|--------|
| `false` (default) | yes | Log warnings, continue startup |
| `false` | no | Log info, continue startup |
| `true` | yes | Abort startup (RuntimeError / exit 1) |
| `true` | no | Log info, continue startup |

**Rollout plan:** Default remains `false` for this sprint. Target flip to `true` after
one clean activation cycle (M1.5 Step 1 complete without config issues).

Document in `docs/claude/environment-variables.md`.

### Task M1.4: Tests for config validation wiring

**Split by ownership:**
- `tests/api/test_config_validation_wiring.py` -- API lifespan tests:
  - [ ] `validate_config()` invoked during lifespan startup
  - [ ] `STRICT_CONFIG_VALIDATION=true` + error -> RuntimeError raised
  - [ ] `STRICT_CONFIG_VALIDATION=false` + error -> startup continues
  - [ ] Existing `startup_check()` still called (not replaced)

- `tests/cli/test_config_validation_wiring.py` -- CLI main tests:
  - [ ] `print_config_report()` called before command dispatch
  - [ ] `STRICT_CONFIG_VALIDATION=true` + error -> exit code 1
  - [ ] `STRICT_CONFIG_VALIDATION=false` + error -> command proceeds

### Task M1.5: Activation runbook
**File:** `docs/runbooks/feature-activation.md`

Progressive activation sequence:
```
Step 1: Shadow activation (observe, no mutations)
  LLM_THESIS_MODE=shadow
  ML_ENABLEMENT=shadow
  MERGE_WRITES_ENABLED=shadow
  USE_SHADOW_ENTITY_RESOLUTION=true
  -> Verify: canary baseline, SPC charts, no drift alerts
  -> Rollback: set all back to off/disabled
  -> Monitor: Gemini rate limits (15 RPM / 1500 RPD), ML inference latency

Step 2: Low-risk activation (48h after Step 1 clean)
  DRIFT_MONITORING_ENABLED=active
  USE_THIN_FILES=true          (NOTE: writes to company_files table)
  V2_ENABLEMENT=live           (NOTE: changes scoring behavior)
  -> Verify: drift check clean, thin files populating, canary stable
  -> Rollback: set back to disabled/false/shadow
  -> These flags may change persisted/runtime behavior -- canary-gate required

Step 3: Write activation (after Step 2 clean)
  DELIVERY_MODE=manual_publish
  BULK_TRIAGE_ENABLED=active
  HUNTER_PROMOTE_ENABLED=active
  -> Verify: manual push succeeds, triage actions persist
  -> Rollback: DELIVERY_MODE=staging_only

Step 4: Batch activation (after Step 3 clean)
  DELIVERY_MODE=batch_publish
  MERGE_WRITES_ENABLED=active
  -> Verify: batch commit creates Notion pages
  -> Rollback: DELIVERY_MODE=manual_publish, MERGE_WRITES_ENABLED=shadow
```

Each step includes: monitoring checklist, rollback command, success criteria, expected
write/mutation surface.

### Task M1.6: Smoke test for activated features
Extend the G0 smoke suite with activation-aware tests:
- If `LLM_THESIS_MODE != off`: verify Gemini API reachable (rate limit aware)
- If `DRIFT_MONITORING_ENABLED == active`: verify SPC engine can query metrics
- If `DELIVERY_MODE != staging_only`: verify Notion API reachable

**Gate:** Config validation wired in both paths; strict/non-strict behavior verified;
activation runbook reviewed with rollback commands per stage; smoke suite green
**Status:** pending

---

## Phase M3: Batch Publish Wire-up
**Estimated effort:** 2-3 days
**Purpose:** Fix dry-run-only batch commit by wiring NotionPusher into batch router

### Task M3.0: Initialize app-scoped Notion connector in lifespan
**File:** `api/main.py`, `lifespan()` function

**Add after store initialization:**
```python
# Initialize Notion connector (app-scoped, lifecycle-managed)
from connectors.notion_transport import NotionTransport
from connectors.notion_connector_v2 import NotionConnector
try:
    notion_api_key = os.environ["NOTION_API_KEY"]
    notion_db_id = os.environ["NOTION_DATABASE_ID"]
    transport = NotionTransport(api_key=notion_api_key)
    await transport.start()
    connector = NotionConnector(
        api_key=notion_api_key,
        database_id=notion_db_id,
        transport=transport,
    )
    app.state.notion_transport = transport
    app.state.notion_connector = connector
    logger.info("NotionConnector initialized (app-scoped)")
except (KeyError, ValueError) as e:
    app.state.notion_transport = None
    app.state.notion_connector = None
    logger.warning("Notion not configured: %s -- batch commit unavailable", e)
except Exception as e:
    # Network/transport init failure -- fail-open, Notion is optional for most endpoints
    app.state.notion_transport = None
    app.state.notion_connector = None
    logger.warning("Notion transport failed to start: %s -- batch commit unavailable", e)
```

**Failure policy:** Fail-open. Notion is required only for batch commit; most API
operations (triage, search, health) work without it. If `transport.start()` fails
(network error, DNS, etc.), connector is set to None and batch commit returns 503
at request time. This avoids blocking the entire API for an optional integration.

**Add to lifespan shutdown (after `await store.close()`):**
```python
if getattr(app.state, "notion_transport", None):
    await app.state.notion_transport.shutdown()
    logger.info("NotionTransport shut down")
```

**Rationale:** NotionTransport owns `httpx.AsyncClient` requiring explicit `shutdown()`.
Per-request construction would leak connections. Matches SignalStore lifespan pattern.

### Task M3.1: Wire NotionPusher into batch commit endpoint
**File:** `api/routers/batch.py`, `commit_batch_endpoint()` function

**Replace the TODO block with:**
```python
pusher = None
if not body.dry_run:
    connector = request.app.state.notion_connector
    if connector is None:
        raise error_response(
            503, "service_unavailable", "NOTION_NOT_CONFIGURED",
            "Batch commit requires NOTION_API_KEY and NOTION_DATABASE_ID. "
            "This is a configuration issue, not retryable.",
        )
    from verification.verification_gate_v2 import VerificationGate
    from workflows.notion_pusher import NotionPusher
    gate = VerificationGate(strict_mode=False)
    pusher = NotionPusher(
        signal_store=store,
        notion_connector=connector,
        verification_gate=gate,
        dry_run=False,
    )

result = await commit_batch(
    store, batch_id,
    pusher=pusher,
    dry_run=body.dry_run,
    actor=operator.actor_label,
)
```

**Key constraints:**
- Preserve TOCTOU hash guard (batch.py hash validation before commit)
- Preserve `assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)` in `commit_batch()`
- Dry-run path unchanged (pusher=None when dry_run=True)
- 503 (not 500) for missing Notion config -- external service unavailability

**Error contract (aligned with existing batch.py codes):**
| Condition | Status | Code | Retryable |
|-----------|--------|------|-----------|
| Missing Notion env vars | 503 | NOTION_NOT_CONFIGURED | No (config issue) |
| DELIVERY_MODE blocks | 423 | FEATURE_DISABLED | No (policy issue) |
| Pusher error on item | 200 | committed_with_errors | Per-item |
| Stale TOCTOU hash | 409 | BATCH_ITEMS_CHANGED | Yes (re-preview) |
| Invalid batch state | 409 | BATCH_STATE_ERROR | No (re-preview) |
| Batch not found | 404 | BATCH_NOT_FOUND | No |

### Task M3.2: Tests for batch commit real path

**File:** `tests/api/test_batch_real_commit.py`
- [ ] `dry_run=True` remains non-mutating (regression)
- [ ] `dry_run=False` + `DELIVERY_MODE=staging_only` -> `DeliveryPolicyError` (423)
- [ ] `dry_run=False` + `DELIVERY_MODE=batch_publish` + mock pusher -> items pushed, `notion_page_id` stored
- [ ] `dry_run=False` + `app.state.notion_connector=None` -> 503 NOTION_NOT_CONFIGURED
- [ ] `dry_run=False` + pusher error on one item -> batch status `committed_with_errors`
- [ ] TOCTOU: stale hash -> 409 BATCH_ITEMS_CHANGED
- [ ] Batch status transitions: `approved` -> `committing` -> `committed`
- [ ] Verify `status_code`, `detail.code`, and `detail.message` shape for each error

**Resource lifecycle test:**
- [ ] Repeated batch commits don't leak `httpx.AsyncClient` instances (check transport state)

**Test seam:** Set `app.state.notion_connector` to a mock connector (or None for 503 tests).
NotionPusher is constructed inside `commit_batch_endpoint()` using the app-scoped connector,
so the injection point is `request.app.state.notion_connector`. No need to patch the class.

**Mock pattern:**
```python
mock_pusher = MagicMock()
mock_pusher.process_single_prospect = AsyncMock(return_value=PushResult(
    canonical_key="domain:test.com", company_name="Test",
    decision=PushDecision.AUTO_PUSH, confidence=0.8,
    pushed=True, notion_page_id="page-123",
))
```

### Task M3.3: Delivery mode progression integration test
- Test the full flow: `staging_only` blocks (423) -> `manual_publish` allows single -> `batch_publish` allows batch
- Verify error envelopes are consistent across all delivery policy violations

### Task M3.4: Fix pagination count queries (separately gateable)
- `api/routers/entities.py` -- add `SELECT COUNT(*)` query for `total` field
- `api/routers/companies.py` -- add `SELECT COUNT(*)` query for `total` field
- NOTE: If M3.1 slips, M3.4 can ship independently

**Gate:** `dry_run=False` + `DELIVERY_MODE=batch_publish` + valid connector -> real Notion
push with correct `notion_page_id` persisted; error contracts verified; no leaked clients
**Status:** pending

---

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| G0 before any changes | ~6709 tests need a clean baseline before activation |
| G0 time-boxed to 2 days | Prevent infinite "fix all issues" spiral |
| **App-scoped Notion connector (v2)** | NotionTransport owns httpx.AsyncClient; per-request leaks connections |
| STRICT_CONFIG_VALIDATION default false | Graduated rollout avoids breaking existing deploys |
| Mock-based CI tests for Notion | Real Notion API tests are manual gate (network-dependent) |
| M3 before M2 | Batch publish has direct business ROI (deals reach CRM) |
| 503 for missing Notion config | External service unavailability, not internal error |
| Split API/CLI test files | Failure localization -- different startup paths |
| Generated baseline artifact | Hardcoded failure counts drift; node IDs + commit hash are stable |
| M3.4 separately gateable | Pagination fixes can ship even if M3.1 slips |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Wave 5 ~60% done | 1 | File check proved 100% complete |
| OPS docs missing | 1 | Both exist (operator-guide, architecture-overview) |
| Phase G dormant | 1 | Actively wired with tests |
| Per-request NotionPusher (v1) | 1 | v2: app-scoped connector with lifecycle management |
| Hardcoded "6 failures" (v1) | 1 | v2: generated baseline with node IDs |
| "Read-only activation" (v1) | 1 | v2: renamed "Low-risk activation" |

## Files to Modify
| File | Change | Milestone |
|------|--------|-----------|
| `utils/config_validator.py` | Delivery-mode-aware Notion key validation | M1.0 |
| `api/main.py` | Wire config validation + Notion connector into `lifespan()` | M1.1, M3.0 |
| `run_pipeline.py` | Wire config validation into `main()` | M1.2 |
| `api/routers/batch.py` | Wire NotionPusher into `commit_batch_endpoint()` | M3.1 |
| `api/routers/entities.py` | Add count query for pagination | M3.4 |
| `api/routers/companies.py` | Add count query for pagination | M3.4 |
| `docs/claude/environment-variables.md` | Add STRICT_CONFIG_VALIDATION | M1.3 |

## Files to Create
| File | Purpose | Milestone |
|------|---------|-----------|
| `tests/KNOWN_FAILURES.md` | Generated baseline artifact (commit+date+node IDs) | G0.2 |
| `tests/smoke/test_smoke_suite.py` | Baseline smoke tests | G0.3 |
| `tests/api/test_config_validation_wiring.py` | API lifespan validation tests | M1.4 |
| `tests/cli/test_config_validation_wiring.py` | CLI startup validation tests | M1.4 |
| `docs/runbooks/feature-activation.md` | Progressive activation runbook (4 stages) | M1.5 |
| `tests/api/test_batch_real_commit.py` | Batch commit + error contract + lifecycle tests | M3.2 |

## Verification Plan
1. **G0:** `pytest --collect-only -q` exits 0; smoke suite green; baseline artifact committed
2. **M1:** Strict/non-strict validation behavior verified in API and CLI tests; activation runbook reviewed with rollback commands per stage
3. **M3:** `dry_run=False` + `DELIVERY_MODE=batch_publish` performs push in controlled test; error contract tests pass for 503/423/409; resource lifecycle test passes for repeated commits
4. **Full sprint:** All new tests pass; existing tests still collect cleanly; smoke suite green

---

# Post-M2 Exit Criteria (M2.4 Complete)
## Summary

This block closes M2 with objective evidence and defines a clean, low-risk handoff into M4.



- commit_sha
- date_utc
- pytest_collect_count
- collection_errors
- regression command used

### 2. Regression Gate (Required)


python -m pytest tests/api/ tests/integration/ tests/workflows/test_batch_publisher.py --tb=short

- Pass condition:
- zero new collection errors vs G0 baseline

### 3. S0 Canary Consistency (Required)

- One of the following must be documented:


- notion_page_id persisted
- batch_commit audit entry present

2. Fallback: If dry-run only, explicitly mark S0 as "dry-run validated" and make non-dry-run canary a pre-M4 gate
   item.


- Startup probe hardening is deployed and documented with rollback steps.
- /api/v1/health/metrics serves valid OpenMetrics text and fails open on ops collector errors.
- M2.4 rate limiting is implemented and verified (middleware enabled, behavior tested, exemptions documented).

### 5. M2.4 Completion Evidence (Required)

- Include:
- configured limits and scope (which routes/roles)
- rollback/toggle procedure if limits cause operator friction

## M4 Entry Gate
M4 may start only when all items below are checked:

- [x] Post-M2 baseline artifact committed
- [x] Regression gate passing with no new failures
- [x] S0 canary status resolved (non-dry-run preferred)
- [x] M2.1/M2.2/M2.3/M2.4 evidence linked

---

## Phase M4: Activation Readiness
**Status:** COMPLETE
**Purpose:** Automated gates so operators can check readiness before each activation step.

### M4 Evidence
- `monitoring/activation_gate.py` -- step-specific policy (STEP_POLICY matrix)
- `GET /health/activation-readiness?step=N` -- unauthenticated, 422 on invalid step
- `/health/detailed` includes `activation_readiness` component (2s timeout guardrail)
- Batch commit soft gate with `audit_events.record_event()` on non-ready verdict
- `python run_pipeline.py activation-check --step N` CLI command
- `docs/runbooks/feature-activation.md` updated with gate commands + step thresholds

### M4 Test Coverage
| File | Tests |
|------|-------|
| `tests/monitoring/test_activation_gate.py` | 9 |
| `tests/api/test_activation_readiness_api.py` | 8 |
| `tests/api/test_batch_activation_gate.py` | 4 |
| `tests/cli/test_activation_readiness_cli.py` | 5 |
| `tests/smoke/test_smoke_suite.py` (additions) | 2 |
| **Total new tests** | **28** |

## Evidence Links Section (Template)

- Baseline artifact: tests/baseline_snapshot.json (or successor)
- Known failures: tests/KNOWN_FAILURES.md
- CI gate run: <workflow run URL>
- Canary evidence: <log/query/screenshot links>
- Metrics endpoint validation: <test or curl output reference>
- Rate limiting evidence: <test output + config reference>
- Branch protection config: <settings/ruleset reference>

## Defaults and Assumptions

- Deployment target remains Systemd VM.
- Gate philosophy is "prevent regression," not "force zero historical defects."
- Pre-existing known failures are allowed only with explicit ownership and tracking.
