# Milestone Roadmap Plan — Patch R1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply 19 targeted patches to `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md` that fix critical bugs, destructive behaviors, and automation blockers identified in the IterativeDepth review.

**Architecture:** Each task is a direct edit to the target plan document. Tasks are ordered by severity (MUST FIX first). No code is written to the codebase — only the plan document is modified. Tasks are independent and can be applied in any order, but completing all MUST-FIX tasks first is strongly recommended.

**Tech Stack:** Markdown, PowerShell 5.1, Python 3.11+, GitHub Actions gh CLI, SQLite

---

## Files

| File | Role |
|------|------|
| `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md` | **Target** — this is the document being patched |

---

## Verified facts before patching

These facts were verified against the live codebase and resolve ambiguities in the IterativeDepth findings:

- `.omx/specs/` is **not** gitignored → Track 6 gate artifact commit works as written (F8 drops)
- `"qualified"` is a real `signal_processing.status` value (set by the pipeline) → F6 reduces to a pre-check, not a blocker
- `workflow_dispatch` is enabled on `discovery-pipeline.yml` → Task 1.3 Step 1 works
- CI job names ("Core Regression Suite", "Thesis Golden Set Gate", etc.) exactly match workflow `name:` fields → F15 drops

---

## MUST FIX — Bugs that will break or corrupt execution

---

### Task P1: Fix Dangling "Task 0.4" Reference (F1)

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Locate and fix the dangling reference**

Find this line in Task 0.1 Step 2 expected output:

```markdown
- If N ≥ 612: DB is recovered. Skip Tasks 0.2–0.3, proceed to Task 0.4 (watermark check).
```

Replace with:

```markdown
- If N ≥ 612: DB is recovered. Skip Tasks 0.2–0.3, proceed to Task 0.3 Step 1 (watermark check).
```

- [ ] **Step 2: Verify the change**

```powershell
Select-String -Path "docs\superpowers\plans\2026-06-17-milestone-roadmap-execution.md" -Pattern "Task 0.4"
```

Expected: No output (pattern no longer present).

---

### Task P2: Relabel Task 0.2 Step 1 — "Dry-Run" Framing Removed (F2)

`restore_db.py` has no `--dry-run` flag. The step title misleads operators and will block agentic workers who wait for `[DRY RUN]` output that never comes. Without `--force`, the script presents a human confirmation prompt — blocking agentic execution. The step is actually a "preflight check."

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Replace the misleading step title and expectation**

Find:
```markdown
- [ ] **Step 1: Dry-run to verify restore_db.py accepts the backup**

```powershell
.venv\Scripts\python.exe scripts\restore_db.py `
  signals.db.pre-step4b-promotion-20260404 `
  --db-path "$env:DISCOVERY_DB_PATH" `
  --api-url "http://localhost:8000/api/v1/health"
```

If API server is not running, the script checks reachability and skips the server lock. Expected: Either completes or shows `[DRY RUN]` / confirmation prompt.
```

Replace with:

```markdown
- [ ] **Step 1: Preflight — verify backup integrity before restoring**

`restore_db.py` has no `--dry-run` flag. This step only validates the backup file's integrity
without triggering the restore. Without `--force`, the script will prompt for confirmation
(blocking agentic execution). Run this integrity-only check first:

```powershell
.venv\Scripts\python.exe -c "
import sqlite3
backup = 'signals.db.pre-step4b-promotion-20260404'
try:
    conn = sqlite3.connect(f'file:{backup}?mode=ro', uri=True)
    result = conn.execute('PRAGMA integrity_check').fetchone()[0]
    n = conn.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
    conn.close()
    print(f'Integrity: {result}')
    print(f'Row count: {n}')
    if result != 'ok':
        raise SystemExit(1)
except Exception as e:
    print(f'ERROR: {e}')
    raise SystemExit(1)
"
```

