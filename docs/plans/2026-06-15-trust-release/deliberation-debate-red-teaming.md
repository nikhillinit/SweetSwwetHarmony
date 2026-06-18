# Trust Release — Red Team Review

> Applied skill: **deliberation-debate-red-teaming**. Reviewer: Claude (claude-opus-4-8).
> Date: 2026-06-18. Reviewed at live HEAD `6023a29` (plan baseline was `de00bb0`, **31 commits stale**).
> Method: every load-bearing claim checked against live code per `.claude/rules/plan-verification.md`.

## Verdict

**NEEDS CHANGES (blocking) — do not execute as written.**

This is a *second-pass* red team: the plan already absorbed a codex/kimi/gemini panel's
findings (R1–R10). The job here was to verify whether those fixes actually hold against the
running codebase and to find what the first panel missed. They missed a lot, because they
reviewed the *prose* of the proposal, not the *tree*. The headline:

> **Most of this plan is already merged, and the parts that "fix" the first panel's
> concerns are wired into nothing.** Executing the plan task-by-task (as its mandated
> `executing-plans` sub-skill instructs) would re-derive existing code, fail its own TDD
> RED steps (the tests already pass), and risk overwriting merged implementations with the
> plan's *illustrative* pseudocode. A "trust" release that ships test-passing theater over
> an unchanged production path is worse than no release.

Confidence: **0.86**. The fidelity findings (F1–F5) are verified against `file:line`; the
severity scoring on operational risks is judgment.

---

## Step 1 — Proposal & stakes

- **What:** 8-milestone "trust release" (DB durability, dry-run immutability, schema
  migration, collector-health v2, circuit breaker, parity gate, PR-evidence gate, trust CLI)
  gating all future feature work.
- **Stakes:** Irreversible (production `signals.db` corruption / data loss during restore or
  migration). Strategic — it gates *everything* downstream and is branded "trust."
- **Reversibility:** Low. A bad restore overwrites the canonical DB; a CI gate that passes on
  forged/empty evidence silently lowers the bar permanently.
- **Classification:** Technical/Architecture + Security/Safety, **Complex**. Rubric target ≥ 4.2.

## Step 2 — Adversarial roles

| Role | Why chosen |
|------|-----------|
| **Forensic Verifier** | Plan-vs-tree fidelity — the project's own `plan-verification.md` rule. The decisive lens here. |
| **SRE / 2am operator** | Restore safety, lock semantics, migration under live writers. |
| **Malicious / Contrarian actor** | CI evidence forgeability, suspension-state poisoning. |
| **Long-term Maintainer** | Dead code, duplicate mechanisms, two canonical docs. |
| **Contrarian** | Does this "trust release" actually increase trust, or manufacture it? |

## Step 3 — Critiques (evidence-backed)

### Forensic Verifier — FIDELITY FAILURES (the core of this review)

**F1 — The plan is largely already executed; status snapshot is 31 commits stale.**
The status table claims "verified at HEAD `de00bb0`" and marks M2, M3 (and the rest) `🔴 OPEN`.
But:
- `ops/collector_health.py:40` already has `REPORT_SCHEMA_VERSION = 2`, the full `VALID_STATUSES`
  set incl. `api_shape_changed`/`fresh_empty_expected`, and the exact `CollectorHealthReport.__post_init__`
  the plan proposes to "add." → **M3 step 4 is DONE.**
- Commit `7f02719 feat(migration,health): M2 migration runner + M3 collector health v2` already
  merged `scripts/run_migration.py`, `storage/collector_suspension.py`, and both test files.
- **All 8 plan-created files already exist** in the tree (`db_anomaly.py`, `litestream_ctrl.py`,
  `run_migration.py`, `collector_suspension.py`, `cassette_policy.py`, `run_thesis_parity_gate.py`,
  `check_pr_evidence.py`, `trust_status.py`).

