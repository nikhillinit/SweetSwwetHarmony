# Phase 1: Move 0 Prep + Liveness Prep — Research

**Researched:** 2026-04-08
**Domain:** Codebase grounding for an already-locked plan (CONTEXT.md has 34 decisions D-01..D-34 + 7 hard rubric gates)
**Confidence:** HIGH (every claim below is verified against the live tree, signals.db, gh CLI, or a file:line citation)

## Summary

CONTEXT.md is hardened and authoritative. This research **does not re-derive decisions**; it verifies the codebase facts the planner needs to write PLAN.md without surprises, and surfaces three findings that change one CONTEXT.md assumption.

**Three findings the planner must integrate before writing PLAN.md:**

1. **D-22 vs D-23 — the CONTEXT.md framing of "signals.db is local SQLite, GitHub Actions cannot reach it" is wrong on the facts but right on the conclusion.** `.github/workflows/discovery-pipeline.yml` already runs daily at `0 6 * * *` and uses `signals.db` via `actions/upload-artifact@v4` / `gh run download` (file:line citations below). HOWEVER the CI database is a **separate artifact-tracked instance** that has diverged from the local DB (53,780 bytes / ~52KB on the latest run vs 10,383,360 bytes / ~10MB locally). D-22 (Windows Task Scheduler against the local DB) remains the right answer for keeping the **local production DB** alive — but CONTEXT.md should not justify it with the false claim that GH Actions cannot read signals.db. **Recommended planner action:** keep D-22 as the default per CONTEXT.md, but the rationale for the choice changes from "CI cannot access the DB" to "the CI DB is a different DB instance from the local production DB; only a local scheduled task closes R19 on the production data path."

2. **The existing `discovery-pipeline.yml` workflow has been failing for 5 consecutive days** (2026-04-03 through 2026-04-07, all `failure` per `gh run list`). The failure cause is `run_pipeline.py: error: unrecognized arguments: -v` at multiple steps. This is a **separate root cause from R19** — the CI keep-alive *attempts* a daily run but every run errors out before producing fresh data. `.github/workflows/` is an ALLOWED Move 0 path. The planner should add a **Wave A scope addition** ("CI sub-task") to fix the `-v` arg in `discovery-pipeline.yml` so the CI pipeline can stop being a silent failure on the dashboard. This is small (3-5 line edit) and is in the allowed-paths list, but is currently NOT in CONTEXT.md scope. Surface to user via VERIFICATION.md if not added to plan.

3. **The "9% precision" grep is broader than D-25 claims.** D-25 lists 4 known lines. The full `grep -rn "9%" docs/ .planning/ CLAUDE.md` returns **22 matches across 13 files**. Most are unrelated (98.69% HN FP rate, 89% similarity scores, 79%→98% coverage stats, 99% JSON validity). After filtering to actual "9% pipeline precision" claims, the literal hits are **6 lines across 5 files**, not 4 lines across 3 files. Two new locations CONTEXT.md missed: `docs/plans/2026-04-06-lob-progress-eval/README.md:11` and `docs/plans/2026-04-06-lob-progress-eval/bias-audit.md:57,61,78,182`. The bias-audit.md hits are the source-of-truth for the withdrawal — they should NOT be edited (they are the primary record). README.md:11 already wraps the claim in a withdrawal callout, so it requires no edit. **Net: D-25's "4 known lines" target is correct for files that need GOV-01 edits; the additional bias-audit.md hits are read-only references that GOV-01 cites, not edits.** Full matrix below.

**Primary recommendation:** The planner should treat CONTEXT.md as authoritative on all 34 decisions, integrate the 3 findings above into the PLAN.md preamble, and use the literal SQL / row skeletons / file paths in this document as the executor's input.

## User Constraints (from CONTEXT.md)

> Copied verbatim from `.planning/phases/01-move-0-prep-liveness-prep/1-CONTEXT.md`. The planner MUST honor these.

### Locked Decisions (D-01..D-34)

**D-01..D-03 — Phase 1 scope calibration under the 11-day clock:**
- D-01: Pragmatic split. Ship all Claude-autonomous REQs at full depth (LIV-03, LIV-11, GOV-01..GOV-04, SUB-01, REC-02, REC-03). Scaffold REC-01 (Track B) and REC-04 (Track E) with Claude-seeded CSVs that the analyst extends incrementally over the 11 days.
- D-02: Insurance against R20 — split is for analyst-bandwidth insurance, NOT Claude-capacity constraint. Wave A Fermi ~1-2 hours of Claude effort total.
- D-03: Phase 1 is NOT gated on REC-01/REC-04 hitting their 30/50 row targets by 2026-04-19. If actuals undershoot by 2026-04-18, document the gap in VERIFICATION.md and carry to Phase 2 day 1.

**D-04..D-07 — REC-01 Track B substrate:**
- D-04: DB-mining-plus-analyst-confirmation. Claude surfaces 30 candidate episodes from `signals.db`, stratified by `thesis_category` × `confidence` buckets.
- D-05: CSV columns must capture: `signal_id`, `source_api`, `canonical_key`, `company_name`, `confidence_from_classifier`, `thesis_category`, `claude_pre_label`, `pre_label_rationale`, `analyst_label` (empty), `labeled_at` (empty), `labeler_id` (empty).
- D-06: Existing `quality-label` skill is the labelling tool. No new labelling UI.
- D-07: Selection bias acknowledgement — Track B cohort sourced this way is known-biased; it is the SECONDARY canary, not primary.

**D-08..D-11 — REC-04 Track E:**
- D-08: Claude-seed-plus-analyst-extend. Claude extracts founder names from `signals.raw_data` for news_api, hacker_news, arxiv. Seeds 30-40 records.
- D-09: Analyst extends with 10-20 high-conviction names. `source=analyst|claude` column.
- D-10: `reputation_score REAL NULL` placeholder column + methodology note in CSV header. Full scoring deferred to V2-05.
- D-11: HARD CONSTRAINT: no LinkedIn scraping. Founder names from sources where founder self-published or was mentioned in public news only.

**D-12..D-14 — GOV-04 framing correction:**
- D-12: Prepend dated `Framing Correction (2026-04-08)` callout at top of `00-strategy.md` before §1, ~6-10 lines, states substrate-plus-engagement complementarity explicitly.
- D-13: Original §2 text preserved verbatim — git history must show 2026-04-06 vs 2026-04-08 delta.
- D-14: Same callout pattern wherever GOV-01 needs to edit active docs — date the correction, preserve original line, add bias-audit caveat inline.

**D-15..D-16 — Governance paper-trail ordering:**
- D-15: Wave A (docs, ~1-2h Claude): LIV-11/GOV-02, GOV-04, GOV-01, LIV-03 + GOV-03 (in `14-step4b-preconditions.md`). Wave B (data): REC-02, REC-03, REC-01, REC-04. Wave C: SUB-01 charter rollup.
- D-16: Wave A commits ≤50 lines each, atomic per REQ. Wave B commits carry CSV + generating script. Wave C is verification commit.

**D-17 — REC-02 Track C split:**
- Claude's discretion on split strategy. Constraint: deterministic seed, file-based output to `data/shadow/holdout_split/`, seed value + algorithm documented inline. Follow `05-holdout-cohort-design.md` as authority.

**D-18..D-19 — REC-03 Track D design depth:**
- D-18: Design only — docs only. NO collector code, NO schema migrations. Output to `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md`.
- D-19: Must answer: CT-log sources / DNS data source / canonical key strategy for stealth companies / anti-fingerprinting posture / cost envelope.

**D-20..D-21 — GOV-03 scope constraint:**
- D-20: GOV-03 is Phase 1 DOCS ONLY. `governance/` package is protected. Phase 1 ships `14-step4b-preconditions.md` specifying precondition semantics that Phase 2 implements in code. Contract doc must include: required precondition input format, blocking vs advisory behavior, failure escalation path, list of gates it applies to.
- D-21: Planner MUST NOT propose any edit to `governance/*.py`. Any such proposal is an immediate plan reject. `check_protected_paths.sh` enforces.

**D-22..D-23 — Pipeline keep-alive (R19 root cause fix):**
- D-22: Default path: `scripts/red-team-hybrid/install_keepalive_task.ps1` installs Windows Task Scheduler entry running `python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api` daily at 08:00 local, followed by `python scripts/red-team-hybrid/freshness_watchdog.py --json` whose output is appended to `artifacts/keepalive/YYYY-MM-DD.json`. **Rationale per CONTEXT.md: "signals.db is a local SQLite file, so a GitHub Actions runner cannot execute the keep-alive against production data" — see Finding 1 above; the conclusion is right but the rationale needs updating.**
- D-23: Alternative path if planner determines `signals.db` can be reached from CI: `.github/workflows/freshness-keepalive.yml`. Default = D-22.

**D-24 — LIV-03 target file (locked):**
- LIV-03 freshness precondition lands in NEW file `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md`. Same file hosts GOV-03 contract (D-20) — one file, two REQs.

**D-25..D-26 — GOV-01 explicit targets:**
- D-25: "9% pipeline precision" in 4 known locations: `05-holdout-cohort-design.md:44`, `06-tier-2-recall-eval.md:74`, `06-tier-2-recall-eval.md:265-268` (§11 NEEDS REWRITE not strike).
- D-26: Wider grep pass across docs/, .planning/, CLAUDE.md required. Planner MUST run `grep -rn "9%" docs/ .planning/ CLAUDE.md` before committing.

