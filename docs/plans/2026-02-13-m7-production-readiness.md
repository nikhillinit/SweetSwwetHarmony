# Task Plan: M7 Production Readiness Gate

## Goal
Final validation gate before starting the 4-step progressive activation sequence on
production data. M7 delivers automated pre-flight checks, backup/restore automation,
a production env template, an end-to-end activation simulation test, and an operator
quickstart -- everything an operator needs to safely begin Step 1 (Shadow Activation).

## Current Phase
ALL PHASES COMPLETE (M7.1-M7.6)

---

## Context

**Snapshot (2026-02-13, commit 8178550):**
- M1-M6 all COMPLETE (config validation, batch publish, ops hardening, activation gates, containerization, Phase G activation)
- CURRENT_SCHEMA_VERSION = 41, 6861+ total tests
- Regression gate (measured 2026-02-13): 583 passed, 4 skipped
- 3 GitHub Actions workflows (regression-gate, discovery-pipeline, thesis-eval)
- Dockerfile + docker-compose.yml ready
- 8 runbooks covering all operational scenarios
- 4-step activation sequence defined (feature-activation.md + phase-g-activation.md)
- All features DISABLED by default (10+ feature flags)

**What's missing for production:**
1. No automated backup/restore for SQLite
2. No production `.env` template (operators must assemble from docs)
3. No end-to-end activation simulation test
4. No pre-flight script that checks ALL prerequisites before Step 1
5. No operator quickstart (single doc for day-1)

**What's NOT in scope (deferred):**
- Multi-region / failover (not needed for single VM)
- OpenTelemetry / distributed tracing (structured logs sufficient)
- Auto-scaling (single-worker uvicorn for SQLite)
- Runtime feature flag override API (env vars sufficient initially)
- Kubernetes HPA config (systemd target)

---

## Phase M7.1: SQLite Backup/Restore Automation
**Time-box:** 2-3 hours
**Purpose:** Ensure production data can be backed up and restored safely

**Existing state:** `scripts/backup.sh` and `scripts/restore.sh` exist (bash/Linux/systemd,
zstd compression, S3/B2 upload). These are Linux-deployment convenience scripts.

**Canonical path decision:** Python modules are the canonical cross-platform implementation.
Shell scripts are kept as-is for Linux systemd deployment convenience. Python modules use
`sqlite3.Connection.backup()` directly (no shell dependency, no zstd requirement).
Operator quickstart (M7.5) references Python scripts. Linux runbook can reference either.

### Tasks
- [ ] **M7.1.1** Create `scripts/backup_db.py`:
  - Uses `sqlite3.Connection.backup()` (online, WAL-safe)
  - Output: `backups/signals-YYYYMMDD-HHMMSS.db`
  - Configurable retention (default 7 copies, oldest rotated)
  - Validates backup integrity with `PRAGMA integrity_check`
  - CLI: `python scripts/backup_db.py [--db signals.db] [--out-dir backups/] [--retain 7]`
- [ ] **M7.1.2** Create `scripts/restore_db.py`:
  - Validates backup integrity before restore
  - **Safety rule:** Default behavior allows restore only when API is NOT reachable.
    Probes `http://localhost:8000/api/v1/health` (configurable via `--api-url`);
    if 200 returned, abort: "API server is running. Stop it before restoring. Use --force to override."
    If connection refused / timeout, proceed (API is down, safe to restore).
  - `--force` bypasses the reachability check (emits loud WARNING to stderr)
  - Creates pre-restore backup of current DB (always, even with --force)
  - Restores from specified backup file
  - Runs `PRAGMA integrity_check` post-restore
  - Verifies schema version matches CURRENT_SCHEMA_VERSION post-restore
  - CLI: `python scripts/restore_db.py <backup-file> [--db signals.db] [--force]`
- [ ] **M7.1.3** Tests: `tests/scripts/test_backup_restore.py`
  - Backup creates valid DB copy
  - Backup rotation (8th backup removes oldest)
  - Restore from backup matches original
  - Restore creates pre-restore safety backup
  - Corrupt backup file rejected
  - WAL-mode backup while DB has pending transactions
  - Restore when API reachable (no --force) -> refused with actionable message
  - Restore when API unreachable (no --force) -> proceeds normally
  - Restore with --force when API reachable -> proceeds with WARNING to stderr
  - Restore with --force when API unreachable -> proceeds normally

**Gate:** Backup + restore round-trip produces identical schema + data
**Status:** COMPLETE

---

## Phase M7.2: Production Environment Template
**Time-box:** 1-2 hours
**Purpose:** Single source of truth for all production env vars

