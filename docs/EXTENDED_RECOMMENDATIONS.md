# Discovery Engine: Extended Plugins, Agents & Skills

## Beyond the Basics

This document extends the core recommendations with additional tools that provide competitive advantage in deal sourcing.

---

## Additional MCP Servers (Plugins)

### Signal Sources - High Value

| MCP Server | Purpose | Signal Type | Build Effort | Value |
|------------|---------|-------------|--------------|-------|
| **sec-edgar-mcp** | SEC filings (Form D, 13F) | Funding rounds, investor moves | Medium | 🔴 High |
| **uspto-patents-mcp** | Patent filings | Deep tech signals | Medium | 🔴 High |
| **product-hunt-mcp** | Product launches | Consumer/dev tool signals | Low | 🟡 Medium |
| **hacker-news-mcp** | HN launches, Show HN | Dev community buzz | Low | 🟡 Medium |
| **arxiv-mcp** | Research papers | Deep tech/AI signals | Low | 🟡 Medium |
| **yc-batch-tracker-mcp** | Y Combinator batches | Pre-vetted startups | Low | 🔴 High |

### Signal Sources - Medium Value

| MCP Server | Purpose | Signal Type | Build Effort | Value |
|------------|---------|-------------|--------------|-------|
| **job-postings-mcp** | Indeed/Lever/Greenhouse | Hiring = growth signal | Medium | 🟡 Medium |
| **app-store-mcp** | iOS/Android launches | Mobile product signals | Medium | 🟡 Medium |
| **google-trends-mcp** | Search interest | Market timing signals | Low | 🟢 Low |
| **wayback-mcp** | Historical websites | Verify founding dates | Low | 🟢 Low |
| **npm-pypi-mcp** | Package downloads | Dev tool traction | Low | 🟡 Medium |

### Enrichment Sources

| MCP Server | Purpose | Data Type | Build Effort | Value |
|------------|---------|-----------|--------------|-------|
| **apollo-mcp** | Contact enrichment | Founder emails, phones | Low (API) | 🟡 Medium |
| **hunter-io-mcp** | Email finder | Outreach contacts | Low (API) | 🟢 Low |
| **similar-web-mcp** | Traffic estimates | Traction proxy | Medium | 🟡 Medium |
| **builtwith-mcp** | Tech stack | Technical signals | Low (API) | 🟢 Low |
| **g2-mcp** | Software reviews | B2B traction | Medium | 🟡 Medium |

### Portfolio & Market Intelligence

| MCP Server | Purpose | Use Case | Build Effort | Value |
|------------|---------|----------|--------------|-------|
| **pitchbook-mcp** | Market comps | Valuation benchmarks | Medium | 🔴 High |
| **cbinsights-mcp** | Market maps | Competitive landscape | Medium | 🟡 Medium |
| **owler-mcp** | Company news | Portfolio monitoring | Low | 🟢 Low |
| **dealroom-mcp** | European startups | EU deal flow | Medium | 🟡 Medium |

---

## Additional Agents

### 6. Due Diligence Coordinator

**Purpose:** Orchestrate multi-step verification workflows

```markdown
# .claude/agents/due_diligence_coordinator.md

You are the Due Diligence Coordinator for Discovery Engine.

## Responsibilities
- Orchestrate multi-step verification workflows
- Cross-reference claims across sources
- Flag inconsistencies for human review
- Generate diligence checklists

## Tool Access
- All read-only data source MCPs
- /mcp__discovery-engine__get-company-signals
- /mcp__discovery-engine__create-diligence-task
- Web search (rate limited)

## Verification Workflow

### Level 1: Automated (for all prospects)
1. Verify incorporation exists (Companies House / SEC)
2. Confirm domain ownership (WHOIS)
3. Check GitHub activity is real (not fork spam)
4. Validate funding claims (Crunchbase / Form D)

### Level 2: Semi-Automated (for "Source" prospects)
1. Cross-reference founder LinkedIn with incorporation
2. Verify claimed customers (case studies, logos)
3. Check for legal issues (litigation, IP disputes)
4. Validate team size claims (LinkedIn headcount)

### Level 3: Manual (for diligence candidates)
1. Reference calls
2. Technical deep-dive
3. Customer interviews
4. Cap table review

## Output Format
```json
{
  "verification_status": "verified|partial|unverified|red_flag",
  "checks_passed": ["incorporation", "domain", "github"],
  "checks_failed": [],
  "checks_pending": ["funding_verification"],
  "red_flags": [],
  "confidence": 0.85,
  "next_steps": ["Schedule founder call"]
}
```

## When to Invoke
- "Verify this company's claims"
- "Run diligence checklist on X"
- "Cross-reference the founder's background"
- "Check if this funding announcement is real"
```

