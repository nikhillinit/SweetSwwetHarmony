# Sandbox Validation: Thesis Refresh Latest

Date: 2026-04-05

## Goal

Validate the approved thesis-refresh-latest plan with:
1. sandbox implementation and targeted regression coverage
2. scratch DB rehearsal on a copy of `signals.db`
3. bounded live execution on `signals.db`

## Implemented Surfaces

1. `ops/quality/thesis.py`
   - added stale latest-row selector for the fixed 90-day `created_at` cohort
   - added append-only latest-row refresh helper
2. `ops/quality_cli.py`
   - added `quality thesis-refresh-latest`
3. `tests/ops/quality/test_thesis.py`
   - added selector, append-only ordering, idempotence, partial-failure, and backdated-row rollback coverage
4. `tests/ops/quality/test_quality_cli.py`
   - added CLI registration / parsing coverage
5. `docs/runbooks/thesis-refresh-latest.md`
   - added scratch-first runbook for the fixed v1 cohort

Final v1 operator-surface tightening:
- removed the operator-facing `--days` flag so v1 is fixed to the approved 90-day `created_at` cohort

## Sandbox Code Verification

Command:

```powershell
pytest tests/ops/quality/test_thesis.py tests/ops/quality/test_quality_cli.py -q
```

Result:
- `38 passed in 7.29s`

## Scratch Rehearsal

Scratch DB copy:
- `.worktrees/thesis-refresh-latest-sandbox/.omx/sandbox/thesis-refresh-latest/signals-20260405-201047.db`

Scratch pre-run stale latest-row count:
- `491`

Scratch bounded refresh run:

```powershell
python -m ops.cli quality --db .omx\sandbox\thesis-refresh-latest\signals-20260405-201047.db thesis-refresh-latest --limit 5 --prompt-version v1.6.0
```

Scratch run result:
- attempted: `5`
- succeeded: `5`
- failed: `0`

Scratch post-run stale latest-row count:
- `486`

## Live Execution

Fresh live backup:
- `.omx/sandbox/thesis-refresh-latest/signals-20260405-201500.db`

Live pre-run stale latest-row count:
- `491`

Live bounded refresh run:

```powershell
python -m ops.cli quality --db .\signals.db thesis-refresh-latest --limit 600 --prompt-version v1.6.0
```

Live run result:
- attempted: `491`
- succeeded: `491`
- failed: `0`

Live post-run stale latest-row count:
- `0`

Post-fix live no-op confirmation:

```powershell
python -m ops.cli quality --db .\signals.db thesis-refresh-latest
```

Result:
- attempted: `0`
- succeeded: `0`
- failed: `0`

## Outcome

The fixed 90-day `created_at` stale latest-row cohort is cleared.

Implication:
- the `thesis-refresh-latest` code path is implemented
- scratch-first rehearsal succeeded
- the live stale latest-row cohort for v1 reached `0`
