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

## Next Steps
- [x] User review of milestone priorities and strategy
- [x] Select first milestone to execute (G0 -> M1 -> M3)
- [x] Write implementation plan with task-level detail (v1)
- [x] Incorporate v2 reviewer refinements (9 findings, all accepted)
- [ ] Create feature branch for sprint
- [ ] Begin G0: fix collection error, generate baseline artifact, create smoke suite
- [ ] Begin M1: wire config validator (API + CLI), create activation runbook
- [ ] Begin M3: initialize app-scoped Notion connector, wire batch commit, test error contracts