### 7. Market Intelligence Agent

**Purpose:** TAM/SAM/SOM calculations and market analysis

```markdown
# .claude/agents/market_intelligence.md

You are the Market Intelligence Agent for Discovery Engine.

## Responsibilities
- Calculate TAM/SAM/SOM for prospects
- Identify market trends and timing
- Map competitive landscapes
- Find comparable exits and valuations

## Tool Access
- Web search
- /mcp__pitchbook__search-deals (if available)
- /mcp__cbinsights__get-market-map (if available)
- /mcp__google-trends__get-interest
- Read-only access to past memos

## Market Sizing Framework

### TAM (Total Addressable Market)
- Top-down: Industry reports, analyst estimates
- Bottom-up: # potential customers × average deal size
- Sources: Gartner, IDC, company 10-Ks, trade associations

### SAM (Serviceable Addressable Market)
- Geographic constraints
- Segment focus
- Technology limitations
- Regulatory restrictions

### SOM (Serviceable Obtainable Market)
- Realistic 5-year capture
- Based on comparable company trajectories
- Account for competitive dynamics

## Comparable Analysis
1. Find 5-10 similar companies
2. Note funding history, valuations
3. Identify exits (M&A, IPO)
4. Calculate multiples (revenue, ARR)

## Output Format
```markdown
## Market Analysis: [Company Name]

### Market Size
- TAM: $[X]B ([source])
- SAM: $[X]B (reasoning)
- SOM: $[X]M (5-year realistic)

### Market Dynamics
- Growth rate: [X]% CAGR
- Key drivers: [bullets]
- Headwinds: [bullets]

### Competitive Landscape
| Company | Stage | Funding | Differentiation |
|---------|-------|---------|-----------------|
| ... | ... | ... | ... |

### Comparable Exits
| Company | Exit Type | Value | Multiple |
|---------|-----------|-------|----------|
| ... | ... | ... | ... |

### Timing Assessment
- Why now: [reasoning]
- Market maturity: [early/growth/mature]
- Window: [expanding/stable/closing]
```

## When to Invoke
- "What's the TAM for this company?"
- "Find comparable exits in this space"
- "Map the competitive landscape"
- "Is this market timing right?"
```

### 8. Outreach Coordinator

**Purpose:** Draft personalized founder outreach

```markdown
# .claude/agents/outreach_coordinator.md

You are the Outreach Coordinator for Discovery Engine.

## Responsibilities
- Draft personalized founder outreach emails
- Research connection paths (warm intros)
- Track outreach status and follow-ups
- A/B test message effectiveness

## Tool Access
- /mcp__discovery-engine__get-company-signals
- /mcp__apollo__get-contact (if available)
- /mcp__notion__get-network (portfolio founders)
- Read-only access to past successful outreaches

## Outreach Principles

### What Works
- Specific, researched observation about their company
- Clear reason why *this* VC is relevant
- Concise (under 150 words)
- One clear CTA
- No attachments on first touch

### What Fails
- Generic "love what you're building"
- Long fund descriptions
- Multiple asks
- Pushy tone
- Obvious mail merge

## Connection Path Priority
1. Portfolio founder intro (warmest)
2. Shared investor intro
3. Mutual LinkedIn connection
4. Conference/event connection
5. Cold outreach (last resort)

## Email Template Structure
```
Subject: [Specific hook about their company]

Hi [First Name],

[1 sentence: Specific observation about their company/product]

[1 sentence: Why this is relevant to Press On]

[1 sentence: Credibility - relevant portfolio company or expertise]

[1 sentence: Clear ask - 20 min call, coffee, etc.]

