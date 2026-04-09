# Phase 1 Verification — Move 0 Prep + Liveness Prep

**Date:** 2026-04-08T05:56:25Z
**Branch:** prep/red-team-hybrid-prep
**Phase:** 01-move-0-prep-liveness-prep
**Reviewer:** Claude (autonomous Wave C synthesis per CONTEXT.md D-15)
**Audit evidence:** `artifacts/sub-01/rubric_gate_results.txt`
**Phase HEAD at audit:** `e53a3cc`
**Phase result:** **BLOCK** (Hard Gate 2 FAIL — pre-existing DB truncation incident, see §1 and §9)

---

## 1. Hard rubric gates (5 PASS / 1 FAIL / 1 DEFERRED)

Per CONTEXT.md `<rubric>` section. Any failure blocks the phase and postpones the 2026-04-18 Step 4B regret check per LIV-03.

| # | Gate | Command | Result | Status |
|---|------|---------|--------|--------|
| 1 | Protected paths clean | `bash scripts/red-team-hybrid/check_protected_paths.sh` | rc=0; "OK: main...HEAD changes do not touch protected paths" | **PASS** |
| 2 | Freshness all 4 collectors FRESH | `python scripts/red-team-hybrid/freshness_watchdog.py --json` | rc=1; status=FAIL; 4 operational collectors (arxiv, hacker_news, news_api, rss_feeds) report `MISSING — no signals in DB` | **FAIL** (pre-existing DB truncation, see §9) |
| 3 | R20 row in risk register | `grep "^\| \*\*R20\*\*" 10-risk-register.md` | 2 matches (one per table); severity 5×5=25 Showstopper string present; "MITIGATING (interim) / Pending permanent (Phase 2)" string present | **PASS** |
| 4 | 14-step4b-preconditions.md complete | `test -f` + `grep "^## "` | exists; 8 sections; LIV-03 referenced 5x; GOV-03 referenced 3x | **PASS** |
| 5 | Keep-alive installer + ≥2 runs | `test -f` + `ls artifacts/keepalive/*.json` | installer present (`install_keepalive_task.ps1`, 159 lines, idempotent); `artifacts/keepalive/.gitkeep` present; **0 JSON evidence files**; `KEEPALIVE-INSTALL-DEFERRED.md` operator runbook present | **DEFERRED** (operator must install from `C:\dev\Harmonic` not from worktree) |
| 6 | Framing correction callout | `grep "Framing Correction (2026-04-08)"` | 1 match; "complementary, not substitutive" language present | **PASS** |
| 7 | 9% withdrawal complete | `grep "Withdrawn 2026-04-08 (GOV-01)"` + §11 rewritten | §11 new heading present in 06-tier-2-recall-eval.md; 05 has 1 caveat; 06 has 2 caveats; `artifacts/gov-01/grep_9pct_sweep.txt` audit artifact present | **PASS** |

**Hard gate result: 5 of 7 PASS, 1 FAIL, 1 DEFERRED**

### Failures
- **Hard Gate 2 (freshness)**: FAIL because the live `signals.db` is truncated to 4 test signals (suspected recurrence of the 2026-04-04 sidecar incident — see §9 and `01-08-SUMMARY.md`). The watchdog correctly reports 4 operational collectors as MISSING. **This is a pre-existing production-state bug, not caused by Phase 1's work.** The R19 mitigation watchdog (LIV-02, shipped pre-Phase-1 in commit `4efe8cf`) is doing its job — it correctly detected the missing data.

### Deferred
- **Hard Gate 5 (keep-alive)**: installer ships, but per the `KEEPALIVE-INSTALL-DEFERRED.md` runbook the operator must run `Register-ScheduledTask` from the canonical repo path `C:\dev\Harmonic` (not from a worktree) to avoid baking an ephemeral path into Windows Task Scheduler. The two successful runs cited in the rubric will accumulate to `artifacts/keepalive/YYYY-MM-DD.json` after install. Phase 1 ships everything required; the operator action is the only missing step.

---

## 2. Soft rubric gates (6 of 6 PASS)

Per CONTEXT.md. Failures are documented but do not block the phase per D-03.