Expected: `Integrity: ok`, row count ≥ 612. If integrity check fails, do NOT proceed — the backup
itself is corrupt. Source an alternative backup from CI artifacts.
```

- [ ] **Step 2: Verify the change**

```powershell
Select-String -Path "docs\superpowers\plans\2026-06-17-milestone-roadmap-execution.md" -Pattern "DRY RUN"
```

Expected: No output.

---

### Task P3: Add Test-Restore to Temp Path Before Production Restore (F14)

SRE practice requires a test restore before promoting to production. Insert a new step between the preflight and the actual restore.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Insert test-restore step between Step 1 and the old Step 2**

After the preflight step (P2 above), add a new step before the existing "Step 2: Execute the restore":

```markdown
- [ ] **Step 1b: Test-restore to temp path — verify before promoting**

Restore to a temp location first. Only promote to `DISCOVERY_DB_PATH` if this passes:

```powershell
$tempDb = "$env:TEMP\signals_test_restore.db"
.venv\Scripts\python.exe scripts\restore_db.py `
  signals.db.pre-step4b-promotion-20260404 `
  --db-path $tempDb `
  --force

# Verify the temp restore
.venv\Scripts\python.exe -c "
import sqlite3, os, sys
db = os.environ['TEMP'] + '/signals_test_restore.db'
conn = sqlite3.connect(db)
integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
n = conn.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
schema = conn.execute('PRAGMA user_version').fetchone()[0]
conn.close()
print(f'integrity_check: {integrity}')
print(f'row count: {n}')
print(f'schema_version: {schema}')
if integrity != 'ok' or n < 612:
    print('FAIL: temp restore did not pass checks — do NOT promote', file=sys.stderr)
    sys.exit(1)
print('PASS: temp restore verified — safe to promote')
"
```

Expected: `integrity_check: ok`, row count ≥ 612, schema_version ≥ 51, exit code 0.
Only proceed to Step 2 after this passes.
```

---

### Task P4: Fix PowerShell `<<< $body` Syntax in Track 5 Step 4 (F3)

`<<<` is bash herestring syntax. PowerShell 5.1 does not support it. `gh api --input -` reads from stdin via pipeline.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Replace the broken heredoc syntax**

Find:
```powershell
# Step 4d: Apply merged payload
gh api -X PUT repos/nikhillinit/SweetSwwetHarmony/branches/main/protection `
  --input - <<< $body
```

Replace with:

```powershell
# Step 4d: Apply merged payload (pipe $body to stdin — PowerShell 5.1 compatible)
$body | gh api -X PUT repos/nikhillinit/SweetSwwetHarmony/branches/main/protection --input -
```

- [ ] **Step 2: Verify fix**

```powershell
Select-String -Path "docs\superpowers\plans\2026-06-17-milestone-roadmap-execution.md" -Pattern "<<< \`$body"
```

Expected: No output.

---

### Task P5: Fix `$BUCKET_NAME`/`$ACCOUNT_ID` Undefined in PowerShell Block (F4)

These variables were defined in the bash block of Task 1.1. PowerShell does not inherit bash variables. Task 1.2's PowerShell block uses them without re-defining.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Add variable re-definition at the top of Task 1.2 Step 1**

Find the start of Task 1.2 Step 1 PowerShell block:

```powershell
# Replace these values with your actual credentials from Task 1.1 Step 2
$ACCESS_KEY_ID    = "AKIAIOSFODNN7EXAMPLE"   # from aws iam create-access-key output
$SECRET_KEY       = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
$BUCKET_NAME      = "harmonic-signals-backup-prod-$ACCOUNT_ID"  # from Task 1.1 Step 1
```

Replace with:

```powershell
# Re-define bucket name here — PowerShell does not inherit bash variables from Task 1.1.
# ACCOUNT_ID: get from AWS CLI in PowerShell
$ACCOUNT_ID       = (aws sts get-caller-identity --query Account --output text).Trim()
$BUCKET_NAME      = "harmonic-signals-backup-prod-$ACCOUNT_ID"