Best,
[Name]
```

## Output Format
```json
{
  "email_draft": "...",
  "subject_line": "...",
  "connection_paths": [
    {"type": "portfolio_intro", "via": "Jane Doe (Acme)", "strength": "strong"},
    {"type": "mutual_connection", "via": "John Smith", "strength": "weak"}
  ],
  "best_approach": "portfolio_intro",
  "timing_notes": "They just announced Series A, may be too late"
}
```

## When to Invoke
- "Draft outreach to this founder"
- "Find warm intro paths to X"
- "What's the best way to reach this company?"
- "They haven't responded - draft follow-up"
```

### 9. Portfolio Monitor

**Purpose:** Track existing investments for follow-ons and alerts

```markdown
# .claude/agents/portfolio_monitor.md

You are the Portfolio Monitor for Discovery Engine.

## Responsibilities
- Track portfolio company news and signals
- Alert on significant events (funding, exits, issues)
- Identify follow-on opportunities
- Monitor competitive threats to portfolio

## Tool Access
- /mcp__discovery-engine__get-portfolio-signals
- /mcp__owler__get-company-news (if available)
- /mcp__crunchbase__get-funding-events
- Google Alerts integration
- Notion portfolio database

## Monitoring Triggers

### Positive Signals (Opportunity)
- Funding round announced → follow-on opportunity
- Major customer win → case study
- Key hire announced → validation
- Product launch → portfolio cross-sell

### Negative Signals (Risk)
- Key executive departure → check in with founder
- Negative press → crisis support
- Competitor funding → strategy discussion
- Layoff announcement → runway check

### Neutral Signals (Info)
- Office move
- Conference speaking
- Award/recognition

## Alert Priority
- 🔴 Critical: Founder departure, acquisition rumor, legal issue
- 🟡 Important: Funding round, major customer, key hire
- 🟢 Informational: Press mention, award, conference

## Weekly Digest Format
```markdown
## Portfolio Weekly: [Date Range]

### 🔴 Requires Attention
- [Company]: [Event] - [Recommended action]

### 🟡 Notable Updates
- [Company]: [Event]

### 🟢 Good News
- [Company]: [Event]

### Upcoming
- [Company]: Board meeting [date]
- [Company]: Runway < 6 months

### Follow-on Opportunities
- [Company]: Raising Series B, [details]
```

## When to Invoke
- "What's happening with our portfolio?"
- "Any red flags this week?"
- "Which companies are raising?"
- "Alert me if [company] announces anything"
```

### 10. LP Reporting Agent

**Purpose:** Generate investor reports and metrics

```markdown
# .claude/agents/lp_reporting.md

You are the LP Reporting Agent for Discovery Engine.

## Responsibilities
- Generate quarterly LP reports
- Track fund metrics (TVPI, DPI, IRR)
- Create deal flow analytics
- Produce attribution analysis

## Tool Access
- Read-only Notion (portfolio, pipeline)
- /mcp__discovery-engine__get-pipeline-metrics
- /mcp__postgres__query (read-only analytics)
- Historical reports

## Quarterly Report Sections

### 1. Portfolio Summary
- Companies: [count]
- Total deployed: $[X]M
- Current NAV: $[X]M
- TVPI: [X]x
- DPI: [X]x
- IRR: [X]%

### 2. New Investments
| Company | Date | Amount | Stage | Thesis |
|---------|------|--------|-------|--------|

### 3. Portfolio Updates
- [Company]: [Key milestone]
- [Company]: [Key milestone]

### 4. Markups/Markdowns
| Company | Previous | Current | Change | Reason |
|---------|----------|---------|--------|--------|

### 5. Exits/Distributions
| Company | Type | Proceeds | Multiple | IRR |
|---------|------|----------|----------|-----|

### 6. Pipeline & Deal Flow
- Deals reviewed: [X]
- Meetings taken: [X]
- Term sheets issued: [X]
- Conversion rate: [X]%

### 7. Discovery Engine Attribution
- Deals sourced by Discovery: [X] ([Y]%)
- Meetings from Discovery: [X]
- Investments from Discovery: [X]
- Discovery ROI: $[X] invested / $[Y] cost = [Z]x

## When to Invoke
- "Generate Q3 LP report"
- "What's our TVPI?"
- "How many deals came from Discovery?"
- "Create attribution analysis"
```

---

## Additional Skills

### 5. Red Flag Detection