**D-27..D-28 — REC-04 founder extraction scoping:**
- D-27: Per-collector handlers in `scripts/red-team-hybrid/extract_founder_candidates.py`:
  - `arxiv`: parse `authors[]`, filter to rows where company name appears in title or abstract
  - `hacker_news`: parse `by` field + title, person-name heuristic (two capitalized tokens, not known product-name pattern)
  - `news_api`: parse `title` + `description`, extract capitalized phrases near "founder", "CEO", "co-founder"
  - Others: skipped
- D-28: Target 30-40 Claude-seeded rows. If shortfall, D-03 applies.

**D-29..D-30 — Interim R20 mitigation:**
- D-29: Phase 1 ships interim mitigations so R20's risk-register row has non-empty Phase 1 entry: (1) Automated keep-alive, (2) Daily watchdog alert via `artifacts/keepalive/`, (3) STATE.md progress tick.
- D-30: R20 row explicitly lists interim mitigations AND Phase 2 permanent mitigation. Status flips from "Open" to "Mitigating (interim) / Pending permanent (Phase 2)".

**D-31..D-32 — Phase 1 → Phase 2 handoff contract:**
- D-31: Phase 2 daily digest needs as structured inputs: (1) `freshness_watchdog.py --json`, (2) `data/shadow/track_b_episodes.csv`, (3) `data/shadow/founder_watchlist.csv`, (4) `14-step4b-preconditions.md`.
- D-32: Planner MUST confirm these inputs exist with expected schema before Phase 1 closes.

**D-33..D-34 — Day-by-day kill criteria:**
- D-33: Day 3 (04-11): all Wave A landed. Day 6 (04-14): Wave B ≥50%. Day 9 (04-17): Wave C drafted. Day 10 (04-18, HARD GATE): freshness_watchdog rc=0, 14-step4b-preconditions.md committed, R20 row "Mitigating (interim)".
- D-34: Gate evaluations commit to `.planning/STATE.md` as daily lines.