Consequence: an agent running the plan's TDD loop hits "Step 2: verify it fails → Expected:
ModuleNotFoundError" and the module *imports fine*. The RED step never goes red. Per the project's
own `cli-commands.md` Red Flags ("Tests passing immediately upon writing → Requires Restart"),
the plan is structurally un-executable as written and invites overwriting merged code with
pseudocode.

**F2 — M2 targets a schema version that is already taken and means something else (SHOWSTOPPER).**
- Real mechanism: `storage/signal_store.py:2148` reads `SELECT MAX(version) FROM schema_migrations`
  and applies a `MIGRATIONS` dict. `CURRENT_SCHEMA_VERSION = 53` (`signal_store.py:98`).
- **Real v52** (`signal_store.py` ~2166) = `ALTER TABLE thesis_classifications ADD COLUMN
  classification_status`. **Real v53** = three columns on `thesis_classifications`.
- The plan's M2 claims "v52 migration adds `rows_returned_this_iter`, `rows_after_filter_this_iter`,
  `last_failure_mode`" **to the `signals` table.** That is fiction: v52 is already used, for a
  different table. Those three columns exist **nowhere** in the live `signals` schema or any real
  code path — only in `scripts/run_migration.py:13-15` and its own tests.

**F3 — `run_migration.py` is dead code that cannot run against the real DB (SHOWSTOPPER).**
`scripts/run_migration.py:35` does `SELECT version FROM schema_version` — a **table that does not
exist in `signals.db`.** The only `schema_version` *table* in the repo is in
`storage/relationship_store.py:209` (a different store). Production uses the `schema_migrations`
table. So `MigrationRunner.run()` against the canonical DB raises `no such table: schema_version`.
Its tests pass only because they fabricate a `schema_version` table in a tmp DB. Grep confirms
`MigrationRunner` is **referenced by nothing** outside its own test. It is orphaned: merged, green,
and inert.

**F4 — The W1 Litestream-safe restore is not wired into the restore path (SHOWSTOPPER).**
`grep litestream scripts/restore_db.py` → **0 hits.** `litestream_ctrl.py` exists as a standalone
module, and `restore_with_integrity_check()` exists as a standalone function, but the actual
`restore_backup()` flow (the one that takes the lock + writes the ledger, `restore_db.py:254+`)
calls **neither.** A real restore today still does *not* stop Litestream, flush WAL, or reset the
generation. R1 is marked "incorporated"; in the running code it is not. This is the single most
dangerous gap — the brief's W1 data-loss scenario is **unmitigated in production** while the plan
reports it fixed.

**F5 — The W2 fix is a dead constant addressing a misdiagnosed problem.**
- `scripts/restore_db.py:36` defines `MAINTENANCE_LOCK_TIMEOUT_SECONDS = 180`. Grep: it is **used
  nowhere** — never passed to `lock.acquire()`. `restore_backup()` still defaults
  `lock_timeout_seconds = LOCK_TIMEOUT_SECONDS` (`=5`, line 35). The constant exists only to satisfy
  `test_maintenance_lock_timeout_is_at_least_120s`.
- Worse, the premise is wrong. `DBToolLock.acquire(timeout_seconds=...)` is the **wait-to-acquire**
  time, not the hold time. Actual lock expiry is `utils/db_tool_lock.py:26 DEFAULT_TTL_SECONDS = 3600`
  (1 hour). A 30–120s restore is nowhere near a 1-hour TTL — **the lock does not expire mid-operation.**
  The first panel invented W2, and the "fix" tunes a parameter that governs neither the imagined
  problem nor anything real.

### SRE / 2am operator

**O1 — Migration-under-writers fix tests a fabricated DB, not production.** `MigrationRunner._assert_no_active_writers`
uses `BEGIN EXCLUSIVE` polling — reasonable in isolation, but it is never invoked by the real
`_apply_migrations()` path (F3), so live migrations still run without it. The protection is real in
the test, absent in production.

**O2 — `assert_wal_flushed` is a heuristic that can both false-pass and false-fail.** It checks
`-wal` file size > 0. Under WAL mode a non-empty `-wal` is *normal* during operation and an empty
one does not prove S3 sync. Even if wired, this gate is not a correctness guarantee for "WAL synced
to S3 before SIGTERM" (the actual W1b concern).

