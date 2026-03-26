# Next Steps Plan - Post Phase 3

**Created:** 2026-03-25  
**Validated against code and sandbox state:** 2026-03-25

## Current State

| Item | Status |
|------|--------|
| Pending queue | 129 signals (`arxiv:56`, `rss_feeds:35`, `hacker_news:28`, `news_api:10`) |
| Quality labels (all time) | `FP:187`, `TP:19`, `UNSURE:5` |
| LLM thesis mode | `.env` is still `LLM_THESIS_MODE=shadow` |
| Skip threshold | `THESIS_SKIP_LLM_BELOW` is unset, so the effective code default is `0.2` |
| Delivery mode | `DELIVERY_MODE=batch_publish` |
| Merge writes | `MERGE_WRITES_ENABLED=shadow` |
| Activation gate | Step 3 and Step 4 are blocked until canary freshness is restored |
| Overdue regret checks | `0` |
| Scheduled regret date | 2026-03-30 for `DELIVERY_MODE` and `LLM_THESIS_MODE` |

## Validated Sequence

### Phase 0: Refresh gates and capture preflight [status: blocked]
**Priority: HIGH** - The immediate blocker is stale gate evidence, not the March 30 regret date.

Tasks:
- [ ] T0.1: Run a fresh DB backup.
- [ ] T0.2: Refresh the canary.
- [ ] T0.3: Run `python run_pipeline.py activation-check --step 3 --json`.
- [ ] T0.4: Run `python run_pipeline.py activation-check --step 4 --json`.
- [ ] T0.5: Run `python scripts/thesis_activation_sandbox.py --db-path signals.db --source-api hacker_news --json`.

Exit criteria:
- Step 3 and Step 4 are green or an explicit SPC exception is documented.
- The sandbox report matches the expected pending-by-source shape.

### Phase 1: Scratch proof on hacker_news [status: not_started]
**Priority: HIGH** - Prove LLM firing on a scratch DB before any live env flip.

Tasks:
- [ ] T1.1: Run `python scripts/thesis_activation_sandbox.py --db-path signals.db --source-api hacker_news --batch-size 28 --llm-mode shadow --skip-llm-below 0.0 --execute-process --json`.
- [ ] T1.2: Verify `sandbox_run.proof.llm_fired=true`.
- [ ] T1.3: Verify `sandbox_run.proof.new_llm_rows > 0`.
- [ ] T1.4: Review `backlog.pending_by_source` and `target_source.current_pending_state` in the report.

Rationale:
- `process --dry-run` is not a safe proof step on the live DB.
- The scratch copy absorbs thesis rows and confidence ledger writes.
- Shadow mode proves LLM execution without changing live routing posture.

### Phase 2: Live activation after gate green [status: not_started]
**Priority: HIGH** - Only start after Phase 0 and Phase 1 are clean.

Tasks:
- [ ] T2.1: Set `LLM_THESIS_MODE=active` in `.env`.
- [ ] T2.2: Set `THESIS_SKIP_LLM_BELOW=0.0` in `.env`.
- [ ] T2.3: Process `hacker_news` first: `python run_pipeline.py process --source-api hacker_news --batch-size 28`.
- [ ] T2.4: Verify fresh thesis rows have `model != null`.
- [ ] T2.5: If HN looks clean, process `arxiv`, `rss_feeds`, and `news_api` as separate follow-on batches.

### Phase 3: Source-specific backlog triage [status: not_started]
**Priority: HIGH** - The backlog is not a single `keyword_score=0` problem.

Tasks:
- [ ] T3.1: Group each source into `missing_thesis`, `keyword_only_latest`, and `llm_latest`.
- [ ] T3.2: Treat `hacker_news` keyword-only backlog separately from `arxiv` missing-row backlog.
- [ ] T3.3: Compare FP reduction by `source_api` after activation, not just globally.

### Phase 4: Backfill strategy [status: not_started]
**Priority: MEDIUM** - Split missing-row backfill from reclassification.

Tasks:
- [ ] T4.1: Use `python -m ops.cli quality --db signals.db thesis-classify-batch --days 90 --limit 25` only for signals missing any thesis row.
- [ ] T4.2: Plan a separate reclassify path for existing keyword-only rows.
- [ ] T4.3: Sample-check older HN keyword-only rows before scaling any historical reclassify.

### Phase 5: Monitoring hardening [status: not_started]
**Priority: LOW-MEDIUM**

Tasks:
- [ ] T5.1: Add zero-volume alerting for collectors.
- [ ] T5.2: Review SPC configuration and exception posture.

### Phase 6: Regret check (2026-03-30) [status: scheduled]
**Priority: HIGH** - This remains the Step 4B decision date, but it is not the current blocker.

Tasks:
- [ ] T6.1: Run `python -m monitoring.feature_gate overdue --db signals.db --json`.
- [ ] T6.2: If clear, evaluate `MERGE_WRITES_ENABLED shadow -> active`.
- [ ] T6.3: Update governance audit trail.

## Key Decisions

1. Validate on a scratch DB before any live `.env` change.
2. Process `hacker_news` first, then the other sources only after live verification.
3. Treat `thesis-classify-batch` as a missing-row backfill only, not a fix for keyword-only rows.
