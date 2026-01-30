# Discovery Engine

Automated deal sourcing system for Press On Ventures (early-stage VC).

## Quick Context

**Fund Focus:** Consumer | Pre-Seed to Series A | US/UK

> **Investment Thesis (One-Liner):** Press On Ventures invests in Pre-Seed to Series A consumer companies in CPG (food, beverage, beauty), health tech (fitness, wellness, mental health), travel & hospitality, and consumer marketplaces — excluding B2B, enterprise SaaS, developer tools, crypto, and hardware.

**Thesis Categories:**
- **Consumer CPG**: Food, beverage, snacks, beauty, personal care, household products
- **Consumer Health Tech**: Fitness apps, wellness, mental health, supplements, wearables
- **Travel & Hospitality**: Travel booking, hospitality tech, restaurants, experiences
- **Consumer Marketplaces**: Consumer-facing two-sided markets

**Exclusions:** B2B/Enterprise, developer tools, crypto/Web3, cleantech/climate, services/agencies, Series B+, hardware-only

**What This Does:**
1. Collects signals (GitHub, incorporations, domains, SEC filings, job postings, Product Hunt, Hacker News, ArXiv, patents)
2. Filters by thesis fit using two-stage classification (keyword pre-filter + Gemini LLM)
3. Pushes qualified prospects to Notion CRM
4. Maintains suppression to avoid duplicates
5. Monitors signal health and detects anomalies

## Critical: Notion Schema

**Statuses (EXACT strings - note the typo in Dilligence):**
- Source, Initial Meeting / Call, Dilligence, Tracking, Committed, Funded, Passed, Lost

**Stages:**
- Pre-Seed, Seed, Seed +, Series A, Series B, Series C, Series D

**New properties needed:**
- Discovery ID (Text)
- Canonical Key (Text) - e.g., "domain:acme.ai"
- Confidence Score (Number)
- Signal Types (Multi-select)
- Why Now (Text)

## Key Files

| File | Purpose |
|------|---------|
| `run_pipeline.py` | **Main CLI** - Run collectors, process, sync, stats |
| `workflows/pipeline.py` | Pipeline orchestrator (DiscoveryPipeline class) |
| `workflows/notion_pusher.py` | Batch push processor with confidence routing |
| `workflows/suppression_sync.py` | Sync Notion → local cache |
| `collectors/base.py` | BaseCollector with storage integration |
| `collectors/*.py` | Signal collectors (see Collectors section below) |
| `storage/signal_store.py` | SQLite storage for signals & suppression cache |
| `discovery_engine/mcp_server.py` | Internal MCP server (5 prompts, 3 tools) |
| `connectors/notion_connector_v2.py` | Notion integration (use v2, not v1) |
| `verification/verification_gate_v2.py` | Signal verification (use v2) |
| `utils/canonical_keys.py` | Multi-candidate deduplication |
| `utils/thesis_matcher.py` | Keyword-based thesis fit scoring (stage 1) |
| `utils/signal_health.py` | Signal quality and anomaly detection |
| `consumer/thesis_filter/llm_classifier.py` | Gemini LLM thesis classification (stage 2) |
| `utils/exit_predictor.py` | Exit prediction scoring (heuristic MVP) |
| `utils/exit_predictor_batch.py` | Nightly batch job for percentile ranking |

## Collectors

| Collector | Source | Signal Strength | API Key |
|-----------|--------|-----------------|---------|
| `github.py` | GitHub trending repos | 0.5-0.7 | GITHUB_TOKEN |
| `github_activity.py` | Founder GitHub activity | 0.5-0.7 | GITHUB_TOKEN |
| `sec_edgar.py` | SEC Form D filings | 0.6-0.8 | None |
| `companies_house.py` | UK incorporations | 0.6-0.8 | COMPANIES_HOUSE_API_KEY |
| `domain_whois.py` | Domain registrations | 0.4-0.6 | None |
| `job_postings.py` | Greenhouse/Lever ATS | 0.7-0.95 | None |
| `product_hunt.py` | Product Hunt launches | 0.5-0.7 | PH_API_KEY |
| `hacker_news.py` | HN mentions/Show HN | 0.5-0.7 | None |
| `arxiv.py` | ArXiv research papers | 0.3-0.5 | None |
| `uspto.py` | USPTO patent filings | 0.4-0.6 | None |
| `linkedin.py` | LinkedIn company/jobs | 0.5-0.8 | PROXYCURL_API_KEY |
| `crunchbase.py` | Crunchbase funding data | 0.6-0.9 | CRUNCHBASE_API_KEY |
| `opencorporates.py` | Global incorporations | 0.6-0.75 | OPENCORPORATES_API_KEY |
| `news_api.py` | GNews consumer news | 0.4-0.75 | GNEWS_API_KEY |
| `rss_feeds.py` | TechCrunch, PR Newswire, etc. | 0.35-0.65 | None |
| `changedetection.py` | Website change monitoring | 0.5-0.85 | CHANGEDETECTION_API_KEY |