```markdown
# .claude/skills/red_flag_detection.md

## When to Use
When evaluating a prospect for potential issues.

## Red Flag Categories

### Founder Red Flags
- [ ] Serial failed founder (3+ failures, no learning narrative)
- [ ] Glassdoor complaints about founder personally
- [ ] LinkedIn shows short tenures everywhere
- [ ] Claims don't match public records
- [ ] Founder selling significant secondary
- [ ] Vesting already complete (no skin in game)

### Business Red Flags
- [ ] Revenue claims don't match employee count
- [ ] No customer references available
- [ ] Competitors have more funding, similar product
- [ ] Key patents held by others
- [ ] Regulatory uncertainty unaddressed
- [ ] Unit economics don't work at scale

### Legal Red Flags
- [ ] Active litigation
- [ ] IP assignment issues
- [ ] Previous company IP claims
- [ ] Regulatory violations
- [ ] Outstanding liens or judgments

### Cap Table Red Flags
- [ ] Heavy secondary already taken
- [ ] Unusual investor rights
- [ ] Complex preferred structure
- [ ] Founder ownership < 20% at Seed
- [ ] Dead equity (departed founders with large stakes)

### Market Red Flags
- [ ] TAM based on "if we get 1%" logic
- [ ] No clear wedge into market
- [ ] Timing too early (need behavior change)
- [ ] Timing too late (incumbents entrenched)

## Severity Levels
- 🔴 Deal-breaker: Litigation, fraud indicators, fundamental business flaw
- 🟡 Serious concern: Requires satisfactory explanation
- 🟢 Minor flag: Note for diligence, not disqualifying

## Output Format
```json
{
  "red_flags": [
    {"category": "founder", "flag": "...", "severity": "yellow", "source": "..."}
  ],
  "yellow_flags": [...],
  "clear_areas": ["legal", "cap_table"],
  "recommendation": "proceed_with_caution|pass|requires_discussion"
}
```
```

### 6. Technical Due Diligence

```markdown
# .claude/skills/technical_due_diligence.md

## When to Use
When assessing a company's technical capabilities and architecture.

## Assessment Areas

### 1. Technology Stack
- Languages/frameworks (modern vs legacy)
- Infrastructure (cloud-native vs on-prem)
- Data architecture (scalable vs brittle)
- Security posture (SOC2, encryption, etc.)

### 2. Engineering Team
- Team size vs product complexity
- Key person risk
- Hiring velocity
- Technical leadership background

### 3. Product Architecture
- Monolith vs microservices (appropriate for stage?)
- API-first design
- Mobile/web parity
- Integration capabilities

### 4. Technical Moat
- Proprietary algorithms
- Data network effects
- Integration lock-in
- Patents/trade secrets

### 5. Scalability
- Current load handling
- Scaling bottlenecks identified
- Cost structure at scale
- Technical debt level

## Scoring Rubric

### Architecture (1-5)
- 5: Best-in-class, cloud-native, highly scalable
- 3: Adequate for current stage, clear upgrade path
- 1: Legacy, brittle, needs rebuild

### Team (1-5)
- 5: World-class, ex-FAANG/unicorn, no key person risk
- 3: Competent, can execute current roadmap
- 1: Understaffed, key person risk, no senior leadership

### Moat (1-5)
- 5: Deep technical moat, hard to replicate
- 3: Some differentiation, 12-18 month lead
- 1: Easily copied, no technical advantage

## Output Format
```markdown
## Technical Assessment: [Company]

### Summary
[2-3 sentence overview]

### Scores
- Architecture: [X]/5
- Team: [X]/5
- Moat: [X]/5
- **Overall: [X]/5**

### Strengths
- [bullet]

### Concerns
- [bullet]

### Questions for Technical Deep-Dive
1. [question]
2. [question]

### Recommendation
[Pass/Proceed with caution/Strong proceed]
```
```

### 7. Reference Check Framework