| # | Gate | Command | Actual | Target | Status |
|---|------|---------|--------|--------|--------|
| 8 | Track B ≥15 rows | row count (excluding `#` comments and header) | 30 | 15 (target 30) | **PASS** at target |
| 9 | Founder watchlist ≥30 rows | row count | 44 (all `claude_NNN`) | 30 (target 50) | **PASS** at 88% of target |
| 10 | Holdout split committed | `ls data/shadow/holdout_split/*.csv` | `episodes_v1.csv` (574 lines, 568 episode rows + header + comments) | exists | **PASS** |
| 11 | Track D design exists with 5 answers | `grep "^## [1-6]\."` + TBD count | 6 sections (5 answers + §6 implementation gating); 0 TBDs | 5/5 + 0 TBD | **PASS** (exceeds target with §6) |
| 12 | STATE.md day-by-day gates | `grep "Phase 1"` | 4 mentions in current STATE.md (will reach ≥7 after this plan's Task 3 update) | ≥3 entries | **PASS** |
| 13 | Phase 1→2 handoff inputs (D-31) | per-input check | freshness_watchdog runnable (script + interface present, runtime fails gate 2 due to underlying DB truncation); track_b_episodes.csv present; founder_watchlist.csv present; 14-step4b-preconditions.md present | 4/4 | **PASS** (contracts present; runtime caveat for input #1) |

**Soft gate result: 6 of 6 PASS**

### Soft gate caveats
- **Soft Gate 13 input #1 (freshness JSON)**: the script exists with the right interface and emits valid JSON to stdout, but the rc=1 exit reflects the same DB-truncation incident as Hard Gate 2. Phase 2 day 1 will be able to consume this input as soon as the live DB is restored from `signals.db.pre-step4b-promotion-20260404`.
- **Soft Gate 9 founder watchlist**: 44 rows vs target 50. Per CONTEXT.md D-09, the analyst extends with 10-20 names from their external network over the 11-day window — that closes the gap to 54-64 total without further Claude action.

---

## 3. SUB-01 Move 0 Charter Deliverables Matrix (D1-D12)

Per `docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md` §3. The 80% rule applies — each deliverable must be at the 80%+ level. Verified file existence + smoke check that each is substantive.

| # | Charter Deliverable | Location | Phase 1 Action | Status |
|---|---------------------|----------|----------------|--------|
| D1 | Strategy charter (renamed) | `00-strategy.md` | GOV-04 added Framing Correction callout (plan 01-02, commit `9c52f24`) | **PASS** |
| D2 | Move 0 charter | `01-move-0-charter.md` | unchanged, exists | **PASS** |
| D3 | Bounded-context map | `02-bounded-context-map.md` | unchanged, exists (80% rule applies — TBC tables acknowledged) | **PASS** |
| D4 | Dead-letter contract | `03-dead-letter-contract.md` | unchanged, exists | **PASS** |
| D5 | LLM failure mode decision | `04-llm-failure-mode.md` | unchanged, exists | **PASS** |
| D6 | Hold-out cohort split design | `05-holdout-cohort-design.md` | GOV-01 caveat at line 44 (plan 01-03, commit `458d95e`) | **PASS** |
| D7 | Tier-2 recall eval design | `06-tier-2-recall-eval.md` | GOV-01 caveat at line 74 + §11 REWRITTEN (plan 01-03) | **PASS** |
| D8 | Collector audit catalog | `07-collector-audit.md` | unchanged, exists | **PASS** |
| D9 | Track B labelling sprint plan | `08-track-b-labelling.md` | doc unchanged; REC-01 shipped seeded CSV (plan 01-08, 30 candidates) | **PASS** |
| D10 | Track E founder watchlist | `09-track-e-watchlist.md` + CSV | doc unchanged; REC-04 shipped seeded CSV (plan 01-09, 44 founders, 0 LinkedIn) | **PASS** |
| D11 | Risk register snapshot | `10-risk-register.md` | LIV-11/GOV-02 added R20 row (plan 01-01, commit `07e9646`) | **PASS** |
| D12 | Protected-paths verifier | `scripts/red-team-hybrid/check_protected_paths.sh` | unchanged, runs clean (Hard Gate 1 PASS) | **PASS** |

**Charter deliverable result: 12 of 12 PASS** (all at 80%+ level; SUB-01 satisfied)

### New Phase 1 deliverables (beyond charter D1-D12)

| Item | Location | Plan | Status |
|------|----------|------|--------|
| Freshness watchdog | `scripts/red-team-hybrid/freshness_watchdog.py` | shipped pre-Phase-1 in commit `4efe8cf` (LIV-02) | **PASS** |
| Step 4B preconditions contract | `14-step4b-preconditions.md` | 01-04 (LIV-03 + GOV-03), commit `a014bd2` | **PASS** |
| Track D design doc | `13-track-d-design.md` | 01-07 (REC-03), commit `81a258c` | **PASS** |
| Track B candidate seed | `data/shadow/track_b_episodes.csv` | 01-08 (REC-01), commit `dad166e` | **PASS** |
| Track E founder seed | `scripts/data/founder_watchlist_manual_seed.csv` + `data/shadow/founder_watchlist.csv` | 01-09 (REC-04), commit `85f7086` | **PASS** |
| Track C hold-out split | `data/shadow/holdout_split/episodes_v1.csv` | 01-06 (REC-02), commit `2f30e44` | **PASS** |
| Reputation stub | `data/shadow/founder_reputation_stub.csv` | 01-09 (REC-04 / D-10 V2-05 stub) | **PASS** |
| Keep-alive installer | `scripts/red-team-hybrid/install_keepalive_task.ps1` + `artifacts/keepalive/` | 01-05 (D-22 R19 root cause fix), commit `878bea6` | **DEFERRED** (operator action) |
| CI workflow `-v` flag fix | `.github/workflows/discovery-pipeline.yml` | 01-11 (R19 CI lane), commit `ca2ae8a` | **PASS** |

### Schema migration notes (Phase 2 awareness)

- **`data/shadow/track_b_episodes.csv`** schema migrated from Phase 0 episode-level (`episode_id,canonical_key,episode_start,...`) to D-05 per-signal-candidate (`signal_id,source_api,canonical_key,...`). Migration is documented in the CSV header comment block. Phase 2 episode-level rollup goes to a separate file.
- **`data/shadow/founder_watchlist.csv`** populator schema unchanged. Claude-extracted rows are tagged via `founder_id=claude_NNN` prefix; analyst rows use `founder_id=manual_NNN`. Both have `source=manual_seed` from the populator.
- **`.gitignore`** added explicit exceptions for `data/shadow/track_b_episodes.csv`, `founder_watchlist.csv`, `founder_reputation_stub.csv`, and `holdout_split/*.csv` to allow Phase 1 versioned deliverables under the otherwise-ignored `data/shadow/*.csv` rule.

---

## 4. REQ Traceability (11 of 11 traced)

All Phase 1 REQs traced to a specific commit + file.

| REQ | Description | Plan | Commit | File(s) | Status |
|-----|-------------|------|--------|---------|--------|
| LIV-03 | Freshness precondition for Step 4B regret check | 01-04 | `a014bd2` | `14-step4b-preconditions.md` | **PASS** |
| LIV-11 | R20 (Analyst abandonment) in risk register | 01-01 | `07e9646` (and `ed88e6a` for the actual R20 edit) | `10-risk-register.md` | **PASS** |
| GOV-01 | Withdraw 9% precision claim | 01-03 | `458d95e` | `05-holdout-cohort-design.md`, `06-tier-2-recall-eval.md` lines 44, 74, §11 | **PASS** |
| GOV-02 | R20 governance traceability | 01-01 | `07e9646` | `10-risk-register.md` (same edit as LIV-11) | **PASS** |
| GOV-03 | Freshness precondition contract for ALL governance gates | 01-04 | `a014bd2` | `14-step4b-preconditions.md` (co-located with LIV-03 per D-24) | **PASS** |
| GOV-04 | Framing correction in 00-strategy.md | 01-02 | `9c52f24` | `00-strategy.md` | **PASS** |
| SUB-01 | Move 0 charter deliverables verification | 01-10 (this plan) | (this commit) | this VERIFICATION.md | **PASS** |
| REC-01 | Track B 30-episode seed (DB-mined) | 01-08 | `dad166e` | `mine_track_b_candidates.py`, `track_b_episodes.csv` | **PASS** |
| REC-02 | Track C hold-out cohort split | 01-06 | `2f30e44` | `build_holdout_split.py`, `holdout_split/episodes_v1.csv` | **PASS** |
| REC-03 | Track D design only | 01-07 | `81a258c` | `13-track-d-design.md` | **PASS** |
| REC-04 | Track E founder watchlist seed | 01-09 | `85f7086` | `extract_founder_candidates.py`, `founder_watchlist.csv`, `founder_reputation_stub.csv` | **PASS** |

**REQ traceability result: 11 of 11 traced**

Note: LIV-01 (R19 manual restart) and LIV-02 (freshness watchdog script) were already shipped in commit `4efe8cf` before Phase 1 planning began. Those REQs are not in Phase 1's scope but are referenced as prior context. Plan 01-11's CI workflow `-v` flag fix (R19 CI lane) is a same-failure-mode-different-lane fix not tied to a specific REQ ID; it ships under Phase 1's general LIV-* umbrella.

---

## 5. Phase 1 → Phase 2 handoff inputs (D-31)

Phase 2 (Move 0.5 Liveness Restoration) needs these structured inputs from Phase 1:

| # | Input | Path | Schema | Status |
|---|-------|------|--------|--------|
| 1 | Freshness JSON | `python scripts/red-team-hybrid/freshness_watchdog.py --json` | `{checked_at, threshold_hours, exit_code, status, collectors[], failures[]}` | **PASS (contract)** / **FAIL (runtime)** — script exists with valid JSON output schema; runtime exit_code=1 because the underlying signals.db is truncated. Restoring the DB from backup makes this PASS. |
| 2 | Track B label progress | `data/shadow/track_b_episodes.csv` | D-05 per-signal schema (signal_id, source_api, canonical_key, company_name, confidence_from_classifier, thesis_category, claude_pre_label, pre_label_rationale, analyst_label, labeled_at, labeler_id) | **PASS** |
| 3 | Founder watchlist count | `data/shadow/founder_watchlist.csv` | populator 7-column schema (founder_id, full_name, github_username, linkedin_url, source, associated_company_id, added_at) | **PASS** |
| 4 | Governance gate contract | `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` | docs §1-8 covering preconditions, gate contract, escalation tree, R19/R20 back-links | **PASS** |

All four inputs verified present with expected schemas. Phase 2 day 1 can consume inputs 2-4 immediately; input 1 unblocks as soon as the DB truncation is remediated.

---

## 6. Threat-model verification (cross-plan)

Per CONTEXT.md `<security_enforcement>` and the threat models embedded in each plan:

- **Protected paths:** zero touches to `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, `storage/migrations/` across all 10 prior plan commits. Verified via Hard Gate 1 (rc=0).
- **Read-only DB access:** every Phase 1 script that touches `signals.db` uses `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`. Verified by inspecting `mine_track_b_candidates.py`, `extract_founder_candidates.py`, and `build_holdout_split.py`.
- **No new secrets:** no new env vars, no new API keys, no `.env` modifications.
- **Supply chain:** new scripts are stdlib-only Python + one PowerShell installer using only Windows built-in cmdlets (`New-ScheduledTask*`, `Register-ScheduledTask`). No `pip install`, no `Install-Module`.
- **LinkedIn hard constraint (D-11):** zero LinkedIn URLs in `data/shadow/founder_watchlist.csv`, `scripts/data/founder_watchlist_manual_seed.csv`, `data/shadow/founder_reputation_stub.csv`, or `artifacts/rec-04/founder_candidates_raw.csv`. Verified by `grep -E ",https?://[^,]*linkedin"` returning zero matches across all four files.
- **Populator immutability (D-10):** `scripts/build_founder_watchlist.py` was NOT modified. The reputation stub ships as a parallel file (`data/shadow/founder_reputation_stub.csv`), not as a column edit to the populator's output.

---

## 7. Overall pass condition

Per CONTEXT.md rubric: **all 7 hard gates must be PASS**. Soft gate shortfalls are tolerated if documented in this VERIFICATION.md with explicit reason and carry-over to Phase 2.

**Phase 1 result: BLOCK** (Hard Gate 2 FAIL)

### Why BLOCK
- Hard Gate 2 (freshness): FAIL because `signals.db` is truncated to 4 test signals. The R19 mitigation watchdog correctly detects this and exits rc=1 with status FAIL.
- Hard Gate 5 (keep-alive runs): DEFERRED because the operator action (`Register-ScheduledTask` from the canonical repo path) has not yet been performed. The installer ships and is correct.

### Phase 1 work itself is complete
- All 11 REQs traced to specific commits
- All 12 charter deliverables (D1-D12) at 80%+ level
- All other 5 hard gates PASS
- All 6 soft gates PASS
- Phase 1 → Phase 2 handoff schemas all present
- Zero protected-path violations
- D-11 LinkedIn hard constraint enforced (0 URLs in any file)
- D-10 populator immutability enforced

### Remediation steps (escalated to user; orchestrator cannot self-heal these)

1. **Restore live `signals.db` from backup** (resolves Hard Gate 2):
   - Stop any Python processes that might be holding the WAL/SHM sidecars open
   - Back up the current truncated DB: `mv signals.db signals.db.truncated-20260408`
   - Copy the most recent useable backup: `cp signals.db.pre-step4b-promotion-20260404 signals.db`
   - Handle the WAL/SHM sidecars per the PR #131 hardening retro
   - Re-run `python scripts/red-team-hybrid/freshness_watchdog.py --json` and verify rc=0
   - Re-evaluate Hard Gate 2 → expected PASS

2. **Run the keep-alive installer from canonical repo path** (resolves Hard Gate 5):
   - Per `KEEPALIVE-INSTALL-DEFERRED.md` runbook
   - Confirm 2 successful runs accumulate JSON evidence in `artifacts/keepalive/`
   - Re-evaluate Hard Gate 5 → expected PASS

3. **After both remediations, re-run plan 01-10 (this plan) Task 1** to regenerate `artifacts/sub-01/rubric_gate_results.txt` and update this VERIFICATION.md from BLOCK to PASS.

### Critical context for the 2026-04-18 Step 4B regret reviewer

The Phase 1 substrate work is complete and correct. The BLOCK is entirely caused by a pre-existing production-state bug (signals.db truncation) that Phase 1 work *exposed* via the LIV-02 freshness watchdog. This is the watchdog doing its job. The R19 root-cause work (keep-alive installer in plan 01-05) ships the structural fix; the operator just needs to install it and restore the DB once.

The 2026-04-18 regret check should be **postponed** per LIV-03 until Hard Gate 2 PASSes. Postponement is not a regression — it is the contract.

---

## 8. Audit appendix

- Full rubric gate command output: `artifacts/sub-01/rubric_gate_results.txt`
- GOV-01 wider-grep sweep: `artifacts/gov-01/grep_9pct_sweep.txt`
- Founder candidates buffer: `artifacts/rec-04/founder_candidates_raw.csv`
- Per-plan SUMMARY files: `.planning/phases/01-move-0-prep-liveness-prep/01-0[1-9,11]-SUMMARY.md`
- Keep-alive operator runbook: `.planning/phases/01-move-0-prep-liveness-prep/KEEPALIVE-INSTALL-DEFERRED.md`
- DB truncation incident report: `.planning/phases/01-move-0-prep-liveness-prep/01-08-SUMMARY.md` §"CRITICAL DEVIATION"

---

## 9. Production state incident: signals.db truncation (escalation)

**Discovery:** During plan 01-08 execution (2026-04-08), the orchestrator discovered that the live `signals.db` had been re-truncated to 4 test signals + 0 thesis_classifications, despite the 2026-04-04 hardening (PR #131) intended to prevent recurrence.

**Evidence captured in `artifacts/sub-01/rubric_gate_results.txt` and `01-08-SUMMARY.md`:**
- Live `signals.db`: 4 signals, 0 thesis_classifications, 1.4MB
- Backup `signals.db.pre-step4b-promotion-20260404`: 612 signals, 2593 thesis_classifications, 9.7MB

**Impact on Phase 1:**
- Plans 01-06, 01-08, 01-09 all needed to read signals.db; ran against the backup instead. CSV outputs are deterministic from the backup state.
- Hard Gate 2 (freshness watchdog) correctly detects the truncation as a FAIL because the watchdog reads the live DB.
- Hard Gate 5 (keep-alive) is unaffected — the installer ships independently.

**Recommended structural fix beyond DB restoration:**
- The R19 keep-alive (LIV-02 watchdog + plan 01-05 Task Scheduler installer) detects "no recent collection" but does NOT detect "DB was truncated". MEMORY.md hardening retro from 2026-04-04 mentions "Signal-count watermark guard" — this should be added as a Phase 2 day 1 task to plan 01-10 follow-up. The watchdog should fail-fast if `signals.db` has fewer than `MIN_SIGNAL_COUNT` rows (e.g., 100), regardless of recency.

---

*Phase: 01-move-0-prep-liveness-prep*
*Status: BLOCK (Hard Gate 2 FAIL — pre-existing DB truncation, remediation steps documented in §7)*
*Generated by plan 01-10 Wave C synthesis (SUB-01)*