### Tasks
- [ ] **M7.2.1** Create `.env.production.template`:
  - All env vars grouped by category (Core, Feature Flags, Thresholds, Secrets)
  - Inline comments with valid values and defaults
  - Step-1 through Step-4 activation sections (copy-paste blocks)
  - Strict validation enabled by default (`STRICT_CONFIG_VALIDATION=true`)
- [ ] **M7.2.2** Create `scripts/validate_env.py`:
  - Reads a `.env` file and runs `validate_config()` against it
  - Reports missing required vars, invalid values, and warnings
  - Exit 0 = clean, exit 1 = errors found
  - CLI: `python scripts/validate_env.py [--env-file .env.production]`
- [ ] **M7.2.3** Tests: `tests/scripts/test_validate_env.py`
  - Valid production env passes
  - Missing NOTION_API_KEY with batch_publish ->error
  - Invalid DELIVERY_MODE value ->error
  - staging_only without Notion keys ->warnings only

**Gate:** Template covers all documented env vars; validate script catches known misconfigs
**Status:** COMPLETE

---

## Phase M7.3: Pre-flight Checklist Script
**Time-box:** 2-3 hours
**Purpose:** Automated script that validates ALL prerequisites before starting activation

### Tasks
- [ ] **M7.3.1** Create `scripts/preflight_check.py`:
  - Check 1: DB exists + `PRAGMA integrity_check` passes
  - Check 2: Schema version == CURRENT_SCHEMA_VERSION (`from storage.signal_store import CURRENT_SCHEMA_VERSION`)
    - Currently 41; import guards against stale hardcoded values
    - Fail if version < CURRENT_SCHEMA_VERSION (missing migrations)
    - Warn if version > CURRENT_SCHEMA_VERSION (unknown future migration -- binary/schema mismatch)
  - Check 3: Config validation clean (`validate_config()` returns no errors)
  - Check 4: Smoke suite passes (--mode full only; invoke via `subprocess.run(["pytest", "tests/smoke/", "-q"])`)
  - Check 5: Activation gate Step 1 returns ready/warn (not blocked)
  - Check 6: Backup exists (at least 1 backup in `backups/` dir < 24h old)
  - Check 7: Canary golden set defined (>0 canary items in DB)
    - 0 canary items -> **warn** (not fail). Step 1 (shadow) is lenient on missing canary data
      per STEP_POLICY in `monitoring/activation_gate.py`. Fail would block fresh installs.
    - >0 canary items -> pass
  - Check 8: Regression freshness -- query CI status via Python `httpx` (NOT `gh --jq`)
    - Check name filter: `"Core Regression Suite"` (matches workflow job name)
    - Commit fallback order: `origin/main` tip -> current SHA (if pushed) -> "unknown" warn
    - Uses `httpx.get("https://api.github.com/repos/{owner}/{repo}/commits/{sha}/check-runs")` + GITHUB_TOKEN
    - Timeout: 10s connect, 15s read. Single retry with 2s backoff on 5xx/timeout.
    - Status mapping:
      - `conclusion == "success"` -> pass
      - `conclusion == "failure"` -> fail
      - No matching check run found -> warn
      - HTTP 422 (commit not on remote) -> warn
      - No GITHUB_TOKEN env var -> warn
      - Network error after retry -> warn
  - Check 9: API server reachable (`/api/v1/health` returns 200) -- optional, skip if not running
  - Output: JSON report with pass/warn/fail per check + overall verdict
  - CLI: `python scripts/preflight_check.py [--db signals.db] [--json] [--mode quick|full]`
  - **`--mode quick` (default):** Checks 1-3, 5-9 (fast, no test invocation, ~5s)
  - **`--mode full`:** All checks including Check 4 (smoke suite, adds ~30-60s)
  - **Shell-agnostic:** All checks implemented in pure Python (no shell commands, no `gh` CLI)
- [ ] **M7.3.2** Tests: `tests/scripts/test_preflight_check.py`
  - All checks pass ->overall pass
  - Missing DB ->fail on check 1
  - Old schema ->fail on check 2
  - Config error ->fail on check 3
  - No recent backup ->warn on check 6
  - Individual check isolation (each check runs independently)

**Gate:** Pre-flight script catches all known misconfigs; JSON output parseable
**Status:** COMPLETE

---

## Phase M7.4: End-to-End Activation Simulation
**Time-box:** 3-4 hours
**Purpose:** Test that walks through the full activation sequence in a controlled environment

