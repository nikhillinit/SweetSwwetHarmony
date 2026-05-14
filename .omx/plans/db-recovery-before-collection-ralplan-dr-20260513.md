# Final RALPLAN-DR: DB Recovery Before Collection

## Decision

Use an audit-first recovery sequence centered on repo-local `612`/schema `53`
restore candidates and the canonical `scripts/restore_db.py` tool, but do not
mutate the live default DB until all writer-exclusion, candidate-selection,
evidence-copy, and sidecar/API gates pass.

The default recovery target remains the `612`/schema `53` family already
consistent with `.omx/state/db_watermark.json`, with
`backups/signals-20260511-030832.db` as the leading candidate only if the audit
proves it is byte-for-byte or content-equivalent to the other visible
`612`/`53` candidates. If that audit fails, stop and escalate to an off-host or
VSS source decision rather than forcing a restore.

Two restore branches are explicit:

- Branch A: in-place restore back to the default live `signals.db`. This is the
  only branch that directly unblocks default collection and the existing
  keepalive task after post-restore validation.
- Branch B: fresh-target restore to a new DB path. This preserves the corpse DB
  in place, but it does not unblock default collection or `HarmonicKeepAlive`
  unless runtime is explicitly retargeted. CLI collection can target the fresh
  DB with `--db-path`; MCP and `SignalStore()` resolve
  `DISCOVERY_DB_PATH` then `SIGNAL_DB_PATH`; the current keepalive runner has no
  `--db-path` override and remains pointed at the default path unless its
  wrapper/environment is explicitly regenerated or updated for the fresh target.

Disable `HarmonicKeepAlive` before the next scheduled 08:00 local run unless
recovery is already complete and verified. Leave `HarmonicFreezeDrill`
disabled and otherwise untouched.

## Principles

1. Audit before mutation; destructive recovery is allowed only after explicit
   gates pass.
2. Prefer the smallest recovery that returns Harmonic to a known-good
   repo-grounded baseline before another collection.
3. Treat live writers, WAL/SHM sidecars, and API reachability as first-class
   safety gates, not cleanup details.
4. Preserve incident evidence before invoking tooling that may checkpoint or
   delete sidecars.
5. Keep this plan bounded to DB recovery before collection, not Phase 5.2
   durability architecture.

## Decision Drivers

1. The live `signals.db` is a `4`-row schema `26` corpse while runtime expects
   schema `53`, and DB guard correctly blocks writes as
   `catastrophic_drop_detected`.
2. `.omx/state/db_watermark.json` already names `612`/schema `53` as the
   accepted repo-local baseline.
3. `scripts/restore_db.py` is the canonical restore path, but its sidecar
   handling can checkpoint and remove `signals.db-wal` / `signals.db-shm`
   before it creates its own pre-restore backup, so external evidence capture
   must happen first for any in-place attempt.
4. The current runtime routing is split: CLI flows can use `--db-path`, while
   MCP / `SignalStore()` fall back through `DISCOVERY_DB_PATH` then
   `SIGNAL_DB_PATH`, and the keepalive wrapper currently targets the default DB
   path only.

## Options Considered

| Option | Verdict | Pros | Cons |
|---|---|---|---|
| Branch A: restore the audited `612`/schema `53` family in place to `signals.db` with `scripts/restore_db.py` after exclusivity and evidence-copy gates pass | Chosen default | Matches watermark; uses canonical lock/ledger/pre-restore-backup path; directly returns default runtime to a known-good baseline | Accepts loss of later `614`/`616` rows unless separately recovered; requires live-writer exclusion and sidecar-safe evidence handling first |
| Branch B: restore the same candidate into a fresh target DB path | Fallback only | Avoids mutating the corpse DB immediately; useful if sidecars/writers cannot be cleared safely | Does not unblock default collection or keepalive unless runtime is explicitly retargeted; keepalive wrapper currently has no target override |
| Bypass the guard with `--recovery-override` and collect on the `4`-row corpse | Rejected | Fastest short-term unblock | Continues on a schema `26` DB below the catastrophic threshold; high risk of compounding corruption and muddying evidence |
| Delay restore and attempt to recover a richer `614`/`616` post-2026-05-11 state first | Conditionally viable but not default | Could preserve more recent rows if a trustworthy source exists | No repo-local candidate is yet proven authoritative; increases downtime and invites evidence drift if done before first re-baseline |

## Architect Tension

The real tradeoff is speed versus provenance fidelity:

