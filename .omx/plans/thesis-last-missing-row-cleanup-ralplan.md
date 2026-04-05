# Thesis Last Missing Row Cleanup RALPLAN

## Scope

Close the final known missing-any-thesis-row item in the last-90-day cohort using the existing `thesis-classify-batch` path only.

Evidence base:
- Context snapshot: `.omx/context/thesis-last-missing-row-cleanup-20260405T211507Z.md`
- Live DB fact: exactly one missing-any-row signal remains in the last 90 days: `signal_id=438`, `Mercari`, `greenhouse_jobs`, `created_at=2026-02-26T06:33:29.255596+00:00`
- `iter_signals_missing_thesis()` selects `signals.created_at >= ?`, orders by `created_at DESC, id DESC`, and honors `LIMIT` ([ops/quality/thesis.py](C:/dev/Harmonic/ops/quality/thesis.py):300)
- CLI exposes `thesis-classify-batch --days --limit --model --prompt-version --stop-on-error` with defaults `limit=200`, `prompt_version=quality-ops-v1` ([ops/quality_cli.py](C:/dev/Harmonic/ops/quality_cli.py):120)
- Existing regression proves the selector now includes recent ingests with older `detected_at` values ([tests/ops/quality/test_thesis.py](C:/dev/Harmonic/tests/ops/quality/test_thesis.py):227)
- Earlier validated cleanup plan pinned `--prompt-version v1.6.0` for this operational cohort ([post-v1.6.0-cleanup-validated-plan.md](C:/dev/Harmonic/.omx/plans/post-v1.6.0-cleanup-validated-plan.md):181)

## RALPLAN-DR

### Principles

1. Use the existing production path only; no new code path, no schema work.
2. Prefer the smallest blast radius that can still prove closure.
3. Pin runtime inputs that affect auditability when prior cleanup work already established a versioned baseline.
4. Verify cohort closure with DB evidence, not CLI success text alone.

### Decision Drivers

1. Operational risk is low but non-zero because this mutates the live `thesis_classifications` table and depends on LLM runtime behavior.
2. The live target cohort is exactly one signal, so over-broad selection is unnecessary.
3. The cleanup series already used `v1.6.0`; switching to CLI defaults would introduce avoidable provenance drift.

### Viable Options

#### Option A: exact closure run (`--limit 1`)

Pros:
- Matches the validated live cohort size exactly.
- Minimizes accidental extra writes if the selector changes between rehearsal and live run.
- Makes before/after verification trivial.

Cons:
- If a second eligible row appears between rehearsal and live run, the run closes only one row and leaves another for a follow-up pass.

#### Option B: slightly larger bound (`--limit 5` or `--limit 10`)

Pros:
- Tolerates small selector drift between rehearsal and live execution.
- Could close newly arrived missing-any-row candidates in the same pass.

Cons:
- Expands blast radius without evidence that drift exists now.
- Weakens the attribution that this run was solely for signal `438`.

### Decision

Choose **Option A: `--limit 1`**.

Rationale:
- Current evidence says the cohort size is exactly one.
- `LIMIT` is enforced by the selector and already regression-covered.
- This task is operational closure, not opportunistic backfill expansion.

## Deliberate Pre-Mortem

1. **Selector drift between rehearsal and live run**
   - Scenario: a new missing-any-row signal appears before the live command.
   - Effect: `--limit 1` may classify a different newest signal first.
   - Mitigation: rerun the pre-flight cohort query immediately before live execution and confirm `signal_id=438` is still the sole candidate.

2. **Runtime classification failure**
   - Scenario: LLM/API failure yields `attempted=1`, `failed=1`, no new row.
   - Effect: operational closure is not achieved.
   - Mitigation: require scratch rehearsal first; use `--stop-on-error`; treat any failure as a stop-and-investigate, not a retry loop on live DB.

3. **Provenance inconsistency**
   - Scenario: operator relies on CLI defaults instead of the prior cleanup baseline.
   - Effect: the final cleanup row is harder to compare with the rest of the v1.6.0 cleanup cohort.
   - Mitigation: pin both `--model gemini-2.0-flash` and `--prompt-version v1.6.0` explicitly in rehearsal and live execution.