### Tasks
- [ ] **M7.4.1** Create `tests/integration/test_activation_simulation.py`:
  - Test 1: Baseline state (all flags off) ->verify all features disabled
  - Test 2: Step 1 activation ->verify shadow features running, no mutations
  - Test 3: Step 2 activation ->verify drift monitoring active, thin files populating
  - Test 4: Step 3 activation ->verify manual publish works, triage actions persist
  - Test 5: Step 4 activation ->verify batch commit works (mock Notion)
  - Test 6: Emergency rollback ->verify all features disabled, smoke passes
  - Test 7: Gate blocking ->verify step advancement blocked when canary fails
  - Each test sets env vars, calls relevant endpoints/functions, asserts expected behavior
  - Uses monkeypatch for env vars, mock for external APIs (Notion, Gemini)
  - **Deterministic fixture contract (anti-flake):**
    - Fixed `FROZEN_NOW = "2026-01-15T12:00:00Z"` for all timestamps
    - Seeded entity IDs via `entity_id_for_seed("test-entity-N")`
    - Mocked network: `httpx.AsyncClient` ->`MockTransport` (no real HTTP)
    - Mocked Gemini: `google.generativeai` ->returns fixed classification
    - Mocked Notion: `NotionConnector` ->returns fixed `page-{n}` IDs
    - `@pytest.fixture` providing pre-populated DB with known signal/review/company_file rows
    - All randomness via `random.seed(42)` where applicable
- [ ] **M7.4.2** Create `tests/integration/test_backup_integration.py`:
  - Test 1: Pipeline run ->backup ->restore ->verify data integrity
  - Test 2: Backup during active shadow resolution ->data consistent

**Gate:** All 9 simulation tests pass; full activation sequence validated end-to-end
**Status:** COMPLETE

---

## Phase M7.5: Operator Quickstart Guide
**Time-box:** 1-2 hours
**Purpose:** Single-page guide for day-1 production operations

### Tasks
- [ ] **M7.5.1** Create `docs/operator-quickstart.md`:
  - Section 1: System requirements (Python 3.11+, disk space, network)
  - Section 2: Initial setup (clone, install deps, create .env from template)
  - Section 3: Pre-flight check (`python scripts/preflight_check.py`)
  - Section 4: First backup (`python scripts/backup_db.py`)
  - Section 5: Start API server (`uvicorn api.main:app --host 0.0.0.0 --port 8000`)
  - Section 6: Begin activation (Step 1 commands from feature-activation.md)
  - Section 7: Daily operations (pipeline run, backup, health check)
  - Section 8: Emergency procedures (quick links to runbooks)
  - Section 9: Key CLI commands reference table
- [ ] **M7.5.2** Update `docs/runbooks/feature-activation.md`:
  - Add pre-flight check command before Step 1
  - Add backup command before each step advancement
  - Cross-reference operator-quickstart.md

**Gate:** Quickstart covers end-to-end from setup to Step 1; no dead links
**Status:** COMPLETE

---

## Phase M7.6: Final Regression Gate + Evidence
**Time-box:** 1-2 hours
**Purpose:** Run full regression suite, document evidence, update baseline

### Tasks
- [ ] **M7.6.1** Run full regression gate:
  ```bash
  pytest tests/api/ tests/integration/ tests/workflows/test_batch_publisher.py tests/smoke/ --tb=short -q
  ```
  - Document: test count, pass count, any new failures
  - Compare against M6 baseline (583 passed, 4 skipped) -- no new failures allowed
- [ ] **M7.6.2** Update `tests/baseline_snapshot.json`:
  - New commit hash
  - New test count
  - M7 evidence: backup/restore tested, activation simulation green, pre-flight clean
- [ ] **M7.6.3** Create M7 evidence block in milestone plan:
  - Pre-flight script output (JSON)
  - Regression gate results
  - Backup/restore round-trip evidence
  - Activation simulation test results
  - Commit hash + date

**Gate:** No new failures vs M6 baseline (583 passed); actual count recorded in evidence; all M7 artifacts committed
**Status:** COMPLETE

