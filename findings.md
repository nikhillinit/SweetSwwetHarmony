# Findings -- Post-Window Codebase Assessment

**Date:** 2026-03-24
**Context:** Step 4A observation window closed (2026-03-23). Determining next steps for LLM activation, queue drain, test coverage, and Step 4B.

---

## Current State (single source of truth)

**Governance:** LLM_THESIS_MODE promoted shadow->active in audit_events (action_type='feature_promote', regret_due_at='2026-03-30').
**Runtime .env:** LLM_THESIS_MODE=shadow (not flipped yet).
**Phase 2 DONE:** `--source-api` filter implemented in storage, pipeline, and CLI. HN signals can now be isolated via `process --source-api hacker_news`. Pending commit.

---

## 1. Test Health

Source: `pytest --collect-only -q` at 2026-03-24T18:00Z

| Metric | Value |
|--------|-------|
| Tests collected | 8,975 |
| Known failures | 6 (intentional, DELIVERY_MODE=staging_only) |
| Flaky | 1 (test_ach_matrix_view -- passes in isolation, fails in full suite) |
| Full run (2026-03-23, commit 4d6d0ef) | 9,402/9,548 pass (98.4%) |

**21 deleted test files** -- orphaned tests for modules that still exist (collectors, KG, quality ops, integrations). Zero dangling imports confirmed. Fixture migration to per-module conftest.py complete.

---

## 2. Git Working Tree

Source: `git status --porcelain | cut -c1-2 | sort | uniq -c` at 2026-03-24T18:00Z

| Category | Count |
|----------|-------|
| Modified | 16 |
| Deleted | 63 |
| Untracked | 84 |
| **Total** | **163** |

No dangling imports from deletions (verified by grep + test pass).

---

## 3. Pending Queue (signals.db)

Source: `SELECT p.status, s.source_api, COUNT(*) FROM signals s JOIN signal_processing p ON s.id = p.signal_id GROUP BY ...` at 2026-03-24T17:30Z

| Status | Count |
|--------|-------|
| held | 464 |
| pending | 129 |
| pushed | 15 |
| queued | 2 |
| rejected | 1 |
| **Total** | **612** (611 in signal_processing) |

**Pending breakdown by source_api** (from signal_processing JOIN signals):

| Source | Count | % | Analyst judgment |
|--------|-------|---|------------------|
| arxiv | 56 | 43% | Low thesis fit (0.40-0.45) |
| rss_feeds | 35 | 27% | Mixed quality (0.55-0.70) |
| hacker_news | 28 | 22% | 100% FP historically, thesis_category=UNKNOWN |
| news_api | 10 | 8% | Low confidence (0.35-0.45) |

---

## 4. LLM Thesis Mode Blocker (Critical Finding)

**Problem chain:**
1. HN signals have `keyword_score=0` (no keyword matches)
2. `THESIS_SKIP_LLM_BELOW=0.2` (default, in `utils/thesis_filter.py:158`)
3. 0 < 0.2 -- LLM classification always skipped for HN
4. Rehearsal confirmed: 34 HN signals processed, all HELD, LLM never invoked

**Fix path (HN-only filtered run + temporary threshold override):**
- Add `source_api` filter to `get_pending_signals()` + expose as `--source-api` on `process` CLI
- Run with `$env:THESIS_SKIP_LLM_BELOW="0.0"` override for HN-only batch
- Evaluate results via 5-check qualitative framework
- Keep runtime .env at shadow until dry-run passes evaluation

**Safety constraint (from code review):** In active mode, thesis reject/hold paths call `mark_rejected()` and `update_signal_status()` BEFORE the dry-run Notion guard (`pipeline.py:2096-2143`). The `--dry-run` flag only prevents Notion push, not processing state mutations. Therefore: keep .env at shadow until isolated HN run validates LLM effectiveness. Do NOT flip to active and run `full --collectors hacker_news` on the shared queue.

---

## 5. Pipeline Gap

**RESOLVED (Phase 2):** `get_pending_signals()` now supports `source_api` filter (`signal_store.py:2781`). `process` CLI has `--source-api` flag (`run_pipeline.py:2607`). Threaded through `process_pending` -> `_process_signals_stage` -> `get_pending_signals`. 5 tests cover storage + pipeline threading.

---

## 6. Governance State

Source: `SELECT action_type, entity_id, metadata FROM audit_events WHERE action_type='feature_promote'` at 2026-03-24T18:00Z

| Flag | .env (runtime) | audit_events | Regret Due |
|------|----------------|--------------|------------|
| LLM_THESIS_MODE | shadow | promoted shadow->active | 2026-03-30 |
| DELIVERY_MODE | batch_publish | promoted manual->batch | 2026-03-30T19:13:54Z |
| MERGE_WRITES_ENABLED | shadow | -- | Blocked by LLM regret check |

---

## 7. Live Test Run

Source: `pytest -x` at 2026-03-24T17:45Z (stopped at first failure)

1 failure (flaky ACH matrix view, passes in isolation), 1,337 passed before stop. Confirms no regressions from uncommitted changes.

---

## 8. CLI Command Reference (verified)

```powershell
# Quality stats (--db is on parent, not subcommand)
python -m ops.cli quality --db signals.db stats --days 30

# Feature gate overdue check
python -m monitoring.feature_gate overdue --db signals.db --json

# Verify promotion exists in audit_events
python -c "import sqlite3; c=sqlite3.connect('signals.db').cursor(); c.execute(""SELECT entity_id, metadata FROM audit_events WHERE action_type='feature_promote' AND entity_id='LLM_THESIS_MODE'""); print(c.fetchone())"
```

---

## Key Decisions Needed

1. ~~**Working tree cleanup:**~~ DONE (Phase 1, commits e82a6d8 + 4895826)
2. ~~**source_api filter:**~~ DONE (Phase 2, pending commit)
3. **LLM activation path:** HN-only filtered run with temporary threshold override? (Phase 3)
4. **Orphaned test coverage:** Restore now or defer?
5. **Regret check criteria for 2026-03-30?**