**O3 — `litestream stop` / `litestream generations` command surface is unverified.** The brief (W1)
itself flags Litestream has no clean "pause"; `litestream_ctrl.py` shells out to `litestream stop`
and `litestream generations` without any check that those subcommands/flags exist in the deployed
version. No integration test exercises a real binary.

### Malicious / Contrarian actor

**S1 — Suspension store has a poisoning + bypass seam.** `storage/collector_suspension.py` keys the
scratch-guard on `HARMONIC_SCRATCH_DB`. Two edges: (a) production code that forgets to set the env
var in a diagnostic context still writes real suspension state; (b) anyone running with the env var
set can never *suspend* (writes silently no-op) — a foot-gun that disables the circuit breaker
wholesale if the var leaks into a prod shell. The "durable" store is a single JSON file with
last-writer-wins and no locking — concurrent collectors can clobber each other's suspensions.

**S2 — PR-evidence gate (M6) is still bypassable, just with one more step.** The check now requires a
regex-matched `actions/runs/\d+` URL, but does not verify the run exists, belongs to this PR, passed,
or is recent. `https://github.com/x/y/actions/runs/1` satisfies it. Syntactic → slightly-less-syntactic,
not semantic. Calibrate: this is acceptable as a speed bump, *not* as a trust guarantee — don't market
it as one.

**S3 — Parity gate (M5) tests arithmetic, not parity.** `test_*` feed hand-picked integers to
`gate.evaluate(cli_correct, api_correct, total)` — they verify `abs(a/n − b/n) ≤ 0.02`, i.e. division.
Nothing tests that the CLI path *accepts or honors* `temperature=0.0`, or that the two paths are
actually invoked. The real W3 risk (CLI ignoring temperature → nondeterminism) is **untested**. The
config default `temperature=0.0` is necessary but unproven to flow anywhere.

### Long-term Maintainer

**M-1 — Three schema-versioning mechanisms now coexist:** `schema_migrations` table (real, signal_store),
`schema_version` table (relationship_store + the orphaned run_migration), and the `CURRENT_SCHEMA_VERSION`
constant. This is exactly the drift the project keeps fighting. Adding a fourth runner that speaks a
non-existent dialect increases entropy.

**M-2 — Two canonical docs persist (the W10 fix is itself unverified).** The plan claims it "replaces
00-strategy.md," but `00-strategy.md` (this file's sibling) is the 57KB doc, and
`00-strategy-pre-deliberation.md` (28KB) still exists and is still referenced as "authoritative for
P0-x." A future session reading only one will get a partial picture — the precise failure mode W10
named, only half-closed.

### Contrarian

**C1 — Does this raise trust or manufacture it?** The deliverable of a trust release is *behavioral*:
a restore that is provably safe, a migration that provably coordinates, a gate that provably blocks
bad PRs. What landed is a set of green unit tests over modules that the production paths don't call
(F3, F4, F5, O1, S3). Green CI on inert code is *anti-trust*: it produces a dashboard that says "safe"
while the restore path is unchanged. If one thing is fixed first, it must be F4.

## Step 4 — Risk register (Severity × Likelihood)

