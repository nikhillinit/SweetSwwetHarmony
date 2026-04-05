## Task Statement
Create a deliberate consensus plan to close the last operational thesis cleanup item using the existing `thesis-classify-batch` path.

## Desired Outcome
- Produce an execution-ready plan for a scratch-first rehearsal and bounded live run of the existing batch path.
- Use the existing CLI surface only; no new code path.
- Define clear verification so the remaining missing-any-thesis-row cohort reaches zero.

## Known Facts / Evidence
- Live DB query shows exactly one signal in the last 90 days still missing any thesis row:
  - `signal_id=438`
  - `company_name=Mercari`
  - `source_api=greenhouse_jobs`
  - `created_at=2026-02-26T06:33:29.255596+00:00`
- The stale latest-row cohort is already zero.
- Overdue regret checks are already zero.
- `python -m ops.cli quality --help` shows `thesis-classify-batch` is available.
- `ops.quality.thesis.iter_signals_missing_thesis()` selects missing-any-row signals using `signals.created_at`, which matches the current cleanup goal.
- The current cleanup plan already treats the missing-any-row cohort as addressable by the existing batch path.

## Constraints
- Planning only in this turn.
- Use the existing batch path, not a new code path.
- Because the command mutates live thesis classifications and depends on runtime LLM behavior, use scratch-first rehearsal before any live run.
- No new dependencies.

## Unknowns / Open Questions
- Whether the safest execution shape is `thesis-classify-batch --limit 1` or a slightly higher bounded limit for defense against selector drift.
- Whether the live run should use the same prompt version as the earlier thesis cleanup plan (`v1.6.0`) or rely on the current CLI default.

## Likely Codebase Touchpoints
- [ops/quality/thesis.py](/C:/dev/Harmonic/ops/quality/thesis.py)
- [ops/quality_cli.py](/C:/dev/Harmonic/ops/quality_cli.py)
- [tests/ops/quality/test_thesis.py](/C:/dev/Harmonic/tests/ops/quality/test_thesis.py)
- [tests/ops/quality/test_quality_cli.py](/C:/dev/Harmonic/tests/ops/quality/test_quality_cli.py)
- [post-v1.6.0-cleanup-validated-plan.md](/C:/dev/Harmonic/.omx/plans/post-v1.6.0-cleanup-validated-plan.md)
