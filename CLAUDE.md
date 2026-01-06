# Discovery Engine

Automated deal sourcing system for Press On Ventures (early-stage VC).

## Quick Context

**Fund Focus:** Healthtech, Cleantech, AI Infrastructure | Pre-Seed to Seed+ | $500K-$3M checks | US/UK

**What This Does:**
1. Collects signals (GitHub spikes, incorporations, domain registrations, SEC filings)
2. Ranks by thesis fit with multi-source verification
3. Pushes qualified prospects to Notion CRM
4. Maintains suppression to avoid duplicates

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
| `collectors/*.py` | Signal collectors (github, sec_edgar, companies_house, domain_whois) |
| `storage/signal_store.py` | SQLite storage for signals & suppression cache |
| `discovery_engine/mcp_server.py` | Internal MCP server (5 prompts, 3 tools) |
| `connectors/notion_connector_v2.py` | Notion integration (use v2, not v1) |
| `verification/verification_gate_v2.py` | Signal verification (use v2) |
| `utils/canonical_keys.py` | Multi-candidate deduplication |

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

# Run canonical key tests
python utils/canonical_keys.py

# Test signal storage
python storage/test_signal_store.py
```

## Current Sprint: Storage & Collectors ✅ COMPLETE

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

- Don't use `notion_connector.py` (v1) - has wrong status strings
- Don't use `verification_gate.py` (v1) - routes to non-existent statuses
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
```
