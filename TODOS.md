# TODOS

Deferred work with enough context to pick up cold. Format: What / Why / Context / Depends on.

## Hermes: gemini-provider test expectation broken on main

- **What:** Fix `tests/ops/hermes/test_gemini_provider.py::test_project_config_enables_gemini_cli_without_api_key` — it expects gemini `supportsExecute=true`, but cbcab36 disabled the auth-dead gemini lane. Update the expectation (or parametrize on the provider-config state).
- **Why:** A permanently-red local test erodes the signal of the local suite; it is not in CI gates, so it will never force itself onto anyone's queue.
- **Context:** Documented in `.tmp/queue-exec-20260710/execution-status.md` follow-ups (2026-07-10). Local-only failure.
- **Depends on:** a decision whether gemini stays disabled (current state: disabled, auth-dead).

## Local suite: pre-existing failing tests on main (not in CI gates)

- **What:** Triage/fix the known local-only failures: `test_backup_restore.py` TestBackupMain (subprocess sys.path, 4), `test_db_hardening_priority_scripts.py` (6), `test_healthcheck_startup.py` (14), `test_migration_v42_v43.py` (schema-version assert vs v53), `test_pipeline_news_collectors.py` (stale mock fixture, 9/11).
- **Why:** ~34 permanently-red local tests bury real regressions; each has a known, mundane cause (sys.path in subprocess, stale fixtures, hardcoded schema version).
- **Context:** List captured 2026-07-10 in `.tmp/queue-exec-20260710/execution-status.md`; none are in the 7 CI merge gates.
- **Depends on:** nothing; each file is an independent small slice.

## Docs: trust-release status-table generator does not exist

- **What:** Build the generator the trust-release strategy doc promises: emit the milestone-status table from live evidence (`ops/trust_status.py` summarize output + `hermes task ledger-audit` operatorSummary + gh run queries), or explicitly retire the "do not hand-edit" instruction.
- **Why:** `docs/plans/2026-06-15-trust-release/00-strategy.md` and the wiki hot-cache both point to `python -m ops.cli trust status`, which does not exist (`ops.cli` has no `trust` subcommand; `ops/trust_status.py` is the M7 collector-health summarizer with no `__main__`). Every doc refresh currently violates the doc's own instruction.
- **Context:** Discovered during the 2026-07-14 Q9 reconciliation; the 07-14 refresh hand-edited two rows with cited run evidence as the least-bad option.
- **Depends on:** M7 hardening direction (max-age/expiry semantics) if the generator should consume collector-health reports.

## Docs: CLAUDE.md "Active Sprint" block is stale

- **What:** Regenerate the CLAUDE.md "## Active Sprint" block via the `claude-md-improver` skill (per the block's own maintenance note). It still says branch `prep/red-team-hybrid-prep`, "Move 0 ends 2026-04-19", and Move 0 protected paths.
- **Why:** CLAUDE.md is loaded into every session; a three-months-stale sprint block misroutes every fresh session until it cross-checks `docs/claude/active-sprint.md` (refreshed 2026-07-14).
- **Context:** Handoff follow-up from `.omx/plans/queue-completion-handoff-20260711.md` item 4. The block's own instructions say to regenerate at the start of each new move rather than hand-edit mid-session.
- **Depends on:** deciding what the next "move" is (Q10 preparation vs TODOS backlog burn-down).
