# Discovery Engine: Complete Tool Reference Card

## At a Glance

### MCP Servers (19 total)

| Category | Server | Status | Priority |
|----------|--------|--------|----------|
| **Core** | discovery-engine (internal) | Build | 🔴 Critical |
| **Core** | @anthropic/filesystem | Use | 🔴 Critical |
| **Core** | @anthropic/postgres | Use | 🟡 Medium |
| **Data** | companies-house-mcp | Build | 🔴 Critical |
| **Data** | @anthropic/github | Use | 🔴 Critical |
| **Data** | whois-mcp | Build | 🟡 Medium |
| **Data** | sec-edgar-mcp | Build | 🔴 High |
| **Data** | uspto-patents-mcp | Build | 🟢 Low |
| **Data** | product-hunt-mcp | Build | 🟡 Medium |
| **Data** | hacker-news-mcp | Build | 🟡 Medium |
| **Data** | arxiv-mcp | Build | 🟢 Low |
| **Data** | yc-batch-tracker-mcp | Build | 🟡 Medium |
| **Data** | job-postings-mcp | Build | 🟢 Low |
| **Enrich** | crunchbase-mcp | Build | 🟡 Medium |
| **Enrich** | apollo-mcp | Build | 🟢 Low |
| **Enrich** | similar-web-mcp | Build | 🟢 Low |
| **Ops** | @anthropic/sentry | Use | 🟡 Medium |
| **Intel** | pitchbook-mcp | Build | 🟡 Medium |
| **Intel** | cbinsights-mcp | Build | 🟢 Low |

### Agents (10 total)

| Agent | Purpose | Phase |
|-------|---------|-------|
| **collector_specialist** | Run signal collectors | 1 |
| **ranking_specialist** | Score and explain rankings | 1 |
| **crm_specialist** | Manage Notion pipeline | 1 |
| **secops_governor** | Security oversight | 1 |
| **research_analyst** | Deep company research | 2 |
| **due_diligence_coordinator** | Verification workflows | 2 |
| **market_intelligence** | TAM/competitive analysis | 2 |
| **outreach_coordinator** | Founder outreach | 3 |
| **portfolio_monitor** | Track investments | 3 |
| **lp_reporting** | Investor reports | 4 |

### Skills (10 total)

| Skill | Purpose | Phase |
|-------|---------|-------|
| **signal_quality** | Assess signal reliability | 1 |
| **thesis_matching** | Match to investment thesis | 1 |
| **founder_evaluation** | Assess founders | 1 |
| **ranking_explanation** | Explain scores | 1 |
| **investment_memo** | Generate memos | 2 |
| **red_flag_detection** | Identify issues | 2 |
| **technical_due_diligence** | Tech assessment | 2 |
| **reference_check** | Structured references | 3 |
| **valuation_benchmarking** | Price assessment | 3 |
| **competitive_response** | Portfolio defense | 4 |

---

## Implementation Phases

### Phase 1: Foundation (Weeks 1-2)
```
MCP Servers:
✅ discovery-engine (internal)
✅ @anthropic/filesystem
✅ @anthropic/postgres (read-only)

Agents:
✅ collector_specialist
✅ ranking_specialist
✅ crm_specialist
✅ secops_governor

Skills:
✅ signal_quality
✅ thesis_matching
✅ founder_evaluation
✅ ranking_explanation
```

### Phase 2: Data & Diligence (Weeks 3-4)
```
MCP Servers:
✅ companies-house-mcp
✅ @anthropic/github
✅ sec-edgar-mcp
✅ yc-batch-tracker-mcp

Agents:
✅ research_analyst
✅ due_diligence_coordinator
✅ market_intelligence

Skills:
✅ investment_memo
✅ red_flag_detection
✅ technical_due_diligence
```

### Phase 3: Enrichment & Outreach (Weeks 5-6)
```
MCP Servers:
✅ whois-mcp
✅ product-hunt-mcp
✅ hacker-news-mcp
✅ crunchbase-mcp (if budget)

Agents:
✅ outreach_coordinator
✅ portfolio_monitor

Skills:
✅ reference_check
✅ valuation_benchmarking
```

### Phase 4: Scale & Polish (Weeks 7-8)
```
MCP Servers:
✅ @anthropic/sentry
✅ arxiv-mcp
✅ uspto-patents-mcp

Agents:
✅ lp_reporting

Skills:
✅ competitive_response
✅ fund_strategy_alignment
```

---

## High-Value Quick Wins

### This Week
1. **SEC Form D API** - Free, shows real funding rounds
2. **Red Flag Detection skill** - Prevent bad investments
3. **YC batch tracker** - Pre-vetted deal flow
4. **Reference Check template** - Standardize diligence

### This Month
1. **Due Diligence Coordinator** - Automate verification
2. **Market Intelligence agent** - Faster analysis
3. **Outreach Coordinator** - Better founder contact

### This Quarter
1. **Portfolio Monitor** - Proactive management
2. **LP Reporting automation** - Save 2-3 days/quarter
3. **Full MCP security audit**

---

## Cost Estimates

| Component | Monthly Cost | Notes |
|-----------|--------------|-------|
| Core infrastructure | $0 | All free APIs |
| Crunchbase API | $500-2,000 | If needed |
| PitchBook | $1,000+ | Enterprise pricing |
| Apollo.io | $100-500 | Contact enrichment |
| Clearbit | $200-1,000 | Company enrichment |
| Sentry | $0-50 | Free tier usually enough |
| **Total (minimal)** | **$0** | Free APIs only |
| **Total (enriched)** | **$1,000-3,000** | With paid data |

---

## File Locations

```
discovery_engine/
├── connectors/
│   └── notion_connector_v2.py     # Corrected Notion integration
├── verification/
│   └── verification_gate_v2.py    # Corrected routing
├── utils/
│   └── canonical_keys.py          # Multi-candidate key generation
├── docs/
│   ├── MCP_ARCHITECTURE.md        # Security & MCP design
│   ├── PLUGINS_AGENTS_SKILLS.md   # Core recommendations
│   └── EXTENDED_RECOMMENDATIONS.md # Extended tools & agents

.claude/
├── agents/
│   ├── collector_specialist.md
│   ├── ranking_specialist.md
│   ├── crm_specialist.md
│   ├── secops_governor.md
│   ├── research_analyst.md
│   ├── due_diligence_coordinator.md
│   ├── market_intelligence.md
│   ├── outreach_coordinator.md
│   ├── portfolio_monitor.md
│   └── lp_reporting.md
├── skills/
│   ├── signal_quality.md
│   ├── thesis_matching.md
│   ├── founder_evaluation.md
│   ├── ranking_explanation.md
│   ├── investment_memo.md
│   ├── red_flag_detection.md
│   ├── technical_due_diligence.md
│   ├── reference_check.md
│   ├── valuation_benchmarking.md
│   └── competitive_response.md
└── .mcp.json                      # MCP server configuration
```

---

## Next Steps

1. **Review this reference card** - Confirm priorities match your needs
2. **Set up Phase 1 infrastructure** - Internal MCP server first
3. **Add SEC Form D monitoring** - Quick win, high value
4. **Create `.claude/` directory structure** - Agents and skills
5. **Security audit** - Review credential scope before launch