---

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| SQLite `backup()` API (not file copy) | WAL-safe, handles concurrent readers, no checkpoint needed |
| 7-copy retention default | One week of daily backups without unbounded disk growth |
| Pre-flight as standalone script (not API endpoint) | Runs before API is started; no chicken-and-egg |
| Activation simulation uses monkeypatch | Env var changes must not leak between tests |
| Skip OpenTelemetry | Structured logs + SPC monitoring sufficient for initial launch |
| Skip runtime flag override API | Env vars + restart sufficient for 4-step manual activation |
| Operator quickstart is separate from architecture docs | Different audience (ops vs dev); quick reference vs deep dive |
| Pure Python for CI checks (not `gh` CLI) | `gh --jq` is brittle on PowerShell; `httpx` + GitHub REST API is shell-agnostic |
| Regression freshness fallback: origin/main ->SHA ->warn | Local commits return 422 from GitHub; fail-open avoids false negatives |
| Deterministic fixture contract for E2E | Fixed timestamps, seeded IDs, mocked network prevent flaky tests |
| Explicit staging profile for exit criteria | "All-green on staging" needs concrete env/flags, not ambiguous |
| Schema gate imports CURRENT_SCHEMA_VERSION | Hardcoded version drifts; import from signal_store.py keeps it authoritative |
| Python backup = canonical, shell scripts = Linux convenience | backup.sh/restore.sh exist but are bash/systemd-specific; Python is cross-platform + testable |
| Restore requires --force (not advisory stop) | Advisory stop is not safe; DB corruption risk if API writes during restore |
| Pre-flight --mode quick\|full | Smoke suite adds 30-60s; routine operator checks should be fast (~5s) |
| Baseline-relative regression gate (not hardcoded count) | Absolute thresholds drift as test count grows; "no new failures" is stable policy |
| Restore default = API-not-reachable guard (not always --force) | Safe default: proceed only when API is confirmed down; --force for scripted maintenance |
| Health endpoint: /api/v1/health consistently | Root /health is ambiguous; /api/v1/health matches all existing router contracts |
| Regression freshness: "Core Regression Suite" + timeout + status map | Decision-complete spec prevents ambiguous implementation; single retry with 2s backoff |
| Canary 0 items = warn (not fail) | Step 1 STEP_POLICY is lenient on missing canary; fail would block fresh installs |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | -- | -- |

## Files to Create
| File | Purpose | Phase |
|------|---------|-------|
| `scripts/backup_db.py` | Online SQLite backup with rotation | M7.1.1 |
| `scripts/restore_db.py` | Validated restore from backup | M7.1.2 |
| `tests/scripts/test_backup_restore.py` | Backup/restore tests | M7.1.3 |
| `.env.production.template` | Production env var template | M7.2.1 |
| `scripts/validate_env.py` | Standalone env validation | M7.2.2 |
| `tests/scripts/test_validate_env.py` | Env validation tests | M7.2.3 |
| `scripts/preflight_check.py` | Pre-activation checklist | M7.3.1 |
| `tests/scripts/test_preflight_check.py` | Pre-flight tests | M7.3.2 |
| `tests/integration/test_activation_simulation.py` | End-to-end activation test | M7.4.1 |
| `tests/integration/test_backup_integration.py` | Backup integration test | M7.4.2 |
| `docs/operator-quickstart.md` | Day-1 operator guide | M7.5.1 |

## Files to Modify
| File | Change | Phase |
|------|--------|-------|
| `docs/runbooks/feature-activation.md` | Add pre-flight + backup commands | M7.5.2 |
| `tests/baseline_snapshot.json` | Update with M7 evidence | M7.6.2 |
| `docs/plans/2026-02-12-post-wave5-milestones.md` | Add M7 evidence block | M7.6.3 |

## Staging Profile (M7 Exit Criteria Environment)

All M7 exit criteria are validated against this staging profile:

```bash
# --- Staging Profile ---
DELIVERY_MODE=staging_only
STRICT_CONFIG_VALIDATION=true
LLM_THESIS_MODE=off
ML_ENABLEMENT=disabled
MERGE_WRITES_ENABLED=disabled
USE_SHADOW_ENTITY_RESOLUTION=false
USE_PHASE_G_IDENTITY_RESOLUTION=false
USE_CLAIM_FACTS=false
DRIFT_MONITORING_ENABLED=disabled
USE_THIN_FILES=false
V2_ENABLEMENT=shadow
BULK_TRIAGE_ENABLED=disabled
HUNTER_PROMOTE_ENABLED=disabled
DISCOVERY_DB_PATH=signals.db

# Secrets (must be present but staging_only won't call them)
NOTION_API_KEY=           # empty OK for staging_only
NOTION_DATABASE_ID=       # empty OK for staging_only
GITHUB_TOKEN=ghp_xxx      # needed for regression freshness check
GOOGLE_API_KEY=           # empty OK when LLM_THESIS_MODE=off
```

**"All-green on staging" means:**
1. `python scripts/preflight_check.py --json` ->overall verdict "pass"
2. Regression gate: no new failures vs M6 baseline (583 passed, 4 skipped); actual count recorded
3. Backup/restore round-trip integrity verified
4. Activation simulation tests all pass with monkeypatched env

## Verification Plan
1. **M7.1:** Backup + restore round-trip produces identical DB (PRAGMA integrity_check + row count match)
2. **M7.2:** Template covers all env vars; validate script catches known misconfigs
3. **M7.3:** Pre-flight catches missing DB, old schema, config errors, missing backups
4. **M7.4:** All 9+ simulation tests pass; full activation sequence validated with deterministic fixtures
5. **M7.5:** Quickstart covers setup ->Step 1; no dead links
6. **M7.6:** No new failures vs M6 baseline; actual pass count recorded in evidence
7. **Overall:** `python scripts/preflight_check.py` returns "pass" on the final commit against staging profile
