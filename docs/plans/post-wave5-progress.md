# Progress: Post-Wave 5 Milestone Planning

## Session: 2026-02-12
## Branch: main (856689f)

---

## Session Log

### 2026-02-12 — Assessment Phase

**Actions taken:**
1. Restored memory-keeper checkpoint `wave5-committed-pushed`
2. Started new session `wave6-planning`
3. Launched 3 parallel exploration agents:
   - Agent 1: Roadmap and planning document survey (comprehensive)
   - Agent 2: Wave 5 file existence validation (all files confirmed present)
   - Agent 3: TODO/FIXME/disabled feature catalog (10+ systems found)
4. Launched Agent 4: Production readiness gap analysis (scored 6.5/10)
5. Created planning files:
   - `docs/plans/2026-02-12-post-wave5-milestones.md` (task plan)
   - `docs/plans/post-wave5-findings.md` (findings)
   - `docs/plans/post-wave5-progress.md` (this file)

**Key discoveries:**
- Wave 5 is 100% complete (initial estimate of 60% was wrong)
- 10+ implemented feature systems are disabled behind flags
- Batch publish to Notion is dry-run only (TODO in batch.py:193)
- Config validator exists but not auto-invoked at startup
- OPS docs exist but need consolidation (operator-guide.md: 159 lines, architecture-overview.md: 162 lines)

### 2026-02-12 — Reviewer Corrections

**Corrections accepted (3 factual errors in original proposal):**
1. Phase G is NOT dormant — wired in pipeline.py:575 with 977 lines of tests
2. OPS docs already exist (operator-guide.md, architecture-overview.md)
3. Total test count is 6709 (not 2009) with 1 collection error

**Reviewer additions endorsed:**
- G0 Baseline Integrity Gate (before M1) — best addition
- M3 moved before M2 (higher business ROI)
- STRICT_CONFIG_VALIDATION graduated rollout
- Phase G cleanup deferred until post-activation

### 2026-02-12 — Sprint Decision

**User selected:** Full G0 -> M1 -> M3 sequence (~7-9 days)

**Revised sprint plan (reconciled with reviewer):**
| # | Milestone | Priority | Est. Effort |
|---|-----------|----------|-------------|
| G0 | Baseline Integrity Gate | PREREQUISITE | 1-2 days |
| M1 | Production Activation | CRITICAL | 3-4 days |
| M3 | Batch Publish Wire-up | HIGH | 2-3 days |

### 2026-02-12 — Deep Exploration (Phase 2)

**Agent 5: Config validation + startup paths**
- `validate_config()` returns `List[ConfigIssue]`, `print_config_report()` returns bool
- API insertion: `api/main.py` line 55-57 (after startup_check)
- CLI insertion: `run_pipeline.py` line ~6096 (after setup_logging)
- No STRICT_CONFIG_VALIDATION env var exists yet — new addition
- 33 existing tests (27 unit + 6 integration)

**Agent 6: Batch publish wiring**
- NotionPusher needs: SignalStore (available), NotionConnector (from env), VerificationGate (no deps)
- Factory: `create_connector_from_env()` reads NOTION_API_KEY + NOTION_DATABASE_ID
- Delivery policy: `assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)` already in commit_batch()
- TOCTOU hash guard: SHA256[:16] of sorted review_ids — must preserve
- Recommended: per-request pusher construction (not singleton) **(SUPERSEDED -- see v2 Refinement Review below: app-scoped connector in lifespan)**

---

### 2026-02-13 -- v2 Refinement Review

**9 findings from v2 reviewer, all evaluated:**

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | HIGH | NotionTransport lifecycle leak (per-request) | CONFIRMED -- plan changed to app-scoped |
| 2 | HIGH | Hardcoded known failure count brittle | ACCEPTED -- generated baseline artifact |
| 3 | HIGH | `tail -1` shell-fragile on Windows | ACCEPTED -- Python-parsed, shell-agnostic |
| 4 | MEDIUM | "Read-only activation" misnaming | ACCEPTED -- renamed "Low-risk activation" |
| 5 | MEDIUM | Error contract underspecified | ACCEPTED -- 503 NOTION_NOT_CONFIGURED |
| 6 | MEDIUM | Test ownership blurred (API+CLI) | ACCEPTED -- split test files |
| 7 | LOW | Volatile numeric claims | ACCEPTED -- snapshot context added |
| 8 | LOW | Encoding artifacts (em-dashes) | ACCEPTED -- ASCII-safe |
| 9 | LOW | Line-number references brittle | ACCEPTED -- function anchors |