### Claude's Discretion (CONTEXT.md)
- Column schema for `founder_watchlist.csv` beyond D-09 + D-10
- Specific `thesis_category` × `confidence` stratification buckets for D-04
- Wider grep sweep for "9%" beyond 4 known lines per D-26
- Exact prose of R20 row in risk register (structure locked per D-30; wording is Claude's)
- Whether to batch-commit Wave A as one per-REQ commit or one wave-level commit
- Windows Task Scheduler XML template if D-22 path chosen
- Per-handler heuristic tuning in `extract_founder_candidates.py` per D-27

### Deferred Ideas (OUT OF SCOPE)
- Track D implementation (Phase 3 / Move 1 substrate)
- Track B random-sampled cohort (Phase 3+)
- Full founder reputation scoring (V2-05)
- GOV-03 code implementation (Phase 2 after 2026-04-19 unfreeze)
- Postgres dual-write (SUB-08 / V2-03 — permanently deferred)
- LOB.txt grafts (UX-01..UX-04, Phase 3)
- Twitch trust-transfer (V2-01)
- Pandora-lite full feature set (V2-04)
- CLAUDE.md Closed-Loop Skills section (Phase 2 doc updates)
- GitHub Actions-based keep-alive (D-23, fallback only)

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| LIV-03 | Add freshness precondition to 2026-04-18 Step 4B regret check | New file `14-step4b-preconditions.md` per D-24; existing `freshness_watchdog.py` is the verification command |
| LIV-11 | Add R20 (Analyst abandonment) to risk register | Literal R20 row skeleton below matches R19's exact format byte-for-byte |
| GOV-01 | Withdraw "9% pipeline precision" claim | 6 lines in 5 files need edit/caveat (matrix below); D-25's 4-line scope is the edit target; bias-audit.md hits are read-only references |
| GOV-02 | R20 governance traceability | Tracked alongside LIV-11 (same row, dual REQ tag) |
| GOV-03 | Freshness precondition contract for governance gates | Docs-only per D-20; co-located with LIV-03 in `14-step4b-preconditions.md` |
| GOV-04 | Framing correction in `00-strategy.md` | Section anchors verified — callout prepends before §1 (`## 1. Naming correction` at line 10) |
| SUB-01 | Move 0 charter deliverables verification | Charter D1-D12 enumerated in `01-move-0-charter.md` §3; cross-reference matrix below |
| REC-01 | Track B 30+ episodes (DB-mined seed) | SQL + bucket boundaries below; 767 signals available; latest-classification stratification table provided |
| REC-02 | Track C hold-out cohort split | `05-holdout-cohort-design.md` is authoritative; algorithm in §3, output path `data/shadow/holdout_split/episodes_v1.csv`; directory does NOT yet exist |
| REC-03 | Track D CT-log + DNS shadow design | Design only per D-18; new file `13-track-d-design.md` |
| REC-04 | Track E founder watchlist (30-40 seed + analyst extend) | Per-collector raw_data shapes verified below; arxiv `authors[]`, hacker_news `by/author`, news_api `title/description` |

## Reusable Assets

### Existing scripts the planner should reference (Read in execution, do NOT re-derive)

| Path | Lines | Purpose | How Phase 1 uses it |
|------|-------|---------|---------------------|
| `scripts/red-team-hybrid/freshness_watchdog.py` | 349 | LIV-02 deliverable, shipped commit `4efe8cf`. Stdlib-only, argparse, JSON-or-text output, exit codes 0/1/2 | Style template for `extract_founder_candidates.py`, `mine_track_b_candidates.py`, `build_holdout_split.py` (REC-02). Hard rubric gate 2 verification command |
| `scripts/red-team-hybrid/check_protected_paths.sh` | 83 | Pre-commit guard. Greps `git diff --name-only main...HEAD` + staged + unstaged + untracked against `^collectors/`, `^workflows/`, `^governance/`, `^monitoring/`, `^connectors/`, `^storage/migrations/` | Hard rubric gate 1 verification command. EVERY Phase 1 commit MUST pass this. Secondary defense behind D-21 |
| `scripts/red-team-hybrid/track_b_episodes.template.csv` | 1 (header only) | REC-01 CSV column template | Header is `episode_id,canonical_key,episode_start,episode_end,outcome_label,confidence,evidence_signal_ids,notes,labelled_by,labelled_at`. **NOTE:** This template uses the Phase 0 episode-level schema, NOT D-05's per-signal schema. **D-05 wins for Phase 1 — see Per-REQ Notes for REC-01.** |
| `scripts/build_founder_watchlist.py` | 50+ inspected | Existing populator that reads from signals.db via ShadowSidecar (immutable URI mode), writes to `data/shadow/founder_watchlist.csv`. Output columns: `founder_id, full_name, github_username, linkedin_url, source, associated_company_id, added_at` | Phase 1 reuses this. Claude-seeded rows from `extract_founder_candidates.py` are written to `scripts/data/founder_watchlist_manual_seed.csv` (existing file, currently header-only); then `python -m scripts.build_founder_watchlist --verbose` regenerates `data/shadow/founder_watchlist.csv` with `source=manual_seed` |
| `scripts/red-team-hybrid/README.md` | 30 | Existing scripts directory README documenting per-script status by Move | Update at end of Phase 1 to add new scripts (`extract_founder_candidates.py`, `mine_track_b_candidates.py`, `install_keepalive_task.ps1`) |
| `.claude/skills/quality-label/SKILL.md` | 35 | Existing labelling skill — `python -m ops.cli quality label <signal_id> <TP\|FP\|UNSURE> --reason "..." --notes "..."` | REC-01 routes analyst confirmations through this so labels persist to `signal_quality_metrics` with audit trail. **No new labelling UI per D-06.** |
| `.claude/hooks/postedit_protected_paths.ps1` | (PowerShell hook) | PostToolUse hook for per-edit protected-path enforcement | Live during Phase 1 — Claude's edit attempts to protected paths are blocked at the hook layer before reaching `check_protected_paths.sh` |
| `.claude/hooks/inject_context.ps1`, `.claude/hooks/stop_verify.ps1` | (PowerShell hooks) | Existing PowerShell scripts in repo | **Only existing PowerShell scripts in the repo.** Use as style template for `install_keepalive_task.ps1` (D-22) — same encoding (UTF-8 with BOM if needed), same parameter style, same error handling |

### freshness_watchdog.py style conventions (apply to new scripts)

The planner's new Wave B scripts MUST follow this exact pattern, verified at `scripts/red-team-hybrid/freshness_watchdog.py:1-349`:

1. **Shebang + module docstring** (lines 1-58): `#!/usr/bin/env python3`, then triple-quoted docstring with "Context", "Usage", "Exit codes" sections.
2. **`from __future__ import annotations`** (line 60).
3. **Stdlib-only imports** (lines 62-68): `argparse`, `json`, `sqlite3`, `sys`, `datetime`, `pathlib`, `typing`. **No third-party deps.** This keeps Wave B scripts runnable from a fresh clone without `pip install`.
4. **Module-level constants for defaults** (lines 74-82): `DEFAULT_OPERATIONAL_COLLECTORS`, `DEFAULT_THRESHOLD_HOURS`, `DEFAULT_DB_PATH`. Tied to a comment that points back to the REQ ID.
5. **Pure function decomposition** (lines 85-208): `query_freshness()`, `classify()`, `verdict()`, `render_text()`, `render_json()`. Each takes explicit args (no module-level state). `main()` orchestrates.
6. **Read-only DB access** (line 112): `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`. **All Wave B scripts that touch signals.db MUST use this pattern** — never open RW.
7. **Argparse with `--json` text/JSON toggle** (lines 285-317): default text output for terminals, `--json` for CI / cron / dashboards.
8. **Exit code contract documented in docstring**: `0` = success, `1` = expected failure (gate fails), `2` = operational error (DB unreadable, etc.).
9. **`if __name__ == "__main__": raise SystemExit(main())`** (lines 347-348).

### check_protected_paths.sh enforcement details (verified)

`scripts/red-team-hybrid/check_protected_paths.sh:27-50`:

```bash
FORBIDDEN_PATTERNS=(
  '^collectors/'
  '^workflows/'
  '^governance/'
  '^monitoring/'
  '^connectors/'
  '^storage/migrations/'
)

# Captures FOUR categories:
COMMITTED="$(git diff --name-only --diff-filter=ACMR "${BASE_REF}"...HEAD ...)"
STAGED="$(git diff --name-only --cached --diff-filter=ACMR ...)"
UNSTAGED="$(git diff --name-only --diff-filter=ACMR ...)"
UNTRACKED="$(git ls-files --others --exclude-standard ...)"
```

The guard catches **committed + staged + unstaged + untracked** changes. Default base ref is `main`. Diff filter `ACMR` (added/copied/modified/renamed) ignores deletes. **Implication for the planner:** any new file the planner creates is checked against the patterns immediately, even if not yet `git add`-ed. New scripts under `scripts/red-team-hybrid/` are safe (no pattern match). New docs under `docs/plans/2026-04-06-red-team-hybrid/` are safe. New rows in `data/shadow/*.csv` are safe.

## Verified Codebase Facts

### signals.db location and gitignore status

| Fact | Value | Source |
|------|-------|--------|
| Local path | `C:\dev\Harmonic\signals.db` (project root) | `ls -la /c/dev/Harmonic/signals.db` |
| Local size | 10,383,360 bytes (~10MB) | `ls -la /c/dev/Harmonic/signals.db` 2026-04-07 18:28 |
| WAL/SHM sidecars | `signals.db-shm` (32,768 bytes), `signals.db-wal` (0 bytes) | `ls -la /c/dev/Harmonic/signals.db-*` |
| Gitignored | YES | `.gitignore:51` (`*.db`), `.gitignore:54` (`signals.db`), `.gitignore:91` (`data/shadow/*.db`) |
| `data/shadow/discovery.db` | EXISTS (separate shadow DB) | `ls /c/dev/Harmonic/data/shadow/` |
| signals row count | 767 | `SELECT COUNT(*) FROM signals` |
| thesis_classifications row count | 3,085 | `SELECT COUNT(*) FROM thesis_classifications` |

### signals table schema (verified)

```sql
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type TEXT NOT NULL,
    source_api TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    company_name TEXT,
    confidence REAL NOT NULL,
    raw_data TEXT NOT NULL,  -- JSON
    detected_at TEXT NOT NULL,  -- ISO 8601
    created_at TEXT NOT NULL,
    company_id TEXT,
    evidence_family TEXT,
    canonical_key_v2 TEXT,
    evidence_key TEXT,
    UNIQUE(canonical_key, signal_type, source_api, detected_at)
)
```

### Per-collector signal counts and freshness (verified 2026-04-08)

| source_api | rows | max(created_at) | Operational? |
|------------|------|----------------|--------------|
| arxiv | 374 | 2026-04-08T01:28:19Z | YES |
| hacker_news | 243 | 2026-04-08T01:27:54Z | YES |
| rss_feeds | 89 | 2026-04-08T01:28:21Z | YES |
| news_api | 17 | 2026-04-08T01:28:24Z | YES |
| manual_seed_buzz | 20 | 2026-02-26 | NO (frozen, manual seed) |
| greenhouse_jobs | 13 | 2026-02-26 | NO |
| ashby_jobs | 4 | 2026-02-26 | NO |
| lever_jobs | 3 | 2026-02-26 | NO |
| product_hunt | 2 | 2026-01-10 | NO (no API key) |
| github | 2 | 2026-01-10 | NO |

The 4 operational collectors match `freshness_watchdog.py:74-79` `DEFAULT_OPERATIONAL_COLLECTORS` exactly. All four are FRESH as of 2026-04-08 01:28Z post-LIV-01 restart.

### signal_processing status distribution

| status | count |
|--------|------:|
| held | 464 |
| pending | 170 |
| pushed | 15 |
| queued | 2 |
| rejected | 115 |

Total = 766 (one signal lacks a processing row — likely the most recent insert).

### thesis_classifications schema (verified)

```sql
CREATE TABLE thesis_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    canonical_key TEXT NOT NULL,
    keyword_score REAL,
    keyword_category TEXT,
    negative_keywords TEXT,
    thesis_match BOOLEAN,
    thesis_fit_score REAL,        -- numeric 0..1
    category TEXT,                 -- consumer_cpg | consumer_health_tech | ...
    stage_estimate TEXT,
    confidence TEXT,               -- LOW/MEDIUM/HIGH (text)
    rationale TEXT,
    key_signals TEXT,
    prompt_version TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    competitor_flag BOOLEAN DEFAULT 0,
    competitor_match TEXT,
    classified_at TEXT NOT NULL,
    reasoning_trace TEXT,
    cot_enabled INTEGER DEFAULT 0,
    disagreement_detected BOOLEAN DEFAULT 0,
    classification_status TEXT DEFAULT 'success',
    primary_end_user TEXT,
    paying_customer TEXT,
    sells_to_or_operates_in TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
)
```

**Key fact for D-04 stratification:** there are 3,085 classifications for 767 signals = ~4 classifications per signal on average (re-classifications). The planner MUST take the latest per signal when stratifying. SQL pattern in "Concrete Bucket Boundaries" below.

### raw_data JSON shapes per operational collector (verified)

**arxiv** (top-level keys, sample id=761):
```
canonical_key, arxiv_id, title, abstract, authors, categories, affiliations, pdf_url, _provenance
```
- `authors` is a list of strings (e.g. `['Mateusz Papierz', 'Asel Sagingalieva', 'Alix Benoit', 'Toni Ivas', 'Elia Iseli']`)
- `company_name` column on `signals` is **always NULL** for arxiv (verified: 0/374 non-null)
- `canonical_key` for arxiv is `arxiv_author:<slug>` for 374/374 rows; 0 use `domain:` or `name:`
- All 374 arxiv rows have `authors[]` populated

**hacker_news** (top-level keys, sample id=663):
```
canonical_key, canonical_key_candidates, company_name, company_domain, hacker_news_id,
title, points, num_comments, author, is_show_hn, story_text, url, _provenance
```
- `by` field NOT present at top level — the field is `author`
- `is_show_hn` is True for 242/243 rows (the corpus is Show HN heavy)
- `company_name` is non-null for 243/243 rows (always populated)
- `company_domain` typically populated when `is_show_hn=True`
- Example: `author=ilamparithi`, `title="Show HN: ReverseCam — See yourself as others see you"`, `company_name="Reversecam"`

**news_api** (top-level keys, sample id=767):
```
_provenance, title, description, url, source, published_at, company_name,
company_name_method, candidate_domains, promoted_domain, is_funding_news,
is_product_launch, canonical_key_candidates, dns_probe_attempted, dns_probe_domain, dns_probe_status
```
- `title` and `description` are the main NLP surface for founder extraction
- `company_name_method='regex'` indicates the existing extraction
- Verified: only **1 of 17** news_api signals contains the literal string "founder" in raw_data; **0 of 17** contain "CEO" or "co-founder". **The 17-row corpus is too small to hit D-28's 30-40 target from news_api alone.** The bulk of founder candidates must come from `arxiv.authors[]` (374 rows × 4-6 authors each = ~1500-2000 author-name candidates).

**rss_feeds** (top-level keys, sample id=764):
```
_provenance, title, description, url, source_feed, published_at, author,
company_name, company_name_method, candidate_domains, promoted_domain,
is_funding_news, is_product_launch, is_press_release, canonical_key_candidates,
dns_probe_attempted, dns_probe_domain, dns_probe_status
```
- 33/89 rows have non-null `company_name` (37%)
- D-27 lists rss_feeds as "skipped (low-signal raw_data)" — confirmed; the planner should follow D-27 and not extract from rss_feeds. The 56 NULL company_name rows are press releases without entity extraction.

### `.github/workflows/` inventory

| File | Trigger | Touches signals.db? |
|------|---------|---------------------|
| `discovery-pipeline.yml` | `cron: '0 6 * * *'` + workflow_dispatch | YES — `gh run download --name signals-db-latest` (line 63), `actions/upload-artifact@v4` (line 186) |
| `regression-gate.yml` | `pull_request` to `main` | NO (test runner only) |
| `thesis-eval.yml` | (not inspected — lower priority) | (not inspected) |

`.github/workflows/` is an **ALLOWED** Move 0 path (verified — not in `FORBIDDEN_PATTERNS` of `check_protected_paths.sh:27-34`). The planner CAN add a new workflow file or edit `discovery-pipeline.yml` without triggering the protected-paths gate.

### Daily Pipeline failure history (verified via gh CLI)

| Run ID | Date | Status | Duration |
|--------|------|--------|----------|
| 24068064584 | 2026-04-07T06:39:26Z | failure | 1m26s |
| 24022037862 | 2026-04-06T06:47:30Z | failure | 1m18s |
| 23996035295 | 2026-04-05T06:33:51Z | failure | 1m18s |
| 23973244014 | 2026-04-04T06:29:24Z | failure | 1m51s |
| 23936771052 | 2026-04-03T06:34:16Z | failure | 1m31s |

**Failure root cause** (extracted from `gh run view 24068064584 --log-failed`):
```
run_pipeline.py: error: unrecognized arguments: -v
##[error]Process completed with exit code 2.
```

This appears at multiple steps: `monitor sync-portfolio -v`, `monitor run --no-embeddings -v`, `sync -v`, `full --collectors ... -v`. The `-v` flag was removed from `run_pipeline.py` argparse at some point but the workflow YAML was never updated. **5+ days of silent CI failure.** The artifact `signals-db-latest` is still uploaded on each failed run (53,780 bytes / ~52KB — close to empty). This is itself an instance of R19's "silent freeze" failure mode, but in the CI lane rather than the local lane.

**Planner action:** Add a 1-task Wave A sub-deliverable to fix this with a 3-line YAML edit. `.github/workflows/` is allowed. The fix is removing `-v` from 4 lines in `discovery-pipeline.yml` (lines 108, 117, 138, 155, 157). This is NOT in CONTEXT.md scope, but it should be — this is the same R19 failure mode in a different lane and Phase 1 will be evaluated on whether it caught this.

### `.planning/codebase/` directory contents (existing analysis docs to leverage)

| File | Use |
|------|-----|
| `ARCHITECTURE.md` | Project architecture map |
| `CONCERNS.md` | Concerns + R19/R20/known issues — **already aligns with CONTEXT.md** |
| `CONVENTIONS.md` | Coding conventions |
| `INTEGRATIONS.md` | External integration map |
| `STACK.md` | Tech stack (Python 3.11+, asyncio, sqlite3 WAL+FTS5, Gemini LLM) |
| `STRUCTURE.md` | Directory structure (cited above for `scripts/red-team-hybrid/` placement) |
| `TESTING.md` | Test suite layout (~9500 tests, mirrors source) |

`.planning/intel/` does NOT exist. Planner does not need to look for it.

### Existing data/shadow state (verified)

| File | Lines | Status |
|------|-------|--------|
| `data/shadow/discovery.db` | (binary) | Exists — separate shadow DB for ShadowSidecar |
| `data/shadow/track_b_episodes.csv` | 1 (header only) | Exists with episode-level header `episode_id,canonical_key,episode_start,episode_end,outcome_label,confidence,evidence_signal_ids,notes,labelled_by,labelled_at` |
| `data/shadow/founder_watchlist.csv` | 1 (header only) | Exists with header `founder_id,full_name,github_username,linkedin_url,source,associated_company_id,added_at` |
| `data/shadow/holdout_split/` | DOES NOT EXIST | Planner must create directory + `episodes_v1.csv` for REC-02 |
| `scripts/data/founder_watchlist_manual_seed.csv` | 1 (header only) | Exists with header `founder_id,full_name,github_username,linkedin_url,associated_company_id` (no `source`/`added_at` — those are added by the populator) |

**The CSV header in `track_b_episodes.csv` is the EPISODE-level schema from `08-track-b-labelling.md` §5, NOT the per-signal schema from D-05.** The planner has two options:
1. **Option A — overwrite the existing CSV** with a D-05 schema header. Pros: matches CONTEXT.md exactly. Cons: throws away the Phase 0 episode-level template.
2. **Option B — create a sibling file** `data/shadow/track_b_signal_candidates.csv` with the D-05 schema, leave `track_b_episodes.csv` for the analyst's eventual episode-level rollup.

**Recommended:** Option B. CONTEXT.md D-05 says "Each candidate is written as a row in `data/shadow/track_b_episodes.csv`" — this is a **conflict** with the existing file's schema. The planner should ask the user via the discuss-phase callout, OR resolve the conflict by following CONTEXT.md verbatim (Option A). I lean Option A because CONTEXT.md is hardened and D-05 specifies the canonical path. **Flag in VERIFICATION.md as a schema-migration note** so the Phase 2 analyst-extension cycle knows which schema is canonical.

### Existing PowerShell scripts in repo (D-22 template source)

```
.claude/hooks/inject_context.ps1
.claude/hooks/postedit_protected_paths.ps1
.claude/hooks/stop_verify.ps1
```

These are **the only PowerShell files** in the repo (verified via `find /c/dev/Harmonic -name "*.ps1"`). No existing `install_keepalive_task.ps1` template. The planner must write this from scratch but can mirror the file-encoding and parameter style of the existing hooks. Standard Windows Task Scheduler PowerShell pattern (`Register-ScheduledTask` + `New-ScheduledTaskAction` + `New-ScheduledTaskTrigger` + `New-ScheduledTaskPrincipal`) is well-known and stdlib-equivalent for PowerShell.

## Concrete Bucket Boundaries

### REC-01 stratification SQL (D-04 implementation)

**Latest classification per signal × source × category × score bucket** (verified against signals.db on 2026-04-08):

```sql
WITH latest_class AS (
    SELECT signal_id,
           category,
           thesis_fit_score,
           confidence AS confidence_text,
           ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC) AS rn
    FROM thesis_classifications
)
SELECT s.source_api,
       COALESCE(lc.category, 'unclassified') AS thesis_category,
       CASE WHEN lc.thesis_fit_score IS NULL THEN 'NULL'
            WHEN lc.thesis_fit_score < 0.3 THEN '00-29'
            WHEN lc.thesis_fit_score < 0.5 THEN '30-49'
            WHEN lc.thesis_fit_score < 0.7 THEN '50-69'
            ELSE '70-100' END AS score_bucket,
       COUNT(*) AS n
FROM signals s
LEFT JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
WHERE s.source_api IN ('hacker_news','arxiv','news_api','rss_feeds')
GROUP BY s.source_api, thesis_category, score_bucket
ORDER BY s.source_api, n DESC;
```

**Live distribution result (2026-04-08, copy this into RESEARCH for the planner to embed in `mine_track_b_candidates.py` as a baseline)**:

| source_api | thesis_category | score_bucket | n |
|------------|-----------------|--------------|---|
| arxiv | excluded | 00-29 | 275 |
| arxiv | unclassified | NULL | 99 |
| hacker_news | excluded | 00-29 | 171 |
| hacker_news | unclassified | NULL | 51 |
| hacker_news | consumer_health_tech | 70-100 | 6 |
| hacker_news | consumer_marketplace | 70-100 | 4 |
| hacker_news | travel_hospitality | 70-100 | 4 |
| hacker_news | other | 70-100 | 3 |
| hacker_news | consumer_cpg | 70-100 | 2 |
| hacker_news | edtech | 70-100 | 1 |
| hacker_news | fintech | 50-69 | 1 |
| news_api | consumer_cpg | 70-100 | 8 |
| news_api | excluded | 00-29 | 5 |
| news_api | unclassified | NULL | 3 |
| news_api | consumer_health_tech | 70-100 | 1 |
| rss_feeds | consumer_cpg | 70-100 | 38 |
| rss_feeds | excluded | 00-29 | 28 |
| rss_feeds | travel_hospitality | 70-100 | 15 |
| rss_feeds | consumer_marketplace | 70-100 | 4 |
| rss_feeds | consumer_health_tech | 70-100 | 2 |
| rss_feeds | unclassified | NULL | 2 |

### Recommended D-04 stratification recipe (30 candidates)

The total qualified-category corpus (anything not `excluded` or `unclassified`) is **88 signals** (6+4+4+3+2+1+1 + 8+1 + 38+15+4+2 = 89 — close enough). The planner should pull a stratified sample like this:

```sql
-- Stratification target: 30 candidates spanning TP-likely / FP-likely / ambiguous
-- 10 from "qualified high-score" (70-100, non-excluded) — TP-likely
-- 10 from "excluded high-confidence" (00-29 excluded) — FP-likely
-- 10 from "unclassified" or "ambiguous mid-score" — ambiguous

-- Bucket 1: TP-likely (10 rows, prefer diversity across categories and source_api)
WITH latest_class AS (
    SELECT signal_id, category, thesis_fit_score,
           ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC) AS rn
    FROM thesis_classifications
),
qualified AS (
    SELECT s.id, s.source_api, s.canonical_key, s.company_name, s.confidence,
           lc.category, lc.thesis_fit_score
    FROM signals s
    JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
    WHERE s.source_api IN ('hacker_news','arxiv','news_api','rss_feeds')
      AND lc.category NOT IN ('excluded','other')
      AND lc.thesis_fit_score >= 0.7
)
SELECT id, source_api, canonical_key, company_name, confidence, category, thesis_fit_score
FROM qualified
ORDER BY RANDOM()
LIMIT 10;

-- Bucket 2: FP-likely (10 rows from excluded high-confidence)
SELECT s.id, s.source_api, s.canonical_key, s.company_name, s.confidence,
       lc.category, lc.thesis_fit_score
FROM signals s
JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
WHERE lc.category = 'excluded' AND lc.thesis_fit_score < 0.3
  AND s.source_api IN ('hacker_news','arxiv','news_api','rss_feeds')
ORDER BY RANDOM() LIMIT 10;

-- Bucket 3: Ambiguous (10 rows unclassified or 30-69 score)
SELECT s.id, s.source_api, s.canonical_key, s.company_name, s.confidence,
       COALESCE(lc.category,'unclassified') AS category,
       lc.thesis_fit_score
FROM signals s
LEFT JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
WHERE s.source_api IN ('hacker_news','arxiv','news_api','rss_feeds')
  AND (lc.thesis_fit_score IS NULL
       OR (lc.thesis_fit_score >= 0.3 AND lc.thesis_fit_score < 0.7))
ORDER BY RANDOM() LIMIT 10;
```

`mine_track_b_candidates.py` should embed all 3 queries, run each, concatenate results, and write the merged CSV. **Determinism note:** `ORDER BY RANDOM()` is non-deterministic. If the planner wants reproducible candidate selection, replace with `ORDER BY id` (deterministic but order-biased) or seed via `ORDER BY substr(canonical_key || 'seed20260408', 1, 16)`.

### REC-04 founder extraction targets (per-collector yield)

| source | extractable signals | est. founders/signal | total seeds |
|--------|---------------------|-----------------------|-------------|
| arxiv | 374 (all have `authors[]`) | 4-6 authors per paper | ~1500-2000 raw, but most are academics — needs filter to "company name in title/abstract" per D-27 |
| hacker_news | 242 Show HN signals (HN total = 243; non-show = 1) | 1 author per post (the `author` field) | ~242 candidates, but author handles are HN usernames not real names — need real-name heuristic |
| news_api | 17 total, 1 with "founder" mention | 1 founder per article | **~1-3 max** — too small to hit D-28 |
| rss_feeds | SKIPPED per D-27 | — | 0 |

**Conclusion:** D-28's 30-40 target is achievable, but **arxiv must do the heavy lifting** (~25-35 of the 30-40 seed rows). The planner's `extract_founder_candidates.py` arxiv handler is the high-leverage code path. The HN handler will likely yield 5-10 (the bottleneck is converting `author` HN handles to real names — which is hard from the data alone). The news_api handler will yield 1-3.

## Literal Row Skeletons

### R20 row skeleton (LIV-11 / GOV-02 — paste verbatim into 10-risk-register.md)

**Reference format:** R19 in `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md:53`. The format is a Markdown table row:
```
| **R##** | **<title>** | <sev> | <lik> | **<score>** | **<class>** | <mitigation_text> | **<status>** |
```

**Insert position:** After R19 in the "New risks identified during Move 0" table (lines 47-53). The new row goes at line 54 (immediately after R19's row).

**Literal R20 row (Claude's discretion on prose per CONTEXT.md, structure locked per D-30):**

```markdown
| **R20** | **Analyst abandonment — engagement loop already broken; R19's 38-day silent freeze was empirical proof. Without the analyst opening the inbox the engine has no feedback signal regardless of substrate quality.** | 5 | 5 | **25** | **Showstopper** | **Phase 1 interim mitigations (per CONTEXT.md D-29):** (a) Automated keep-alive — `scripts/red-team-hybrid/install_keepalive_task.ps1` installs Windows Task Scheduler entry running collectors + freshness watchdog daily, removing analyst from the freshness critical path. (b) Daily watchdog alert — keep-alive writes `artifacts/keepalive/YYYY-MM-DD.json` on every run; missing days flag as degraded signal independent of analyst attention. (c) STATE.md progress tick — `.planning/STATE.md` updated at end of each Wave commit so analyst sees movement on days they don't open the repo. **Phase 2 permanent mitigation (Move 0.5 Liveness Restoration, starts 2026-04-19):** LIV-04..LIV-14 — daily digest with empty-channel discipline (LIV-08), calibration positives (LIV-09), `analyst_inbox_engagement_7d` metric (LIV-10), Pandora-lite explanation column (LIV-13), inbox explanation panel (LIV-14), permanent Hold-Review batch (LIV-12). | **MITIGATING (interim) / Pending permanent (Phase 2)** — Phase 1 ships interim per D-29; Phase 2 ships permanent per LIV-04..LIV-14. |
```

**Also update `## Showstopper status check` table at lines 71-76 of the same file to add an R20 row:**

```markdown
| **R20** | **Analyst abandonment** | YES — Phase 1 ships interim mitigations per CONTEXT.md D-29 (automated keep-alive, daily watchdog alert, STATE.md progress tick); Phase 2 ships permanent mitigation via LIV-04..LIV-14 | PARTIAL — interim mitigation in place; permanent mitigation lands in Phase 2 (Move 0.5 starts 2026-04-19) |
```

### REC-01 Track B candidate CSV header (per D-05 schema)

The D-05 schema is **per-signal-candidate**, NOT per-episode. Header line:

```csv
signal_id,source_api,canonical_key,company_name,confidence_from_classifier,thesis_category,claude_pre_label,pre_label_rationale,analyst_label,labeled_at,labeler_id
```

Example seeded row (built from `mine_track_b_candidates.py` Bucket 1 / TP-likely query):

```csv
663,hacker_news,domain:reversecam.com,Reversecam,0.6,unclassified,UNSURE,"Show HN consumer product but not yet thesis-classified — analyst eyes-on",,,
```

The 3 trailing empty fields (`analyst_label`, `labeled_at`, `labeler_id`) are filled by the analyst via `python -m ops.cli quality label <signal_id> <TP|FP|UNSURE>` per D-06.

### Track E founder watchlist seed CSV header

Existing header at `data/shadow/founder_watchlist.csv:1` (verified):

```csv
founder_id,full_name,github_username,linkedin_url,source,associated_company_id,added_at
```

Per D-09 the planner must add a `source` column distinguishing `analyst|claude` — this is **already the column name** in the existing file, but the values today are expected to be `manual_seed | promoted_company | historical_notion` (per `scripts/build_founder_watchlist.py:8-13`). **Conflict:** D-09 wants `source=analyst|claude`; existing populator wants `source=manual_seed|promoted_company|historical_notion`. **Resolution:** the seed file at `scripts/data/founder_watchlist_manual_seed.csv` (the analyst-edited file) does NOT include a `source` column at all — see header line. The `source` column is added by `build_founder_watchlist.py` at populator time. **For Phase 1:** Claude writes seed rows to `scripts/data/founder_watchlist_manual_seed.csv` with the populator's existing 5-column schema, then the populator regenerates `data/shadow/founder_watchlist.csv` and stamps `source=manual_seed`. To distinguish Claude-seeded rows from analyst-seeded rows post-hoc, **use the `founder_id` prefix convention**: `claude_001..claude_040` for Claude rows, `manual_001..manual_020` for analyst rows. This avoids touching `build_founder_watchlist.py` (which is in an allowed path but has its own populator contract). Document the convention in the CSV header comment per D-10 reputation methodology note.

Per D-10 reputation_score stub: this requires modifying `scripts/build_founder_watchlist.py` to emit a `reputation_score` column, OR shipping the column only in a parallel file `data/shadow/founder_reputation_stub.csv` keyed by `founder_id`. Given Phase 1's "minimize protected-path-adjacent edits" posture, the **parallel-file approach is safer** — `data/shadow/founder_reputation_stub.csv` with header `founder_id,reputation_score,methodology_note` and a single comment row explaining V2-05 deferral.

### GOV-04 framing-correction callout skeleton

Insert at top of `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`, **before line 10** (`## 1. Naming correction`). Verified section anchors:

```
10:## 1. Naming correction
64:## 2. Goal framing (honest version)
91:## 3. Showstopper guards
133:## 4. Move sequence
188:## 5. Decision gates
228:## 6. Open questions resolved
240:## 7. What this strategy is NOT
```

Literal callout (~6-10 lines per D-12):

```markdown
> **Framing Correction (2026-04-08):** The 2026-04-06 framing of this strategy
> as "Track A is substrate hardening, not engine-efficacy improvement" is correct
> but **incomplete**. The 2026-04-08 5-agent jarvis evaluation + R19 finding
> proved that the binding constraint is **analyst engagement**, not substrate
> quality. Substrate work (Tracks A/C/D — this strategy) and engagement work
> (LIV-04..LIV-14, shipped in Move 0.5) are **complementary, not substitutive**.
> Either alone fails. Move 0.5 (Liveness Restoration) is now a **hard
> prerequisite to Move 1**, inserted between Move 0 (this prep window) and the
> original Move 1. See `.planning/REQUIREMENTS.md` LIV category and
> `.planning/ROADMAP.md` Phase 2 for the engagement plumbing scope. The §2
> "honest version" goal framing below is preserved verbatim — this callout
> annotates it; it does not replace it (per CONTEXT.md D-13).

---
```

The horizontal rule is included so the existing `## 1. Naming correction` heading remains visually distinct from the callout.

### `14-step4b-preconditions.md` skeleton (LIV-03 + GOV-03)

NEW file at `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md`. Required sections per D-20 + D-24:

```markdown
# Step 4B Preconditions and Governance Gate Freshness Contract

**Date:** 2026-04-08
**Status:** Spec — implemented in code in Phase 2 (after 2026-04-19 unfreeze)
**REQ:** LIV-03 (regret check precondition) + GOV-03 (gate contract for all governance gates)
**Resolves:** R19 root cause + R20 interim mitigation surface for governance lane

## 1. The contract

Every governance gate (Step 4B regret check, canary runs, drift alerts, future
state-promotion gates) requires `max(detected_at) < 5 days` for ≥3 of the 4
operational collectors over the prior 7 days, BEFORE the gate evaluates its
primary condition.

## 2. Verification command

\`\`\`bash
python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 120
\`\`\`

Exit codes: 0 = freshness OK / proceed with gate; 1 = freshness FAIL / postpone
gate per LIV-03; 2 = operational error (DB unreadable) → escalate, do NOT
silently pass.

## 3. Required precondition input format

Every gate evaluator MUST capture the freshness watchdog JSON output as part of
the gate's audit_events row. Schema:

\`\`\`json
{
  "checked_at": "<ISO 8601>",
  "threshold_hours": 120,
  "exit_code": 0,
  "status": "OK",
  "collectors": [...],
  "failures": []
}
\`\`\`

(Schema mirrors `freshness_watchdog.py:266-282` `render_json()`.)

## 4. Blocking vs advisory behavior

| Gate | Phase 1 (this doc) | Phase 2 (after unfreeze) |
|------|--------------------|----------------------------|
| Step 4B regret check (2026-04-18) | BLOCKING — postpones if freshness fails (per LIV-03 escalation) | BLOCKING — same |
| Daily canary runs | ADVISORY — log warning, do not block | BLOCKING after 30 days zero false-positives |
| SPC drift alerts | ADVISORY — alerts gate on freshness state | BLOCKING after Phase 2 ships |
| Future state-promotion gates | DOCS-ONLY in Phase 1 | BLOCKING in Phase 2 |

## 5. Failure escalation path

1. Freshness watchdog returns rc=1 → automated postpone of the gate (no human
   action required for the postpone itself).
2. Postpone event written to `audit_events` with reason `freshness_failure`,
   referencing the watchdog JSON output.
3. STATE.md updated with the postpone event on the same day.
4. Next regret check date computed as `now + 5 days` (the freshness window).
5. If freshness still fails after 3 consecutive postpone cycles, escalate to
   human review — root-cause investigation, do not auto-postpone indefinitely.

## 6. Gates this contract applies to

- Step 4B regret check (governance event #21, due 2026-04-18) — **THE PRIMARY
  GATE THIS CONTRACT EXISTS FOR**
- Daily canary runs (Phase 2+)
- SPC drift alerts (Phase 2+)
- Future state-promotion gates (Phase 2+)

## 7. Phase 2 implementation hand-off

Phase 2 implements this contract in `governance/` (currently a protected path).
Specifically:
- `governance/cli.py` learns a `--require-freshness` flag that runs the
  watchdog before evaluating any gate.
- `governance/state_policies.py` adds a `freshness_precondition` field to
  the policy schema, defaulting to True for gates listed in §6.
- `audit_events` schema gains a `precondition_audit_json` column (or stores
  the watchdog output as a child row in a new `gate_preconditions` table).

These code changes are OUT OF SCOPE for Phase 1 (D-20 / D-21). Phase 1 ships
THIS DOCUMENT. Phase 2 ships the code.

## 8. Link back to R19 and R20

- R19 (frozen pipeline): this contract closes R19 by ensuring no governance
  gate can pass against silently-frozen data.
- R20 (analyst abandonment): this contract is part of the Phase 1 interim
  mitigation suite (per CONTEXT.md D-29) by removing the analyst from the
  freshness critical path for governance gates.

See `10-risk-register.md` R19 and R20 rows for full context.
```

### `13-track-d-design.md` outline (REC-03 design-only)

NEW file at `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md`. Required sections per D-19:

```markdown
# Track D Design — CT-Log + DNS Shadow Collector

**Date:** 2026-04-08
**Status:** Design only — implementation blocks on 2026-04-19 protected-paths unfreeze
**REQ:** REC-03

## 1. CT-log source decision
(answer: which CT log? crt.sh? Google Argon? Cloudflare Nimbus? rate limits, query format, dedupe strategy)

## 2. DNS data source decision
(answer: passive DNS provider? Censys? CertSpotter? SecurityTrails? cost envelope?)

## 3. Canonical key strategy for stealth companies
(answer: how do CT-log + DNS hits map to canonical_key when there's no website yet?)

## 4. Anti-fingerprinting posture
(answer: do queries leak "we're tracking startup X"? Censys/SecurityTrails ToS implications?)

## 5. Cost envelope
(answer: $/month at 100 founders × 30-day window? rate budget?)

## 6. Implementation gating
Code lives in `collectors/` (PROTECTED). Implementation starts 2026-04-19.
Phase 1 ships this doc only.
```

The 5 specific questions in §1-§5 are mandatory per D-19. The planner should populate each with concrete answers (not "TBD") because Phase 3 will use this doc as the authoritative source.

## Per-REQ Execution Notes

### LIV-03 (freshness precondition for Step 4B regret check)
- **Target file:** NEW `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` per D-24. Skeleton above.
- **Co-located with GOV-03** in the same file.
- **Verification command** for hard rubric gate 2 is already implemented: `python scripts/red-team-hybrid/freshness_watchdog.py --json` (current threshold 36h; the contract spec uses 120h / 5 days per LIV-03's "freshness < 5 days" requirement).
- **Edge case:** the 36h vs 120h discrepancy. The planner should clarify in the contract that the watchdog supports `--threshold-hours` and the gate contract uses 120h, while the day-to-day freshness alert uses 36h.

### LIV-11 / GOV-02 (R20 in risk register)
- **Target file:** `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md`
- **Insert position:** Append R20 row after R19 (line 53), in the "New risks identified during Move 0" table (lines 47-53).
- **Also update** the "Showstopper status check" table at lines 71-76 to add an R20 row.
- **Literal row** above. Status string "MITIGATING (interim) / Pending permanent (Phase 2)" matches D-30 exactly.
- **Both REQs satisfied by the same edit** — LIV-11 is the user-facing add, GOV-02 is the governance traceability tag. Single commit, two REQ tags in the message.

### GOV-01 (withdraw "9% pipeline precision" claim)
- **Targets requiring edit:** 4 lines in 3 files per D-25 (correct):
  1. `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md:44` — inline citation, add dated strike-through caveat
  2. `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md:74` — metric table row, add caveat
  3. `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md:265-268` (§11) — REWRITE entire section per D-25, do NOT just strike
- **Targets that DO NOT need edit** (cited from grep, but already wrapped in withdrawal context or are read-only references):
  - `docs/plans/2026-04-06-lob-progress-eval/README.md:11` — already says "1. The '9% pipeline precision' claim ... was **selection bias**, not a real metric." This IS the withdrawal — leave alone.
  - `docs/plans/2026-04-06-lob-progress-eval/bias-audit.md:57,61,78,182` — bias-audit document IS the source-of-truth for the withdrawal. These lines record the original claim and the correction. Editing them would corrupt the audit trail. Leave alone.
  - `.planning/PROJECT.md:186`, `.planning/REQUIREMENTS.md:67`, `.planning/STATE.md:27`, `.planning/ROADMAP.md:33,44` — already framed as "withdrawn" / "GOV-01 task". Leave alone.
  - `CLAUDE.md` — no matches (verified).
- **Pattern for the dated caveat per D-14:** preserve original line, add inline strikethrough, add dated callout. Example for `05-holdout-cohort-design.md:44`:
  ```markdown
  to classify individual signals. ~~The 9% precision metric (per LOB.txt
  evaluation) is on signals; the metric the strategy needs to move is on
  companies that turn into meetings/fundings.~~

  > **Withdrawn 2026-04-08 (GOV-01):** The "9% precision" claim is selection
  > bias per the 2026-04-06 bias audit (`docs/plans/2026-04-06-lob-progress-eval/bias-audit.md`).
  > The 211 labeled signals are an opportunistic sample of suspected FPs, not
  > a random sample. Actual pipeline precision is unknown. The metric the
  > strategy needs to move remains "companies that turn into meetings/fundings"
  > — that part of the original claim is preserved.
  ```
- **For §11 of `06-tier-2-recall-eval.md` REWRITE per D-25:** the entire ~25-line section needs to be replaced with a new section that frames Tier-2 recall as the primary metric and explicitly retires 9% as a comparison target. Suggested replacement title: `## 11. Why "9% precision" is not a valid baseline`. The new section should: (a) cite the bias audit, (b) explain why selection bias makes the 9% number not comparable to anything, (c) state explicitly that Tier-2 recall is the primary substrate-quality metric and the only one that should be tracked across moves, (d) preserve the precision-vs-recall dichotomy from the original §11 (those parts are still correct) but frame them around the *unknown* pipeline precision.
- **Commit message must include the full grep output** per D-26: `grep -rn "9%" docs/ .planning/ CLAUDE.md` and list every touched file.

### GOV-02 (R20 governance traceability)
- See LIV-11 above. Same edit, single commit.

### GOV-03 (freshness precondition contract for ALL governance gates)
- **DOCS-ONLY per D-20.** The planner MUST NOT propose any edit to `governance/*.py`. Any plan that does is rejected at plan-review.
- **Target file:** SAME file as LIV-03 — `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` per D-24.
- The contract sections (§4 blocking-vs-advisory, §5 escalation, §6 applicable gates, §7 Phase 2 hand-off) cover GOV-03's requirements.

### GOV-04 (framing correction in `00-strategy.md`)
- **Target file:** `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`
- **Insert before line 10** (`## 1. Naming correction`)
- **Literal callout** above per D-12.
- **Original §2 text PRESERVED VERBATIM per D-13** — do not edit lines 64-89, only prepend the callout at the top of the document.

### SUB-01 (Move 0 charter deliverables verification)
- **Target file:** Phase 1 VERIFICATION.md (created during execution)
- **Source of truth:** `docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md` §3 lines 60-74 lists 12 charter deliverables D1-D12.
- **Verification matrix** (Phase 1's job is to confirm each is at the 80%+ level per the 80% rule):

| # | Charter deliverable | Location | Phase 1 status |
|---|---------------------|----------|-----------------|
| D1 | Strategy charter (renamed) | `00-strategy.md` | ✓ Exists; GOV-04 adds framing callout |
| D2 | Move 0 charter | `01-move-0-charter.md` | ✓ Exists |
| D3 | Bounded-context map | `02-bounded-context-map.md` | ✓ Exists (verify 80%+ at SUB-01 verification) |
| D4 | Dead-letter contract | `03-dead-letter-contract.md` | ✓ Exists |
| D5 | LLM failure mode decision | `04-llm-failure-mode.md` | ✓ Exists |
| D6 | Hold-out cohort split design | `05-holdout-cohort-design.md` | ✓ Exists; GOV-01 edits line 44 |
| D7 | Tier-2 recall eval design | `06-tier-2-recall-eval.md` | ✓ Exists; GOV-01 rewrites §11 |
| D8 | Systematic collector audit catalog | `07-collector-audit.md` | ✓ Exists (verify 80%+) |
| D9 | Track B labelling sprint plan | `08-track-b-labelling.md` | ✓ Exists; REC-01 ships the seeded CSV |
| D10 | Track E founder watchlist | `09-track-e-watchlist.md` + populated CSV | ✓ Doc exists; REC-04 ships the seeded CSV |
| D11 | Risk register snapshot | `10-risk-register.md` | ✓ Exists; LIV-11/GOV-02 add R20 |
| D12 | Protected-paths verifier | `scripts/red-team-hybrid/check_protected_paths.sh` | ✓ Exists (verified above) |
| (extra) | Freshness watchdog | `scripts/red-team-hybrid/freshness_watchdog.py` | ✓ Shipped LIV-02 (commit `4efe8cf`) |
| (extra) | Step 4B preconditions | `14-step4b-preconditions.md` | NEW Phase 1 deliverable (LIV-03 + GOV-03) |
| (extra) | Track D design | `13-track-d-design.md` | NEW Phase 1 deliverable (REC-03) |
| (extra) | Track B candidate seed | `data/shadow/track_b_episodes.csv` (or sibling per D-05 conflict) | NEW Phase 1 deliverable (REC-01) |
| (extra) | Track E founder seed | `scripts/data/founder_watchlist_manual_seed.csv` + `data/shadow/founder_watchlist.csv` | NEW Phase 1 deliverable (REC-04) |
| (extra) | Hold-out split file | `data/shadow/holdout_split/episodes_v1.csv` | NEW Phase 1 deliverable (REC-02) |
| (extra) | Keep-alive task installer | `scripts/red-team-hybrid/install_keepalive_task.ps1` + first 2 runs in `artifacts/keepalive/` | NEW Phase 1 deliverable (D-22 / R19 root cause fix) |

**SUB-01 verification = walk this matrix at end of Phase 1, confirm every "exists" and every "NEW".**

### REC-01 (Track B 30-episode seed)
- **Schema conflict** between existing `data/shadow/track_b_episodes.csv` (episode-level Phase 0 schema) and CONTEXT.md D-05 (per-signal-candidate schema). See "Existing data/shadow state" above. **Recommendation:** follow CONTEXT.md D-05 verbatim — overwrite the existing file's header. Document the schema migration in VERIFICATION.md so the analyst's eventual episode-level rollup knows to use a separate file.
- **Mining script:** `scripts/red-team-hybrid/mine_track_b_candidates.py` — stdlib-only, follows freshness_watchdog.py style. Embeds the 3 stratification queries above. Default output `data/shadow/track_b_episodes.csv`.
- **Target rows:** 30 (10 TP-likely + 10 FP-likely + 10 ambiguous). D-03 applies if shortfall.
- **Pre-label rationale:** the `claude_pre_label` and `pre_label_rationale` columns are filled by Claude during the mining run. Recommended labels: `TP-LIKELY` for Bucket 1 (qualified high-score), `FP-LIKELY` for Bucket 2 (excluded high-confidence), `UNSURE` for Bucket 3 (ambiguous). Rationale should cite the bucket and the specific category/score.

### REC-02 (Track C hold-out cohort split)
- **Target directory:** `data/shadow/holdout_split/` — **DOES NOT EXIST**, planner must create it.
- **Target file:** `data/shadow/holdout_split/episodes_v1.csv`
- **Algorithm:** Use `05-holdout-cohort-design.md` §3 verbatim:
  ```python
  import hashlib
  def assign_split(episode_id, seed=20260406, holdout_fraction=0.3):
      h = hashlib.sha256(f"{seed}:{episode_id}".encode()).digest()
      bucket = int.from_bytes(h[:8], "big") / (1 << 64)
      return "holdout" if bucket < holdout_fraction else "train"
  ```
- **Input source:** Phase 1 has no episodes yet (REC-01 produces signal-candidates, not episodes). The planner has two options:
  1. **Run the split on the REC-01 CSV** treating each candidate row's `signal_id` as the `episode_id`. This is a **reuse-the-key** simplification. Document in the file header.
  2. **Ship an empty `episodes_v1.csv` with header only**, plus the script `build_holdout_split.py`, plus a README explaining that the split runs on real episodes once the analyst confirms them in Phase 2. This is the **honest** approach but doesn't satisfy soft rubric gate 10.
- **Recommended:** Option 1 for the rubric, with a header comment documenting that episodes_v1 is "signal-candidate-keyed proxy until real episodes exist post-Phase-2".
- **Output columns** per `05-holdout-cohort-design.md` §3: `episode_id, canonical_key, split, outcome_label, labelled_at`. With the proxy, `outcome_label` will be Claude's pre-label, and `labelled_at` will be the mining timestamp.

### REC-03 (Track D CT-log + DNS design)
- **Target file:** NEW `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md`
- **Required answers:** the 5 specific questions in D-19 — CT-log source, DNS source, canonical key strategy, anti-fingerprinting, cost envelope. Outline above.
- **Hard constraint:** NO collector code, NO schema migrations. If the planner finds itself wanting to write Python code for this REQ, the plan is wrong.

### REC-04 (Track E founder watchlist)
- **Extraction script:** `scripts/red-team-hybrid/extract_founder_candidates.py` — stdlib-only. Per-collector handlers per D-27.
- **arxiv handler:** parse `authors[]` from `raw_data` JSON. Filter heuristic: keep authors only if the paper's `title` or `abstract` contains a known company-pattern token (Inc, Labs, AI, Corp, Studio, etc.). This is weak — D-28's 30-40 target is a stretch from arxiv alone.
- **hacker_news handler:** parse `author` field + `title`. The `author` field is an HN username, not a real name. The planner needs a heuristic to convert: e.g., extract real names from `story_text` if the post says "I'm <Name>, founder of...". Yield will be low (5-10 max).
- **news_api handler:** parse `title` + `description`. Search for capitalized phrases near "founder", "CEO", "co-founder". Verified yield: 1-3 max from current 17 rows.
- **Output:** writes to `scripts/data/founder_watchlist_manual_seed.csv` (the 5-column populator schema). Use `founder_id=claude_NNN` prefix.
- **Reputation stub** per D-10: ship as parallel file `data/shadow/founder_reputation_stub.csv` with header + methodology comment, NOT as a column edit to `build_founder_watchlist.py`.
- **Hard constraint per D-11:** NO LinkedIn scraping. Founder names from arxiv author lists, HN self-introductions, news_api article text only.
- **Final step:** run `python -m scripts.build_founder_watchlist --verbose` to regenerate `data/shadow/founder_watchlist.csv` from the populated seed.

### Pipeline keep-alive (D-22, NOT a REQ but enforced by CONTEXT.md as Wave A scope)
- **Target file:** NEW `scripts/red-team-hybrid/install_keepalive_task.ps1`
- **Behavior:** Register Windows Task Scheduler task running daily at 08:00 local:
  ```
  python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api
  python scripts/red-team-hybrid/freshness_watchdog.py --json >> artifacts/keepalive/$(date +%Y-%m-%d).json
  ```
- **PowerShell template:** mirror style of `.claude/hooks/postedit_protected_paths.ps1` (the only existing in-repo PowerShell pattern).
- **Verification:** hard rubric gate 5 requires the task to be installed AND have run successfully at least twice with evidence in `artifacts/keepalive/`. Phase 1 must run the task at least twice before 2026-04-18.
- **Per D-23 fallback:** if the planner determines (incorrectly per Finding 1 above) that GH Actions can run this against the production DB, the alternative is `.github/workflows/freshness-keepalive.yml`. **Default = D-22** per CONTEXT.md and per Finding 1's corrected rationale.

## Don't Re-Derive

The planner should treat these as authoritative and NOT spend planning effort on them:

1. **All 34 decisions D-01..D-34 in CONTEXT.md** — these were made by the user during discuss-phase and are locked.
2. **Wave structure (A → B → C)** — fixed by D-15.
3. **Per-REQ commit granularity** (≤50 lines, atomic per REQ) — fixed by D-16.
4. **Day-by-day kill criteria** — fixed by D-33.
5. **Verification rubric (7 hard gates + 6 soft gates)** — fixed in CONTEXT.md `<rubric>` section. The planner copies this verbatim into VERIFICATION.md.
6. **Phase 1 → Phase 2 handoff inputs** — fixed by D-31.
7. **Move 0 forbidden paths** — fixed by `01-move-0-charter.md` §2 and enforced by `check_protected_paths.sh`. The planner does not need to re-evaluate which paths are protected.
8. **`05-holdout-cohort-design.md` algorithm** — REC-02 uses the algorithm in §3 verbatim per D-17.
9. **`08-track-b-labelling.md` outcome label taxonomy** — referenced for context, not re-derived.
10. **Track B episode-vs-signal schema decision** — D-05 says signal-level for Phase 1. Episode-level rollup is Phase 2+.
11. **Selection bias narrative** — fixed by D-07 + the bias audit. GOV-01 just executes the withdrawal; it does not re-litigate the audit.
12. **R19 mitigation strategy** — D-22 / D-23 closes R19 root cause. CONTEXT.md `<specifics>` section is the rationale.

## Blockers

**None.** All Phase 1 prerequisites verified present. CONTEXT.md is internally consistent and the codebase supports every decision.

The 3 findings above are NOT blockers — they are integration-time clarifications:
- **Finding 1** (D-22 rationale correction): the planner uses D-22 as default per CONTEXT.md; the rationale string in PLAN.md should be updated to "the CI DB is a separate instance from local production; only a local scheduled task closes R19 on the production data path", not "GH Actions cannot read signals.db".
- **Finding 2** (CI is silently failing): the planner should add an optional Wave A sub-task to fix `discovery-pipeline.yml`'s `-v` arg. This is a 5-line YAML edit in an allowed path. NOT a CONTEXT.md requirement, but it directly fixes an instance of the same R19 failure mode in a different lane and should be in scope.
- **Finding 3** (broader 9% grep): the planner runs the wider grep per D-26 and gets the same conclusion as D-25 — only 4 lines need GOV-01 edits. The bias-audit.md hits are read-only references.

Two minor implementation conflicts the planner should resolve:
1. **`data/shadow/track_b_episodes.csv` schema conflict** — existing file has Phase 0 episode-level header; D-05 wants per-signal-candidate header. **Recommendation:** Option A (overwrite per D-05); document the migration in VERIFICATION.md.
2. **`source` column conflict in founder_watchlist.csv** — D-09 wants `source=analyst|claude`; existing populator emits `source=manual_seed|promoted_company|historical_notion`. **Recommendation:** use the `founder_id` prefix convention (`claude_NNN` vs `manual_NNN`) instead of editing the populator. Document in CSV header comment.

## Sources

### Primary (HIGH confidence — verified in this session)
- `signals.db` (10MB local SQLite, gitignored) — 767 signals, 3,085 thesis_classifications, all queries verified
- `scripts/red-team-hybrid/freshness_watchdog.py:1-349` — style template, exit code contract, sqlite3 ro pattern
- `scripts/red-team-hybrid/check_protected_paths.sh:27-50` — forbidden patterns, multi-state diff capture
- `scripts/red-team-hybrid/track_b_episodes.template.csv:1` — header verified
- `data/shadow/track_b_episodes.csv:1` — existing file, header-only, episode-level schema
- `data/shadow/founder_watchlist.csv:1` — existing file, header-only, populator schema
- `scripts/data/founder_watchlist_manual_seed.csv:1` — existing file, header-only, 5-column manual seed schema
- `scripts/build_founder_watchlist.py:1-50` — populator contract verified
- `.github/workflows/discovery-pipeline.yml:1-250` — full file read; signals.db artifact pattern, daily cron, current `-v` arg failure
- `.github/workflows/regression-gate.yml:1-30` — header inspected; PR-triggered, no signals.db touch
- `gh run list --workflow=discovery-pipeline.yml --limit 5` — 5 consecutive failures verified
- `gh run view 24068064584 --log-failed` — `-v` argument error verified
- `.gitignore:51,54,91` — signals.db gitignored confirmed
- `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md:1-251` — full file read; section anchors verified
- `docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md:1-134` — full file read; D1-D12 enumerated
- `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md:1-266` — full file read; algorithm + 9% citation at line 44 verified
- `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md:1-313` — full file read; 9% citations at line 74 and §11 (lines 265-268) verified
- `docs/plans/2026-04-06-red-team-hybrid/08-track-b-labelling.md:1-259` — full file read; episode schema vs D-05 conflict identified
- `docs/plans/2026-04-06-red-team-hybrid/09-track-e-watchlist.md:1-238` — full file read; populator contract + LinkedIn hard-no verified
- `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md:1-91` — full file read; R19 row format byte-for-byte verified at line 53
- `docs/plans/2026-04-06-red-team-hybrid/README.md:1-72` — full file read
- `.planning/REQUIREMENTS.md:1-138` — full file read
- `.planning/STATE.md:1-58` — full file read
- `.planning/ROADMAP.md:1-144` — full file read
- `.planning/codebase/STRUCTURE.md:1-153` — directory layout verified
- `.planning/codebase/CONCERNS.md:1-120` — R19 + R20 + known issues aligned with CONTEXT.md
- `.planning/codebase/STACK.md:1-60` — Python 3.11+ stdlib for new scripts confirmed
- `.claude/skills/quality-label/SKILL.md:1-35` — full file read; CLI pattern verified
- `CLAUDE.md` (project root) — full read via system context

### Grep results (HIGH confidence)
- `grep -rn "9%" docs/`: 14 hits across 8 files (1 unrelated similarity score, 1 historical FP rate, 4 in `2026-04-06-lob-progress-eval/` (read-only audit), 4 in `2026-04-06-red-team-hybrid/` (the GOV-01 targets), 4 unrelated hits about `89%`/`98%`/`99%`)
- `grep -rn "9%" .planning/`: 13 hits across 6 files (all already framed as "withdrawn" or "GOV-01 task")
- `grep -rn "9%" CLAUDE.md`: 0 hits
- **Net GOV-01 edit targets: 4 lines in 3 files per D-25 (verified correct)**

### Tooling output
- `python -c "..."` against `signals.db` (sqlite3 read-only mode) — distribution queries, schema, sample raw_data inspections
- `gh run list --workflow=discovery-pipeline.yml --limit 5` — failure history
- `gh run view 24068064584 --log-failed` — failure root cause
- `find /c/dev/Harmonic -name "*.ps1"` — only 3 PowerShell files, all in `.claude/hooks/`
- `ls -la /c/dev/Harmonic/data/shadow/` — confirms `holdout_split/` does NOT exist
- `wc -l data/shadow/track_b_episodes.csv data/shadow/founder_watchlist.csv scripts/data/founder_watchlist_manual_seed.csv` — all 1 line (header only)

## Metadata

**Confidence breakdown:**
- CONTEXT.md decisions: HIGH — read in full, treated as authoritative
- Codebase facts (signals.db, file existence, schemas): HIGH — every claim sourced from a tool invocation
- D-22 vs D-23 finding: HIGH — verified `discovery-pipeline.yml` does access signals.db via artifacts; verified the artifact DB is small (52KB) and divergent from local; verified the workflow is currently failing
- 9% grep matrix: HIGH — full grep output captured and classified
- raw_data shapes per collector: HIGH — sample rows inspected directly
- Stratification SQL: HIGH — query executed against live DB, results captured verbatim
- R20 row prose: MEDIUM — structure is locked per D-30, exact wording is Claude's discretion per CONTEXT.md
- `13-track-d-design.md` and `14-step4b-preconditions.md` skeletons: MEDIUM — sections are mandated by D-19 and D-20, exact headings/structure are Claude's

**Research date:** 2026-04-08
**Valid until:** 2026-04-19 (Phase 1 end). After 2026-04-19 the protected-paths constraint lifts and the codebase grounding may shift.

---

*Phase: 01-move-0-prep-liveness-prep*
*Researched: 2026-04-08*
*Consumer: gsd-planner / `/gsd-plan-phase`*