# Replace these values with your actual credentials from Task 1.1 Step 2
$ACCESS_KEY_ID    = "AKIAIOSFODNN7EXAMPLE"   # from aws iam create-access-key output
$SECRET_KEY       = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
```

- [ ] **Step 2: Verify fix**

```powershell
Select-String -Path "docs\superpowers\plans\2026-06-17-milestone-roadmap-execution.md" -Pattern "ACCOUNT_ID.*PowerShell"
```

Expected: Matches the new comment line.

---

### Task P6: Add Delivery Policy Check to Track 4 Push Implementation (F5)

`cmd_push` (line 6426) calls `assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)` before instantiating NotionPusher. Track 4's implementation omits this. In `DELIVERY_MODE=batch_publish`, the push will be silently blocked by the delivery policy INSIDE NotionPusher — after unnecessary network calls.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Insert delivery policy check into Track 4.1 Step 4**

Find the start of the `# REPLACE WITH:` block in Task 4.1 Step 4:

```python
        # REPLACE WITH:
        notion_api_key = os.environ.get("NOTION_API_KEY")
```

Replace with:

```python
        # REPLACE WITH:
        # Check delivery policy first — matches the guard in cmd_push (line 6426)
        from workflows.delivery_policy import (
            assert_notion_write_allowed,
            DeliveryIntent,
            DeliveryPolicyError,
        )
        if not dry_run:
            try:
                assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)
            except DeliveryPolicyError as e:
                print(f"ERROR: Delivery policy blocked push: {e}")
                sys.exit(1)

        notion_api_key = os.environ.get("NOTION_API_KEY")
```

- [ ] **Step 2: Verify the DeliveryIntent import is not already imported earlier in the block**

```powershell
Select-String -Path "docs\superpowers\plans\2026-06-17-milestone-roadmap-execution.md" -Pattern "from workflows.delivery_policy import" -Context 0,2
```

Expected: Only one occurrence (the one just added). If two exist, remove the duplicate `from workflows.delivery_policy import DeliveryIntent` line that appears later in the block.

---

### Task P7: Add "Qualified Signals Exist" Pre-Check to Track 4 (F6)

Before wiring the push path, verify the database actually contains signals with `status='qualified'`. If none exist, Track 4 runs correctly but pushes nothing — the fix is invisible and untestable.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Add pre-check step before Track 4.1 Step 1**

At the beginning of Task 4.1 (before Step 1: Read process_single_prospect), insert:

```markdown
- [ ] **Step 0: Verify "qualified" signals exist in the database**

```powershell
.venv\Scripts\python.exe -c "
import sqlite3, os, sys
db = os.environ['DISCOVERY_DB_PATH']
conn = sqlite3.connect(db)
n = conn.execute(
    'SELECT COUNT(*) FROM signals s JOIN signal_processing sp ON sp.signal_id = s.id WHERE sp.status = ?',
    ('qualified',)
).fetchone()[0]
conn.close()
print(f'Qualified signals: {n}')
if n == 0:
    print('WARNING: No qualified signals found. Run the pipeline first to generate qualified signals.')
    print('Track 4 will wire the push path but will have no signals to push until the pipeline runs.')
"
```

Expected: Prints qualified signal count. If 0, the implementation is still correct — the pipeline sets
signals to `qualified` status during processing. Confirm by running a dry-run pipeline cycle first.
```

---

### Task P8: Verify `candidate_v3.jsonl` Exists Before Track 6 Step 2 (F7)

`--compare-against artifacts/thesis_diagnostics/candidate_v3.jsonl` will fail if this file was lost in the incident or never committed.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Insert verification step before Track 6 Step 2**

Before the `thesis_diagnostic_runner.py` step, add:

```markdown
- [ ] **Step 1b: Verify comparison baseline file exists**

```powershell
if (Test-Path "artifacts\thesis_diagnostics\candidate_v3.jsonl") {
    Write-Host "Found candidate_v3.jsonl — comparison baseline available"
} else {
    Write-Host "WARNING: candidate_v3.jsonl not found."
    Write-Host "Options:"
    Write-Host "  A) Run without --compare-against (omit the flag) — produces accuracy report only"
    Write-Host "  B) Restore from git history: git show HEAD:artifacts/thesis_diagnostics/candidate_v3.jsonl > artifacts/thesis_diagnostics/candidate_v3.jsonl"
    Write-Host "  C) Check CI artifacts for a prior run that produced this file"
}
```

If the file does not exist, update the `thesis_diagnostic_runner.py` command in Step 2 to omit
`--compare-against`:

```powershell
# Use this command if candidate_v3.jsonl is missing:
.venv\Scripts\python.exe scripts\thesis_diagnostic_runner.py `
  --dataset tests/fixtures/thesis_llm_golden_set.jsonl `
  --output-dir artifacts/thesis_diagnostics `
  --run-id candidate_v3_promotion_run_20260617 `
  --temperature 0
```
```

---

## SHOULD FIX — Incorrect behavior or misleading instructions

---

### Task P9: Strengthen Step 4B Regret Check — Add Substance (F9)

The current check queries `audit_events` to confirm the feature was activated. A regret check must evaluate whether activation caused harm — merge quality metrics, not governance bookkeeping.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Expand Task 0.3 Step 2 with substantive metric checks**

After the existing `audit_events` query block, append:

```markdown
- [ ] **Step 2b: Evaluate merge write quality (substantive regret check)**

The governance event confirms activation. This step answers whether activation caused harm:

```powershell
.venv\Scripts\python.exe -c "
import sqlite3, os, sys

db = os.environ['DISCOVERY_DB_PATH']
conn = sqlite3.connect(db)

# Count entities that have been merged
merged = conn.execute(
    'SELECT COUNT(*) FROM entity_migrations WHERE created_at >= ?',
    ('2026-04-04',)
).fetchone()[0]

# Count rollbacks (evidence of bad merges)
rollbacks = conn.execute(
    '''SELECT COUNT(*) FROM audit_events
       WHERE event_type LIKE ? AND created_at >= ?''',
    ('%merge_rollback%', '2026-04-04')
).fetchone()[0]

# Count entities with conflicting status (evidence of merge collisions)
conflicts = conn.execute(
    '''SELECT COUNT(*) FROM entity_identity
       WHERE status = ? AND status_updated_at >= ?''',
    ('conflicting', '2026-04-04')
).fetchone()[0] if conn.execute(
    \"SELECT name FROM sqlite_master WHERE type='table' AND name='entity_identity'\"
).fetchone() else 0

conn.close()

print(f'Merges since Step 4B activation: {merged}')
print(f'Rollbacks since Step 4B activation: {rollbacks}')
print(f'Conflicting entities: {conflicts}')

# Regret threshold: >5% rollback rate on merges is a regret signal
if merged > 0 and (rollbacks / merged) > 0.05:
    print(f'REGRET SIGNAL: rollback rate {rollbacks/merged:.1%} exceeds 5% threshold', file=sys.stderr)
    print('Consider deactivating MERGE_WRITES_ENABLED and investigating.', file=sys.stderr)
    sys.exit(1)
elif merged == 0:
    print('INFO: No merges recorded since activation — feature is armed but has not fired.')
else:
    print(f'PASS: Rollback rate {rollbacks/merged:.1%} within acceptable range. No regret signal.')
"
```

Expected: Exit code 0 with PASS message. Exit code 1 means regret threshold exceeded — stop and
evaluate whether to deactivate `MERGE_WRITES_ENABLED` before proceeding with other tracks.
```

---

### Task P10: Raise `SQLITE_RESTORE_MIN_SIGNALS` from 100 to 490 (F10)

100 is 16% of the 612-row corpus. A catastrophic partial restore to 200 rows would pass this check. The threshold should be 80% of the known corpus floor.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Update the variable set command in Task 1.2 Step 1**

Find:
```powershell
gh variable set SQLITE_RESTORE_MIN_SIGNALS `
  --repo nikhillinit/SweetSwwetHarmony `
  --body "100"
```

Replace with:
```powershell
# Set to 80% of the 612-row corpus floor (490 rows).
# A restore below this count indicates partial restore failure.
gh variable set SQLITE_RESTORE_MIN_SIGNALS `
  --repo nikhillinit/SweetSwwetHarmony `
  --body "490"
```