```markdown
# .claude/skills/reference_check.md

## When to Use
When conducting reference calls on founders or key executives.

## Reference Types

### 1. Investor References
- Previous investors (if any)
- Investors who passed (why?)
- Board observers

### 2. Customer References
- Early customers (why did they buy?)
- Churned customers (why did they leave?)
- Prospects who didn't convert

### 3. Employee References
- Current employees (culture, leadership)
- Former employees (why did they leave?)
- Candidates who declined offers

### 4. Founder References
- Co-founder (if multiple)
- Previous co-workers
- Previous managers

## Questions by Category

### Character & Integrity
- "Tell me about a time they made a difficult ethical decision."
- "How do they handle bad news?"
- "Would you trust them with your money?"

### Leadership & Management
- "How do they handle conflict on the team?"
- "Describe their communication style."
- "How do they make decisions under pressure?"

### Execution & Competence
- "What's their biggest professional accomplishment?"
- "Where do they need support?"
- "How do they prioritize?"

### Coachability
- "How do they respond to feedback?"
- "Tell me about a time they changed their mind."
- "What would they need to learn to succeed?"

### The Killer Questions
- "Would you invest in their next company?"
- "Would you work for them again?"
- "What should I ask that I haven't?"

## Red Flags in References
- Hesitation on integrity questions
- "Good person, but..." pattern
- Can't name specific accomplishments
- Reference seems coached
- Declined reference requests

## Output Format
```markdown
## Reference Summary: [Founder Name]

### References Completed
1. [Name], [Relationship], [Date]
2. ...

### Themes
- **Strengths**: [consistent feedback]
- **Development areas**: [consistent feedback]
- **Concerns raised**: [if any]

### Notable Quotes
> "[Quote]" - [Source]

### Recommendation
[Strong hire / Proceed / Concerns / Pass]
```
```

### 8. Valuation Benchmarking

```markdown
# .claude/skills/valuation_benchmarking.md

## When to Use
When assessing whether a valuation is reasonable.

## Valuation Methods by Stage

### Pre-Seed / Seed
- Comparable transactions (similar stage, sector, geography)
- Team quality premium/discount
- Market timing adjustment
- NOT revenue multiples (too early)

### Series A
- ARR multiples (if SaaS): 15-30x for high growth
- Revenue multiples: 10-20x
- Comparable raises in last 6 months
- Growth rate adjustment

### Series B+
- ARR multiples with growth adjustment
- Path to profitability consideration
- Public market comps (discounted)

## Current Market Benchmarks (Update Quarterly)

### Seed (2024)
- Median pre-money: $10-15M
- Range: $6-25M
- Check size: $1-3M
- Ownership target: 10-15%

### Series A (2024)
- Median pre-money: $25-40M
- Range: $15-60M
- ARR expectation: $1-3M
- Ownership target: 15-20%

## Adjustment Factors

### Premiums (+)
- Repeat founder with exit: +20-50%
- Hot sector (AI): +20-30%
- Strong growth (3x+ YoY): +20-40%
- Strategic interest: +10-20%

### Discounts (-)
- First-time founder: -10-20%
- Competitive market: -10-20%
- Slow growth: -20-40%
- High burn rate: -10-20%

## Sanity Checks
- "Would we pay this for 100% of the company?"
- "At exit, what multiple do we need?"
- "What has to go right to justify this?"

## Output Format
```markdown
## Valuation Analysis: [Company]

### Proposed Terms
- Pre-money: $[X]M
- Round size: $[X]M
- Our check: $[X]M
- Ownership: [X]%

### Comparable Transactions
| Company | Date | Stage | Pre-$ | ARR | Multiple |
|---------|------|-------|-------|-----|----------|

### Assessment
- Market comp range: $[X-Y]M
- Our fair value estimate: $[X]M
- Premium/(Discount): [X]%

### Path to Return
- Entry: $[X]M pre
- Exit needed at [X]x for 10x return
- Implied exit value: $[X]M
- Probability assessment: [High/Medium/Low]

### Recommendation
[Fair / Stretched / Pass on price]
```
```

### 9. Competitive Response Playbook

```markdown
# .claude/skills/competitive_response.md

## When to Use
When a portfolio company faces competitive threat.

## Competitive Threat Assessment

### Threat Level Matrix

|  | Weak Team | Strong Team |
|--|-----------|-------------|
| **Similar Product** | Monitor | Respond |
| **Better Product** | Differentiate | Urgent Action |

### Response Strategies

#### 1. Ignore (Low threat)
- Competitor is weak team, weak product
- Different market segment
- No overlap in target customers

#### 2. Monitor (Medium threat)
- Set up Google Alerts
- Track their hiring
- Monitor customer churn for competitive losses
- Quarterly competitive review

#### 3. Differentiate (High threat)
- Double down on unique strengths
- Accelerate roadmap on differentiating features
- Lock up key customers with longer contracts
- Increase switching costs

#### 4. Respond Directly (Urgent)
- Competitive battlecards for sales
- Pricing response (if appropriate)
- Accelerate hiring in key areas
- Consider M&A (acquire threat?)

## Battlecard Template
```markdown
## vs [Competitor Name]

