# Concerns

**Analysis Date:** 2026-04-08
**Branch:** prep/red-team-hybrid-prep (Move 0, ends 2026-04-19)
**Sources:** Concerns mapper agent + risk register R19 + MEMORY.md known issues + orchestrator verification

---

## CRITICAL — Showstopper

### R19: Data collection pipeline silently frozen since 2026-03-01

**Location:** Operational state, not code-localizable
**Reference:** `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` R19 (score 20, OPEN)
**Discovered:** 2026-04-08 by 5-agent codebase audit

**Facts:**
- Signal corpus = 612 (frozen)
- `max(detected_at)` = 2026-03-01
- Last actual pipeline run = 2026-03-24 (run_id 44)
- "X signals processed" entries from 2026-04-04 in MEMORY.md were **re-classifications of existing rows**, not new ingest
- Total `thesis_classifications` rows = 3,085 (max `classified_at` = 2026-04-05)

**Impact:**
- **2026-04-18 Step 4B regret check** (MERGE_WRITES_ENABLED=active, governance event #21) will run on stale data
- Drift alerts (0 unacknowledged) and canary (run #56, 91.46%) reflect frozen-data behavior, not live system health

**Required Decision (before 2026-04-18):**
1. **Restart collection now** and let ≥5 days of fresh data accrue before the regret check
2. **Document explicitly** that the regret check evaluates batch_publish/MERGE_WRITES_ENABLED stability against frozen data and adjust pass/fail criteria accordingly
3. **Postpone the regret check** itself

**Severity:** Catastrophic (5) × Very Likely (5) = **25 → Showstopper**
**Status:** OPEN — REQUIRES DECISION

---

## IMPORTANT

### Savepoint rollback error path untested
**Location:** `ops/quality/thesis.py:614-641`, `ops/quality/thesis.py:670-697`
**Issue:** Exception handlers around `SAVEPOINT … ROLLBACK TO … RELEASE` for thesis refresh do not have tests covering the case where the ROLLBACK or RELEASE statement itself raises. If the SAVEPOINT cleanup throws, the outer transaction state is undefined.
**Source:** Code review 2026-04-05 (re-verified 2026-04-08, status OPEN)
**Suggested action:** Add 2 exception-path tests forcing `aiosqlite.OperationalError` on the `ROLLBACK TO SAVEPOINT` and `RELEASE SAVEPOINT` statements.

### `_processed_identities` per-run dedup ordering bug (R8)
**Location:** `collectors/base.py:118`, `collectors/base.py:336-340`
**Issue:** Per-run dedup set `_processed_identities` is order-sensitive — multi-source signals could be deduplicated against the *first* observation of an identity within a run, masking a stronger signal arriving later in the same run.
**Reference:** Risk register R8 (score 9, MITIGATING). Reviewed during Move 0 read-only collector audit; top 3-5 fixes deferred to Move 2.
**Note:** Lives inside the protected `collectors/` path during Move 0 — no fix until 2026-04-19.

### Pipeline `--dry-run` does not guard all state mutations in active LLM_THESIS_MODE
**Location:** `workflows/pipeline.py` (multi-call chain via `_process_signals_stage` → `get_pending_signals`)
**Reference:** `.claude/rules/plan-verification.md` Known Gotcha #3
**Issue:** In `LLM_THESIS_MODE=active`, thesis reject/hold paths call `mark_rejected()` and `update_signal_status()` **before** the `--dry-run` Notion push guard. `--dry-run` only prevents Notion writes, not processing-state mutations on `signal_processing.status`.
**Severity:** Important — surprising semantics; documented in plan-verification rule.
**Suggested action:** Document in `docs/claude/cli-commands.md` and add a runtime warning, or refactor to gate state mutations behind dry-run as well. Lives in protected `workflows/` path during Move 0.

### DB guard read-error handler intentionally uncovered
**Location:** `run_pipeline.py:251`
**Issue:** Exception handler for the DB watermark read-error path is marked `# pragma: no cover`. Covered indirectly by integration tests, not unit tests.
**Source:** Code review 2026-04-05 (re-verified 2026-04-08, status OPEN — intentional)
**Suggested action:** Either add a mock-exception unit test or document the pragma rationale inline.

---

## MEDIUM

### ~360 historical signals with NULL `company_name`
**Location:** `signals` table (data, not code)
**Counts (from 2026-04-08 audit):**
- arxiv: 275
- rss_feeds: 55
- manual_seed_buzz: 20
- news_api: 10
- **Total:** 360 signals

**Status distribution:** 277 held + 83 rejected (none pending)
**Likely root cause:** Expected for arxiv (papers don't always name companies) and rss_feeds (titles without company entities)
**Action:** Re-evaluate whether this is a real defect or expected behavior. If expected, add a documented exemption; if not, add NER/extraction step for these sources.

### Disk growth from artifact retention (R9)
**Location:** `artifacts/` directory tree
**Reference:** Risk register R9 (score 6, DEFERRED to Move 1)
**Issue:** No retention/eviction policy yet; expected to exceed Fermi estimate at 10x volume.
**Mitigation:** 90-day raw / 180-day archive policy specified in `03-dead-letter-contract.md` §10. Cron lands in Move 1 day 5 BEFORE soft validation enables.

### Missing API keys disable 6 collectors
**Reference:** `CLAUDE.md` API Key Coverage section
**Disabled:** companies_house, product_hunt, linkedin, crunchbase, opencorporates, uspto
**Impact:** 6 / 16 collectors offline (37.5% reduction in source diversity). Multi-source verification gates operate on a smaller surface.
**Status:** Awaiting key provisioning; not blocking but reduces signal coverage.

### Orchestration debt in `workflows/pipeline.py` (R12)
**Location:** `workflows/pipeline.py` (~2000+ lines)
**Reference:** Risk register R12 (score 9, ACCEPTED for Moves 1-3)
**Issue:** Pipeline orchestrator monolith; split deferred until Move 4 decision gate evaluates whether queueing is the next bottleneck.

---

## RESOLVED / MITIGATED (Verified 2026-04-08)

| Issue | Resolution | Verification |
|---|---|---|
| Phase G alias UNIQUE constraint error | Fixed in commit `88461bf` (+252 test lines) | Verified — error no longer occurs |
| HN false positive rate (98.69% / 90d) | Mitigated 2026-03-25 by enabling `LLM_THESIS_MODE=active`; HN now 100% rejected as B2B/dev tools | Live processing 2026-03-25: 28 HN signals, 28 rejected |
| SPC zero-volume blind spot (LCL=-76 for collector_volume) | Fixed in commit `f6602c1` — `SPCMonitor.check_zero_volume()` + daily aggregator backfill | 7 new tests pass; `SPC_ZERO_VOLUME_ALERTING=true` default |
| DB recovery incident 2026-04-04 (signals.db truncated to 4 test signals) | Resolved + hardened in commit `04a5e6e` (46 files, 3010 lines) — signal-count watermark guard, DBToolLock, CI lint guards, restore script sidecar handling | PR #131 merged |
| `spc_override_decision.py` missing `load_dotenv()` | Fixed in PR #122 (2026-03-18) | Merged |
| Audit event for DELIVERY_MODE used raw SQL | Fixed in PR #123 (governance debt cleanup) | Merged |
| `feature-promotion-runbook.md` stale metadata-free INSERT recipes | Fixed (CLI-first rewrite) | Merged |
| Governance CLI demote couldn't encode `batch_publish → manual_publish` | Fixed (two-lane state policies) | Merged |
| Manifest path resolves against `cwd()` not manifest dir (claimed in code review) | **FALSE POSITIVE** — code uses `manifest_path.parent`; test `test_load_benchmark_manifest_resolves_relative_dataset_path_from_manifest_directory` covers it | Closed 2026-04-08 |
| `test_mtta_with_acknowledged_alerts` failure | **PASSING 2026-04-08** — verified by running pytest | Closed |
| `test_grid_renders_with_dataframe` failure | **PASSING 2026-04-08** — verified | Closed (report was stale) |
| Signal 438 Mercari not classified | **CLASSIFIED 2026-04-08** — has 1 classification, status=held | Closed |

---

## SECURITY OBSERVATIONS

- **No hardcoded secrets** found in source files (orchestrator-level scan deferred to `/cso` skill for full audit)
- **`.env` handling:** Uses `python-dotenv` with `load_dotenv()` calls in CLI entry points (PR #122 fix)
- **MCP server boundary:** All external access goes through `discovery_engine/mcp_server.py` (per `.claude/rules/invariants.md`)
- **Schema preflight:** Mandatory before Notion ops (per invariants); validates Dilligence-spelled status etc.
- **Read-only DB credentials:** Per invariants — no write DB credentials in agent context

## Move 0 Operating Constraints

The following paths are **frozen** until 2026-04-19 by `scripts/red-team-hybrid/check_protected_paths.sh` and the `postedit_protected_paths.ps1` PostToolUse hook:

- `collectors/`
- `workflows/`
- `governance/`
- `monitoring/`
- `connectors/`
- `storage/migrations/`

Any concerns located in these paths above (R8, dry-run gating bug, etc.) **cannot be remediated until Move 0 closes**, regardless of severity. R19 is the exception because it requires only operational decisions, not code edits to protected paths.

## Categories Not Investigated In This Pass

- TODO/FIXME/HACK comment census (orchestrator did not run a project-wide grep)
- `@pytest.mark.skip` / `xfail` test inventory beyond `tests/KNOWN_FAILURES.md`
- Performance bottleneck deep-dive
- Full security audit (use `/cso` for that)
- Dependency CVE scan

These are explicit gaps in this concerns map; do not assume "no entry" means "no issue."
