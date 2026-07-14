# Active Sprint - 2026-07-10 Queue Complete; Recovery Ratified (T3-A)

Rebuilt on 2026-07-14 from live `main` == `origin/main` at `e6ed3e2`
(merge of PR #302) after `git fetch origin main --prune`. Supersedes the
2026-06-16 Hermes Track A refresh; Track A history is condensed to the
historical note at the bottom.

## Current State

- `main` == `origin/main` == `e6ed3e2`. `gh pr list --state open` returns `[]`.
  No open issues.
- The **2026-07-10 operator-adjudicated queue is complete** through live
  evidence: PRs #293 (Q5 restore re-fingerprint), #294 (Q4 thesis-eval +
  `.github/actions/failure-issue`), #295 (Q6+6b Hermes spawn classification +
  agy lane fix), #296 (Q8 import-light startup), #297 (Q7 private-graph
  resolver), #298 (Q2/Q3 recovery workflows), #299 (Q1 ruleset evidence +
  runbook), #302 (cloud-backup runbook fixes) all merged.
- **Branch ruleset 12778551 is live and strict**: pull_request rule
  (approvals=0, solo-maintainer), 7 required status checks (strict/up-to-date),
  deletion + non-fast-forward blocked. Branches behind main need
  `gh pr update-branch` before merge. Rollback JSON:
  `docs/runbooks/evidence/ruleset-prior-20260710.json`.
- **Recovery-complete gate T3-A CLOSED 2026-07-14.** Cloud backup provisioned
  2026-07-12 (bucket `harmonic-signals-backup-prod`, scoped IAM user, 6
  environment secrets under `sqlite-production-backups`,
  `SQLITE_RESTORE_MIN_SIGNALS=500`); the verified 612-row / schema-53 snapshot
  (SHA-256 `AB1CA9C8...2B51`) was seeded and proven by restore-from-S3.
  Evidence bundle: `.tmp/queue-exec-20260710/execution-status.md`.

## Scheduled-Workflow Outcomes (the three always-on production loops)

| Workflow | Cadence | Status (as of 2026-07-14) |
|---|---|---|
| Daily Pipeline (`discovery-pipeline.yml`) | cron 0 6 UTC | GREEN — bootstrap-from-replica ratified (manual 29205726663, scheduled 29231587906); republishes `signals-db-latest` at 90d retention |
| Litestream Restore Verify Nightly | cron 37 10 UTC | GREEN — two consecutive scheduled successes (29247089443 on 07-13, 29327958072 on 07-14); summary = integrity ok / schema 53 / 612 rows / min 500 |
| Thesis Classification Evaluation | cron 0 2 * * 0 (Sun) | GREEN — scheduled run 29177808939 accuracy 1.000 (threshold 0.75); below-threshold opens `thesis-eval-alert`, infra failure opens `thesis-eval-infra` |

All three fail closed and open/update a labeled tracking issue via
`.github/actions/failure-issue` on failure. The action does NOT auto-close on
recovery (TODOS.md) — close tracker issues manually with a link to the first
green run.

## Hermes Provider Configuration (current)

- **gemini: disabled** (auth-dead; cbcab36). A pre-existing local test
  expectation (`test_project_config_enables_gemini_cli_without_api_key`) is
  broken on main because of this — tracked in TODOS.md, not in CI gates.
- **antigravity (agy): reviewer-only.** The spawn lane was repaired by #295
  (6b: `--print-timeout Ns --print` stdin flavor, contract-tested);
  `supportsExecute` stays false.
- **codex: execute-capable but reviewer lane returns empty content** (silently
  degrades to skip) — do NOT count codex toward deliberation panel quorum until
  the TODOS.md item is fixed.
- **kimi: working** (execute + review).
- **claude: orchestrator lane** (no wrapper execute).
- **`routing.runtimeFallbackEnabled` = false** in
  `.claude/hermes/model-routing.json` and MUST stay false until the Q10
  preconditions are met: `ai-logs/hermes/provider-state.json` does not exist
  yet (it is created lazily by the cooldown tracker; zero execute-lane runs
  have occurred since #295 merged), so there is no by-provider FP/FN shadow
  evidence to evaluate. Promotion requires `hermes task config-promote
  --policy-evidence` + explicit operator approval — never a silent flip.

## Next Queue

1. **Q10 (parked, operator decision):** accumulate shadow telemetry from real
   routed runs; then the unadopted donor items (plan-hash staleness matrix,
   per-run event JSONL, fail-closed reviewer repair/verdict sentinel,
   child-process model-API-key blocklist), then a low/medium-risk canary,
   then the promotion proposal.
2. **TODOS.md backlog** (all with pickup context in that file): codex
   empty-content reviewer lane; deliberation 12k silent truncation;
   ruleset-parity drift check; failure-issue auto-close; gemini test
   expectation; Q7 residual `--db-path` cwd defaults; trust-release
   status-table generator; CLAUDE.md Active Sprint block regeneration
   (via `claude-md-improver`).
3. No other active code slice is named. New work starts from fresh live
   discovery + a fresh `.worktrees/...` lane per CLAUDE.md practice.

## Operating Notes

- Primary checkout `C:\dev\Harmonic` stays dirty with local state and
  keepalive artifacts; verify `signals.db` row count (612) after any pull
  (gitignored-clobber risk) and prefer fresh worktrees for all changes.
- Use `C:\dev\Harmonic\.venv\Scripts\python.exe` for anything importing
  project code; the venv has no editable install, so run from the worktree
  under test.
- PR Evidence Gate gotcha: after `gh pr update-branch`, refresh the evidence
  section for the new head SHA and let the `edited` event re-run the gate —
  never `gh run rerun` the stale gate run.

## Historical Note

Hermes Track A (H0-H5) completed on `main` 2026-06-02 through PR #260 and was
verified in the 2026-06-16 refresh (registry pinned in
`tests/ops/hermes/test_task_registry.py`; 12 registered tasks including
`thesis-eval`). The April red-team Move 0 plan, the 2026-05 signals.db
reversion incident (#149, closed), and the 2026-06 trust-release bursts are
historical; see `docs/archive/sprint-history.md`,
`docs/plans/2026-06-15-trust-release/00-strategy.md`, and the wiki session
entries. Do not reopen H0-H5 without fresh live-repo evidence of a defect.