- Branch A is the safest repo-supported path to re-enable the default runtime,
  but it may discard later `614`/`616` rows as accepted loss;
- Branch B preserves the corpse in place, but it only becomes operational if
  runtime routing is deliberately moved to the fresh target;
- delaying for a richer source could preserve more data, but only if an
  authoritative source exists and can be verified without improvising the
  recovery path.

This plan resolves the tension by making audited `612`/`53` in-place restore
the default, while keeping fresh-target restore as a bounded fallback when
exclusivity or sidecar safety blocks Branch A.

## Deliberate Pre-Mortem

### Scenario 1: restore runs while a live writer still owns the DB

Failure mode: one of the live `discovery_engine.mcp_server` processes or an API
writer recreates sidecars or writes during restore, yielding a mixed-state DB.

Mitigation: require a pre-mutation writer audit, stop or isolate all live
writers, verify sidecar state, and allow `restore_db.py` to refuse if
checkpoint or sidecar removal is unsafe.

### Scenario 2: sidecar evidence is lost before incident capture

Failure mode: `restore_db.py` checkpoints and deletes `signals.db-wal` /
`signals.db-shm`, and the only surviving pre-restore artifact is the copied
main DB, leaving incident evidence incomplete.

Mitigation: before any in-place restore attempt, copy the current corpse main DB
plus `signals.db-wal` and `signals.db-shm` when present into a safe local
incident artifact path such as
`.omx/incident/evidence/db-recovery-20260513/`. Treat the tool's own
pre-restore backup as main-DB-only safety coverage, not full sidecar evidence
preservation.

### Scenario 3: the wrong backup is chosen because candidate equivalence was assumed

Failure mode: the newest-looking `612`/`53` backup differs materially from the
other visible candidates, or an off-host source is actually the authoritative
baseline.

Mitigation: compare row count, schema version, file size, integrity, source
distribution, and content hashes before selecting a restore target. If the
visible candidates diverge materially, stop and escalate source selection before
mutation.

### Scenario 4: fresh-target restore succeeds mechanically but runtime still points at the corpse DB

Failure mode: Branch B produces a healthy fresh-target DB, but collection,
MCP, API, or keepalive still read/write the default `signals.db`, so operators
believe recovery succeeded while production behavior remains broken.

Mitigation: treat runtime retargeting as a separate explicit gate. Confirm the
specific caller path being used: CLI `--db-path`, env-based
`DISCOVERY_DB_PATH` / `SIGNAL_DB_PATH`, and the keepalive wrapper's generated
default path behavior.

### Scenario 5: recovery succeeds mechanically but collection is resumed before guard and runtime reality are revalidated

Failure mode: a restored DB exists, but stale routing, schema mismatch, or
blind scheduler re-enable reintroduces failure or false confidence.

Mitigation: require post-restore integrity, schema, row-count, watermark, DB
guard, and caller-path validation plus one bounded post-recovery collection gate
before normal scheduling resumes.

## Expanded Test Plan

### Unit / Contract

- Reconfirm `tests/ci/test_restore_db_cli_contract.py` still protects shared
  DB-path handling.
- Reconfirm `tests/utils/test_db_guard.py` covers
  `catastrophic_drop_detected`, `watermark_missing`, and override scope.
- Reconfirm `tests/test_db_path_resolution.py` still documents and protects the
  env priority `DISCOVERY_DB_PATH > SIGNAL_DB_PATH > signals.db`.
- Reconfirm `scripts/restore_db.py` still enforces:
  - integrity check before restore
  - API reachability gate unless `--force`
  - sidecar refusal/cleanup path
  - pre-restore safety backup of the target main DB
  - `DBToolLock` and ledger rows

### Integration / Recovery Rehearsal

- Read-only compare all repo-local candidates in the `612`/`53` family.
- Rehearse Branch B against a scratch target path if candidate readability or
  schema fit needs proof without mutating `signals.db`.
- Verify the chosen candidate and any restored DB satisfy:
  - `PRAGMA integrity_check = ok`
  - `MAX(schema_migrations.version) = 53`
  - `COUNT(*) FROM signals >= 612`
- Validate that the evidence-copy path captures the corpse main DB and, when
  present, both WAL/SHM sidecars before any in-place restore attempt.

### E2E / Operational

- Verify live writers are gone or proven isolated before Branch A.
- Verify `restore_db.py` returns a named pre-restore backup and ledger evidence.
- For Branch B, verify the intended runtime path truly points at the fresh DB
  before calling recovery operationally successful.
