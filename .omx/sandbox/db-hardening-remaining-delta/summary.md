# Sandbox Validation: DB Hardening Remaining Delta

Date: 2026-04-05
Sandbox worktree:
- `C:\dev\Harmonic\.worktrees\db-hardening-remaining-delta-sandbox`
Sandbox branch:
- `sandbox/db-hardening-remaining-delta`

## Goal

Validate the approved remaining-delta plan in an isolated implementation lane before any main-branch coding.

## Sandbox Changes

1. Normalized `scripts/restore_db.py` onto the shared DB-path helper contract:
   - `add_db_path_args()`
   - `resolve_db_path()`
   - `resolve_db_path_env()`
2. Updated restore tests so the canonical runtime path uses `--db-path`.
3. Added a targeted CI guardrail:
   - `tests/ci/test_restore_db_cli_contract.py`
4. Updated the operator-facing restore example in:
   - `docs/operator-quickstart.md`

## Focused Verification

Command:

```powershell
pytest tests/scripts/test_db_hardening_priority_scripts.py tests/scripts/test_backup_restore.py tests/ci/test_no_hardcoded_production_db_access.py tests/ci/test_no_default_signals_db_parser_fallbacks.py tests/ci/test_restore_db_cli_contract.py tests/utils/test_db_tool_lock.py -q
```

Result:
- `35 passed in 26.12s`

## Validated Revisions Surfaced By Sandbox

1. The best bounded implementation path is to normalize `restore_db.py` onto the shared helper contract, not keep it as a special `--db` exception.
2. The right CI follow-up is a targeted restore-contract guardrail, not a broader non-literal hardcoded-path expansion.
3. The plan should treat broader CI widening as out of scope unless a new concrete bypass case appears in the priority script class.

## Integration Rule

These sandbox findings were integrated back into:
- `.omx/plans/prd-db-hardening-remaining-delta.md`
- `.omx/plans/test-spec-db-hardening-remaining-delta.md`
- `.omx/plans/db-hardening-remaining-delta-spec-ralplan.md`

No main-branch code was changed in this validation step.
