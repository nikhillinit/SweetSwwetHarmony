# Q-26 Cross-Script DB-Tooling Parity Audit (F.1)

**Date:** 2026-05-10
**Author:** Claude (session: handoff continuation post-CEO-review)
**Branch context:** `chore/post-r19-keepalive-may-2-5` (audit work is read-only, no commits)
**Promotion gate:** ADR-043 (`phase-5-tranche-1-db-tooling-lock-ledger-discipline`)
**Queue row:** `wiki/decisions/_adr_followup_queue.md:55-56` (Q-26)
**Canonical pattern source:** PR #153, merge commit `8111d199`, 2026-05-10T01:07:31Z UTC

## 1. Frame

Q-26 was added to the ADR follow-up queue on 2026-05-10 in response to PR #153 creating the first code touchpoint at `scripts/backup_db.py`. The 2026-05-09 CEO review explicitly declined the queue-add at that time because no touchpoint existed; PR #153 reversed that condition.

Promotion to ADR-043 requires **cross-script parity** — every DB-mutating script in the repo must adopt the same `DBToolLock` + `append_db_ops_ledger` pattern with subprocess CLI tests. This document audits the current state and produces a per-script classification.

## 2. Canonical pattern (PR #153 `scripts/backup_db.py` on `origin/main`)

Four criteria define "covered":

1. **`DBToolLock` acquired before mutation, released in `finally`.** Explicit timeout, structured `lock_blocked` ledger row on failure, holder info captured.
2. **`append_db_ops_ledger` calls covering all four outcome statuses:** `success`, `lock_blocked`, `error` (including pre-lock validation errors).
3. **Structured exception class** (e.g. `BackupError(RuntimeError)`) with partial-evidence attributes (`backup_path`, `integrity_check`) so failed runs leave usable forensic data in the ledger row.
4. **Subprocess CLI tests** following the `tests/scripts/test_backup_restore.py` pattern: at minimum, lock-blocking test, ledger-success test, error-path ledger test.

The canonical exception class:

```python
class BackupError(RuntimeError):
    """Backup failure with partial evidence for DB ops ledger rows."""
    def __init__(self, message, *, backup_path=None, integrity_check=None):
        super().__init__(message)
        self.backup_path = backup_path
        self.integrity_check = integrity_check
```

## 3. Audit method

- **Scope discovery:** grep `scripts/` for `sqlite3\.connect`, `INSERT|UPDATE|DELETE|COMMIT|executescript|executemany|VACUUM|shutil\.copy|shutil\.move|os\.replace`.
- **Coverage check:** grep each candidate for `DBToolLock`, `append_db_ops_ledger`, `class \w+Error`, `except Exception`.
- **Test check:** read `tests/scripts/test_db_hardening_priority_scripts.py` to enumerate scripts already covered by subprocess CLI tests.
- **Runbook cross-reference:** `docs/runbooks/db-ops-policy.md` Tranche-1 list (5 scripts) and quarantine list (5 scripts).

## 4. Tranche-1 list (from runbook §"Prioritized Script Class")

| Script | Lock | Ledger | Structured Error | Tests | Tier |
|---|---|---|---|---|---|
| `scripts/restore_db.py` | ✓ (line 247) | ✓ (lines 250, 267, 277) | ✗ — bare `except Exception as e:` at :276 | ✓ (3 tests at `test_db_hardening_priority_scripts.py:311-376`) | **B (partial)** |
| `scripts/run_backfill.py` | ✓ (line 52) | ✓ (lines 39, 55, 98) | ✗ — no try/except around mutation work; only success ledger row | ✓ (3 tests at :263-308) | **B (partial)** |
| `scripts/e2e_batch_approve.py` | ✓ (line 69) | ✓ (lines 58, 72, 127) | ✗ — no try/except around mutation work; only success ledger row | ✓ (3 tests at :171-240) | **B (partial)** |
| `scripts/e2e_batch_check.py` | ✗ | ✗ | n/a | ✓ (1 test at :161-168) | **C (read-only)** — verified no INSERT/UPDATE/DELETE/COMMIT |
| `scripts/export_labeling_review.py` | ✗ | ✗ | n/a | ✓ (1 test at :243-260) | **C (read-only)** — verified no INSERT/UPDATE/DELETE/COMMIT |

