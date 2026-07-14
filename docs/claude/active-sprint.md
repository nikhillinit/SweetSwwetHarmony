# Active Sprint - Move "Q10 Fallback Promotion"; Track A Complete 2026-07-14

Rebuilt on 2026-07-14 from live `main` == `origin/main` at `2f8edad`
(merge of PR #308) after the operator-ordered Track A backlog burn-down
completed. Supersedes the 2026-07-14 "Queue Complete" refresh; queue
history (Q0-Q9, T3-A) is condensed to the historical note at the bottom.

## Current State

- `main` == `origin/main` == `2f8edad`. No open PRs. Track A of the
  "Q10 Fallback Promotion" move is COMPLETE: PRs #304-#308 all merged
  2026-07-14.
- **Operator decisions recorded 2026-07-14:** (a) Track A order was
  codex-reviewer first, then Q7 db-path, truncation, auto-close,
  ruleset-parity; (b) **gemini is permanently deprecated** -- the Google
  lane is antigravity (agy) only; this is a standing ruling, not an auth
  outage; (c) the move is named **"Q10 Fallback Promotion"**.

## Track A Outcomes (all merged 2026-07-14)

| PR | Slice | Net effect |
|---|---|---|
| #304 | Hermes reviewer loud lane failures | Dispatched-but-failed reviewer lanes are harness-assigned `verdict: error` (never silent `skip`); new `no_reviewer_lane_errors` postflight gate fails the run even when quorum is otherwise satisfied; codex wrapper always hands codex a closed stdin (codex >= 0.144 reads piped stdin to EOF); Codex CLI upgraded 0.140.0 -> 0.144.4 (operator-directed, model stays `gpt-5.6-sol`) |
| #305 | Q7 residual private-graph db-path | `import-emails`, `sync-lps`, `relationship-health`, `warm-intros` resolve `--db-path` via `resolve_private_graph_db_path()`; a source-level test pins that no cwd-relative `private_graph.db` fallback reappears |
| #306 | failure-issue auto-close-on-recovery | `failure-issue` action gains `mode: close` + validated `recovery-run-id`; Daily Pipeline and Litestream Restore Verify close their trackers in-workflow on production-equivalent success; thesis trackers close via the `thesis-eval-recovery.yml` workflow_run observer (scheduled green runs only) |
| #307 | ruleset-parity drift check | `python -m scripts.ci.check_ruleset_parity` diffs live ruleset 12778551 vs the runbook check table (plus enforcement + strict policy); exit 1 on drift; live run at merge: PARITY OK (7 checks, enforcement=active, strict=True) |
| #308 | deliberation truncation preflight | `TASK_TEXT_LIMIT` no longer silently slices plan input; new `input_within_task_text_limit` preflight gate fails oversized input before any reviewer spawns |

## Hermes Provider Configuration (current)

- **codex: REPAIRED (execute + review).** Root causes of the empty
  reviewer lane were (1) CLI 0.140.0 rejecting `gpt-5.6-sol` (HTTP 400)
  -- fixed by upgrading to 0.144.4 -- and (2) codex >= 0.144 blocking on
  inherited piped stdin under non-TTY parents -- fixed in
  `integrations/codex_wrapper.py` (#304). Live wrapper evidence 2026-07-14:
  exit 0 in 20.6s with clean JSON on stdout. Host requires codex-cli
  >= 0.144.4.
- **gemini: PERMANENTLY DEPRECATED** (operator ruling 2026-07-14). Do
  not propose re-enabling. The gemini-provider local test expectation is
  a TODOS slice, now unblocked by this ruling.
- **antigravity (agy): reviewer-only** (the Google lane).
- **kimi: working** (execute + review).
- **claude: orchestrator lane.**
- **`routing.runtimeFallbackEnabled` = false** and MUST stay false until
  the Q10 preconditions complete with explicit operator approval via
  `hermes task config-promote --policy-evidence`.

## Track B - Q10 Promotion (in progress: telemetry accumulation)

1. **Telemetry accumulation (STARTED 2026-07-14).** First real routed
   runs recorded: `contract-check` dry-run passed; `ledger-audit`
   dry-run ran (see review item below); the first live deliberation
   panel (`hermes_20260714_183654_9b0dead9`, panel codex+kimi) validated
   the loud-failure machinery in production -- a codex 300s timeout
   (caused by an orphaned codex process, since cleaned) surfaced as
   `verdict: error` + `malformed_reviewer_output`, not a silent skip.
   `ai-logs/hermes/provider-state.json` still does not exist (created
   lazily on spawn-failure classification). More low-risk routed runs
   are needed to compute by-provider FP/FN rates.
2. **Donor items** (each its own TDD slice + PR): plan-hash staleness
   matrix; per-run event JSONL; fail-closed reviewer repair/verdict
   sentinel; child-process model-API-key blocklist.
3. **Canary:** one low/medium-risk run demonstrating fallback advancing
   after a spawn/rate-limit failure.
4. **Proposal to operator** with by-provider evidence; only an explicit
   operator decision flips the flag.

## Operator Review Items (open)

- **Local ledger audit findings:** `ledger-audit` dry-run
  (`hermes_20260714_183501_081efde1`) reports 11 critical
  `missing_required_artifact: restore_readiness.json` findings across
  historical restore runs 2026-05-31..2026-06-03 in the LOCAL ledger.
  CI's ledger gate does not see these (fresh checkouts). Decide:
  backfill, waive, or retire those run dirs.
- **Thesis Golden Set Gate Gemini quota:** three back-to-back PR gate
  runs (58 samples each) exhausted the Gemini API quota on 2026-07-14.
  Fail-closed behavior worked as designed; expect transient gate
  failures when several PRs run concurrently.

## Scheduled-Workflow Outcomes (the three always-on production loops)

| Workflow | Cadence | Status (as of 2026-07-14) |
|---|---|---|
| Daily Pipeline (`discovery-pipeline.yml`) | cron 0 6 UTC | GREEN; closes `daily-pipeline-failure` tracker on production-equivalent success (#306) |
| Litestream Restore Verify Nightly | cron 37 10 UTC | GREEN; closes `litestream-verify-failure` tracker on success (#306) |
| Thesis Classification Evaluation | cron 0 2 * * 0 (Sun) | GREEN; trackers auto-close via `thesis-eval-recovery.yml` observer on scheduled green runs (#306) |

Failure trackers now close automatically with a link to the first green
run; manual closure is no longer required.

## Remaining TODOS (see TODOS.md for pickup context)

1. Hermes gemini-provider test expectation (unblocked: gemini is
   permanently deprecated).
2. Local failing-test triage (5 independent files).
3. Trust-release status-table generator (or retire the do-not-hand-edit
   instruction).

## Operating Notes

- Primary checkout `C:\dev\Harmonic` stays dirty with local state;
  verify `signals.db` row count (612, schema 53) after any pull and
  prefer fresh worktrees for all changes.
- Use `C:\dev\Harmonic\.venv\Scripts\python.exe`; the venv has no
  editable install, so run from the worktree under test.
- PS 5.1: embedded double quotes break `git commit -m` here-strings and
  `python -c` payloads (the commit fails but a chained push still runs);
  use quote-free messages / script files and verify with `git log -1`.
- Killed/stopped Hermes runs can orphan codex children that block later
  spawns until their 300s timeout; check `Get-Process codex` when a
  lane times out unexpectedly.

## Historical Note

The 2026-07-10 operator-adjudicated queue (Q0-Q9) closed 2026-07-14 with
recovery gate T3-A ratified (cloud backup live: bucket
`harmonic-signals-backup-prod`, environment `sqlite-production-backups`,
verified 612-row/schema-53 snapshot proven by restore-from-S3). Branch
ruleset 12778551 is live and strict (7 required checks, now
parity-checked by `scripts/ci/check_ruleset_parity.py`). Hermes Track A
(H0-H5), the April red-team Move 0, the 2026-05 signals.db incident
(#149, closed), and the 2026-06 trust-release bursts are historical; see
`docs/archive/sprint-history.md` and the wiki session entries.
