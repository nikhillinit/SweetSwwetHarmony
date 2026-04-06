# Red-Team v2 Phase 0 — Execution

**Plan reference:** `docs/plans/2026-04-06-lob-progress-eval/`
**Phase 0 window:** 2026-04-06 → 2026-04-19 (Step 4B regret check)
**Status:** All Phase 0 tasks p0.1–p0.10 implemented (engineering portion)

## Hard constraints (re-verified at session start)

| Constraint | Status |
|---|---|
| Step 4B regret check due **2026-04-18** | Active |
| `MERGE_WRITES_ENABLED=active` since 2026-04-04 | Verified in `.env` |
| `LLM_THESIS_MODE=active` since 2026-03-25 | Verified in `.env` |
| `USE_THIN_FILES=true` (Step 2 live) | Verified in `workflows/thin_file_manager.py` |
| **No production routing / governance / Notion-push code may be edited** before 2026-04-19 | All Phase 0 work writes only to `analytics/`, `data/shadow/`, `artifacts/`, or `scripts/` |

## What was built in Phase 0

### Code (analytics/ and scripts/)

| Path | Task | Tests |
|---|---|---|
| `analytics/__init__.py` | scaffolding | — |
| `analytics/evidence_ontology.py` | `p0.1` | `analytics/test_evidence_ontology.py` (23 tests) |
| `analytics/shadow_sidecar.py` | `p0.5` | `analytics/test_shadow_sidecar.py` (12 tests) |
| `analytics/shadow_collectors/__init__.py` | scaffolding | — |
| `analytics/shadow_collectors/base.py` | `p0.7-9` shared | (covered by collector tests) |
| `analytics/shadow_collectors/ct_log.py` | `p0.7` | `test_shadow_collectors.py` (5 tests) |
| `analytics/shadow_collectors/dns_fingerprint.py` | `p0.8` | `test_shadow_collectors.py` (3 tests) |
| `analytics/shadow_collectors/gh_negative_space.py` | `p0.9` | `test_shadow_collectors.py` (3 + 2 base tests) |
| `scripts/build_founder_watchlist.py` | `p0.6` | `scripts/test_build_founder_watchlist.py` (6 tests) |
| `scripts/compute_discovery_kpi_baseline.py` | `p0.10` | `scripts/test_compute_discovery_kpi_baseline.py` (8 tests) |

**Total tests added: 62.** All passing locally as of 2026-04-06.

### Documentation (artifacts/red-team-execution/phase0/)

| File | Task |
|---|---|
| `README.md` | this file |
| `evidence-ontology.md` | `p0.1` — schema mapping, derived ontology |
| `collector-inventory.md` | `p0.2` — keep/drop/merge decisions, legal sign-off |
| `episode-labelling-spec.md` | `p0.3` — company-episode CSV schema, sprint targets |
| `replay-harness-spec.md` | `p0.4` — design for the replay harness used in Phase 1 |

### Discovery KPI baseline (`p0.10`)

The baseline script (`python -m scripts.compute_discovery_kpi_baseline`)
produces both `discovery-kpi-baseline.md` and `discovery-kpi-baseline.json`.

**Run against live signals.db on 2026-04-06.** Headline findings (90-day window):

| KPI | Value | Note |
|---|---|---|
| Promoted companies (lifetime) | 118 | from `company_files.status='promoted'` |
| **Sole-ambient promotions** | **98 / 118 (83.1%)** | all `["hacker_news"]` |
| Promotions with any discovery class | 20 / 118 (16.9%) | all are `(ATS + manual_seed)` |
| Cross-source convergence (≥2 discovery classes) | **0 / 118 (0%)** | not a single multi-class promotion |
| Analyst precision @ queue 20 | 15% (3/20 labelled) | small denominator, ~labelled cohort |
| Meetings booked rate | 9.8% (58/589) | actual product KPI |
| Pre-launch detection (TPs) | 42.1% (8/19 TPs) | the "19 TPs" exist as labels — strategy doc was wrong about cohort name |
| Lead time vs first public mention | **uncomputable** | discovery-class and ambient-class cohorts are **disjoint** |

