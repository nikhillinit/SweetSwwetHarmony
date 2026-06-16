# SweetSwwetHarmony — Trust Release Strategy

**Status:** Proposed · **Date:** 2026-06-15 · **Branch of record:** `claude/affectionate-davinci-2g8wjg`
**Supersedes:** ad-hoc "Trust Release" chat notes (2026-06-15)
**Scope of this document:** strategy + execution sequence only. The milestones below are *not*
executed by this commit.

> **Headline promise (acceptance bar for all P0 work):** after this release an operator can
> *run, dry-run, restore, evaluate, and inspect* the system without wondering whether the
> tooling changed production state, lost data, or hid missing evidence.

---

## 0. Quick-start for the implementer

Execute top-to-bottom; respect the **Prereq** column. Sizes: S ≤ 0.5d, M ≈ 1–2d, L ≈ 3–5d.

| Milestone | Branch | First command / action | Exit condition | Prereq | Size |
|---|---|---|---|---|---|
| **P0-0** Restore eval capability | _(ops, no branch)_ | Re-run **Thesis Golden Set Gate** on PR #271 (Actions → Re-run jobs) | Gate reaches a real accuracy verdict (≥ 0.9), not `no_go: rate limiting` | Paid Gemini tier ✅ 2026-06-16 | S |
| **P0-2** Land gate hardening | `fix/thesis-gate-fail-closed` (=PR #271) | Confirm 4-file diff, merge once gate green | #271 merged on green gate; then **immediately** refresh `active-sprint.md` | P0-0 | S |
| **P0-1** DB chain-of-custody + recovery | `hardening/signals-db-durability` | `git rm --cached signals.db && printf 'signals.db\n' >> .gitignore` | Canonical DB out of tree; restore is manifest-only; anomaly check scheduled; #149 closed | none (parallel to P0-2) | L |
| **P0-3** Prove dry-run immutability | `fix/process-dry-run-readonly` | `pytest tests/integration/test_process_dry_run_readonly.py` (expect RED) | Copied-DB table hashes identical across all lanes; #187 closed | P0-1 relocation | L |
| **P1-1** F6 thesis revalidation | `eval/thesis-f6-revalidation` | run documented 64-sample live eval (paid Gemini) | F6 no longer "pending"; baseline doc updated | P0-0 | M |
| **P1-2a** news_api diagnostic | `fix/news-api-freshness` | narrow `news_api` run on scratch DB | text report: API status, pre/post-filter counts, timestamps | P0-1 | S |
| **P1-2b** Freshness health vocab | `fix/news-api-freshness` | extend `ops/collector_health.py` | `fresh_empty_expected` + last-nonempty wired into watchdog | P1-2a | M |
| **P1-3** Source-specific quality | `eval/source-quality-rollout` | `--source-api` tuning on scratch DB | measured FP↓ / TP-loss; golden set grows | P0-3, P1-1, recovered data | M |
| **P1-4** Make release model explicit | `ops/required-checks` | mark required checks in branch protection | contributor answers "safe to merge?" from CI+docs | P0-3 (canary) | S |
| **P2-1** Operator observability | `ops/pipeline-visualization` | wire `PipelineProgress` into `workflows/pipeline.py` | failures visible without raw SQL; no dry-run writes | P0-3 | L |

**Operating guardrail (applies throughout):** until P0-1 relocates the DB *and* P0-3 proves
immutability, **do not run any local mutating pipeline command** (`process`/`collect` without
`--dry-run`, restore, migrate) against the canonical DB. Use scratch copies. This guardrail
does **not** block PR #271 (CI-only; see §4).

---

## 1. Why trust-first, and why now

SweetSwwetHarmony is already feature-rich: multi-source collection, keyword + Gemini thesis
classification, Notion routing, suppression, quality ops, Hermes eval gates, dashboards, and a
wide test surface. The product is not "collect more signals" — it is *credible, repeatable,
auditable* promotion of candidates into the investment workflow.

The bottleneck is **trust in state transitions**, not feature count. Three open issues prove
the compounding loops can currently lie:

- **#187** — `process --dry-run` can mutate persistent tables, so evals/rehearsals/incident
  debugging can corrupt their own evidence.
- **#149** — the live `signals.db` was reverted to a 4-row truncated state; the writer was
  never proven. *(This document proves it — Appendix B.)*
- **#148** — collector freshness can't distinguish "broken" from "legitimately empty."

Feature velocity without state trust creates false confidence. State trust creates reusable
velocity. So this release hardens the loops before expanding them.

## 2. The corrected critical path

```
P0-0 Restore eval capability (Gemini paid tier)   ── DONE 2026-06-16
   └─▶ P0-2 Merge gate-hardening PR #271            (re-run gate → green → merge)
P0-1 DB out of tree + chain-of-custody + recovery   (highest-severity durability fix, #149)
   └─▶ P0-3 Prove process --dry-run immutability     (adopt approved .omx plan, #187)
            └─▶ P1 (F6 / freshness / source tuning / required checks)
                     └─▶ P2 (operator observability)
```

North star: **Protect the gates → protect the database → restore signal freshness → improve
thesis precision → improve operator UX.**

Two corrections vs. the original strategy drove this ordering:
1. "Merge #271 once CI is green" is not a rubber-stamp — its gate was **red on Gemini quota**,
   not code (Appendix B). Hence new **P0-0**.
2. DB durability is **active, high-severity harm** (the truncated DB is committed to git), so it
   is promoted ahead of the dry-run work it also de-risks.

## 3. The four invariants (no feature work until proven)

| Invariant | Guarantee | Proof artifact |
|---|---|---|
| **Eval integrity** | Thesis-sensitive changes fail closed; live eval completes under paid quota or is explicitly label-approved | Green Thesis Golden Set Gate + recorded F6 result |
| **Dry-run immutability** | `process --dry-run` mutates **no** persistent table (101 enumerated) and opens **no** writable connection | Feature-matrix copied-DB hash/row-count proof + `-wal`/`-shm` canary (#187) |
| **DB chain-of-custody** | Canonical DB lives **out of the git tree**; replace/restore is manifest-backed; reverts are detected | Untracked DB + restore manifests + scheduled anomaly check |
| **Freshness integrity** | Collector health distinguishes broken / stale / missing-key / **expected-empty** | Extended `ops/collector_health.py` + per-collector diagnostic |

## 4. Operating guardrails (corrected coupling)

- **PR #271 is decoupled from DB work.** The Thesis Golden Set Gate
  (`.github/workflows/thesis-golden-gate.yml`) evaluates the **golden fixture**
  (`tests/fixtures/thesis_llm_golden_set.jsonl`) and checks **provider configs** via
  `hermes providers doctor` — it does **not** read `signals.db`. CI runs on isolated runner
  checkouts, so a local operator's `git checkout` cannot affect a CI gate run. Therefore #271
  may merge as soon as its gate is green, in parallel with P0-1. *(This corrects the review
  suggestion to hard-gate #271 on DB relocation — the mechanism does not apply.)*
- **The real in-tree DB writer to fix is the Daily Pipeline.**
  `.github/workflows/discovery-pipeline.yml` restores `signals.db` from a GitHub artifact
  (`mv artifact-tmp/signals.db signals.db`, lines 69-72) and writes it at the repo root
  (`DISCOVERY_DB_PATH: signals.db`). P0-1 must repoint or pause this workflow; it is a second
  propagation vector for #149 (Appendix B).
- **No local mutating pipeline run on the canonical DB** until P0-1 + P0-3 land (see §0).

## 5. Phased roadmap

### P0-0 — Restore live thesis-eval capability ✅ (paid Gemini tier, 2026-06-16)
- **Outcome:** eval-integrity pillar is executable; gate + F6 no longer quota-blocked.
- **Remaining:** re-trigger #271's gate and confirm completion. **Thin contingency only** (not a
  structural-fallback redesign): if the paid tier still intermittently rate-limits, raise
  retry/backoff in the eval preflight (the failed run exhausted only "2 retries") and/or batch
  the 64-sample run.
- **Exit:** a gold-mode gate run reaches a real verdict (≥ 0.9) instead of `no_go: rate limiting`.

### P0-2 — Land the gate-hardening slice (merge PR #271)
- **Outcome:** missing provider-doctor evidence becomes a structural block; Hermes thesis task
  paths are thesis-sensitive.
- **Tasks:** confirm the 4-file diff — resolver (`scripts/ci/resolve_thesis_eval_mode.py`:
  `doctor is None → ["provider doctor evidence is missing"]`), detector
  (`scripts/ci/detect_thesis_sensitive_changes.py`: add `integrations/hermes/tasks/*thesis*.py`)
  and their two tests; re-run gate (P0-0); merge on green.
- **Immediately after merge** *(self-healing governance)*: refresh `docs/claude/active-sprint.md`
  (stale — rebuilt 2026-06-02, omits #271/#187/#148/#149) and **validate** the refreshed doc
  lists #271/#187/#148/#149 with correct statuses.
- **Exit:** #271 merged on a green gate; a no-key/no-doctor thesis-sensitive PR blocks unless
  label-approved; active-sprint doc current.

### P0-1 — DB out of tree, chain-of-custody, and bounded recovery (mitigate/close #149)
- **Outcome:** no `git checkout`, clone, workflow, script, or dry-run can silently replace or
  truncate the live DB; the strongest baseline is restored.
- **Tasks:**
  1. **Freeze evidence** (already captured): `sha256 447c1359…940e`, size `1466368`, 4 rows,
     newest `2026-01-10`, mtime `2026-05-05T22:33:37Z`.
  2. **Reference audit before untracking** — enumerate every in-tree `signals.db` reference and
     map each to a fixture path or `DISCOVERY_DB_PATH` override. The audit is **complete**
     (Appendix C); the three repo-root writers to reconcile are
     `.github/workflows/discovery-pipeline.yml`, `scripts/restore.sh`, and
     `tests/scripts/test_generate_strategy_dashboard.py:31`. Most tests already use `tmp_path`
     scratch DBs and are unaffected; `sqlite-durability-smoke.yml` uses a scratch artifact path
     and is unaffected.
  3. **Untrack + relocate:** `git rm --cached signals.db`; add to `.gitignore`; make
     `DISCOVERY_DB_PATH` the canonical out-of-tree path; **fail fast** if it resolves inside the
     repo working tree. *(Resolution order verified: `DISCOVERY_DB_PATH` > `SIGNAL_DB_PATH` >
     `signals.db`.)* Add a **tracked seed/empty fixture** for any job that legitimately expects a
     repo-relative DB so CI still runs.
  4. **Repoint the Daily Pipeline** (`discovery-pipeline.yml`) to the out-of-tree path or pause
     it until the canonical DB is established (it currently restores from artifact and writes
     in-tree).
  5. **Enforce a single sanctioned mutation path:** make `scripts/restore_db.py` (already emits
     sha256/mtime/integrity manifests, makes a pre-restore backup, validates `schema_version`,
     and refuses while the API server is reachable) the **only** way to replace the canonical DB;
     reject overwrite without manifest + confirmation token.
  6. **Bounded recovery (rollback-safe):**
     a. **Preserve the current truncated DB** to an explicit copy before any restore.
     b. **Prefer the most complete source.** Check the **Litestream S3 replica**
        (`litestream-restore-verify-nightly.yml` already restore-verifies
        `s3://…/litestream/signals.db/` nightly) — it may be more complete than the local
        **612-row pre-Step-4B baseline** (#149's strongest local family). Choose whichever has
        the newest verified, integrity-`ok` state.
     c. **Rehearse first:** restore into a **temp path**, verify row count + `schema_version` +
        `PRAGMA integrity_check`, *then* promote to the canonical path. This removes the
        single-point-of-failure if the backup is partial or the script regresses.
     d. State honestly: the post-R19 ingest delta beyond the chosen baseline may not be fully
        recoverable from local candidates (#149); this is a regression baseline, not a full
        restoration.
  7. **Scheduled anomaly check:** daily hash/size/row-count-velocity check that alerts on sudden
     reverts (reuse the keepalive artifact pattern).
  8. **Document the live-DB operating model** and **close #149** with the root-cause attribution
     (Appendix B) + controls above.
- **Exit:** canonical DB out of tree; replacement requires an auditable manifest path; Daily
  Pipeline repointed; anomaly check scheduled; #149 closed with root cause + mitigations.

### P0-3 — Prove `process --dry-run` immutability (close #187)
- **Outcome:** `process --dry-run` is empirically proven read-only end-to-end.
- **Adopt the existing approved plan:** `.omx/plans/process-dry-run-readonly-ralplan-dr-20260515.md`
  (Option B; 6 steps; 21 acceptance criteria). Do **not** reinvent it. Real remaining holes it
  identifies (the thesis reject/hold paths are *already* guarded on main —
  `pipeline.py:2330-2347, 2393-2405`): `workflows/run_manager.py` writes `run_history` via direct
  `db.execute(); commit()` bypassing `transaction()`; `EntityResolutionStore`/`FounderStore` open
  **own** connections to the same DB; suppression warmup calls `sync(dry_run=False)`;
  classification / confidence-ledger / shadow-log / claim-fact / functional-schema persistence.
- **Tasks:** fail-red first; make dry-run a **process-wide read-only lane before the first DB
  open**; push read-only / `allow_writes = not dry_run` barriers into **every same-DB opener**;
  route dry-run observability to stdout / result payloads / non-persistent sinks; build the
  `tests/support/db_snapshot.py` + `tests/integration/test_process_dry_run_readonly.py`
  feature-matrix proof across the 10 named lanes (`baseline`, `claim_facts`, `functional_schema`,
  `entities`, `phase_g_identity_resolution`, `shadow_entity_resolution`, `exit_predictor`,
  `investor_matching`, `founder_scoring`, `combined_high_risk`).
- **Proof completeness — WAL/SHM canary:** the authoritative invariant is *persistent table
  content* (per-table row counts + stable hashes). **Additionally assert no new
  `signals.db-wal` / `signals.db-shm` sidecars are created during a dry-run** — their appearance
  signals a *writable* connection even when no rows change, so it is a canary for the read-only
  barrier itself. If a journal file is unavoidable on a read path, classify it explicitly as a
  non-data artifact and assert its content is unchanged. Precedent exists:
  `tests/scripts/test_backup_restore.py:354-361` already creates and asserts on
  `signals.db-wal` / `signals.db-shm`, and `discovery-pipeline.yml:195-197` checkpoints WAL.
- **Repair stays gated:** live-DB repair of confirmed dry-run-touched rows
  (`751/betterhelp.com`, `753/faire.com`) remains a **separate** backup-first, rehearse-on-copy
  runbook (per the `.omx` plan's DB Repair Gates).
- **Exit:** copied-DB table hashes identical before/after across all lanes; no `-wal`/`-shm`
  created; every read-only write attempt fails closed; #187 closed with the hash matrix + repair
  notes.

### P1-1 — F6 thesis revalidation (now unblocked)
- **Outcome:** thesis-sensitive PRs are protected by current, not stale, eval data.
- **Tasks:** run the documented 64-sample live eval (paid Gemini) against the golden fixture
  (fingerprint `536e081d…`); update `docs/evals/thesis-golden-gate-baseline.md` (F6 → done).
  F6 depends on the **fixture + quota, not `signals.db`**, so it is independent of recovery.
  Promote a new baseline only via the documented flow: diagnostic run → gate run → CODEOWNER
  review → `baseline-promotion-approved` label (distinct from the structural-bypass
  `thesis-label-drift-approved`).
- **Exit:** F6 no longer "pending"; candidate baseline is the 64-sample result.

### P1-2a — `news_api` scratch diagnostic (#148, part 1)
- **Outcome:** a one-off, reproducible diagnostic that classifies the failure mode.
- **Tasks:** run a narrow `news_api` collection on a scratch DB; capture raw API status,
  pre-filter count, post-filter count, persisted rows, and newest timestamp. (No per-collector
  diagnostic command exists today.)
- **Exit:** a text report distinguishing `missing_key` / `quota_or_auth` / `api_shape_changed` /
  `zero_results_expected` / `filtered_all` / `persist_failed` / `timestamp_mismatch` / `fresh`,
  with a reproducible command.

### P1-2b — Permanent freshness-health vocabulary (#148, part 2)
- **Outcome:** the watchdog stops conflating "no rows" with "broken."
- **Tasks:** **extend** `ops/collector_health.py` (which already has
  `success/partial_success/dry_run/stale/failing/disabled_missing_key/blocked_access`) with
  `fresh_empty_expected` and a **last-nonempty-ingest** signal distinct from `monitor_store.py`'s
  `last_checked_at` / `last_success_at`; update `freshness_watchdog.py` (36h threshold) to use it.
- **Exit:** legitimate expected-empty results don't alert; genuine API/auth/persist failures
  still do; regression coverage or a keepalive artifact demonstrates the fix.

### P1-3 — Source-specific quality rollout (needs recovered data)
- **Outcome:** LLM improvements isolated and measured safely.
- **Tasks:** use the verified end-to-end `--source-api` filtering (CLI → `process_pending` →
  `get_pending_signals`) on scratch DBs to tune per-source behavior (e.g., isolate Hacker News);
  measure FP↓ vs TP-loss; forbid one source regressing others; expand the golden set from
  confirmed production FPs/FNs.
- **Exit:** measured FP/TP-loss evidence; golden set grows from real labels.

### P1-4 — Make the release model explicit
- **Outcome:** a contributor can answer "what is safe to merge?" from CI + docs alone.
- **Tasks:** mark as **required** the checks already running on PRs (verified on #271): Thesis
  Golden Set Gate, Core Regression Suite, SQLite Durability Smoke, Docker Build & Smoke, Hermes
  Ledger Audit, Local Artifact Validation. **Add a dry-run immutability canary as a required
  check** — wire the P0-3 `tests/integration/test_process_dry_run_readonly.py` harness (a fast
  baseline-lane subset) as a path-filtered required status check for PRs touching
  `workflows/pipeline.py`, `workflows/run_manager.py`, or `storage/`. This operationalizes
  Invariant 2 continuously rather than as a one-time proof. Codify atomic-PR + evidence-bundle
  norms; keep structural bypass label-gated.
- **Exit:** required checks enforced (including the immutability canary); structural bypasses
  auditable.

### P2-1 — Operator observability & UX (gated on P0-3)
- **Outcome:** operators trace pipeline health without raw SQL — over *trustworthy* facts.
- **Tasks:** wire `visualization/terminal_progress.py::PipelineProgress` into
  `workflows/pipeline.py`; add `--progress` to `run_pipeline.py` (only after proving it writes no
  persistent metrics in dry-run); add HTML reports (signal-flow / collector-performance /
  verification-funnel — currently absent); surface semantic collector health + thesis-fit trends
  in the existing `dashboard/` pages; add the net-new visualization tests (currently 0 — the
  "47 tests" figure in `IMPLEMENTATION_SUMMARY.md` is investor-matching, not visualization).
- **Exit:** failures visible without DB inspection; reporting never mutates state in dry-run.

## 6. Decision rules

- **Continue only if:** PRs stay small/evidence-backed; the dry-run hash matrix stays clean;
  DB changes carry manifests; thesis-sensitive changes pass live eval or are label-approved;
  collector health states are explainable.
- **Stop and fix immediately if:** a dry-run mutates any persistent table *or creates a
  `-wal`/`-shm` sidecar*; a DB copy/restore occurs without a manifest; a thesis-sensitive PR
  passes structural mode without approval; a collector alert can't distinguish broken from
  expected-empty; a dashboard metric can't be traced to a table/query/artifact.
- **Defer by default if:** the work adds a collector before freshness semantics; changes thesis
  behavior before F6; adds persistent observability before dry-run immutability; expands Notion
  writes before DB durability is verified.

## 7. Governance & cadence

Single-purpose branches mapped to milestones (see §0). Atomic PRs (≤ ~4 files where possible);
**evidence bundles** (test output, command transcript, before/after DB or eval hash); docs
updated in the same PR; a session-start "current state" bundle (fetch, status, open PRs, Hermes
registry, provider doctor). `active-sprint.md` never drifts more than one merged safety PR behind.

---

## Appendix A — Fact-check ledger (evidence)

- Schema `CURRENT_SCHEMA_VERSION = 53` — `storage/signal_store.py:98`.
- Read-only stack — `ReadOnlyStoreError` `:90`, `read_only` ctor `:1999`, `PRAGMA query_only`
  `:2035`, `_ensure_writable()` `:2071` (18+ writers call it; `transaction()` catches at runtime).
- Dry-run wiring — `pipeline.py:1259` `initialize(read_only=dry_run)`; run-tracking skipped
  `:1262-1273`; outbox drain skipped `:1281-1282`; thesis reject/hold **already** guarded
  `:2330-2347, 2393-2405`.
- **101 persistent tables** in `signals.db` (core + `storage/migrations/quality_tables.py` +
  migration modules). `EntityResolutionStore`/`FounderStore` use own connections to the same file
  (per the `.omx` plan).
- Thesis gate — `.github/workflows/thesis-golden-gate.yml`; runbook
  `docs/runbooks/thesis-golden-gate.md` (0.9 floor; structural-bypass label
  `thesis-label-drift-approved`); manifest `tests/fixtures/thesis_llm_golden_set.manifest.json`
  (64 samples, `536e081d…`); baseline `docs/evals/thesis-golden-gate-baseline.md` (F6 PENDING);
  `candidate_v3` scored on 40 samples; promotion label `baseline-promotion-approved`.
- PR #271 — open, non-draft, mergeable_state **unstable**, 4 files / +30 / −2; **Thesis Golden
  Set Gate = failure** (Gemini `RateLimitError`); other 7 checks pass; label present.
- Issues #187 (open; approved plan `.omx/plans/process-dry-run-readonly-ralplan-dr-20260515.md`),
  #148 (open; `freshness_watchdog.py`, 36h threshold), #149 (open; truncated `447c1359…`,
  1466368 B, mtime `2026-05-05T22:33:37Z`).
- Existing durability assets — `scripts/backup_db.py`, `scripts/restore_db.py` (manifests);
  **`.github/workflows/litestream-restore-verify-nightly.yml`** (S3 Litestream replica +
  nightly restore verify); `tests/scripts/test_backup_restore.py:354-361` (WAL/SHM-aware).
- Health model — `ops/collector_health.py`
  (`success/partial_success/dry_run/stale/failing/disabled_missing_key/blocked_access`);
  `monitoring/monitor_store.py` tracks `last_checked_at`/`last_success_at` but not last-nonempty.
- Visualization — `visualization/terminal_progress.py::PipelineProgress` (not wired; no
  `--progress`); HTML reports absent; `dashboard/` has real pages; visualization tests = 0.
- Move 0 protected-paths freeze window (2026-04-06 → 2026-04-19) has **passed** — not blocking.

## Appendix B — Root-cause analyses

### #149 — DB reverted/truncated
The committed `signals.db` is byte-identical (`sha256 447c1359…940e`, 1466368 B, 4 rows) to the
known truncated file **and is git-tracked** (`git ls-files signals.db` returns it). Two
propagation vectors explain the incident; both are closed by P0-1:
1. **Primary — git restore of a tracked truncated DB.** `git checkout`/clone/reset rewrites
   `signals.db` with the committed truncated bytes and stamps a *fresh* mtime — matching the
   observed `2026-05-05T22:33:37Z` and explaining why #149 ruled out `restore_db.py` (which
   preserves mtime via `copy2`).
2. **Secondary — Daily Pipeline artifact restore.** `discovery-pipeline.yml:69-72` does
   `mv artifact-tmp/signals.db signals.db` from a GitHub artifact; a stale/truncated artifact
   would repopulate the in-tree DB with a fresh mtime as well.
**Fix:** untrack + relocate out of the working tree; repoint the Daily Pipeline; enforce the
manifest-only restore path (P0-1).

### #271 — gate red
CI has `GOOGLE_API_KEY`; the gate ran live gold mode and blocked on `RateLimitError`
("keep blocked until quota recovers or **billing changes**"). The `thesis-label-drift-approved`
label does not bypass a *quota* failure in gold mode. **Fix:** paid Gemini tier (P0-0, done),
then re-run.

## Appendix C — In-tree `signals.db` reference audit (for P0-1)

Captured 2026-06-15 via repo search. Classification drives the repointing work in P0-1.

**Repo-root writers to reconcile (must repoint/pause before untracking):**
- `.github/workflows/discovery-pipeline.yml` — restores from artifact (`:69-72`), `DISCOVERY_DB_PATH:
  signals.db` (`:88,121,130,139,151,165`), WAL checkpoint + integrity (`:194-200`), uploads
  `signals.db`/`-wal`/`-shm` (`:219-233`).
- `scripts/restore.sh:20,87-89` — copies into `$DATA_DIR/signals.db`.
- `tests/scripts/test_generate_strategy_dashboard.py:31` — literal `"db_path": "signals.db"`.

**Already safe (scratch / tmp_path / explicit `--db` / read-only) — no change needed:**
- Tests using `tmp_path/.../signals.db`: `tests/test_phase0_integration.py:33`,
  `tests/test_triage_cli.py:20`, `tests/test_analytics.py:20`,
  `tests/scripts/test_create_evaluation_splits.py` (×6),
  `tests/scripts/test_seed_script_db_tool_hardening.py` (×6),
  `tests/scripts/test_backup_restore.py` (×many, incl. WAL/SHM at `:354-361`),
  `tests/scripts/test_generate_strategy_dashboard.py:775,1153`.
- `.github/workflows/sqlite-durability-smoke.yml:65,86,95` — scratch artifact path.
- `.github/workflows/regression-gate.yml:105` — in-container `DISCOVERY_DB_PATH`.
- `.github/workflows/litestream-restore-verify-nightly.yml:78-79` — S3 replica + `$RUNNER_TEMP`.
- Read-only/operator-`--db` scripts: `scripts/generate_strategy_dashboard.py` (",never writes"),
  `scripts/recalibrate_conformal.py`, `scripts/compute_discovery_kpi_baseline.py`
  (ShadowSidecar immutable URI), and the `scripts/quality/*` + maintenance scripts that take an
  explicit `--db` (default `signals.db` is convenience only).

> Each repo-root writer above maps to: set `DISCOVERY_DB_PATH` to the out-of-tree canonical path
> (workflows/scripts) or a `tmp_path` fixture (the dashboard test). Record the final mapping in
> the P0-1 PR's evidence bundle.
</content>
