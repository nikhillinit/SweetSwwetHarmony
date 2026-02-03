# Sprint history (archived)

## Current Sprint: Signal Quality & Enrichment ✅ COMPLETE

> **Sprint Summary:**
> - All 5 phases completed successfully
> - Signal consolidation, enrichment boost, thesis integration, exit predictor all operational
> - Collector evaluation research complete (no new collectors needed)
> - Existing collectors provide sufficient coverage for all signal types

**Phase 1: Signal Consolidation** ✅ COMPLETE

- [x] Implemented `utils/signal_consolidator.py` with ConsolidatedSignal dataclass
- [x] Source priority for company_name (Companies House > SEC > Crunchbase > etc.)
- [x] Conflict detection for different company names
- [x] Description aggregation from raw_data
- [x] Social proof aggregation (stars, votes, upvotes)
- [x] Founding date extraction (earliest from raw_data)
- [x] Why now aggregation
- [x] Weighted confidence calculation
- [x] Pipeline integration after grouping, before verification gate
- [x] Metrics: signals_consolidated, conflicts_detected
- [x] 34 tests (25 unit + 9 integration)

**Phase 2: Enrichment Boost Integration** ✅ COMPLETE

- [x] Created `utils/enrichment_boost.py` with EnrichmentBoostCalculator
- [x] Threshold-based scoring (validated by MCDM/SAW research):
  - company_age > 2yr: +0.03
  - company_age > 1yr: +0.02
  - stars > 1000 OR upvotes > 200: +0.02
  - stars > 500 OR upvotes > 100: +0.01
  - Max total enrichment boost: 0.05
- [x] Added enrichment_boost parameter to VerificationGate
- [x] Wired EnrichmentBoostCalculator into pipeline
- [x] Added founding_date and social_proof_score to ProspectPayload
- [x] Added enrichment metrics: enrichment_boosts_applied, avg_enrichment_boost
- [x] 52 tests (25 calculator + 14 integration + 13 gate)

**Phase 3: Thesis Integration** ✅ COMPLETE

- [x] Rewrote `utils/thesis_matcher.py` with Consumer keywords (CPG, Health Tech, Travel, Marketplace)
- [x] Added `thesis_classifications` table (migration 5) with full audit trail
- [x] Created `utils/thesis_filter.py` combining keyword + LLM classification
- [x] Integrated thesis filter into pipeline with routing:
  - QUALIFIED: thesis_fit >= 0.3, continues to verification gate
  - HELD: thesis_fit < 0.3, awaits batch review
  - REJECTED: category == "excluded", filtered out
- [x] Confidence adjustments from keyword matching:
  - HIGH fit (≥0.7): +0.08 confidence
  - LOW fit (<0.4): -0.08 confidence
  - Negative keywords: -0.12 penalty
- [x] Added CLI dashboard commands:
  - `pipeline status` - overview of signal counts by status
  - `pipeline qualified` - list signals ready for push
  - `pipeline push --confirm` - export to Notion (user-triggered)
