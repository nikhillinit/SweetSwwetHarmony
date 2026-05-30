# Hermes-Orchestrated P0 Recovery Sprint — Design Spec

> Date: 2026-05-29
> Branch: main
> Status: DRAFT — awaiting operator spec review, then -> writing-plans
> Strategy provenance: `docs/plans/2026-05-29-hermes-recovery-sprint/decision-matrix.md`
> (Matrix 1 -> Option 4 orchestration; Matrix 2 -> S4 sequencing; both CONFIRMED 2026-05-29)

## 1. Problem

`signals.db` is in the 4-row truncated state. The discovery pipeline is non-functional:
no real signal corpus means no deal sourcing. The 2026-05-08 incident (Issue #149) was
never recovered; the Phase 4 recovery PR never landed. The Hermes orchestration harness is
fully built and hardened (Track A PRs #217-#233) with 11 registered task runners, but has
never executed a real production restore — only mock-backed tests.

The truncation writer was never definitively identified (narrowed to a manual copy
co-occurring with a `git reset`, unproven). Every local backup is co-resident with whatever
caused the truncation, and no offsite/cloud backup exists. Restoring without containment
reopens the recurrence window.

## 2. Goal and non-goals

**Goal:** Use the hardened Hermes harness to restore `signals.db` to a known-good baseline,
reactivate the pipeline, and close Issue #149 — with the recovery doubling as the Hermes
harness's first real production proof and the enforcement program's first artifacts.

**Decided constraints (locked):**
- Recovery-first reactivation.
- Safe baseline (612-row `signals.db.pre-step4b-promotion-20260404`) + containment-first.
- P0 sprint only; defer all other backlog.
- Graduated/gated Hermes execution.
- Sequencing S4: recover now (emergency bypass) + enforcement down-payment.

**Non-goals (explicitly out of scope):**
- Salvaging the lost ~300-signal 909-row restart corpus (no backup exists; Notion delta = 0
  confirmed nothing CRM-trackable was lost; VSS forensic recovery deferred).
- Building the full E0-E5 Hermes enforcement program (this sprint seeds it; it does not
  deliver it).
- Step 4B regret-check re-run, Q-25 merge-review fix, 360 NULL-company-name cleanup, the 2
  open code-review findings, drift re-baseline (all deferred until corpus is live).
- Identifying the truncation writer (containment makes recurrence detectable; attribution is
  a separate forensic track).

## 3. Verified facts (this session, 2026-05-29)

| Fact | Source |
|---|---|
| Live `signals.db` = 4 rows, 1.46 MB | `sqlite3 COUNT(*)`; byte-size matches `*-truncated` |
| Safe baseline = `signals.db.pre-step4b-promotion-20260404` (9.76 MB, 612 rows) | `ls -la`; MEMORY hash `fcd06c6b...` |
| Watermark already reads 612 / schema 53 | `.omx/state/db_watermark.json` |
| Backup has stale May-13 `-wal` (0 B) + `-shm` (32 KB) sidecars | `ls -la signals.db.pre-step4b*` |
| `scripts/backup_db.py` = local-only online backup + rotation + DBToolLock + db_ops_ledger | read in full |
| restore-db ack-risk token = `RESTORE_DB`, risk_level=critical, required_locks=("signals.db",) | `integrations/hermes/tasks/restore_db.py:22-28` |
| restore-db consumes `--handle-sidecars` (preflight), `--min-row-count` + `--expected-schema-version` (postflight), `--allow-target-create`, `--force`, `--api-url` | `restore_db.py:30-39`, `:188-201`, `:326-345` |
| execute() re-checks backup hash AND target hash drift plan-vs-execute; snapshots target to `snapshots/pre_restore_target.db`; delegates to `scripts.restore_db.restore_backup_with_lock_and_ledger` | `restore_db.py:227-304` |
| `hermes task` modes: `--plan-only | --preflight-only | --dry-run | --execute` + `--ack-risk` | `python -m ops.cli hermes task --help` |
| incident task: `--phase-name {freeze,analyze,repair-plan,verify}`, `--incident-id`; verify -> resolved | help + PR #229 |
| deliberate task: `--plan`, `--panel`, `--rounds`, `--synthesizer` | `hermes task --help` |
| `HarmonicKeepAlive` disabled since 2026-05-08 (containment) | MEMORY / git status keepalive artifacts |
| Backup schema = 53 = `CURRENT_SCHEMA_VERSION` (no migration needed) | `signal_store.py:98`; `SELECT MAX(version)` on backup |
| Backup `-wal` = 0 bytes (no pending frames); live `signals.db` has NO `-wal`/`-shm` sidecars | `ls -la` 2026-05-29 |
| restore_backup refuses if API reachable at `localhost:8000/api/v1/health` unless `--force`; `--force` warns of corruption if a writer is live | `scripts/restore_db.py:396-408` |
| Restore is deterministic (file/sqlite ops); only `deliberate` invokes LLM executors | `scripts/restore_db.py:346-451`; `restore_db` task has no executor |
| Rollback is DOUBLE-covered: run-dir `snapshots/pre_restore_target.db` + canonical `pre-restore-<ts>.db` next to target (always written) | `restore_db.py:264-269`; `scripts/restore_db.py:412-419` |

## 4. Architecture

Linear, gated, single-operator pipeline. Seven phases. Each mutating step climbs
`dry-run -> preflight-only -> execute`. The live restore is the single high-risk gate
(manual `deliberate` quorum + `--ack-risk RESTORE_DB`). All steps run through Hermes so the
sprint produces the incident's clean audit trail in `ai-logs/hermes/`.

**Determinism note (eng-review F5):** `restore-db` and `incident` are deterministic task
runners (pure file/sqlite ops, no LLM). `deliberate` (Phase 2) is the only executor-backed
step (codex/kimi). The `--codex/--kimi` provider flags belong to `hermes run`, not
`hermes task` — do not expect an LLM in the restore path.

**Global precondition (eng-review F1):** before any restore (canary or live), confirm the
Harmonic API server and all DB writers are stopped. `restore_backup` refuses when
`localhost:8000/api/v1/health` is reachable. `--force` is FORBIDDEN on the production target
(its own code warns of corruption if a writer is live); stop the writer instead.

```
P0 Containment      -> offsite backup exists (precondition for P3)
P1 Canary restore   -> prove restore-db on signals.db.canary (throwaway)  --\
P2 Deliberation     -> codex+kimi quorum on restore plan (bypass record)    | harvest
P3 Live restore     -> restore signals.db to 612 baseline (HIGH-RISK GATE)  | as E3
P4 Watermark+verify -> reconcile watermark; incident verify -> resolved     | fixture
P5 Reactivate+health-> keepalive on; health --json non-catastrophic        --/
P6 Down-payment     -> H1 policy doc + E3 fixture committed
```

### Guard semantics (load-bearing)

- **Preflight** (blocks before mutation): `backup_exists`, `backup_readable`,
  `backup_hash_recorded`, `backup_sqlite_integrity_ok`, `target_exists_or_create_allowed`,
  `target_snapshot_possible`, `no_unhandled_wal_shm_sidecars`.
- **Plan-vs-execute drift** (raises TaskFailure): backup sha256 and target sha256 must match
  the plan snapshot.
- **Postflight** (validates result): `target_exists`, `target_integrity_ok`,
  `row_count_above_watermark` (>= `--min-row-count`), `schema_version_matches_if_declared`
  (== `--expected-schema-version`), `no_unexpected_sidecars`.
- **Rollback**: execute() writes `snapshots/pre_restore_target.db`; recipe = restore that
  snapshot to the target path.

## 5. Phases

### Phase 0 — Containment (net-new control)

**Goal:** an offsite copy of a verified backup exists before any live mutation.

**Mechanism (CONFIRMED 2026-05-29): Antigravity + Google Drive.** Two-part division of labor:

- **Local / deterministic (operator or Antigravity shell):** `scripts/backup_db.py` run against
  the **612-row baseline** — `python scripts/backup_db.py --db-path
  signals.db.pre-step4b-promotion-20260404 --out-dir backups/`. The online-backup API flattens
  the WAL and emits one sidecar-free `backups/signals-<UTC>.db` (612 rows, integrity ok,
  schema 53). This is the irreplaceable restore SOURCE going offsite — not the truncated live DB.
- **Offsite upload (Antigravity, launched directly via terminal, NOT via Hermes):** Antigravity's
  Google Drive extension uploads the verified backup to an operator-named Drive folder and
  verifies the remote copy. Driven by the engineered prompt
  `docs/plans/2026-05-29-hermes-recovery-sprint/antigravity-phase0-prompt.v2.md`.

**Verification nuance:** Google Drive exposes `md5Checksum` (MD5), not SHA256 — verify local
MD5 == Drive `md5Checksum` AND size match. Three terminal states:
- `success` — md5_match true + size match. Phase 0 done.
- `degraded` — extension cannot expose `md5Checksum`; size-only verified. NOT sufficient;
  bring `drive_file_id` back for an independent checksum before proceeding.
- `failed` — any pre-upload gate (integrity/row/schema) or upload/checksum/size failure.

**Why Antigravity is in-bounds here:** it is launched directly by the operator, outside the
Hermes control plane, so the "Antigravity non-mutating in Hermes v1" invariant does not apply.
It also never mutates production state — `backup_db.py` only reads `signals.db` (online backup),
and the upload touches only the created backup file and the Drive folder.

**Exit criteria:** Antigravity returns `status: success` with `md5_match: true`; local
db_ops_ledger row from `backup_db.py` written. Low-risk; no deliberation gate.

### Phase 1 — Canary restore (prove the harness)

**Goal:** prove restore-db end-to-end against a throwaway before touching `signals.db`.

**Precondition (F1):** API/writers stopped (verify `localhost:8000/api/v1/health` not
reachable). **Restore source (updated 2026-05-29):** the clean Phase-0 online backup
`backups/signals-20260529-190655.db` (WAL-flattened, sidecar-free, integrity ok, 612 rows,
schema 53; sha256 `01ced671a3c1a3800646edad42c2fa9ef2841f587d8255b4049a7c6e3fdd0a26`). This
supersedes restoring from the raw `signals.db.pre-step4b-promotion-20260404` and **moots the
F2 backup-sidecar concern** — the online-backup API already flattened the WAL, so there are no
backup `-wal`/`-shm` to quarantine. (F2 cleanup applies only if a future run restores from a
raw baseline.)

```
# stage throwaway target (copy of the truncated live DB)
Copy signals.db -> signals.db.canary

python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db \
  --target signals.db.canary --dry-run --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db \
  --target signals.db.canary --preflight-only --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db \
  --target signals.db.canary --execute --ack-risk RESTORE_DB --handle-sidecars \
  --min-row-count 612 --expected-schema-version 53
```

**Risk retired by source change (2026-05-29):** this concern applied only to the raw
`signals.db.pre-step4b-promotion-20260404` source (its stale May-13 `-wal`/`-shm`).
`--handle-sidecars` guards the *target's* sidecars, not the backup's, and `sqlite_integrity(backup)`
would otherwise open the backup and apply a stale `-wal`. The restore source is now the clean
Phase-0 online backup — a single WAL-flattened file with no sidecars — so this corruption path
cannot occur. The canary still proves the harness end-to-end (guards, lock, ledger, repair-prompt,
postflight); it just no longer has to answer the backup-sidecar question. (Original raw-baseline
concern preserved in git history.)

**Exit criteria (F6 — these become the E3 fixture assertions):** canary at exactly 612 rows;
`integrity_check == ok`; `schema_version == 53`; no unexpected sidecars on the canary target;
Hermes ledger run dir + `db_ops_ledger` row present; **no `repair_prompt.md` written**.
**Harvest:** this run dir becomes the enforcement program's E3 restore-db rehearsal fixture.

### Phase 2 — Deliberation (emergency-bypass authorization)

**Goal:** satisfy the proposal's E2 policy (all 5 critical-restore triggers fire: production
target, stale backup, unobserved hash in ledger sense, large row-count delta, sidecars
present) without waiting for gate-binding (PR H2) to be built.

```
python -m ops.cli hermes task deliberate \
  --plan ai-logs/hermes/runs/<canary-run-id>/task_plan.json \
  --panel codex,kimi --rounds 1 --json
```

**Exit criteria:** >= 2 independent approvals recorded; verdict ledgered as the emergency-
bypass authorization (anti-goal #9 of the enforcement proposal). Blockers/dissent halt the
sprint.

### Phase 3 — Live restore (single high-risk gate)

**Goal:** restore `signals.db` to the 612-row baseline.

**Preconditions (checked before execute):** Phase 0 offsite backup confirmed — Antigravity
returned `status: success` with `md5_match: true` (a `degraded` result must be independently
checksum-verified first); Phase 2 deliberation approved; **API/writers stopped** (F1 —
`localhost:8000/api/v1/health` not reachable); restore source = clean Phase-0 backup
`backups/signals-20260529-190655.db` (sidecar-free; F2 satisfied by construction). **Do NOT
pass `--force` on the production target.**

```
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db \
  --target signals.db --dry-run --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db \
  --target signals.db --preflight-only --json
python -m ops.cli hermes task restore-db --backup backups/signals-20260529-190655.db \
  --target signals.db --execute --ack-risk RESTORE_DB --handle-sidecars \
  --min-row-count 612 --expected-schema-version 53
```

**Exit criteria:** `signals.db` at 612 rows, integrity ok, schema 53, no unexpected
sidecars; pre-restore snapshot written; db_ops_ledger + Hermes ledger rows present.
**Rollback (F7 — double-covered):** either restore the run-dir
`snapshots/pre_restore_target.db`, OR the canonical `pre-restore-<ts>.db` written next to
`signals.db` by `restore_backup` (always created before overwrite). Both return the target to
its pre-restore (4-row) state.

### Phase 4 — Watermark + incident verify

**Goal:** reconcile state and close the incident.

- Confirm `.omx/state/db_watermark.json` reads `signal_count:612, schema_version:53`
  (already does; re-init only if drifted post-restore).
- `python -m ops.cli hermes task incident --phase-name verify --incident-id 149 --execute`
  -> Issue #149 mapped to `resolved` (PR #229 semantics).

**Exit criteria:** watermark consistent; incident state = resolved.

### Phase 5 — Reactivate + health-verify

**Goal:** pipeline alive and proven.

- Re-enable `HarmonicKeepAlive`.
- `python run_pipeline.py health --json` -> must NOT return `catastrophic_drop_detected`
  (it timed out at 120s during the incident; clean health is the success signal).
- Optional smoke: `python run_pipeline.py full --collectors github,sec_edgar --dry-run`.

**Exit criteria:** health green; keepalive emitting again.

### Phase 6 — Down-payment harvest (enforcement seed)

**Goal:** leave the control plane stronger than before the incident.

- Write enforcement-proposal PR H1: policy-reconciliation doc (Track A wrappers marked live;
  Gemini = review/artifact executor, not production mutator).
- Commit the Phase 1 canary run as the E3 restore-db rehearsal fixture.

**Exit criteria:** H1 doc committed; E3 fixture committed; bypass debt recorded as paid.

## 6. Failure handling

- Any task failure writes `repair_prompt.md` in its run dir (Hermes built-in). The sprint
  halts at the failing phase; operator reads the repair prompt before retry.
- Phase 1 (canary) failure is contained — `signals.db` is untouched. Diagnose and fix the
  harness/plan before Phase 3.
- Phase 3 failure after snapshot: roll back via `snapshots/pre_restore_target.db`; live DB
  returns to pre-restore (4-row) state; re-plan.
- 3-attempt rule: if any phase fails 3 times, stop and escalate.

## 7. Verification plan

```
# pre-sprint baseline
python -c "import sqlite3;print(sqlite3.connect('signals.db').execute('SELECT COUNT(*) FROM signals').fetchone()[0])"   # expect 4
python -m ops.cli hermes providers doctor --json

# post each restore (canary, then live)
python -c "import sqlite3;print(sqlite3.connect('<target>').execute('SELECT COUNT(*) FROM signals').fetchone()[0])"     # expect 612

# post-sprint
python run_pipeline.py health --json    # expect no catastrophic_drop_detected
python -m ops.cli hermes task ledger-audit --check all --dry-run    # expect clean trail
```

## 8. Open decisions

1. ~~Offsite backup target~~ **RESOLVED 2026-05-29: Antigravity + Google Drive.** Operator
   launches Antigravity directly (not via Hermes) with
   `antigravity-phase0-prompt.v2.md`. Remaining runtime sub-details (not blockers): the Drive
   folder name/ID, and whether the extension exposes `md5Checksum` (handled by the
   success/degraded/failed branch).
2. ~~Backup sidecar quarantine~~ **RESOLVED by verification 2026-05-29:** backup `-wal` is
   0 bytes (no frames) and may already be absent. Additionally, Phase 0 now creates a FRESH
   online backup via `backup_db.py` (WAL flattened, single file), so the offsite artifact has
   no sidecar concern at all. The live-restore source handling (F2) still deletes any backup
   `-wal`/`-shm` before restore.

(No open blockers remain. The Drive folder name is a runtime parameter; `md5Checksum`
availability is handled by the degraded-state branch.)

## 9. Down-payment deliverables (this sprint's enforcement seed)

- `docs/...` PR H1 policy-reconciliation doc.
- E3 restore-db rehearsal fixture under `tests/ops/hermes/` derived from the Phase 1 run.
- (The full E0-E5 program is a separate, later effort tracked against
  `hermes_track_a_post_pr235_updated_proposal.md`.)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 5 issues (F1-F2-F5-F6-F7), 0 critical gaps, all applied |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | N/A | no UI scope (early-exit) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**ENG REVIEW SUMMARY:**
- F1 [P1] API-reachability precondition added (Phases 1 & 3); `--force` forbidden on prod.
- F2 [P2] backup `-wal`/`-shm` cleanup added before restore (deterministic).
- F5 [clarity] determinism note added (restore/incident deterministic; only deliberate = LLM).
- F6 [test] canary exit criteria strengthened -> become E3 fixture assertions.
- F7 [rollback] double rollback coverage documented (run-dir snapshot + canonical pre-restore).
- Suspected schema-migration gap investigated and CLEARED (backup already schema 53).

**UNRESOLVED:** 0. **VERDICT:** ENG CLEARED — spec is implementation-ready pending operator
commit decision. Design review N/A (no UI). CEO review not required (recovery, not new product).
