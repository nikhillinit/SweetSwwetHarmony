# Discovery Engine

Automated deal sourcing system for Press On Ventures (early-stage VC).

## Quick Context

**Fund Focus:** Consumer | Pre-Seed to Series A | US/UK

**Thesis Categories:**
- **Consumer CPG**: Food, beverage, snacks, beauty, personal care, household products
- **Consumer Health Tech**: Fitness apps, wellness, mental health, supplements, wearables
- **Travel & Hospitality**: Travel booking, hospitality tech, restaurants, experiences
- **Consumer Marketplaces**: Consumer-facing two-sided markets

**Exclusions:** B2B/Enterprise, developer tools, crypto/Web3, services/agencies, Series B+, hardware-only

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

## Current Sprint: Operational Excellence 🚧 IN PROGRESS

**Phase 1: Automated Monitoring** ✅
- [x] Auto-trigger SignalHealthMonitor after pipeline runs (pipeline.py:645)
- [x] Wire Slack alerts to health anomalies (pipeline.py:1049-1065)
- [ ] Add pipeline run metrics/telemetry

**Phase 2: Code Cleanup** ✅
- [x] Remove deprecated v1 files (notion_connector.py, verification_gate.py)
- [x] Complete process_pending_with_gating() in SignalProcessor (4 tests)

**Phase 3: Feature Enablement** (requires architecture planning)
- [ ] Wire EntityResolver into processing flow (initialized but not called)
- [ ] Wire SourceAssetStore into collection flow (initialized but not called)
- Note: Components exist but integration points need design work

**Phase 4: New Collectors** ✅
- [x] Add LinkedIn collector (22 tests, uses Proxycurl API)
- [x] Add Crunchbase collector (26 tests, uses Crunchbase API)

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
DISCOVERY_DB_PATH=signals.db (default)
```
