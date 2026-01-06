# Discovery Engine: Recommended Plugins, Agents & Skills

## Overview

This document outlines the complete ecosystem of tools, agents, and skills recommended for the Discovery Engine. Organized by priority and implementation phase.

---

## MCP Servers (Plugins)

### Phase 1: Core Infrastructure (Week 1-2)

| MCP Server | Purpose | Priority | Notes |
|------------|---------|----------|-------|
| **discovery-engine** (internal) | All Discovery operations | 🔴 Critical | We build this - wraps all DB/API access |
| **@anthropic/mcp-server-filesystem** | Read project files | 🔴 Critical | Official, low-risk |
| **@anthropic/mcp-server-postgres** | Direct DB queries | 🟡 Medium | Read-only mode only |

### Phase 2: Data Sources (Week 3-4)

| MCP Server | Purpose | Priority | Notes |
|------------|---------|----------|-------|
| **companies-house-mcp** (build) | UK incorporation data | 🔴 Critical | Build minimal wrapper around API |
| **github-mcp** | Repository activity signals | 🟡 Medium | Can use official `@anthropic/mcp-server-github` |
| **whois-mcp** (build) | Domain registration dates | 🟢 Low | Simple RDAP API wrapper |

### Phase 3: Enrichment (Week 5+)

| MCP Server | Purpose | Priority | Notes |
|------------|---------|----------|-------|
| **crunchbase-mcp** (build) | Funding data enrichment | 🟡 Medium | Requires API access ($) |
| **linkedin-mcp** | Founder background | 🔴 Avoid | High legal/TOS risk |
| **clearbit-mcp** | Company enrichment | 🟢 Optional | Paid API |

### Phase 4: Observability

| MCP Server | Purpose | Priority | Notes |
|------------|---------|----------|-------|
| **@anthropic/mcp-server-sentry** | Error tracking, job monitoring | 🟡 Medium | Query failures from Claude |
| **postgres-readonly** | Analytics queries | 🟢 Low | Separate from write access |

---

## Claude Code Agents

### Core Agents

```
.claude/agents/
├── collector_specialist.md
├── ranking_specialist.md
├── crm_specialist.md
├── secops_governor.md
└── research_analyst.md
```

### 1. Collector Specialist

**Purpose:** Run and debug signal collectors

```markdown
# .claude/agents/collector_specialist.md

You are the Collector Specialist for Discovery Engine.

## Responsibilities
- Run signal collectors (GitHub, Companies House, WHOIS)
- Debug collection failures
- Validate data quality before storage
- Monitor rate limits and API quotas

## Tool Access
- /mcp__discovery-engine__run-collector
- /mcp__discovery-engine__check-rate-limits
- Read-only filesystem access
- Sentry error queries

## When to Invoke
- "Run the GitHub collector"
- "Why did the Companies House job fail?"
- "Check if we're hitting rate limits"
- "Validate the latest batch of signals"

## Behaviors
- Always use --dry-run first for new collectors
- Log all API errors to Sentry
- Alert if error rate exceeds 5%
- Never store PII in signal data
```

### 2. Ranking Specialist

**Purpose:** Score companies and explain ranking decisions

```markdown
# .claude/agents/ranking_specialist.md

You are the Ranking Specialist for Discovery Engine.

## Responsibilities
- Calculate confidence scores for prospects
- Explain why a company ranked high/low
- Tune ranking weights based on feedback
- Identify false positives/negatives

## Tool Access
- /mcp__discovery-engine__get-ranking-explanation
- /mcp__discovery-engine__search-founders
- /mcp__discovery-engine__get-company-signals
- Read-only Weaviate access

## When to Invoke
- "Why did this company score so high?"
- "Find similar companies to X"
- "Which signals contributed most?"
- "This was a false positive - how do we fix?"

## Behaviors
- Always show confidence breakdown
- Reference specific signals, not vague scores
- Suggest weight adjustments when patterns emerge
- Track ranking accuracy over time
```

### 3. CRM Specialist

**Purpose:** Manage Notion pipeline and sync

```markdown
# .claude/agents/crm_specialist.md

You are the CRM Specialist for Discovery Engine.

## Responsibilities
- Push qualified prospects to Notion
- Sync suppression list from Notion
- Resolve duplicate deals
- Track deal progression

## Tool Access
- /mcp__discovery-engine__push-to-notion
- /mcp__discovery-engine__sync-suppression-cache
- /mcp__discovery-engine__check-suppression
- Notion API (via internal MCP server only)

## When to Invoke
- "Push this company to Notion"
- "Is this company already in our pipeline?"
- "Sync the suppression list"
- "Merge these duplicate deals"

## Behaviors
- Always check suppression before pushing
- Use dry-run for batch operations
- Never overwrite user-edited fields
- Log all Notion operations for audit
```

### 4. SecOps Governor

**Purpose:** Security oversight and access control

