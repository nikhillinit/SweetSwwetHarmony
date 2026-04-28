# Phase 2 Day 2.5 Hotfix — Self-Review

**Branch:** `phase2/hotfix-day2-5`
**Base:** `phase2/instrumentation`
**Commits:** 4 (`ce392b9`, `a963843`, `02c9b28`, `0eb9e08`)
**Reviewed at:** 2026-04-27 by author (self-review pass — independent agent review was attempted and aborted by user)
**Verdict:** **SHIP** with two non-blocking fast-followers (see §5).

---

## 1. Verdict

Ship. The four commits are atomic, individually reviewable, all green on the targeted gate (114 tests across affected files plus adjacent suites), and individually revertible if any single workstream regresses. No critical findings.

Two test gaps and one operational nit are documented as fast-followers — none gate the merge.

---

## 2. Critical findings (would block merge)

**None.**

I specifically pried at:

- **`db_guard` strict contract** (`utils/db_guard.py:57-87`, `90-153`) — `check_db_health` no longer mutates the filesystem on missing watermark; `guard_command` blocks `watermark_missing` writes regardless of `allow_override`; the override path is gated on `message == "catastrophic_drop_detected"`, so `db_read_error` is also correctly blocked. The only path that creates `.omx/state/db_watermark.json` is `run_pipeline.py:8117-8132` (`init-watermark` CLI). Confirmed via grep — only `run_pipeline._enforce_signal_count_guard` calls `check_db_health` outside tests, and it was updated to surface the bootstrap hint.

- **Split dedup CTE** (`scripts/create_evaluation_splits.py:188-228`) — ROW_NUMBER OVER (PARTITION BY signal_id ORDER BY ...) with `COALESCE(timestamp, '')` handles NULL timestamps correctly; rowid DESC is the deterministic tiebreaker. Live dry-run reproduces 124/40/47 split (no live duplicates exist today; dedup is preventive).

- **Split invariants** (`scripts/create_evaluation_splits.py:267-297`) — checks per-split uniqueness, pairwise disjointness, union = labeled population. Wired into `main()` at line 493-497 with `EXIT_INVARIANT_FAILED=5`. Artifacts are NOT written when invariants fail (the `write_split_artifacts` call is gated by the prior `try/except`).

- **Untrack** (`02c9b28`) — `git ls-files state/` returns only `state/collectors.json` (heartbeat). Disk listing confirms all four split artifacts still present locally. `.gitignore` has the four patterns under a clear comment block.

- **`override_active` derivation** (`ops/collector_health.py:157-182`) — pure read-time computation, no state-schema migration, no new persisted field. Live `python -m ops.collector_health --format json` reports `override_active_count=0` against the current 23-collector live state.

---

## 3. Important findings (should fix in fast-follow, not blocking)

### 3a. Test gap: `db_read_error + override = blocked` is not directly asserted

`tests/utils/test_db_guard.py::TestCheckDbHealth::test_db_read_error_returns_unhealthy` (lines 138-148) tests `check_db_health` only. There is no test that confirms `guard_command(write, allow_override=True)` blocks when the message is `db_read_error: ...`. The behavior is correct (the override branch only fires on `message == "catastrophic_drop_detected"`), but a missing test means a future refactor could regress it without CI noticing.

**Fast-follow:** add one test. ~10 lines. See §5.

### 3b. Stale split files persist on a failed run

If `assert_split_invariants` raises in `main()`, the script exits 5 without writing new artifacts — but stale artifacts from a *prior* run remain on disk under `state/`. Downstream tools (Day 4+ calibration) reading `state/holdout_ids.json` could silently pick up stale data without realizing the latest run failed.

Now that the artifacts are untracked, the operator-visible blast radius is smaller (no git history), but the run-to-run integrity gap remains. Two options:

- (i) Clean the output dir at the start of `main()` — aggressive, prevents stale-read.
- (ii) Document that consumers must check `evaluation_splits_summary.json:generated_at` against the expected run timestamp.

I'd lean (ii) because (i) deletes data on every dry-run and would surprise operators. Worth raising before Day 4 calibration consumes the holdout file.

**Fast-follow:** add a one-line check in any Day 4+ script that consumes the holdout file (`generated_at` within the last N hours). Track in tracker, not this hotfix.