---

### Task P11: Make 40-70% FP Range Explicit in Decision Gate (F11)

The current gate maps `< 10 samples → 2.2a`, `≥ 70% FP → 2.2b`, `< 40% FP → no change`. The 40-70% range is implied to map to 2.2a but isn't stated. An agent hits an unhandled case.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Rewrite the decision gate in Task 2.1 Step 2**

Find:
```markdown
**Decision gate** — choose the task variant below based on output:
- Post-LLM labeled HN signals < 10 → proceed to Task 2.2a (confidence floor, not enough data to disable)
- Post-LLM FP rate still ≥ 70% with ≥ 10 labeled signals → proceed to Task 2.2b (disable collector)
- Post-LLM FP rate < 40% → LLM is working; update baseline doc only (no code change)
```

Replace with:
```markdown
**Decision gate** — choose the task variant below based on output:

```
                    ┌─ Post-LLM labeled HN signals available? (≥ 10 post-2026-03-25)
                    │
         NO (< 10) ─┤→ Task 2.2a (confidence floor at 0.70)
                    │  Rationale: pre-LLM data (98.69% FP) is the best evidence we have;
                    │  apply conservative floor until post-LLM data accumulates.
                    │
        YES (≥ 10) ─┤
                    │
     FP rate < 40% ─┤→ No code change. Update baseline doc only.
                    │  Rationale: LLM is working; HN quality improved post-classification.
                    │
  40% ≤ FP < 70%  ─┤→ Task 2.2a (confidence floor at 0.70)
                    │  Rationale: Improvement but still elevated; raise the routing bar.
                    │
      FP rate ≥ 70% ─┤→ Task 2.2b (disable collector)
                       Rationale: LLM failed to improve HN signal; collector cost exceeds value.
```
```

---

### Task P12: Preserve `restrictions` Field in Branch Protection PUT (F12)

Setting `restrictions: $null` in the PUT payload removes any existing push restrictions (e.g., preventing direct commits to main). The plan should read the existing value and preserve it.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Update Track 5.1 Step 4c payload to preserve existing restrictions**

Find:
```powershell
# Step 4c: Build merged payload preserving all existing settings
$body = @{
    required_status_checks = @{
        strict   = $current.required_status_checks.strict
        contexts = $mergedContexts
    }
    enforce_admins = $current.enforce_admins.enabled
    required_pull_request_reviews = @{
        required_approving_review_count = $current.required_pull_request_reviews.required_approving_review_count
        require_code_owner_reviews      = $true   # preserve CODEOWNER requirement
        dismiss_stale_reviews           = $current.required_pull_request_reviews.dismiss_stale_reviews
    }
    restrictions = $null
} | ConvertTo-Json -Depth 5
```

Replace with:
```powershell
# Step 4c: Build merged payload preserving ALL existing settings.
# IMPORTANT: restrictions=$null in the GitHub API removes all push restrictions.
# Read the existing restrictions and pass them through unchanged.
$existingRestrictions = $null
if ($current.restrictions -ne $null) {
    $existingRestrictions = @{
        users = @($current.restrictions.users | ForEach-Object { $_.login })
        teams = @($current.restrictions.teams | ForEach-Object { $_.slug })
        apps  = @($current.restrictions.apps  | ForEach-Object { $_.slug })
    }
}

$body = @{
    required_status_checks = @{
        strict   = $current.required_status_checks.strict
        contexts = $mergedContexts
    }
    enforce_admins = $current.enforce_admins.enabled
    required_pull_request_reviews = @{
        required_approving_review_count = $current.required_pull_request_reviews.required_approving_review_count
        require_code_owner_reviews      = $true   # preserve CODEOWNER requirement
        dismiss_stale_reviews           = $current.required_pull_request_reviews.dismiss_stale_reviews
    }
    restrictions = $existingRestrictions   # null only if no restrictions existed; preserves push limits
} | ConvertTo-Json -Depth 5
```

---

### Task P13: Move Track 6 to Parallel After Track 0 (F13)