- Run one bounded post-recovery collection before any broader collect cadence.
- Keep `HarmonicKeepAlive` disabled until the one-shot collection and health
  checks pass.

### Observability / Evidence

- Capture process inventory, candidate DB stats, hashes, and sidecar state
  before mutation.
- Capture the pre-restore evidence-copy artifact path and inventory.
- Capture the restore CLI output, the created pre-restore backup path, and the
  resulting ledger row after mutation.
- Capture post-restore DB stats, DB-guard status, and effective caller DB path
  before another collection.

## Audit-First Execution Plan

### Step 1: Freeze the recovery lane and contain scheduled writes

- Do not run any collector, sync, or process command against the default DB.
- Disable `HarmonicKeepAlive` before the next scheduled 08:00 local run unless
  recovery will already be complete first.
- Leave `HarmonicFreezeDrill` disabled and untouched.
- Record the current corpse DB, sidecar presence, live process inventory, and
  candidate backup inventory in a recovery note or command transcript.

Acceptance criteria:

- No new write-capable pipeline command has run on default `signals.db`.
- `HarmonicKeepAlive` is prevented from firing the next known-failing 08:00
  run.
- `HarmonicFreezeDrill` state is unchanged.
- The current `signals.db` state and candidate inventory are documented.

### Step 2: Audit writers and restore candidates read-only

- Enumerate the live `python -m discovery_engine.mcp_server` processes and any
  reachable API or other Python processes that might hold the default DB.
- Audit each visible `612`/`53` candidate read-only for:
  - file hash
  - size
  - `PRAGMA integrity_check`
  - `COUNT(*) FROM signals`
  - `MAX(schema_migrations.version)`
  - per-source distribution for a small invariant slice
- Compare `backups/signals-20260511-030832.db`,
  `backups/signals.backup-20260511T105827.db`,
  `backups/signals-20260404-072102.db`,
  `signals.db.pre-step4b-promotion-20260404`, and
  `signals.db.restore-stage-20260404T195300Z`.

Acceptance criteria:

- A candidate matrix exists for all visible restore files.
- The chosen restore source is either proven equivalent to the others or the
  plan explicitly stops for source-selection escalation.

### Step 3: Preserve pre-restore evidence before any in-place mutation

- Before Branch A, copy the current corpse `signals.db` plus `signals.db-wal`
  and `signals.db-shm` when present into a safe local incident artifact path
  such as `.omx/incident/evidence/db-recovery-20260513/`.
- Treat this evidence copy as separate from `restore_db.py`'s own safety backup.
- Clarify in the incident note that the tool-generated pre-restore backup is the
  target main DB only and does not replace explicit sidecar evidence capture.

Acceptance criteria:

- A safe local incident artifact directory is chosen and recorded.
- The corpse main DB is copied there before any in-place restore attempt.
- WAL/SHM sidecars are copied too when present.
- The distinction between external evidence copy and tool-generated
  pre-restore main-DB backup is documented.

### Step 4: Decide the restore source and branch

- Default decision: choose the repo-local `612`/`53` family if the read-only
  audit shows no material divergence.
- If a richer `614`/`616` source is required to meet recovery goals, stop and
  escalate before mutation; do not infer its authority from older notes alone.
- Choose Branch A when the goal is to unblock default runtime and exclusivity on
  `signals.db` can be proven.
- Choose Branch B only when preserving the corpse in place or avoiding unsafe
  sidecar/writer clearance outweighs the need to restore the default path
  immediately.
- Decide whether `restore_db.py` can use its normal API gate or will require
  `--force` because the health endpoint is irrelevant or unreachable despite all
  real writers being stopped.

Acceptance criteria:

- A single restore candidate is selected with explicit rationale.
- Branch A or Branch B is chosen explicitly, not implied.
- The need for `--force` is justified explicitly rather than assumed.

### Step 5: Prove routing and exclusivity for the chosen branch

- For Branch A, stop or otherwise isolate every live process that can write the
  default DB, including observed `discovery_engine.mcp_server` processes.
- For Branch A, confirm no target `signals.db-wal` or `signals.db-shm` sidecars
  remain owned by active writers.
- For Branch B, confirm the fresh target path and the exact runtime caller path
  that would use it:
  - CLI collection can use `--db-path <fresh-target>`
  - MCP / `SignalStore()` use `DISCOVERY_DB_PATH`, then `SIGNAL_DB_PATH`, then
    `signals.db`
  - the current `HarmonicKeepAlive` wrapper has no `--db-path` override and
    therefore remains disabled for fresh-target recovery unless explicitly
    regenerated or updated to point at that target
