# KG Post-Window Hardening Tickets

**Created:** 2026-03-19
**Earliest execution:** 2026-03-24

Priority: P1 = before operator adoption, P2 = before pipeline integration, P3 = future

---

## P1: Before operator adoption

### T1. CLI test coverage for graph etl/query commands
**Files:** `tests/ops/test_graph_etl_cli.py` (new)
**What:** The repo has `test_graph_build_cli.py` covering the architecture `build` command, but no equivalent tests for the 9 new subcommands (etl, etl-status, evidence, gaps, conflicts, sector, duplicates, rank, ego). Add CLI-level tests that exercise argparse registration, help output, and basic invocation against an in-memory DB.

### T2. Snapshot-based ETL validation script
**Files:** `scripts/validate_kg_etl.py` (new)
**What:** Wrap the validation runbook phases A-D into a single script that creates a DB snapshot, runs ETL, validates, checks idempotency, and reports pass/fail. Intended for use in CI or manual acceptance.

### T3. Query-result sanity assertions
**Files:** `tests/storage/test_kg_queries_sanity.py` (new)
**What:** Tests that run the full ETL on fixture data and then verify query results against known expected values. Distinct from the existing unit tests which insert nodes/edges directly — these tests go through the ETL path end-to-end.

---

## P2: Before pipeline integration (Phase 3A)

### T4. Notion enricher opt-in wiring
**Files:** `workflows/notion_enricher.py` (new, per plan Phase 3A)
**What:** Enricher that reads KG evidence chains and injects richer `Why Now`, `Confidence Score`, and `Sector` values into `ProspectPayload`. Must be opt-in via dependency injection — default behavior unchanged.

### T5. Evidence-weighted confidence score validation
**What:** The `weighted_score` in `KGQueryEngine.company_evidence_chain()` uses a diversity bonus heuristic (+0.1 per source beyond 1). Validate this against the existing `VerificationGate` scoring to ensure they don't diverge or contradict. Document the relationship.

### T6. Schema validation for new optional Notion properties
**What:** Phase 3A adds optional Notion properties (`Evidence Summary`, `Conflict Flags`). Schema preflight must tolerate their absence (already designed this way, but needs a test).

---

## P3: Future / deferred

### T7. Founder/investor extraction (Phase 3B)
**Blocked by:** Collector changes to persist structured officer/investor data in `raw_data` JSON.

### T8. GraphRAG query router (Phase 4)
**Blocked by:** Phases 1-2 validation + real-data query review.

### T9. Incremental ETL watermark testing
**What:** The incremental mode reads `source_watermark` from `kg_runs` but the current ETL doesn't write it back. Either implement watermark write-back or document that incremental mode currently re-scans all signals on every run.

### T10. ETL performance at scale
**What:** Current ETL writes nodes/edges one-at-a-time in a single transaction. At 657 signals this is fine (~16ms). At 10K+ signals, consider batch inserts or chunked transactions.