Track 6 (thesis baseline promotion) requires only `GOOGLE_API_KEY` and the golden set fixture. It has no dependency on Track 4 (push consolidation) or Track 5 (governance). Moving it earlier saves calendar time.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Update the execution order diagram**

Find:
```
Track 0: DB Recovery (gate — must close first)
    ├── Track 1: Cloud Backup          ┐
    ├── Track 2: HN Source Quality     ├─ parallel after Track 0
    └── Track 3: Operator Correctness  ┘
         └── Track 4: Push Consolidation
              └── Track 5: Governance Enforcement
              └── Track 6: Thesis Baseline Promotion
```

Replace with:
```
Track 0: DB Recovery (gate — must close first)
    ├── Track 1: Cloud Backup              ┐
    ├── Track 2: HN Source Quality         ├─ parallel after Track 0
    ├── Track 3: Operator Correctness      │
    └── Track 6: Thesis Baseline Promotion ┘   ← moved up; no dependency on 4 or 5
         └── Track 4: Push Consolidation
              └── Track 5: Governance Enforcement
```

- [ ] **Step 2: Update Track 6 header to reflect new parallelism**

Find:
```markdown
## Track 6: Thesis Baseline Promotion
```

Replace with:
```markdown
## Track 6: Thesis Baseline Promotion (parallel with Tracks 1, 2, 3 after Track 0)
```

---

### Task P14: Add Rollback Instructions to Tracks 2, 3, 4 (F16)

No track currently documents how to undo its changes. An operator who causes a regression has no documented recovery path.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Add rollback section to end of Track 2.2a commit step**

After the Track 2.2a commit step, add:

```markdown
**Rollback (if confidence floor causes regressions):**
```powershell
# Remove the _SOURCE_MIN_CONFIDENCE dict and _get_min_confidence function from workflows/pipeline.py
# Then:
git revert HEAD --no-edit
git push
```
```

- [ ] **Step 2: Add rollback section to end of Track 3.1 commit step**

After the Track 3.1 commit step, add:

```markdown
**Rollback (if guard_db_path() causes unexpected failures):**
```powershell
git revert HEAD --no-edit
git push
```
Note: Rollback restores the in-tree DB loophole. Set `HARMONIC_ALLOW_IN_TREE_DB=true` temporarily if needed while investigating.
```

- [ ] **Step 3: Add rollback section to end of Track 4.1 commit step**

After the Track 4.1 commit step, add:

```markdown
**Rollback (if push wiring causes Notion API errors or incorrect routing):**
```powershell
git revert HEAD --no-edit
git push
```
The stub behavior is restored. `pipeline push --confirm` will again print "(Push integration with NotionPusher pending)" and exit without touching Notion.
```

---

## NOTABLE — Reduce confusion and improve reliability

---

### Task P15: Fix `gh run watch` to Capture Specific Run ID (F17)

`gh run watch` without `--run-id` watches the most-recent run, which may be a different workflow or a stale run.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Fix Task 1.3 Steps 1 and 2 to capture and use run IDs**

Find in Task 1.3 Step 1:
```powershell
gh workflow run discovery-pipeline.yml --repo nikhillinit/SweetSwwetHarmony
gh run watch --repo nikhillinit/SweetSwwetHarmony
```

Replace with:
```powershell
gh workflow run discovery-pipeline.yml --repo nikhillinit/SweetSwwetHarmony
# Wait briefly for the run to register, then capture its ID
Start-Sleep -Seconds 5
$runId = (gh run list --workflow discovery-pipeline.yml --repo nikhillinit/SweetSwwetHarmony --limit 1 --json databaseId --jq '.[0].databaseId')
Write-Host "Watching run ID: $runId"
gh run watch $runId --repo nikhillinit/SweetSwwetHarmony
```

Find in Task 1.3 Step 2:
```powershell
gh workflow run litestream-restore-verify-nightly.yml --repo nikhillinit/SweetSwwetHarmony
gh run watch --repo nikhillinit/SweetSwwetHarmony
```