### Their Positioning
[How they describe themselves]

### Our Counter-Positioning
[How we differentiate]

### They Win When
- [scenario]

### We Win When
- [scenario]

### Objection Handling
| Their Claim | Our Response |
|-------------|--------------|

### Proof Points
- [Customer quote]
- [Data point]
```

## Output Format
```markdown
## Competitive Analysis: [Portfolio Co] vs [Competitor]

### Threat Assessment
- Level: [Low/Medium/High/Urgent]
- Timeline: [Immediate/6 months/12+ months]

### Competitor Strengths
- [bullet]

### Competitor Weaknesses
- [bullet]

### Recommended Response
[Strategy name]

### Specific Actions
1. [Action] - Owner: [who] - Timeline: [when]
2. ...

### Success Metrics
- [How we know response is working]
```
```

### 10. Fund Strategy Alignment

```markdown
# .claude/skills/fund_strategy_alignment.md

## When to Use
When evaluating how a deal fits overall fund strategy.

## Fund Construction Principles

### Diversification Targets
- Sectors: Max 30% in any single sector
- Stages: 60% Seed, 30% Pre-Seed, 10% Seed+
- Geography: 70% US, 20% UK, 10% other
- Check sizes: $500K-$3M

### Current Portfolio State
[Pull from Notion/database]
- Sector breakdown
- Stage breakdown
- Geographic breakdown
- Capital deployed vs reserved

## Strategic Fit Questions

### 1. Thesis Alignment
- Does this fit our stated thesis?
- If not, is this a thesis expansion moment?
- How do we explain this to LPs?

### 2. Portfolio Synergy
- Can portfolio companies help each other?
- Any conflicts with existing investments?
- Network effects within portfolio?

### 3. Learning Value
- Does this give us insight into new market?
- Relationship value beyond financial return?
- Platform building opportunity?

### 4. Fund Economics
- Does check size fit reserves model?
- Follow-on capacity if successful?
- Impact on TVPI at various outcomes?

## Decision Framework

### Strong Strategic Fit
- Core thesis alignment
- No portfolio conflicts
- Right stage and check size
- Geographic fit

### Opportunistic (Requires IC Discussion)
- Adjacent to thesis
- Exceptional team/opportunity
- Strategic learning value
- Relationship-driven

### Pass on Strategic Grounds
- Outside thesis (even if good company)
- Portfolio conflict
- Check size mismatch
- Overweight in sector/geography

## Output Format
```markdown
## Strategic Fit Analysis: [Company]

### Thesis Alignment: [Strong/Moderate/Weak]
[Reasoning]

### Portfolio Impact
- Sector allocation after: [X]%
- Stage allocation after: [X]%
- Any conflicts: [Yes/No - details]

### Strategic Value Beyond Returns
- [bullet]

### Recommendation
[Core investment / Opportunistic / Strategic pass]
```
```

---

## Specialized MCP Server Implementations

### SEC EDGAR MCP (High Value)

```python
# Minimal implementation sketch

"""
SEC EDGAR MCP Server

Tracks:
- Form D filings (Regulation D offerings - funding rounds)
- 13F filings (institutional holdings)
- 10-K/10-Q (public company financials)
- 8-K (material events)

High-value signals:
- Form D = new funding round
- Multiple 13F additions = institutional interest building
"""

ENDPOINTS = {
    "search_form_d": "Search Form D filings by company name, date range",
    "get_filing": "Get full filing details",
    "track_company": "Set up alerts for company filings",
    "search_investors": "Find all Form D filings mentioning an investor"
}

# Form D contains:
# - Company name, address
# - Amount being raised
# - Amount already sold
# - Number of investors
# - Use of proceeds
# - Investor accreditation
```

### Y Combinator Batch Tracker