## Architecture Rules

1. **All external access through internal MCP server** - No direct DB/API from Claude
2. **Canonical keys for dedupe** - Works for stealth companies without websites
3. **Multi-source verification** - 2+ sources = "Source", 1 source = "Tracking"
4. **Hard kill signals** - company_dissolved = immediate reject
5. **Schema preflight** - Validate Notion properties before operations

## Routing Logic

```
HIGH confidence (0.7+) + multi-source → Status: "Source"
MEDIUM confidence (0.4-0.7) → Status: "Tracking"
LOW confidence (<0.4) → Don't push (hold for batch review)
Hard kill signal → Reject entirely
```

## Commands

```bash
# Run full discovery pipeline
python run_pipeline.py full --collectors github,sec_edgar --dry-run

# Run specific collectors only
python run_pipeline.py collect --collectors companies_house,domain_whois

# Process pending signals (push to Notion)
python run_pipeline.py process --dry-run

# Sync suppression cache from Notion
python run_pipeline.py sync

# View pipeline stats
python run_pipeline.py stats

# View pipeline metrics with collector breakdown
python run_pipeline.py metrics
python run_pipeline.py metrics --limit 10 --collector github

# Health check (DB, APIs, anomaly detection)
python run_pipeline.py health
python run_pipeline.py health --json  # Machine-readable output

# Run canonical key tests
python utils/canonical_keys.py

# Test signal storage (manual tests)
python storage/manual_test_signal_store.py
```

## Development Practices (Superpowers-Inspired)

### TDD Enforcement (The Iron Law)
Write failing tests first, then minimal code to pass them.

**RED-GREEN-REFACTOR Cycle:**
1. Write failing test → 2. Verify RED → 3. Implement minimal code → 4. Verify GREEN → 5. Commit

**Red Flags Requiring Restart:**
- Code written before failing tests
- Tests passing immediately upon writing
- Tests marked for "later" addition

### Git Worktrees
- Worktree directory: `.worktrees/` (in .gitignore)
- Create isolated workspace: `git worktree add .worktrees/<feature> -b <branch>`
- Run baseline tests before claiming readiness

### Code Review Checkpoints
| Severity | Action |
|----------|--------|
| Critical | Fix immediately before progression |
| Important | Fix before proceeding |
| Minor | Document for later |

### Planning
- Plans stored in `docs/plans/YYYY-MM-DD-<feature>.md`
- Tasks should be 2-5 minutes each
- Explicit git commits after each task completion
- Follow DRY, YAGNI, TDD principles

---

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

## Don't Do

- Don't give Claude write DB credentials - read-only only
- Don't add Puppeteer/browser MCP - security risk
- Don't skip schema preflight - catches drift early

## Environment Variables Needed

```bash
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx
DATABASE_URL=postgresql://... (read-only)
GITHUB_TOKEN=ghp_xxx (public repos only)
COMPANIES_HOUSE_API_KEY=xxx
PH_API_KEY=xxx (Product Hunt API key)
GOOGLE_API_KEY=xxx (Gemini - free at aistudio.google.com/apikey)
OPENCORPORATES_API_KEY=xxx (free tier at opencorporates.com/api_accounts/new)
DISCOVERY_DB_PATH=signals.db (default)

# News collectors
GNEWS_API_KEY=xxx (free tier at gnews.io - 100 requests/day)
RSS_FEEDS=https://... (optional, comma-separated custom RSS feed URLs)
RSS_CATEGORIES=startup,health_tech,cpg (optional, filter feed categories)

# Website change monitoring
CHANGEDETECTION_URL=https://your-instance.local (self-hosted changedetection.io)
CHANGEDETECTION_API_KEY=xxx (API key from changedetection.io settings)

# OpenAI Integration (for multi-LLM strategy iteration)
OPENAI_API_KEY=sk-xxx (get at platform.openai.com/api-keys)
```