**Architectural decision change:** M3.1 now uses app-scoped NotionConnector/Transport
in `lifespan()` (not per-request). NotionTransport owns `httpx.AsyncClient` requiring
explicit `shutdown()`. Verified against `connectors/notion_transport.py`.

**New task added:** M3.0 (initialize Notion connector in lifespan + shutdown on teardown)

**Plan updated to v2:** `docs/plans/2026-02-12-post-wave5-milestones.md`
**Findings updated:** `docs/plans/post-wave5-findings.md` (Findings 10-11 added)

---

### 2026-02-13 — G0 Baseline Integrity Gate: COMPLETE

Commit `88d43af` on main. Collection error fixed, smoke suite created (13 tests:
10 pass, 3 skip), baseline artifact generated.

### 2026-02-13 — M1 Production Activation: COMPLETE

Commits `a95168a`, `5974df3` on main. Config validation wired into API lifespan +
CLI startup, activation runbook created, 14 new tests. SPCMonitor constructor fixed.
6759 tests, 0 collection errors.

### 2026-02-13 — M3 Batch Publish Wire-up: COMPLETE

**Files modified (5):**
- `api/main.py` — NotionTransport + NotionConnector in lifespan (fail-open, shutdown on teardown)
- `api/routers/batch.py` — Replaced TODO with NotionPusher wiring; 503 for missing connector
- `api/routers/entities.py` — Real COUNT(*) query replacing `len(results)` for pagination total
- `api/routers/companies.py` — Real COUNT(*) query replacing `len(results)` for pagination total
- `tests/api/test_batch_router.py` — Fixture updated: `notion_connector` mock added for M3 compatibility

**Files created (2):**
- `tests/api/test_batch_real_commit.py` — 14 tests (dry-run regression, 503, 423, TOCTOU, mock push, partial failure, audit)
- `tests/integration/test_delivery_mode_progression.py` — 6 tests (staging->manual->batch progression, envelope consistency)

**New tests:** 20 (14 + 6)
**Regression gate:** 490 passed (API + integration + batch workflow)

**M3 Gate verified:** `dry_run=False` + `DELIVERY_MODE=batch_publish` + valid connector
-> real Notion push with correct `notion_page_id` persisted; error contracts verified.

---

### Post-M3 Stabilization Checkpoint (before next milestone)

Before advancing to M4 or any M3-adjacent work:

1. **Canary publish:** Run one controlled non-dry-run batch publish against Notion
   staging database (`DELIVERY_MODE=batch_publish`, 1-2 items max). Verify:
   - `notion_page_id` persisted on `batch_items` row
   - Audit log entry with `action_type=batch_commit` present
   - Error envelope shape in API logs (503/423/409 as applicable)
2. **Log review:** Confirm NotionTransport startup/shutdown messages appear in
   API logs (`NotionConnector initialized (app-scoped)` / `NotionTransport shut down`)
3. **Gate pass:** Only proceed to next milestone after canary confirms real Notion
   round-trip with correct persistence.

---

### Pre-Merge Regression Gate (M3/M4-adjacent changes)

Required command before merging any change touching batch publish, delivery policy,
Notion wiring, or pagination:

```bash
python -m pytest tests/api/ tests/integration/ tests/workflows/test_batch_publisher.py -v --tb=short
```

Expected: **490+ passed, 0 failed** (count increases as tests are added).

This gate covers: all API routers (296), batch e2e (15), delivery mode progression (6),
batch real commit (14), batch publisher workflow (12), and remaining integration tests.

---

## Next Steps
- [x] User review of milestone priorities and strategy
- [x] Select first milestone to execute (G0 -> M1 -> M3)
- [x] Write implementation plan with task-level detail (v1)
- [x] Incorporate v2 reviewer refinements (9 findings, all accepted)
- [x] G0: fix collection error, generate baseline artifact, create smoke suite
- [x] M1: wire config validator (API + CLI), create activation runbook
- [x] M3: initialize app-scoped Notion connector, wire batch commit, test error contracts
- [ ] Post-M3 stabilization checkpoint (canary publish + log review)
- [ ] Decide next milestone (M4 or M2)