- If Branch A writers or sidecars cannot be cleared safely, stop and switch to
  Branch B or incident escalation rather than force in-place mutation.

Acceptance criteria:

- Branch A: no active writer remains for the target DB and sidecar state is
  recoverably clear.
- Branch B: the fresh target path plus intended caller-path retargeting are
  documented explicitly.
- No one claims keepalive is unblocked on Branch B without wrapper/env
  retargeting evidence.

### Step 6: Run the canonical restore in a bounded maintenance window

- Branch A: invoke `scripts/restore_db.py` against the selected candidate and
  live `signals.db` only after the evidence-copy and exclusivity gates are
  green.
- Branch B: invoke `scripts/restore_db.py` against the selected candidate and a
  fresh target DB path.
- Allow the tool to create the pre-restore safety backup and ledger row.
- Do not broaden this step into Phase 5.2 durability or keepalive-wrapper
  redesign.

Acceptance criteria:

- `restore_db.py` exits successfully on the chosen target path.
- A tool-generated pre-restore backup path is created and recorded when the
  target DB already existed.
- A success ledger row exists for the restore action.

### Step 7: Revalidate the restored baseline before another collection

- Re-check the restored DB for integrity, schema version, signal count, and
  basic source distribution.
- If the accepted restored DB is the expected `612`/`53` baseline already named
  in `.omx/state/db_watermark.json`, do not update the watermark.
- Only if operators intentionally accept a different restored baseline should
  they run the explicit `init-watermark` / save-watermark path after acceptance.
- Keep `HarmonicKeepAlive` disabled until the restored DB is validated and one
  bounded follow-up collection succeeds.

Acceptance criteria:

- Restored DB reads as healthy and compatible with runtime schema `53`.
- Watermark remains unchanged for accepted `612`/`53` recovery.
- Any watermark change is tied to an intentionally chosen different accepted
  baseline.
- No scheduler or collector is resumed blindly.

### Step 8: Run one bounded post-recovery collection gate

- Use one narrow collector run first, not a broad normal cadence.
- For Branch A, run it against default `signals.db`.
- For Branch B, only run it operationally if the caller is explicitly pointed at
  the fresh target with `--db-path` or env retargeting.
- Verify DB guard, row growth, collector health, and effective DB path after the
  bounded run.
- Only after the bounded run passes should regular collection be considered for
  re-enable.

Acceptance criteria:

- The first post-recovery collection succeeds without DB-guard failure on the
  intended target DB.
- Signal count remains stable or grows from the restored baseline.
- Branch B is not misreported as default-runtime recovery without explicit
  retargeting proof.

## ADR

### Decision

Recover to the repo-local `612`/schema `53` baseline using
`scripts/restore_db.py` after a read-only candidate audit, explicit evidence
copy for any in-place attempt, and explicit branch selection between in-place
restore and fresh-target restore.

### Drivers

1. Current live DB is below catastrophic threshold and schema-incompatible with
   runtime expectations.
2. Repo-local watermark already points to `612`/schema `53`.
3. Canonical restore tooling already handles lock, ledger, target-main-DB
   backup, integrity checks, and sidecar refusal, but not incident-grade
   preservation of pre-restore WAL/SHM evidence.
4. Fresh-target recovery is only useful if actual runtime routing is also
   redirected.

### Alternatives Considered

- Continue with `--recovery-override` on the `4`-row corpse.
- Delay for a richer `614`/`616` source before any restore.
- Prefer fresh-target restore as the default path.

### Why Chosen

It is the smallest evidence-backed recovery that returns the system to a known
good baseline before another collection while minimizing improvised mutation and
keeping runtime-routing truth explicit.

### Consequences

- Likely accepts loss of the later `614`/`616` rows unless a better source is
  proven before mutation.
- Requires a short maintenance window with writers stopped for Branch A.
- Requires explicit routing proof for Branch B before any claim of operational
  recovery.
- Keeps Phase 5.2 durability questions out of this incident lane.

### Follow-Ups

1. Decide separately whether the missing `614`/`616` rows warrant a later
   forensic recovery attempt.
2. Revisit keepalive re-enable only after the DB and one bounded collection are
   healthy.
3. If the accepted restore baseline differs from `612`/`53`, run the explicit
   watermark reconciliation path after acceptance.

