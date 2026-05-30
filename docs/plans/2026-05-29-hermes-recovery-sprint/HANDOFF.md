# HANDOFF — Hermes Recovery Sprint (Phase 1 onward)

> Paste the block under "HANDOFF PROMPT" into a fresh Claude Code session opened in
> `C:\dev\Harmonic`. It is self-contained: all verified facts are inline so you do not
> re-derive them. Phase 0 (offsite containment) is DONE and verified. You are resuming at
> Phase 1 (canary restore).
>
> Freshness note: restored docs-only onto `main@53f836b` after PR #239; stale-branch
> code, config, and test changes were intentionally excluded.

---

## HANDOFF PROMPT (copy everything below this line)

You are resuming a P0 database recovery for the Harmonic Discovery Engine, branch `main`,
working dir `C:\dev\Harmonic` (Windows / PowerShell). Read these two docs first, they are
canonical:
- `docs/superpowers/specs/2026-05-29-hermes-recovery-sprint-design.md` (the eng-cleared design)
- `docs/plans/2026-05-29-hermes-recovery-sprint/decision-matrix.md` (why: strategy S4)

REPO RULE (non-negotiable): verify before trusting. Before claiming any step done, run the
verification command and paste its output. Read any function before relying on its behavior.
Do not commit to `main`; if asked to commit, branch first and stage exact paths only (the repo
has unrelated dirt — keepalive artifacts, `state/collectors.json` — never stage it).

### Situation (verified 2026-05-29, re-verify if >1 day old)
- Live `signals.db` = 4 rows (truncated in the 2026-05-08 incident, Issue #149). Pipeline dead.
- Recovery target = the 612-row baseline. A clean, sidecar-free, integrity-verified online
  backup of it already exists and is the RESTORE SOURCE:
  - `backups/signals-20260529-190655.db`
  - sha256 `01ced671a3c1a3800646edad42c2fa9ef2841f587d8255b4049a7c6e3fdd0a26`
  - md5 `ef8f6ac4b6982f51a66787ca2b2c3f7a`, size 9756672, rows 612, schema 53, integrity ok
  - Prefer this over `signals.db.pre-step4b-promotion-20260404` (the raw baseline had stale
    May-13 `-wal`/`-shm` sidecars; this online backup is WAL-flattened and clean).
- Phase 0 DONE: this backup is offsite on Google Drive (file id
  `1SVrjLcypIyyP9bF1TYJzE8g-tunhQIpM`, owner nikhil.bhambi@gmail.com, currently in My Drive
  root), Drive md5 == local md5, independently confirmed.
- `.omx/state/db_watermark.json` already reads signal_count 612 / schema 53.

### Hard preconditions before ANY restore (canary or live)
1. Harmonic API server DOWN. `restore_backup` (`scripts/restore_db.py:396`) refuses if
   `http://localhost:8000/api/v1/health` is reachable. Verify it is NOT reachable.
2. `--force` is FORBIDDEN on the production target (`signals.db`). It risks corruption if a
   writer is live. Stop writers instead.
3. restore-db ack token is `RESTORE_DB` (verified `restore_db.py:25`). Guard flags are real and
   consumed: `--handle-sidecars` (preflight), `--min-row-count` + `--expected-schema-version`
   (postflight).
4. Rollback is double-covered: the run-dir `snapshots/pre_restore_target.db` AND a canonical
   `pre-restore-<ts>.db` written next to the target before overwrite.

### Execute these phases in order. STOP for human approval before Phase 3 (live restore).

PHASE 1 — Canary restore (proves the harness; live DB untouched):
```
# throwaway target = copy of the live truncated DB
Copy-Item signals.db signals.db.canary
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db --target signals.db.canary --dry-run --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db --target signals.db.canary --preflight-only --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db --target signals.db.canary --execute --ack-risk RESTORE_DB --handle-sidecars --min-row-count 612 --expected-schema-version 53
```
PASS = canary at exactly 612 rows, integrity ok, schema 53, no unexpected sidecars, Hermes
ledger run dir + db_ops_ledger row written, NO `repair_prompt.md`. Capture the run-dir id.
Verify independently: `python -c "import sqlite3;print(sqlite3.connect('signals.db.canary').execute('SELECT COUNT(*) FROM signals').fetchone()[0])"` -> 612.

PHASE 2 — Deliberation (emergency-bypass authorization; codex+kimi quorum):
```
python -m ops.cli hermes task deliberate --plan ai-logs/hermes/runs/<canary-run-id>/task_plan.json --panel codex,kimi --rounds 1 --json
```
PASS = >= 2 approvals, no blocker verdict. This is the ledgered bypass record (the live restore
hits all 5 of the proposal's E2 high-risk triggers, so deliberation stands in for the
not-yet-built gate-binding). Blocker/dissent halts the sprint.

>>> STOP HERE. Present Phase 1 + Phase 2 results to the operator and get explicit go-ahead
>>> before Phase 3. Phase 3 mutates the live production DB.

PHASE 3 — Live restore (single high-risk gate):
Preconditions: Phase 1 PASS, Phase 2 approved, API down (re-verify), operator go-ahead.
```
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db --target signals.db --dry-run --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db --target signals.db --preflight-only --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db --target signals.db --execute --ack-risk RESTORE_DB --handle-sidecars --min-row-count 612 --expected-schema-version 53
```
PASS = signals.db 612 rows, integrity ok, schema 53. Rollback if needed: restore the run-dir
`snapshots/pre_restore_target.db` or the canonical `pre-restore-<ts>.db`.

PHASE 4 — Watermark + incident close:
```
# confirm watermark still 612/53 (re-init only if drifted)
python -m ops.cli hermes task incident --phase-name verify --incident-id 149 --execute
```
PASS = Issue #149 mapped to `resolved`.

PHASE 5 — Reactivate + health:
- Re-enable `HarmonicKeepAlive` (disabled since 2026-05-08).
- `python run_pipeline.py health --json` -> MUST NOT return `catastrophic_drop_detected`.

PHASE 6 — Down-payment harvest (enforcement seed):
- Write the enforcement-proposal PR H1 policy-reconciliation doc.
- Commit the Phase 1 canary run as the E3 restore-db rehearsal fixture under
  `tests/ops/hermes/`.

### Open cosmetics (non-blocking)
- The offsite Drive backup is in My Drive root; move it into a dedicated folder.
- Confirm nikhil.bhambi@gmail.com is the intended backup account (not the narralytics work acct).

### Definition of done
signals.db = 612 rows + schema 53 + integrity ok; Issue #149 resolved; keepalive emitting;
`run_pipeline.py health --json` clean; H1 doc + E3 fixture committed; offsite backup verified
(already done). Then the deferred backlog (Step 4B regret check, Q-25, etc.) becomes
unblocked — but that is OUT OF SCOPE for this sprint.