## 5. Already-tested scripts NOT in Tranche-1 list (runbook drift)

Two scripts have parity tests in `test_db_hardening_priority_scripts.py` but are not enumerated in the runbook §"Prioritized Script Class":

| Script | Lock | Ledger | Structured Error | Tests | Tier |
|---|---|---|---|---|---|
| `scripts/db_maintenance.py` | ✓ (line 193) | ✓ (lines 196, 238) | ✗ — 4 bare `except Exception as e:` (lines 45, 79, 105, 140) | ✓ (`test_db_maintenance_records_ledger:379`) | **B (partial)** |
| `scripts/db_ops_note.py` | n/a (meta-tool: records manual operator entries; nothing to lock) | ✓ (line 29) | n/a | ✓ (`test_db_ops_note_records_manual_entry:403`) | **D (meta — out of pattern by design)** |

**Action:** runbook §"Prioritized Script Class" needs a one-line update to add `db_maintenance.py` and explicitly note `db_ops_note.py` as the meta-ledger tool.

## 6. Canonical reference

| Script | Status | Notes |
|---|---|---|
| `scripts/backup_db.py` | **A (covered)** | Canonical pattern. All four criteria satisfied. PR #153 is the touchpoint that opened Q-26. |

## 7. Untracked DB-mutating scripts (the "any others" gap)

These scripts contain INSERT/UPDATE/DELETE/COMMIT or executescript/executemany patterns AND have no `DBToolLock` or `append_db_ops_ledger` references. **Verified directly:**