## Recommended Execution Shape

### 1. Scratch rehearsal

- **Hard gate:** do not run against `signals.db` unless scratch rehearsal succeeds first.
- **Runtime prerequisite:** scratch and live runs must use the same working LLM credentials/runtime as the existing `thesis-classify-batch` path.

- Create the scratch DB with the repo's WAL-safe backup path:

```powershell
python scripts/backup_db.py --db signals.db --out-dir .omx\sandbox\thesis-last-missing-row --retain 3
```

- Use the exact backup path printed by `backup_db.py` as `<scratch-db>`.
- Confirm the scratch cohort still shows only `signal_id=438` in the missing-any-row 90-day set by running:

```powershell
@'
import sqlite3
from datetime import datetime, timedelta, timezone

db = r'.omx\sandbox\thesis-last-missing-row\signals-YYYYMMDD-HHMMSS.db'
cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
q = """
SELECT s.id, s.company_name, s.source_api, s.created_at
FROM signals s
LEFT JOIN (
    SELECT signal_id, MAX(id) AS max_id
    FROM thesis_classifications
    GROUP BY signal_id
) tc ON tc.signal_id = s.id
WHERE s.created_at >= ?
  AND tc.max_id IS NULL
ORDER BY s.created_at DESC, s.id DESC
"""
rows = conn.execute(q, (cutoff,)).fetchall()
print(f"count={len(rows)}")
for row in rows:
    print(dict(row))
'@ | python -
```

- Run:

```powershell
python -m ops.cli quality --db <scratch-db> thesis-classify-batch --days 90 --limit 1 --model gemini-2.0-flash --prompt-version v1.6.0 --stop-on-error
```

- Verify on scratch:
  - command summary is `attempted=1`, `succeeded=1`, `failed=0`
  - `signal_id=438` goes from `0` thesis rows to `1`
  - the scratch missing-any-row cohort for the last 90 days is `0`

### 2. Bounded live run

- **Hard gate:** rerun the live pre-flight cohort query immediately before execution.
- Proceed only if the result still shows exactly one missing-any-row candidate in the 90-day window and that candidate is `signal_id=438`.
- Exact live pre-flight command:

```powershell
@'
import sqlite3
from datetime import datetime, timedelta, timezone

cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
conn = sqlite3.connect('signals.db')
conn.row_factory = sqlite3.Row
q = """
SELECT s.id, s.company_name, s.source_api, s.created_at
FROM signals s
LEFT JOIN (
    SELECT signal_id, MAX(id) AS max_id
    FROM thesis_classifications
    GROUP BY signal_id
) tc ON tc.signal_id = s.id
WHERE s.created_at >= ?
  AND tc.max_id IS NULL
ORDER BY s.created_at DESC, s.id DESC
"""
rows = conn.execute(q, (cutoff,)).fetchall()
print(f"count={len(rows)}")
for row in rows:
    print(dict(row))
'@ | python -
```

- Run:

```powershell
python -m ops.cli quality --db signals.db thesis-classify-batch --days 90 --limit 1 --model gemini-2.0-flash --prompt-version v1.6.0 --stop-on-error
```

### 3. Post-run verification

- Confirm live command summary is `attempted=1`, `succeeded=1`, `failed=0`.
- Confirm `signal_id=438` went from `0` thesis rows to `1` row and that the row records `model='gemini-2.0-flash'` and `prompt_version='v1.6.0'`:

```powershell
@'
import sqlite3

conn = sqlite3.connect('signals.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
    SELECT id, model, prompt_version, classified_at
    FROM thesis_classifications
    WHERE signal_id = 438
    ORDER BY id DESC
    """
).fetchall()
print(f"count={len(rows)}")
for row in rows[:3]:
    print(dict(row))
'@ | python -
```

- Confirm the live missing-any-row cohort in the last 90 days is now `0`.
- Exact live post-run cohort command:

```powershell
@'
import sqlite3
from datetime import datetime, timedelta, timezone

cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
conn = sqlite3.connect('signals.db')
q = """
SELECT COUNT(*)
FROM signals s
LEFT JOIN (
    SELECT signal_id, MAX(id) AS max_id
    FROM thesis_classifications
    GROUP BY signal_id
) tc ON tc.signal_id = s.id
WHERE s.created_at >= ?
  AND tc.max_id IS NULL
"""
print(conn.execute(q, (cutoff,)).fetchone()[0])
'@ | python -
```

- As sanity checks, reconfirm stale-latest-row cohort remains `0` and overdue regret checks remain `0`:

```powershell
@'
import sqlite3
from datetime import datetime, timedelta, timezone

cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
conn = sqlite3.connect('signals.db')
q = """
WITH latest AS (
    SELECT tc.*
    FROM thesis_classifications tc
    JOIN (
        SELECT signal_id, MAX(id) AS max_id
        FROM thesis_classifications
        GROUP BY signal_id
    ) mx ON mx.max_id = tc.id
)
SELECT COUNT(*)
FROM signals s
JOIN latest tc ON tc.signal_id = s.id
WHERE s.created_at >= ?
  AND (
    tc.model IS NULL OR tc.model = ''
    OR tc.prompt_version IS NULL OR tc.prompt_version = ''
  )
"""
print(conn.execute(q, (cutoff,)).fetchone()[0])
'@ | python -

python -m monitoring.feature_gate overdue --db signals.db --json
```

## Acceptance Criteria

1. Scratch rehearsal succeeds with one attempted classification and zero remaining missing-any thesis rows in the 90-day scratch cohort.
2. Live pre-flight happens immediately before the live command and still identifies exactly one candidate: `signal_id=438`.
3. Live run changes `signal_id=438` from `0` thesis rows to exactly `1` appended thesis classification row.
4. Post-run live verification shows the missing-any thesis-row cohort in the last 90 days is `0`.
5. The new live row records `model='gemini-2.0-flash'` and `prompt_version='v1.6.0'`.
6. Stale-latest-row and overdue-regret checks remain `0` as post-run sanity checks.

## Verification Categories

### Unit

- No new unit work required for this operational task.
- Existing coverage already proves:
  - `created_at` windowing for missing-row selection ([test_thesis.py](C:/dev/Harmonic/tests/ops/quality/test_thesis.py):227)
  - `limit` enforcement ([test_thesis.py](C:/dev/Harmonic/tests/ops/quality/test_thesis.py):214)

### Integration

- Required: scratch rehearsal against a DB copy using the real CLI path.
- Purpose: prove the batch path still closes the exact cohort without touching the live DB first.

### E2E

- Required: one bounded live command against `signals.db`.
- Size: single-command, single-row operational closure only.

### Observability

- Required evidence:
  - pre-flight cohort query result showing `count=1` and `signal_id=438`
  - CLI summary from rehearsal and live run
  - post-run SQL proof showing `signal_id=438` now has exactly one thesis row
  - post-run cohort counts for missing-any, stale-latest, and overdue regret checks

## ADR

- **Decision:** Close the last operational thesis cleanup item with `thesis-classify-batch --days 90 --limit 1 --model gemini-2.0-flash --prompt-version v1.6.0 --stop-on-error`, preceded by mandatory successful scratch rehearsal and followed by DB-backed verification.
- **Drivers:** exact cohort size is one; existing path is already fixed and tested; provenance consistency matters more than opportunistic backfill breadth.
- **Alternatives considered:** use a slightly larger limit; rely on CLI default prompt version.
- **Why chosen:** smallest reversible live mutation that still proves end-to-end closure and preserves cleanup provenance continuity.
- **Consequences:** if selector drift occurs between rehearsal and live run, the operator must stop and re-evaluate rather than broadening the command ad hoc.
- **Follow-ups:** if pre-flight no longer shows exactly one target, open a new operational cleanup task instead of stretching this one.