| # | Risk | Role | S | L | Score | Category |
|---|------|------|---|---|-------|----------|
| F4 | Litestream-safe restore not wired → real restore still risks data loss/replica overwrite | Forensic/SRE | 5 | 4 | **20** | SHOWSTOPPER |
| F2 | M2 re-uses taken version v52; intended `signals` columns never land via any real path | Forensic | 4 | 5 | **20** | SHOWSTOPPER |
| F3 | `run_migration.py` crashes on real DB (`no such table: schema_version`); orphaned | Forensic | 4 | 4 | **16** | SHOWSTOPPER |
| F1 | Stale snapshot → agent re-derives/overwrites merged code; TDD RED can't go red | Forensic | 4 | 4 | **16** | SHOWSTOPPER |
| F5 | W2 "fix" is dead constant; lock-expiry premise wrong (TTL=3600s) | Forensic | 3 | 5 | **15** | SHOWSTOPPER |
| O1 | Writer-coordination guard not invoked by real migration path | SRE | 4 | 3 | 12 | High |
| S3 | Parity gate tests division, not temperature/parity behavior | Security | 3 | 4 | 12 | High |
| C1 | Green tests on inert code create false trust signal | Contrarian | 4 | 3 | 12 | High |
| M-1 | 3–4 coexisting schema-version mechanisms | Maintainer | 3 | 4 | 12 | High |
| S1 | Suspension store: env-var bypass + no-lock JSON clobber | Security | 3 | 3 | 9 | Monitor |
| S2 | PR-evidence gate accepts non-existent run URLs | Security | 2 | 4 | 8 | Monitor |
| O3 | `litestream stop/generations` subcommands unverified vs deployed binary | SRE | 3 | 3 | 9 | Monitor |
| O2 | `assert_wal_flushed` heuristic can false-pass/fail | SRE | 3 | 2 | 6 | Monitor |
| M-2 | Two canonical docs persist (W10 half-fixed) | Maintainer | 2 | 4 | 8 | Monitor |

## Step 5 — Required changes (before any execution)

**Gate-0 (do first): Reconcile the plan with live HEAD `6023a29`.** Re-derive the status table from
the tree, not from `de00bb0`. For every milestone, mark DONE / PARTIAL / OPEN with `file:line` proof.
This single step dissolves F1 and reframes the rest.

**SHOWSTOPPER mitigations:**

1. **F4 (own this first):** Wire the Litestream lifecycle into the *actual* `restore_backup()` path —
   `stop → assert_wal_flushed → restore_with_integrity_check → reset_generation → start`, all inside
   the held lock + ledgered. Add an integration test that asserts `restore_backup()` invokes the
   controller (today it invokes neither). Until then, keep the "no mutating commands on canonical DB"
   guardrail and say plainly: **restore is not yet safe.**
2. **F2/F3:** Delete or rewrite `run_migration.py` to speak the production mechanism
   (`schema_migrations` table via `signal_store._apply_migrations`, **not** a `schema_version` table).
   Pick the **next free version (v54)** for the `signals` columns — or drop them if no real code reads
   them (currently nothing does). Add the migration to the `MIGRATIONS` dict so it actually applies.
3. **F1:** Convert already-merged milestones (M2/M3 and any others) from "implement" tasks into
   "verify/harden" tasks. Remove the TDD RED steps for code that exists.
4. **F5:** Either *use* `MAINTENANCE_LOCK_TIMEOUT_SECONDS` (pass it to `acquire`) **or** delete it and
   the test. Document that lock expiry is the `DBToolLock` TTL (3600s), not the acquire timeout, so the
   next reader doesn't re-misdiagnose W2.

**High-priority:**

5. **O1:** Invoke the writer-coordination guard from the real migration entry point, or state that
   migrations are operator-run under the maintenance guardrail.
6. **S3:** Add a test that the CLI path actually receives/honors `temperature=0.0` (or, if it can't,
   document the accuracy-delta fallback as the *primary* mechanism, not the alternate).
7. **C1/M-1:** One schema-version source of truth. Don't ship a fourth.

**Monitor / accept:** S1 (document the env-var contract; add file locking if collectors write
concurrently), S2 (label the evidence gate a speed-bump, not a guarantee), O2/O3 (add a Litestream
binary smoke check), M-2 (collapse to one canonical doc).

## Recommendation

**NEEDS CHANGES — block execution until Gate-0 + the four showstopper mitigations land.** The
intent and structure of this plan are sound and the first panel's instincts were right; the failure
is *fidelity* — the fixes were written but not wired, and the ground moved 31 commits underneath the
snapshot. Reconcile against HEAD, wire F4 into the real restore path, fix the v52/version collision,
and downgrade the already-merged milestones to verification. Then this becomes the boringly
trustworthy release it's branded as. As written, it would ship green tests over an unchanged — and
still unsafe — restore path.