```python
"""
YC Batch Tracker MCP

Sources:
- ycombinator.com/companies (official directory)
- Hacker News "Launch HN" posts
- Demo Day coverage (TechCrunch, etc.)

High-value signals:
- New batch companies (pre-vetted deal flow)
- Post-YC funding announcements
- Launch HN posts (product launches)
"""

ENDPOINTS = {
    "get_current_batch": "List companies in current/recent batch",
    "search_alumni": "Search YC alumni by sector, stage",
    "track_launches": "Monitor Launch HN posts",
    "get_company_profile": "Get YC company details"
}
```

### Patent/USPTO MCP

```python
"""
USPTO Patent MCP

Tracks:
- Patent applications (signal of R&D investment)
- Patent grants
- Inventor names (founder technical credibility)
- Assignee changes (M&A signals)

High-value for:
- Deep tech companies
- Biotech
- Hardware
- AI/ML (specific architectures)
"""

ENDPOINTS = {
    "search_applications": "Search recent patent applications",
    "search_by_inventor": "Find all patents by inventor name",
    "search_by_assignee": "Find all patents assigned to company",
    "get_patent_details": "Get full patent application details",
    "track_technology": "Monitor applications in technology area"
}
```

---

## Integration Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Claude Code + MCP                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Collector  │  │   Ranking   │  │    CRM      │             │
│  │  Specialist │  │  Specialist │  │  Specialist │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│  ┌──────┴────────────────┴────────────────┴──────┐             │
│  │           Internal Discovery MCP               │             │
│  │  (All external access goes through here)       │             │
│  └──────────────────────┬────────────────────────┘             │
│                         │                                       │
├─────────────────────────┼───────────────────────────────────────┤
│                         │                                       │
│  ┌──────────────────────┼──────────────────────────┐           │
│  │     Data Sources     │      Enrichment          │           │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐        │           │
│  │  │Companies│  │ GitHub  │  │   SEC   │        │           │
│  │  │ House   │  │   API   │  │  EDGAR  │        │           │
│  │  └─────────┘  └─────────┘  └─────────┘        │           │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐        │           │
│  │  │  WHOIS  │  │ Product │  │  Arxiv  │        │           │
│  │  │  RDAP   │  │  Hunt   │  │         │        │           │
│  │  └─────────┘  └─────────┘  └─────────┘        │           │
│  └─────────────────────────────────────────────────┘           │
│                                                                  │
│  ┌─────────────────────────────────────────────────┐           │
│  │     Storage & CRM                               │           │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐        │           │
│  │  │Postgres │  │Weaviate │  │ Notion  │        │           │
│  │  │         │  │ (vector)│  │  (CRM)  │        │           │
│  │  └─────────┘  └─────────┘  └─────────┘        │           │
│  └─────────────────────────────────────────────────┘           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Priority Matrix

| Component | Value | Effort | Priority | Phase |
|-----------|-------|--------|----------|-------|
| Due Diligence Coordinator | High | Medium | 🔴 P1 | 2 |
| Market Intelligence Agent | High | Medium | 🔴 P1 | 2 |
| SEC EDGAR MCP | High | Medium | 🔴 P1 | 2 |
| Red Flag Detection Skill | High | Low | 🔴 P1 | 2 |
| Technical DD Skill | Medium | Low | 🟡 P2 | 3 |
| Outreach Coordinator | Medium | Low | 🟡 P2 | 3 |
| Portfolio Monitor | Medium | Medium | 🟡 P2 | 3 |
| Reference Check Skill | Medium | Low | 🟡 P2 | 3 |
| LP Reporting Agent | Low | Medium | 🟢 P3 | 4 |
| Valuation Benchmarking | Low | Low | 🟢 P3 | 4 |
| YC Batch Tracker | Medium | Low | 🟡 P2 | 3 |
| USPTO Patents MCP | Low | Medium | 🟢 P3 | 4 |

---

## Quick Wins (This Week)

1. **Add SEC Form D monitoring** - Free API, high signal value
2. **Create Red Flag Detection skill** - Low effort, prevents bad deals
3. **Add YC batch scraper** - Pre-vetted deal flow
4. **Build Reference Check template** - Standardize diligence

## Next Quarter

1. **Due Diligence Coordinator agent** - Automate verification workflows
2. **Market Intelligence agent** - Faster TAM/competitive analysis
3. **Portfolio Monitor** - Proactive portfolio management
4. **LP Reporting automation** - Save 2-3 days per quarter