```markdown
# .claude/agents/secops_governor.md

You are the SecOps Governor for Discovery Engine.

## Responsibilities
- Maintain MCP server allowlist
- Audit credential scope and rotation
- Review new tool/server requests
- Monitor for suspicious patterns

## Tool Access
- Read-only filesystem (.mcp.json, .env.example)
- /mcp__discovery-engine__list-credentials (masked)
- /mcp__discovery-engine__audit-log
- NO write access to anything

## When to Invoke
- Before adding any new MCP server
- When reviewing credential changes
- During security audits
- If unusual tool usage detected

## Behaviors
- Default deny for new servers
- Require justification for write access
- Flag any credential in logs
- Weekly audit of access patterns
```

### 5. Research Analyst

**Purpose:** Deep-dive on specific companies/founders

```markdown
# .claude/agents/research_analyst.md

You are the Research Analyst for Discovery Engine.

## Responsibilities
- Deep research on high-priority prospects
- Verify founder backgrounds
- Assess competitive landscape
- Generate investment memos

## Tool Access
- Web search (with rate limits)
- /mcp__discovery-engine__get-company-signals
- /mcp__discovery-engine__get-ranking-explanation
- Read-only Notion access

## When to Invoke
- "Research this founder's background"
- "What's the competitive landscape for X?"
- "Generate a one-pager on this company"
- "Verify these claims about the founder"

## Behaviors
- Cite all sources
- Flag unverifiable claims
- Note potential conflicts of interest
- Output structured memos, not chat
```

---

## Skills (Instruction Bundles)

### Core Skills

```
.claude/skills/
├── founder_evaluation.md
├── thesis_matching.md
├── signal_quality.md
├── ranking_explanation.md
├── investment_memo.md
└── competitive_analysis.md
```

### 1. Founder Evaluation

```markdown
# .claude/skills/founder_evaluation.md

## When to Use
When evaluating a founder's background and potential.

## Key Signals (Positive)
- Previous successful exit (2x+ return)
- Technical expertise matching problem space
- Industry experience in target market
- Strong network (advisors, investors)
- Repeat founder

## Key Signals (Negative)
- No relevant domain experience
- History of failed companies (without learnings)
- Currently employed at big tech (< 6 months since departure)
- No technical cofounder for technical product

## Scoring Framework
- 0.8-1.0: Serial founder with exit, domain expert
- 0.6-0.8: First-time founder with strong background
- 0.4-0.6: Promising but unproven
- 0.2-0.4: Significant gaps
- 0.0-0.2: Major red flags

## Output Format
Always produce a structured assessment:
1. Background summary (2-3 sentences)
2. Strengths (bullet list)
3. Concerns (bullet list)
4. Confidence score with justification
```

### 2. Thesis Matching

```markdown
# .claude/skills/thesis_matching.md

## When to Use
When determining if a company matches Press On Ventures' investment thesis.

## Thesis Criteria (Press On Ventures)

### Verticals
- **Healthtech**: Digital health, biotech tools, care delivery
- **Cleantech**: Climate solutions, sustainability, energy
- **AI Infrastructure**: Dev tools, MLOps, data infrastructure

### Stage
- Pre-Seed to Seed+
- $500K - $3M check sizes
- First institutional round preferred

### Geography
- US and UK primary
- Remote-first teams OK
- Must have US market focus

### Signals of Fit
- Founder has domain expertise in vertical
- Clear technical differentiation
- Large TAM ($1B+)
- Capital-efficient model

### Signals of Misfit
- Outside core verticals
- Series A+ (too late)
- Hardware-heavy (outside expertise)
- B2C consumer (outside focus)

## Output Format
1. Vertical match: Yes/No/Partial + reasoning
2. Stage match: Yes/No + details
3. Thesis fit score: 0.0-1.0
4. Key concerns for diligence
```

### 3. Signal Quality Assessment

```markdown
# .claude/skills/signal_quality.md

## When to Use
When evaluating the quality and reliability of a detected signal.

## Signal Tiers

### Tier 1: High Confidence (0.8+)
- Incorporation filing (official registry)
- Funding announcement (press release + Crunchbase)
- Patent filing (USPTO/EPO)

### Tier 2: Medium Confidence (0.5-0.8)
- GitHub repository spike (needs verification)
- Domain registration (could be defensive)
- Job postings (could be existing company)

### Tier 3: Low Confidence (0.2-0.5)
- Social media announcement
- Founder LinkedIn update
- Conference presentation

### Tier 4: Weak (< 0.2)
- Rumor/speculation
- Unverified news
- Name-only matches

## Cross-Verification Requirements
- Tier 1: Standalone OK
- Tier 2: Needs 1 additional signal
- Tier 3: Needs 2 additional signals
- Tier 4: Needs human review

## Decay Rates
- Incorporation: 365-day half-life
- Funding: 180-day half-life
- GitHub spike: 14-day half-life
- Social: 30-day half-life
```

### 4. Investment Memo Generation