Replace with:
```powershell
gh workflow run litestream-restore-verify-nightly.yml --repo nikhillinit/SweetSwwetHarmony
Start-Sleep -Seconds 5
$runId = (gh run list --workflow litestream-restore-verify-nightly.yml --repo nikhillinit/SweetSwwetHarmony --limit 1 --json databaseId --jq '.[0].databaseId')
Write-Host "Watching run ID: $runId"
gh run watch $runId --repo nikhillinit/SweetSwwetHarmony
```

---

### Task P16: Fix `Add-Content .env` Duplicate Entry Risk (F19)

Running Task 2.2b Step 6 twice creates duplicate `DISABLE_HACKER_NEWS_COLLECTOR=true` entries. Some dotenv parsers use the last value; others the first; either way the file gets dirty.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Replace Add-Content with idempotent check-and-set**

Find in Task 2.2b Step 6:
```powershell
Add-Content .env "`nDISABLE_HACKER_NEWS_COLLECTOR=true"
```

Replace with:
```powershell
# Idempotent: only append if not already present
if (-not (Select-String -Path ".env" -Pattern "^DISABLE_HACKER_NEWS_COLLECTOR=" -Quiet)) {
    Add-Content .env "`nDISABLE_HACKER_NEWS_COLLECTOR=true"
    Write-Host "Added DISABLE_HACKER_NEWS_COLLECTOR=true to .env"
} else {
    Write-Host "DISABLE_HACKER_NEWS_COLLECTOR already set in .env — no change"
}
```

---

### Task P17: Make Backup File Path Absolute (F22)

`Test-Path "signals.db.pre-step4b-promotion-20260404"` is CWD-relative. If the operator's working directory is not the repo root, this check silently returns `False` and the restore fails.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Make Task 0.1 Step 3 use an absolute path**

Find:
```powershell
# The canonical backup is signals.db.pre-step4b-promotion-20260404
Test-Path "signals.db.pre-step4b-promotion-20260404"
(Get-FileHash "signals.db.pre-step4b-promotion-20260404" -Algorithm SHA256).Hash
```

Replace with:
```powershell
# Resolve backup path relative to the repo root (not CWD, which may differ)
$repoRoot = git rev-parse --show-toplevel
$backupPath = Join-Path $repoRoot "signals.db.pre-step4b-promotion-20260404"
Write-Host "Backup path: $backupPath"
Test-Path $backupPath
if (Test-Path $backupPath) {
    (Get-FileHash $backupPath -Algorithm SHA256).Hash
} else {
    Write-Host "ERROR: Backup not found at $backupPath"
    Write-Host "Check: (1) artifacts/ directory, (2) prior CI run artifacts, (3) local backup copies"
    exit 1
}
```

Also update the restore commands in Task 0.2 to use `$backupPath` instead of the bare filename:
```powershell
# In Task 0.2 Step 1b (test-restore):
.venv\Scripts\python.exe scripts\restore_db.py $backupPath --db-path $tempDb --force

# In Task 0.2 Step 2 (production restore):
.venv\Scripts\python.exe scripts\restore_db.py $backupPath --db-path "$env:DISCOVERY_DB_PATH" --force
```

---

### Task P18: Add admin Scope Check to Track 5 Step 1 (F24)

`PUT /branches/{branch}/protection` requires admin permissions. The plan only verifies `repo` scope, not `admin:repo`.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Expand Track 5.1 Step 1 auth check**

Find:
```powershell
gh auth status
```

If token is expired or scope is missing:
```powershell
gh auth login --web
# Select: GitHub.com → HTTPS → Login with web browser
```

Expected: `Logged in to github.com as nikhillinit (...)` with `repo` scope.

Replace with:
```powershell
gh auth status