| Script | Mutation evidence | Tier |
|---|---|---|
| `scripts/cleanup_publisher_keys.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/rehydrate_canonical_keys_v2.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/backfill_company_files.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/backfill_company_extraction.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/backfill_evidence_family.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/backfill_evidence_keys.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/backfill_thesis_provenance.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/backfill_hunter_company_names.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/seed_tier_c_domains.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/seed_job_posting_domains.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |
| `scripts/gc_thin_files.py` | INSERT/UPDATE/DELETE pattern | **E (uncovered, mutating)** |

These 11 scripts will fail the Q-26 promotion gate as currently written.

## 8. F.1.1 verification of ambiguous candidates (completed 2026-05-10)

All 18 candidates were grep'd for `INSERT |UPDATE |DELETE |conn\.commit|executescript|executemany`. Mutation-positive results were drilled into for context (target DB and statement type). Outcome:

### 8.1 Confirmed live mutators and scratch-only helper

| Script | Mutation evidence | Risk note |
|---|---|---|
| `scripts/build_case_law_corpus.py` | `INSERT OR REPLACE INTO precedents` (line 187), `DELETE FROM precedents WHERE vectorizer_version != ?` (line 221). Goes through `SignalStore`. | Standard backfill class. |
| `scripts/build_exemplar_library.py` | `INSERT OR REPLACE INTO thesis_exemplars` (line 196), `DELETE FROM thesis_exemplars WHERE vectorizer_version != ? AND source = 'auto'` (line 226). Goes through `SignalStore`. | Standard backfill class. |
| `scripts/spc_override_decision.py` | Source DB is copied with SQLite online backup into a temp snapshot (`evaluate_override_decision`, lines 179-181). `_evaluate_profile` copies that snapshot again into a temp scratch DB (lines 114-119). The `DELETE FROM quality_metrics_daily` happens only inside `_recompute_quality_metrics` on the scratch DB (lines 79-85). | **Reclassify out of Tier E live-mutator priority.** This is source-read and scratch-mutating. It may deserve an explicit source-unchanged regression test, but it should not be the first live DB hardening target. |

### 8.2 Confirmed read-only — Tier C

| Script | How verified |
|---|---|
| `scripts/build_founder_watchlist.py` | 0 mutation matches |
| `scripts/step3b_evidence_pack.py` | 0 mutation matches |
| `scripts/recalibrate_conformal.py` | 0 mutation matches (writes `state/conformal_calibration.json` only) |
| `scripts/generate_strategy_dashboard.py` | 0 mutation matches |
| `scripts/write_router_config_status.py` | 0 mutation matches (writes config file, not DB) |
| `scripts/pipeline_report.py` | 0 mutation matches |
| `scripts/red-team-hybrid/freshness_watchdog.py` | grep hits were comments referring to `created_at` semantics; opens DB with explicit `file:{db_path}?mode=ro` URI (line 112). |
| `scripts/compute_discovery_kpi_baseline.py` | 0 mutation matches |
| `scripts/preflight_check.py` | 0 mutation matches |
| `scripts/inspect_live_schema.py` | 0 mutation matches (PRAGMA inspection only) |
| `scripts/create_evaluation_splits.py` | 0 mutation matches |
| `scripts/thesis_activation_sandbox.py` | 0 mutation matches |
| `scripts/evaluate_phase0_gate.py` | 0 mutation matches |
| `scripts/convergence_diagnostic.py` | 0 mutation matches |
| `scripts/convergence_kpi.py` | 0 mutation matches |

## 9. Out of scope

- `scripts/test_*.py` files (test fixtures).
- Shell scripts: `scripts/backup.sh`, `scripts/restore.sh`, `scripts/Caddyfile` (not Python; separate hardening discipline).
- `scripts/red-team-hybrid/build_holdout_split.py`, `extract_founder_candidates.py`, `mine_track_b_candidates.py` (likely write to their own holdout DBs, not `signals.db` — verify before action).

## 10. Gap summary (post-F.1.1)

| Tier | Count | Names |
|---|---|---|
| A — covered | 1 | `backup_db.py` |
| B — partial (lock + ledger + tests, missing structured exception or error-path ledger row) | 4 | `restore_db.py`, `db_maintenance.py`, `e2e_batch_approve.py`, `run_backfill.py` |
| C — read-only (no mutation; pattern not required) | 17 | `e2e_batch_check.py`, `export_labeling_review.py`, plus 15 verified in §8.2 |
| D — meta tool | 1 | `db_ops_note.py` |
| E — uncovered, confirmed live-mutating | **13** | 11 from §7 + 2 live mutators from §8.1 (`build_case_law_corpus.py`, `build_exemplar_library.py`) |
| Source-read / scratch-mutating | 1 | `spc_override_decision.py` |
| Ambiguous | 0 | F.1.1 closed the set |

## 11. Recommended F.2 / F.3 sequence

**F.1.1 - DONE 2026-05-10.** Verification of the 18 ambiguous candidates closed the set: 15 are confirmed read-only (Tier C), 2 are confirmed mutating `signals.db` and have been promoted to Tier E (§8.1), and `scripts/spc_override_decision.py` is reclassified as source-read/scratch-mutating.

**F.2 - COMPLETE via PR #154 (`1beac36`).** The four former Tier-B scripts now have `DBToolError`-based structured exceptions and error-path ledger rows.

**F.3 - true live-mutator PRs (13 scripts after reclassifying `spc_override_decision.py`).** Recommended chunking, ordered by live-data blast radius:

- **F.3.0 - `cleanup_publisher_keys.py` (priority case).** Direct SQLite transaction rewriting `signals.canonical_key` and `company_files.canonical_key`. Single-script PR. Test pattern: missing-confirmation refusal ledger, lock-blocking ledger, success ledger on `--apply --yes-rewrite-keys`, and error ledger when an apply-mode query fails after the lock is acquired.
- **F.3.1 - direct canonical/evidence rewrite scripts:** `rehydrate_canonical_keys_v2.py`, `backfill_company_extraction.py`, `backfill_evidence_keys.py`, `backfill_evidence_family.py`, `backfill_thesis_provenance.py`.
- **F.3.2 - `SignalStore` corpus/build scripts:** `backfill_company_files.py`, `build_case_law_corpus.py`, `build_exemplar_library.py`. Tests must inject failures after `SignalStore.initialize()` because external writer locks can deadlock on `PRAGMA journal_mode=WAL`.
- **F.3.3 - seed scripts:** `seed_tier_c_domains.py`, `seed_job_posting_domains.py`.
- **F.3.4 - cleanup/GC and hunter identity scripts:** `gc_thin_files.py`, `backfill_hunter_company_names.py`.

Each chunk needs subprocess CLI tests using the `test_db_hardening_priority_scripts.py` pattern.

**F.4 — runbook update (small):** add `db_maintenance.py` to Tranche-1 list; document `db_ops_note.py` as the meta-ledger tool; enumerate the F.3 batches as Tranche-2/F.3 live-mutator slices; add a one-line note that read-only Tranche-1 scripts (`e2e_batch_check`, `export_labeling_review`) are exempt from lock/ledger by design.

**F.5 — ADR-043 write:** when F.2 + F.3 + F.4 close, promote Q-26 to `wiki/decisions/043-phase-5-tranche-1-db-tooling-lock-ledger-discipline.md`. The decision file documents the four-criteria pattern, the runbook reference, the test pattern, and the post-incident motivation (32 ms triple-mtime correlation finding).

## 12. Risks and caveats

1. **Test parity for Tier C scripts.** `e2e_batch_check.py` and `export_labeling_review.py` are read-only but on the Tranche-1 list. Decision needed: do they need ledger entries to record reads (audit trail), or is read-only operation sufficient evidence of non-mutation? Recommend: keep as Tier C, add a one-line runbook note that read-only scripts on Tranche-1 are exempt from lock/ledger by design.
2. **Storage-layer pipeline scripts.** Scripts that go through `SignalStore` (e.g., `run_backfill.py`) inherit the storage layer's connection management. The `DBToolLock` here is at a higher abstraction (process-level coordination) than SQLite's busy-timeout. Both are needed; verify the four Tier-B scripts don't accidentally bypass the storage layer's transaction discipline when adding the structured exception path.
3. **Ambiguous-set verification CLOSED 2026-05-10 (F.1.1).** §8 originally listed 18 ambiguous candidates. F.1.1 grep'd all 18: 15 confirmed read-only (Tier C, §8.2), 2 confirmed live-mutating and promoted to Tier E (§8.1), and `spc_override_decision.py` confirmed source-read/scratch-mutating. The Tier E count is now firm at 13 (11 from §7 + 2 from §8.1). F.2 + F.3 scope is bounded; cap at 4 weeks of intermittent work and reassess at week 2.
4. **The keepalive branch (`chore/post-r19-keepalive-may-2-5`) is forked at R19 close (`51d379d`)** and missing both PR #150 and PR #153 merges. F.2/F.3 work should branch from current `main` (`8111d19`), not from this keepalive branch.

## 13. Verification commands used

```bash
# Canonical pattern check
git show origin/main:scripts/backup_db.py | grep -nE 'DBToolLock|append_db_ops_ledger|class \w+Error'

# Per-script parity grep
grep -nE 'DBToolLock|append_db_ops_ledger|class \w+Error|except Exception' scripts/<script>.py

# Mutation candidate enumeration
grep -lE 'sqlite3\.connect|INSERT |UPDATE |DELETE |COMMIT|executescript|executemany|VACUUM|shutil\.copy|shutil\.move|os\.replace' scripts/*.py

# Test coverage check
grep -nE 'def test_|tool_name' tests/scripts/test_db_hardening_priority_scripts.py
```

## 14. Status

**F.1 COMPLETE (read-only audit).** **F.1.1 COMPLETE (ambiguous-set verified, 2026-05-10).** **F.2 COMPLETE via PR #154 (`1beac36`).** The four former Tier-B scripts now have `DBToolError`-based structured exceptions and error-path ledger rows.

Remaining Q-26 gate work:

- F.2.5: correct durable docs/runbook drift after F.2 and reclassify `spc_override_decision.py`.
- F.3: harden true live DB mutators, starting with `cleanup_publisher_keys.py`.
- F.4: runbook tranche update after F.3 slices are finished.
- F.5: ADR-043 write after F.2.5 + F.3 + F.4 close.
