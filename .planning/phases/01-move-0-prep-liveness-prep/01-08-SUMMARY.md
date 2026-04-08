---
plan: 01-08
phase: 01-move-0-prep-liveness-prep
status: complete
requirements: [REC-01]
commits: [931a663, 24a1eba]
---

# Plan 01-08 Summary — Track B labelling substrate seed (REC-01)

## Closes

- **REC-01** — Track B candidate substrate seed via DB-mined stratified sampling

## What shipped

1. **`scripts/red-team-hybrid/mine_track_b_candidates.py`** (310 lines, stdlib only)
   - Three bucket queries per CONTEXT.md D-04: TP-likely (10), FP-likely (10), ambiguous (10)
   - Latest-classification-per-signal pattern via `ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC)`
   - Deterministic ordering via `substr(canonical_key || 'seed20260408', 1, 16)` for reproducibility
   - D-05 per-signal schema with `claude_pre_label` + `pre_label_rationale` populated per bucket
   - sqlite3 read-only URI mode (`file:?mode=ro`)
   - argparse with `--db`, `--out`, `--json` flags
   - Style follows `scripts/red-team-hybrid/freshness_watchdog.py`

2. **`data/shadow/track_b_episodes.csv`** (42 lines: 12 header comments + 1 column header + 30 data rows)
   - Schema migration from Phase 0 episode-level → D-05 per-signal-candidate
   - Header comment documents the migration, cohort selection bias caveat (D-07), and the analyst-confirmation flow via `python -m ops.cli quality label`
   - 30 candidates: 10 TP-likely + 10 FP-likely + 10 ambiguous

3. **`.gitignore` exception** for `data/shadow/track_b_episodes.csv` (and forward-looking exceptions for `founder_watchlist.csv`, `founder_reputation_stub.csv`, `holdout_split/*.csv` to support Wave 2 plans 01-06 and 01-09)

## Acceptance criteria

| Check | Expected | Actual |
|-------|----------|--------|
| `test -f mine_track_b_candidates.py` | exit 0 | exit 0 |
| `python -c "import ast; ast.parse(...)"` | exit 0 | exit 0 |
| `wc -l mine_track_b_candidates.py` | ≥100 | 310 |
| `mine_track_b_candidates.py` is stdlib only | yes | yes (sqlite3, csv, json, argparse, pathlib only) |
| `test -f data/shadow/track_b_episodes.csv` | exit 0 | exit 0 |
| Total candidate rows | ≥15, target 30 | 30 |
| Bucket distribution | 10 + 10 + 10 | 10 + 10 + 10 |
| Each row has `claude_pre_label` and `pre_label_rationale` | yes | yes |
| CSV header documents schema migration | yes | yes |
| `bash scripts/red-team-hybrid/check_protected_paths.sh` | rc=0 | rc=0 |
| Commits tagged REC-01 | yes | yes (`931a663`, `24a1eba`) |

## Soft rubric gate 8 (Track B ≥15 rows): **GREEN** at 30 rows

## Commits

- `931a663` — `feat(01-08): mine_track_b_candidates.py for REC-01 Track B seed`
- `24a1eba` — `feat(01-08): Track B candidate seed CSV (REC-01, 30 rows)`

## CRITICAL DEVIATION — Production DB truncation incident

**Finding:** During Wave 2 execution, `signals.db` in the main worktree was discovered to contain only **4 signals + 0 thesis_classifications** instead of the expected 612 signals + ~3,085 classifications per MEMORY.md.

**Evidence:**
```
$ python -c "import sqlite3; c=sqlite3.connect('file:signals.db?mode=ro', uri=True); ..."
signals total: 4
thesis_classifications total: 0
source_api distribution: [('product_hunt', 2), ('github', 2)]

$ ls -la signals.db
-rw-r--r-- 1 nikhi 197609 1466368 Apr  7 22:25 signals.db
```

The 1.4MB file size matches the post-incident state described in MEMORY.md ("DB Incident & Hardening (2026-04-04) — RESOLVED ... Incident: signals.db truncated to 4 test signals due to WAL/SHM sidecar corruption"). The DB hardening from PR #131 was supposed to prevent recurrence, but this is a recurrence — or the live DB was never restored.

**Most recent useable backup:** `signals.db.pre-step4b-promotion-20260404` (9.7MB, 612 signals, 2593 thesis_classifications). This is the backup MEMORY.md cites as the pre-promotion baseline (SHA256: fcd06c6b...).

**Action taken in this plan:** ran `mine_track_b_candidates.py --db signals.db.pre-step4b-promotion-20260404` to generate the Track B CSV from the backup. Without this, the script returns rc=1 (zero candidates) and Track B cannot ship at all.

**Recommended orchestrator action (escalate to user):**
1. Verify live `signals.db` state (open in `sqlite3` and check counts).
2. If truncated, restore from `signals.db.pre-step4b-promotion-20260404` using the documented restore procedure (see PR #131 hardening) — make sure to handle WAL/SHM sidecars per the previous incident retro.
3. After restoration, re-run `python scripts/red-team-hybrid/mine_track_b_candidates.py` to regenerate `data/shadow/track_b_episodes.csv` against the live DB. Should produce identical output if the backup matches live state.
4. Add a Phase 1 verification gate (Plan 01-10 SUB-01) that asserts `signals.db` has ≥600 signals before claiming the rubric gate 2 freshness check is meaningful.
5. The R19 mitigation work (LIV-02 freshness watchdog + D-22 keep-alive installer from Plan 01-05) targets the *no-collection* root cause but does NOT detect *truncation*. Consider a separate `signal_count_watermark` guard as an additional liveness check (mentioned in MEMORY.md hardening retro: "Signal-count watermark guard").

**This deviation does not affect the Track B CSV's analytical validity** — the backup is the source MEMORY.md uses as the canonical pre-promotion baseline. But it DOES mean the `freshness_watchdog.py` shipped in Plan 01-05 (LIV-02) is checking a near-empty DB and reporting all collectors as "FRESH" because there are no rows to age out — the watchdog needs a minimum-row-count assertion.

## Execution note

The original gsd-executor agent (agent-ab1a3824) hit two compounding failures: (a) Bash was denied in its environment, AND (b) the worktree was created from `db0f3bd` (main lineage) instead of the expected base `059a18816...`, leaving the worktree without `.planning/`, `scripts/red-team-hybrid/`, `data/shadow/`, or any of the Phase 1 prerequisites the plan referenced. The agent could not self-heal via `git reset --hard` because Bash was denied.

Recovery: orchestrator wrote the script inline (310 lines, byte-for-byte from the plan template at lines 159-470 with leading 4-space indentation removed), ran it against the most recent useable signals.db backup, force-added the CSV with an explicit `.gitignore` exception, and committed atomically (2 commits per D-16). The empty `worktree-agent-ab1a3824` is removed in wave cleanup.

## Deviations from plan

1. **DB source**: ran against `signals.db.pre-step4b-promotion-20260404` instead of live `signals.db` due to truncation incident (see CRITICAL DEVIATION above).
2. **`.gitignore` modification**: added explicit exception for `data/shadow/track_b_episodes.csv` (and forward-looking exceptions for the Wave 2 sibling plans 01-06 and 01-09 outputs). The original plan did not specify a `.gitignore` change because the planner did not anticipate the generic `data/shadow/*.csv` rule. This is a minimal scope addition that unblocks the commit.
3. **No worktree branch**: plan was applied directly in main worktree because the worktree-based execution path was non-functional. Acceptable per the orchestrator escalation pattern used for plans 01-01, 01-04, 01-07.