```markdown
# .claude/skills/investment_memo.md

## When to Use
When generating a structured investment memo for partner review.

## Memo Template

### Executive Summary (3-4 sentences)
- What they do
- Why now
- Why this team
- Key risk

### Company Overview
- Founded: [date]
- Location: [city, country]
- Stage: [Pre-Seed/Seed/etc.]
- Raising: [$amount] at [$valuation]

### Team
- Founder(s): [names and backgrounds]
- Key hires: [if any]
- Advisors: [if notable]

### Product & Market
- Problem: [1 paragraph]
- Solution: [1 paragraph]
- Differentiation: [bullets]
- TAM/SAM/SOM: [with sources]

### Traction
- Users/customers: [numbers]
- Revenue: [if applicable]
- Growth: [metrics]

### Competition
- Direct: [list with notes]
- Indirect: [list with notes]
- Why they win: [bullets]

### Investment Thesis
- Why invest: [3 bullets]
- Key risks: [3 bullets]
- What we need to believe: [2-3 statements]

### Recommendation
- [ ] Pass
- [ ] Tracking
- [ ] Take meeting
- [ ] Move to diligence

### Sources
[List all sources with links]
```

---

## Recommended External Integrations

### Data Providers

| Provider | Purpose | Cost | Priority |
|----------|---------|------|----------|
| **Companies House API** | UK incorporations | Free | 🔴 Critical |
| **GitHub API** | Repo activity | Free (rate limited) | 🔴 Critical |
| **RDAP/WHOIS** | Domain registration | Free | 🟡 Medium |
| **Crunchbase** | Funding data | $$$$ | 🟡 Medium |
| **PitchBook** | Market data | $$$$ | 🟢 Nice-to-have |
| **Clearbit** | Company enrichment | $$ | 🟢 Nice-to-have |
| **Apollo.io** | Contact data | $$ | 🟢 Nice-to-have |

### Observability Stack

| Tool | Purpose | Notes |
|------|---------|-------|
| **Sentry** | Error tracking | Has official MCP server |
| **Grafana** | Metrics dashboards | For collector health |
| **PagerDuty** | Alerting | Critical job failures |

### Workflow Automation

| Tool | Purpose | Notes |
|------|---------|-------|
| **n8n** | Polling Notion for status changes | Self-hosted, already planned |
| **GitHub Actions** | CI/CD for collectors | Free for public repos |
| **Railway/Render** | Cron job hosting | Low-cost, managed |

---

## Implementation Checklist

### Week 1-2: Foundation
- [ ] Build internal discovery-engine MCP server
- [ ] Set up collector_specialist agent
- [ ] Create signal_quality skill
- [ ] Configure filesystem MCP

### Week 3-4: Data Collection
- [ ] Build companies-house-mcp wrapper
- [ ] Configure github MCP
- [ ] Create ranking_specialist agent
- [ ] Add thesis_matching skill

### Week 5-6: CRM Integration
- [ ] Create crm_specialist agent
- [ ] Add founder_evaluation skill
- [ ] Configure n8n for Notion polling
- [ ] Test suppression flow end-to-end

### Week 7-8: Polish
- [ ] Add secops_governor agent
- [ ] Create investment_memo skill
- [ ] Add Sentry MCP for observability
- [ ] Security audit of all MCP servers

### Post-Launch
- [ ] Add research_analyst agent
- [ ] Integrate Crunchbase (if budget allows)
- [ ] Build competitive_analysis skill
- [ ] Weekly ranking accuracy reviews

---

## Security Considerations

### MCP Server Allowlist

Only these servers are approved without additional review:

```json
{
  "approved": [
    "discovery-engine",
    "@anthropic/mcp-server-filesystem",
    "@anthropic/mcp-server-postgres",
    "@anthropic/mcp-server-github",
    "@anthropic/mcp-server-sentry"
  ],
  "requires_review": [
    "Any third-party MCP server",
    "Any server with write access",
    "Any server accessing PII"
  ],
  "denied": [
    "Browser automation (Puppeteer, Playwright)",
    "LinkedIn scrapers",
    "Any server not on allowlist"
  ]
}
```

### Credential Scope

| Credential | Scope | Rotation |
|------------|-------|----------|
| NOTION_API_KEY | Single database (Venture Pipeline) | 90 days |
| GITHUB_TOKEN | Public repos only | 90 days |
| DATABASE_URL | Read-only for Claude | Never (read-only) |
| COMPANIES_HOUSE_API_KEY | Read-only | 365 days |
| SENTRY_AUTH_TOKEN | Read-only | 90 days |

---

## Summary: What to Build vs Buy

| Component | Build | Buy/Use Existing | Notes |
|-----------|-------|------------------|-------|
| Internal MCP Server | ✅ | — | Core to security model |
| Companies House wrapper | ✅ | — | Simple API, no good MCP exists |
| GitHub integration | — | ✅ @anthropic | Official server |
| Notion integration | ✅ | — | Custom for your schema |
| Sentry integration | — | ✅ @anthropic | Official server |
| Crunchbase wrapper | ✅ | — | If you get API access |
| Ranking engine | ✅ | — | Core IP |
| Vector search | — | ✅ Weaviate | Managed or self-hosted |
| Workflow automation | — | ✅ n8n | Self-hosted |