---

## OpenAI/Codex Integration

Multi-LLM strategy iteration for thesis refinement using your ChatGPT Pro subscription.

### Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Claude Code   │────▶│  OpenAI/Codex   │
│  (Orchestrator) │◀────│  (Perspectives) │
└─────────────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│    Consensus    │
│   Synthesizer   │
└─────────────────┘
```

- **Claude Code** orchestrates all actions
- **OpenAI/Codex** provides alternative perspectives in sandbox
- **Consensus patterns** reduce hallucinations

### Key Files

| File | Purpose |
|------|---------|
| `integrations/maestro.py` | **Iterative consensus orchestrator** (Claude + Codex) |
| `integrations/codex_wrapper.py` | Codex CLI wrapper (sandbox execution) |
| `integrations/openai_mcp.py` | OpenAI MCP server (prompts + tools) |
| `integrations/strategy_iterator.py` | Legacy multi-LLM consensus |
| `scripts/setup_openai_integration.sh` | Setup and verification script |

### Setup

```bash
# 1. Run setup script
./scripts/setup_openai_integration.sh

# 2. (Required) Install Codex CLI with ChatGPT Pro
npm install -g @openai/codex
codex login
```

### Maestro Workflow (Iterative Consensus)

The Maestro pattern enables iterative collaboration:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  ┌──────────────┐    task     ┌──────────────┐              │
│  │ Claude Code  │────────────▶│  Codex CLI   │              │
│  │ (Orchestrator│             │  (Sandbox)   │              │
│  │  + Critic)   │◀────────────│              │              │
│  └──────────────┘   proposal  └──────────────┘              │
│         │                            ▲                       │
│         │ critique                   │                       │
│         └────────────────────────────┘                       │
│              (iterate until consensus)                       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Claude's critique focuses on:**
- **Feasibility**: Will this actually work? What assumptions are fragile?
- **Efficiency**: Is there a simpler/faster approach?
- **Sophistication**: What edge cases are missed? How to make it robust?

**Codex is instructed to:**
- Use existing Codex skills when helpful (`/edit`, `/review`, `/test`)
- Create new skills for reusable patterns
- Propose concrete, implementable solutions

### Usage

```bash
# CLI: Iterative collaboration
python -m integrations.maestro collaborate \
    "Improve thesis matcher false positive rate" \
    --context "Currently at 30% FP, mostly B2B tools slipping through" \
    --max-iterations 5

# CLI: Review with consensus
python -m integrations.maestro review collectors/github.py \
    --focus "rate limiting"

# Python: Direct usage
from integrations import Maestro

maestro = Maestro(max_iterations=5)
result = await maestro.collaborate(
    task="Reduce false positives in GitHub signals",
    context="30% FP rate, B2B tools passing thesis filter",
    context_files=["utils/thesis_matcher.py"]
)

print(f"State: {result.state}")
print(f"Iterations: {result.iterations}")
print(f"Skills used: {result.skills_employed}")
print(f"Final proposal:\n{result.final_proposal}")
```

### When Claude Should Use Maestro

When working on complex tasks, Claude should:
1. Send the task + context to Codex via Maestro
2. Receive Codex's proposal
3. **Critically evaluate** (not blindly accept):
   - What could fail? (feasibility)
   - What's overcomplicated? (efficiency)
   - What's missing? (sophistication)
4. Send critique back to Codex
5. Iterate until consensus or identify remaining disagreements
6. Present final agreed solution to user

### Benefits

- **No API costs** - Uses ChatGPT Pro subscription via Codex CLI
- **Sandbox isolation** - Codex runs in read-only mode
- **Iterative refinement** - Multiple rounds improve quality
- **Skill leverage** - Codex uses/creates skills for efficiency
- **Critical evaluation** - Claude scrutinizes, doesn't blindly accept