# Verify admin:repo scope is present — required for branch protection PUT
$scopes = gh auth status 2>&1 | Select-String "Token scopes"
Write-Host "Token scopes: $scopes"
```

If `admin:repo` is not listed:
```powershell
# Re-authenticate with admin scope
gh auth login --web --scopes "repo,admin:repo"
```

Expected: `Token scopes:` line includes both `repo` and `admin:repo`.

> **Note:** Without `admin:repo`, the `gh api -X PUT` call in Step 4 will return HTTP 403.
> The branch protection will NOT be updated even though `gh api` may exit with non-zero code.

---

### Task P19: Document Data Loss from 2026-04-04 to 2026-05-05 in Recovery PR Commit (F20)

The plan's recovery PR commit message doesn't acknowledge the ~30 days of signals lost between the backup date (2026-04-04) and the incident date (2026-05-05).

**Files:**
- Modify: `docs/superpowers/plans/2026-06-17-milestone-roadmap-execution.md`

- [ ] **Step 1: Update Task 0.3 Step 4 commit message**

Find:
```powershell
git commit -m "chore(incident): phase 4 recovery — restore 612-row corpus, re-init watermark

Restored from signals.db.pre-step4b-promotion-20260404 (SHA256: fcd06c6b...).
Watermark re-inited. Integrity: ok. Step 4B regret check re-armed.
Closes #149."
```

Replace with:
```powershell
git commit -m "chore(incident): phase 4 recovery — restore 612-row corpus, re-init watermark

Restored from signals.db.pre-step4b-promotion-20260404 (SHA256: fcd06c6b...).
Watermark re-inited. Integrity: ok. Step 4B regret check re-armed.

ACCEPTED DATA LOSS: Signals collected between 2026-04-04 (backup date) and
2026-05-05 (incident date, ~30 days) are permanently lost. Corpus restored to
612 rows. No CRM (Notion) data was lost — Notion delta since 2026-04-29 = 0.

Closes #149."
```

---

## Self-Review

| Patch | Issue | Verified |
|-------|-------|---------|
| P1 — Fix "Task 0.4" reference | F1 | Exact string in file, grep to confirm |
| P2 — Preflight framing | F2 | No `--dry-run` flag in restore_db.py |
| P3 — Test-restore to temp | F14 | SRE best practice; temp path uses $env:TEMP |
| P4 — PowerShell `<<<` fix | F3 | Verified: PS 5.1 uses pipe, not herestring |
| P5 — $BUCKET_NAME in PS | F4 | AWS CLI works in PowerShell; `--output text` returns plain string |
| P6 — Delivery policy check | F5 | Matched to cmd_push line 6426 pattern |
| P7 — Qualified signal pre-check | F6 | "qualified" is valid status (signal_store.py:3214) |
| P8 — candidate_v3.jsonl guard | F7 | File may not be committed; fallback path provided |
| P9 — Regret check substance | F9 | Queries entity_migrations + rollback audit events |
| P10 — SQLITE_RESTORE_MIN_SIGNALS | F10 | 490 = 80% of 612-row floor |
| P11 — Decision gate 40-70% | F11 | Explicit if/else ASCII diagram |
| P12 — Preserve restrictions | F12 | Reads $current.restrictions before PUT |
| P13 — Track 6 moved up | F13 | Confirmed: only needs GOOGLE_API_KEY + golden set |
| P14 — Rollback instructions | F16 | `git revert HEAD` is safe and reversible |
| P15 — gh run watch with ID | F17 | Captures run ID via `gh run list --limit 1` |
| P16 — .env dedup | F19 | `Select-String -Quiet` idempotent check |
| P17 — Absolute backup path | F22 | `git rev-parse --show-toplevel` gives repo root |
| P18 — admin scope check | F24 | Branch protection API requires admin:repo |
| P19 — Document data loss | F20 | Accepted data loss note in commit message |

**Dropped findings (not real issues):**
- F8: `.omx/specs/` is NOT gitignored — confirmed via `.gitignore` inspection
- F15: CI check names verified to match workflow `name:` fields exactly
- F21: Post-LLM evaluation of HN quality is the right approach (Task 2.1 already gates this)
- F23: `[PASTE FROM STEP 3]` is intentional human-only step for live accuracy values

**Placeholder scan:** No TBD/TODO. All code blocks are complete. All PowerShell commands are PS 5.1 compatible. No forward references to undefined tasks.