- [x] Created `utils/competitor_detector.py` with `config/portfolio.json`
- [x] Competitor detection wired into pipeline (flags, doesn't auto-reject)
- [x] 110 tests (23 matcher + 36 filter + 17 storage + 17 integration + 8 competitor + 9 CLI)

Exit criteria met:
- ✓ Thesis factors into routing decisions
- ✓ Low-fit signals held (not pushed)
- ✓ Classifications persisted in DB
- ✓ User controls push action via CLI

**Phase 4: Exit Predictor MVP** ✅ COMPLETE

- [x] Created `utils/exit_predictor.py` with ExitPredictor class
- [x] Heuristic weighted scoring (academic-validated weights)
- [x] Component scores: thesis_fit, founder_score, traction_score, funding_score, velocity_score, age_score
- [x] Stubbed values: investor_centrality=0.5, patent_count=0 (for Phase 2/3)
- [x] Database storage with migration 6 (exit_predictions table)
- [x] Nightly batch job for percentile ranking (`utils/exit_predictor_batch.py`)
- [x] Pipeline integration (after verification gate, before Notion push)
- [x] Feature-flagged via `ENABLE_EXIT_PREDICTOR` env var (default: false)
- [x] 77 tests (62 predictor + 15 storage/batch)

Exit criteria met:
- ✓ Heuristic exit prediction scoring working
- ✓ Predictions stored in database
- ✓ Percentile ranking via nightly batch
- ✓ Non-blocking pipeline integration

**Phase 5: Collector Evaluation** ✅ COMPLETE

Problem: Proposed collectors may not have accessible APIs.

- [x] Research & document API availability:
  - Wellfound: No public API, third-party scrapers deprecated → **ABANDON**
  - App Store: iTunes Search lacks "new apps" endpoint, App Store Connect only for own apps → **DEFER**
  - Play Store: No official API, scraping violates ToS, 3rd-party $25K+/year → **DEFER**
  - Press releases: Enterprise pricing, RSS feeds already in `rss_feeds.py` → **ABANDON**
- [x] Cost-benefit analysis completed for all options
- [x] Research doc created: `docs/collector-evaluation.md`
- [x] No new collectors to build (all abandoned or deferred)

Exit criteria met:
- ✓ All 4 collectors researched with BUILD/DEFER/ABANDON decisions
- ✓ Research doc with detailed rationale at `docs/collector-evaluation.md`
- ✓ Alternative approaches documented (existing collectors provide coverage)

**Dependency Order:** Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 (sequential)

**Key Risks:**
| Risk | Mitigation |
|------|------------|
| Merge logic corrupts data | Preserve originals, extensive tests |
| Canonical key collision | Conflict detection, human review queue |
| Thesis rejection too aggressive | Start with HOLD not REJECT, tune thresholds |
| New collector APIs unavailable | Research FIRST, commit to building LAST |

---

## Previous Sprint: Operational Excellence ✅ COMPLETE

**Phase 1: Automated Monitoring** ✅
- [x] Auto-trigger SignalHealthMonitor after pipeline runs (pipeline.py:645)
- [x] Wire Slack alerts to health anomalies (pipeline.py:1049-1065)
- [x] Add pipeline run metrics/telemetry

**Phase 2: Code Cleanup** ✅
- [x] Remove deprecated v1 files (notion_connector.py, verification_gate.py)
- [x] Complete process_pending_with_gating() in SignalProcessor (4 tests)

**Phase 3: Feature Enablement** ✅
- [x] Wire EntityResolver into processing flow (integrated, feature-flagged via ENABLE_ENTITY_RESOLVER)
- [x] Wire SourceAssetStore into collection flow (integrated, feature-flagged via ENABLE_ASSET_STORE)
- Note: Both components are fully wired but disabled by default; enable via environment variables

**Phase 4: New Collectors** ✅
- [x] Add LinkedIn collector (22 tests, uses Proxycurl API)
- [x] Add Crunchbase collector (26 tests, uses Crunchbase API)

---

## Previous Sprint: Production Hardening ✅ COMPLETE

**Phase 1: Quick Wins** ✅
- [x] Suppression cache warmup on pipeline init
- [x] Health check CLI command (comprehensive: DB, APIs, anomaly detection)
- [x] Wire up SignalHealthMonitor (integrated in health command)

**Phase 2: Collector Hardening** ✅
- [x] Centralized retry strategy module (18 tests)
- [x] Per-API rate limiter (16 tests)
- [x] Add retry to all 10 collectors

**Phase 3: BaseCollector Refactor** ✅
- [x] Migrate job_postings.py
- [x] Migrate github_activity.py

**Phase 4: Test Coverage** ✅ (445 tests passing)
- [x] Tests for github.py, product_hunt.py, arxiv.py, uspto.py
- [x] Consumer module tests (6 test files, 80+ tests)

---

## Previous Sprint: Storage & Collectors ✅ COMPLETE

- [x] Fix Notion status strings
- [x] Implement canonical key system
- [x] Add anti-inflation scoring
- [x] Add hard kill signals
- [x] Schema preflight validation
- [x] Build internal MCP server
- [x] Add SEC EDGAR collector
- [x] Add GitHub collector
- [x] Create .claude/agents/ structure (7 agents)
- [x] Create .claude/skills/ structure (7 skills)
- [x] Build signal storage layer (SQLite)
- [x] Integrate storage with collectors (BaseCollector class)
- [x] Build Companies House collector
- [x] Build Domain WHOIS collector
- [x] Create push-to-notion workflow (NotionPusher)
- [x] Add suppression cache sync job (SuppressionSync)
- [x] Create pipeline orchestrator (DiscoveryPipeline)
