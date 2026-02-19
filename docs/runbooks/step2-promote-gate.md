# Step 2 Promote / No-Promote Gate Checklist

## 1. Monitoring window elapsed
- [ ] Step 1/Step 2 monitoring window has fully elapsed (target: 2026-02-17 20:29 UTC or later).

## 2. Preflight + backup
- [ ] `python scripts/backup_db.py --db $DB` completed successfully.

## 3. Fresh canary (mandatory, on-demand)
- [ ] `python -m monitoring.canary_checker run --db $DB --store-results` completed.
- [ ] Canary verdict is **not** `fail`.

## 4. Activation gate
- [ ] `python run_pipeline.py activation-check --step 2 --json --db-path $DB` completed.
- [ ] `open_critical_alerts = 0`.
- [ ] If verdict = `warn`, warning reason is understood and accepted.

## 5. Step 2 health signals
- [ ] `python run_pipeline.py drift check --db-path $DB` shows no blocking issues.
- [ ] Thin-file writes observed (company_files increasing / expected rows present).
- [ ] No sustained errors in logs for Step 2 features (`DRIFT_MONITORING_ENABLED`, `USE_THIN_FILES`, `V2_ENABLEMENT`).

## 6. Evidence artifacts written
- [ ] `activation-gate.json`
- [ ] `shadow-status.json`
- [ ] `drift-check.txt`
- [ ] Optional: `shadow-export.jsonl` + shadow report outputs

## 7. Authoritative evidence rule

A cadence tick is **authoritative** if and only if both conditions hold:

1. `git_branch == "main"` — the tick ran from the production branch
2. `event != "blocked"` — it is a normal execution entry, not a guardrail block

**Non-authoritative ticks** (wrong branch, blocked events, test artifacts) MUST NOT count toward gating decisions. When evaluating the monitoring window, filter the cadence ledger to authoritative entries only.

**Pre-guardrail ticks** (runs 1-13, before commit 7199e48): These lack `git_branch` fields. They are authoritative by construction — the guardrail code did not exist yet, and all ran on main.

### Operator-safe monitoring command

Always run canary ticks from the dedicated monitoring worktree with explicit `--db`:

```powershell
cd C:\dev\Harmonic-main; $b = git rev-parse --abbrev-ref HEAD; if ($b -ne "main") { throw "BLOCKED: on '$b'" }; $env:REQUIRE_MAIN_FOR_CANARY="true"; python -m ops.cli --db C:\dev\Harmonic\signals.db schedule tick --name canary-monitor-6h
```

## 8. Decision
- [ ] **PROMOTE** if all checks above pass and risk owner signs off.
- [ ] **HOLD** if any check fails, any critical alert exists, or canary fails.

---

## Promotion command block (if approved)

```powershell
$env:LLM_THESIS_MODE               = "shadow"
$env:ML_ENABLEMENT                 = "shadow"
$env:MERGE_WRITES_ENABLED          = "shadow"
$env:USE_SHADOW_ENTITY_RESOLUTION  = "true"
$env:DRIFT_MONITORING_ENABLED      = "active"
$env:USE_THIN_FILES                = "true"
$env:V2_ENABLEMENT                 = "live"
$env:DELIVERY_MODE                 = "staging_only"
$env:BULK_TRIAGE_ENABLED           = "disabled"
$env:HUNTER_PROMOTE_ENABLED        = "disabled"
# restart/reload service here
```

## If hold/rollback needed

```powershell
$env:DRIFT_MONITORING_ENABLED = "disabled"
$env:USE_THIN_FILES           = "false"
$env:V2_ENABLEMENT            = "shadow"
# restart/reload service here
```