## Verification Commands

### Scheduler Containment

```powershell
Disable-ScheduledTask -TaskName HarmonicKeepAlive
```

```powershell
Get-ScheduledTask -TaskName HarmonicKeepAlive | Select-Object TaskName,State
```

```powershell
Get-ScheduledTaskInfo -TaskName HarmonicKeepAlive | Select-Object LastRunTime,NextRunTime,LastTaskResult
```

```powershell
Get-ScheduledTask -TaskName HarmonicFreezeDrill | Select-Object TaskName,State
```

### Read-Only Audit

```powershell
Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -match 'discovery_engine\.mcp_server'
} | Select-Object ProcessId,Name,CommandLine
```

```powershell
python -c "import sqlite3; from pathlib import Path; dbs=['signals.db','backups/signals-20260511-030832.db','backups/signals.backup-20260511T105827.db','backups/signals-20260404-072102.db','signals.db.pre-step4b-promotion-20260404','signals.db.restore-stage-20260404T195300Z']; \
for p in dbs: \
    conn=sqlite3.connect(f'file:{Path(p).resolve()}?mode=ro', uri=True); \
    cur=conn.cursor(); \
    count=cur.execute('SELECT COUNT(*) FROM signals').fetchone()[0]; \
    ver=cur.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]; \
    integ=cur.execute('PRAGMA integrity_check').fetchone()[0]; \
    print(p, {'count': count, 'schema': ver, 'integrity': integ}); \
    conn.close()"
```

```powershell
Get-FileHash signals.db, backups\signals-20260511-030832.db, backups\signals.backup-20260511T105827.db, backups\signals-20260404-072102.db, signals.db.pre-step4b-promotion-20260404, signals.db.restore-stage-20260404T195300Z
```

```powershell
Get-ChildItem signals.db, signals.db-wal, signals.db-shm -ErrorAction SilentlyContinue | Select-Object Name,Length,LastWriteTime
```

### Routing / Guard Checks

```powershell
curl http://localhost:8000/api/v1/health
```

```powershell
python -c "from utils.db_guard import check_db_health; print(check_db_health('signals.db'))"
```

```powershell
python -c "import os; print({'DISCOVERY_DB_PATH': os.getenv('DISCOVERY_DB_PATH'), 'SIGNAL_DB_PATH': os.getenv('SIGNAL_DB_PATH')})"
```

### Canonical Restore

Use only after the audit, evidence-copy, and exclusivity/branch gates pass:

```powershell
python scripts/restore_db.py backups/signals-20260511-030832.db --db-path signals.db
```

If the API-health guard is unreachable or irrelevant but writer exclusivity is
already proven, the operator may deliberately choose:

```powershell
python scripts/restore_db.py backups/signals-20260511-030832.db --db-path signals.db --force
```

Fresh-target fallback shape:

```powershell
python scripts/restore_db.py backups/signals-20260511-030832.db --db-path .tmp\recovery\signals-restored-20260513.db
```

### Post-Restore Verification

```powershell
python -c "import sqlite3; conn=sqlite3.connect('signals.db'); cur=conn.cursor(); print({'integrity': cur.execute('PRAGMA integrity_check').fetchone()[0], 'count': cur.execute('SELECT COUNT(*) FROM signals').fetchone()[0], 'schema': cur.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]}); conn.close()"
```

```powershell
python -c "from utils.db_guard import check_db_health; print(check_db_health('signals.db'))"
```

```powershell
python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api --db-path signals.db
```

## Acceptance Criteria

1. The recovery path remains non-mutating until candidate-selection,
   evidence-copy, and writer-exclusion gates are explicitly green.
2. The plan distinguishes Branch A in-place restore from Branch B fresh-target
   restore, and states that Branch B does not unblock default collection or
   keepalive unless runtime is explicitly retargeted.
3. Pre-restore incident evidence preserves the corpse main DB plus WAL/SHM
   sidecars when present before any in-place restore can checkpoint/delete
   them.
4. Recovery uses `scripts/restore_db.py`, producing ledger evidence and a
   target-main-DB pre-restore backup.
5. Accepted `612`/`53` recovery leaves `.omx/state/db_watermark.json`
   unchanged; only an intentionally different accepted baseline triggers the
   explicit watermark init/save path.
6. One bounded post-recovery collection succeeds before broader collection is
   considered, and the plan stays bounded to DB recovery before collection
   rather than Phase 5.2 durability.