**The strategy document's central diagnosis is empirically validated by these
numbers.** 83% of promoted companies are sole-source HN. Zero promoted
companies have multi-discovery-class evidence. The system is, structurally,
the "content-first filter" the strategy doc said it was. The Phase 0 work
is now grounded in this reality, not in the abstract proposal.

To re-run::

    python -m scripts.compute_discovery_kpi_baseline --days 90 --verbose

## Safety contract — verification

Every piece of Phase 0 code respects the P1 contract from the red-team v2
plan. The contract states:

1. The shadow sidecar **never opens a writable connection** to `signals.db`.
2. Read access uses immutable URI mode (`?mode=ro&immutable=1`) or a snapshot.
3. All sidecar writes go to `data/shadow/discovery.db`.
4. The sidecar registers with `DBToolLock` so the watermark guard knows.

**Verification:**
- `analytics/test_shadow_sidecar.py::test_production_read_is_readonly_immutable`
  proves writes raise `sqlite3.OperationalError`.
- `analytics/test_shadow_sidecar.py::test_shadow_write_refuses_sql_referencing_production_db`
  proves the SQL substring guard rejects production-DB references.
- `analytics/test_shadow_sidecar.py::test_snapshot_mode_read_does_not_touch_live_db`
  deletes the live DB after snapshot and confirms reads still succeed.

## Verification of strategy-doc claims

Per the red-team verification plan:

| Claim | Verified |
|---|---|
| `signal_class` / `gate_state` / `ladder_level` do NOT exist | grep returns 0 matches in `.py` files |
| RDAP exists in `collectors/domain_whois.py` | confirmed (module docstring lines 6, 18) |
| Thesis classifier runs at processing stage, not ingestion | confirmed at `workflows/pipeline.py:1637` (`_process_signals_stage`) |
| `tests/fixtures/thesis_llm_golden_set.jsonl` is 64 rows, not 19 TPs | `wc -l` returns 64 |
| `TRUSTED_SOURCES` includes `hacker_news` | confirmed at `workflows/thin_file_manager.py:33` |
| `DBToolLock` exists for sidecar registration | `utils/db_tool_lock.py` |

## What was NOT built in Phase 0 (and why)

| Skipped | Reason |
|---|---|
| Live HTTP fetchers for CT-log / DNS / GH negative-space | Phase 0 ships scaffolds with pluggable fetcher protocols. Live integration is a Phase 0 day-2+ task that swaps in `httpx`-based implementations without changing the surrounding scaffold. |
| `ops/cli quality label --unit company-episode` extension | The labelling sprint (`p0.3`) is human work; the CLI extension is Phase 0 day-2 once a labeller is identified. |
| Step 4B regret check (`p0.11`) | Cannot run before 2026-04-18. Will be a follow-up commit. |
| Phase 0 review breakpoint (`p0.12`) | Human approval gate. Triggered by this README. |
| Any change to `workflows/`, `governance/`, `monitoring/`, `connectors/` | Forbidden by the regret window. |

## Test invocation

To run the Phase 0 test suite::

    python -m pytest analytics/ scripts/test_build_founder_watchlist.py \
                     scripts/test_compute_discovery_kpi_baseline.py -q

Expected: 62 passed.

## Phase 1 entry conditions

Phase 1 cannot start until **all** of:

1. Step 4B regret check has cleared (2026-04-18)
2. Labelled cohort `data/shadow/labelled-episodes.csv` exists with ≥30 rows
3. KPI baseline has been run against the live signals.db at least once
4. Phase 0 review breakpoint (`p0.BP`) has been signed off

The shadow sidecar and collectors built in Phase 0 are intentionally idle
in this commit — they have no scheduler entry, no cron, no pipeline hook.
Activation is part of Phase 1's "instrument before changing logic" workstream.

## Cross-references

- Plan: `docs/plans/2026-04-06-lob-progress-eval/` (drop directory from `git status`)
- Memory: `MEMORY.md` (Discovery Engine project memory, last updated 2026-04-06)
- Invariants: `.claude/rules/invariants.md`
- Red-team v2 source: the user-provided plan in this session