### 3c. SQLite version requirement

`ROW_NUMBER OVER (...)` requires SQLite 3.25+ (window functions, 2018). Local Python is on 3.45.1 so the live dev box is fine. CI runners on older Pythons (3.10, possibly 3.9) may bundle older SQLite. If CI ever fails on a `near "OVER"` syntax error, the fix is to bump the runner Python or document a minimum.

**Fast-follow:** add a one-line note in `scripts/create_evaluation_splits.py` module docstring: "Requires SQLite ≥ 3.25 for window functions." Not blocking — local tests proved it works.

---

## 4. Nits (optional, never blocking)

- `db_guard.check_db_health` line 85 `current >= baseline * 0.5` short-circuits to True when `baseline == 0` (stale watermark with `signal_count: 0` always reports healthy). Pre-existing, not introduced by this hotfix. Mention in the runbook if it becomes operationally relevant.
- `_compute_override_active` semantic edge: if YAML literally specifies `configured_status: disabled_missing_key` (very unusual — that value is normally env-derived, not typed) and state is `blocked_access`, the function returns `True` even though both states express "not running". In practice this never fires because `disabled_missing_key` is never typed into YAML. Documenting in the runbook would close the loop.
- Test naming: the rewritten CLI tests (`tests/test_run_pipeline_cli.py`) use long descriptive names like `test_health_read_with_missing_watermark_warns_without_mutating` which is correct but slightly longer than the surrounding suite's average. Stylistic, not actionable.

---

## 5. Recommended fast-followers (post-merge)

```python
# tests/utils/test_db_guard.py — add to TestGuardCommand

def test_db_read_error_with_override_still_blocks_write(self, tmp_path):
    """Override path is scoped to catastrophic drops, not read errors."""
    db_path = tmp_path / "signals.db"
    watermark = tmp_path / "watermark.json"
    watermark.write_text(json.dumps({"signal_count": 100}))

    with patch.object(db_guard, "WATERMARK_PATH", watermark), \
         patch.object(db_guard, "read_current_signal_count",
                      return_value=(None, "forced read failure")):
        assert db_guard.guard_command(
            str(db_path), "write", allow_override=True
        ) is False
```

That's the only one I'd land before opening the PR. The rest are tracker items.

---

## 6. Things I genuinely like

- **Atomic commit topology** — four commits, four commit messages that read like a release note, every commit independently revertible. No "fixup" or "wip" commits. This is the diff structure that makes a 6-month-old bisect actually work.
- **The dedup CTE is clean.** ROW_NUMBER + COALESCE + rowid tiebreaker is exactly the pattern. Not over-engineered (no `quality_feedback_dedup` view, no helper function — just inline SQL). The contract change to require `created_at` is right and honest about a column we already implicitly depend on.
- **`override_active` is the cheapest possible solution.** No schema migration, no new persisted field, no heartbeat code change — just compares state to YAML at read time. The `_compute_override_active` helper is pure and trivially testable. This is what "no schema bump" means done correctly.

---

## 7. Verification gate evidence

```
$ python -m pytest tests/utils/test_db_guard.py tests/test_run_pipeline_cli.py \
    tests/scripts/test_create_evaluation_splits.py \
    tests/scripts/test_inspect_live_schema.py \
    tests/ops/test_collector_health.py \
    tests/ops/test_collector_heartbeat.py \
    tests/ops/test_gp_workload.py -q
114 passed, 1 warning in 17.78s
```

```
$ python scripts/inspect_live_schema.py; echo "exit=$?"
exit=0

$ python scripts/create_evaluation_splits.py --seed 42 --dry-run
split: train=124 calibration=40 holdout=47 (seed=42, label_source=signal_quality_metrics.human_label)

$ python -m ops.collector_health --format json | jq '.summary.override_active_count'
0
```

Full pytest run (`tests/` with no path filter) was attempted twice and stalled past 5 minutes without flushing output under `-q`; the targeted gate above is the verification of record. Suspect cause is unrelated Notion/network tests in the global suite, not the hotfix diff.

---

## Bottom line

Ship the four commits. Land the one test in §5 either as a 5th commit on this branch or as a fast-follow PR. Defer §3b cleanup until Day 4 calibration is being wired up. The §3c SQLite version note can ride in the next docs update.
