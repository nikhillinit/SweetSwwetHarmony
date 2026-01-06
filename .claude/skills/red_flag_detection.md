# Red Flag Detection

## Purpose

This skill defines how to identify warning signs early in the deal process. Apply these heuristics to surface risks before committing significant time or capital.

## Severity Levels

### HARD STOP (Red)
Immediately disqualifying. Do not proceed with DD.
- Company dissolved
- Founder fraud history
- Active litigation/investigation
- Wrong stage (Series B+)
- Wrong geography
- Portfolio conflict

### YELLOW CAUTION
Significant concern requiring investigation.
- Proceed with DD but dig deeper
- Must be addressed before IC
- May become Hard Stop if confirmed

### MONITOR (Orange)
Notable but not blocking.
- Flag for ongoing attention
- May escalate if pattern emerges
- Document but proceed

## Red Flag Categories

### 1. Team Red Flags

**HARD STOP:**
- Founder on fraud/blacklist databases
- CEO has active securities violation
- Criminal conviction (fraud, theft)
- Previous investor lawsuit (as defendant)

**YELLOW CAUTION:**
- Co-founder departed in last 6 months
- CEO still has full-time job elsewhere
- Key technical founder is "advisor" only
- Significant founder equity already vested (>50%)
- No technical co-founder for technical product
- Founder has 3+ failed startups with no learning narrative

**MONITOR:**
- First-time founders (not inherently bad)
- Founder age extremes (<22, >60)
- Remote/distributed founding team
- Founder based far from target market

### 2. Market Red Flags

**HARD STOP:**
- TAM < $500M (too small for VC scale)
- Market actively shrinking
- Regulatory kill shot imminent
- Winner already established (90%+ share)

**YELLOW CAUTION:**
- 10+ well-funded competitors
- Market leader just entered space
- Consolidation wave happening
- Customer concentration in declining industry
- Regulatory uncertainty

**MONITOR:**
- Cyclical market
- Geographic concentration
- Long sales cycles typical
- Heavy capital requirements

### 3. Product Red Flags

**HARD STOP:**
- Vaporware (no working product, raising Seed+)
- Core technology doesn't work
- Obvious patent infringement
- Product harms users

**YELLOW CAUTION:**
- No differentiation from competitors
- Single customer dependency (>50% revenue)
- Massive technical debt admitted
- Pivoted 3+ times in 18 months
- Key feature dependent on third party
- No moat articulated

**MONITOR:**
- MVP quality issues
- Limited feature set
- Platform dependency (Apple, Google)
- Open source alternatives exist

### 4. Financial Red Flags

**HARD STOP:**
- Fraud (fake metrics, fake customers)
- Unaccounted use of previous funding
- Personal loans from company to founder
- Off-balance-sheet liabilities hidden

**YELLOW CAUTION:**
- Burn rate > 24 months of runway
- Negative gross margins
- Previous down round
- Debt on balance sheet (not venture debt)
- Convertible notes with aggressive terms
- Excessive founder salaries (>$200K Pre-Seed)
- No financial model exists

**MONITOR:**
- High customer concentration
- Lumpy revenue
- Deferred revenue issues
- Extended payment terms

### 5. Legal Red Flags

**HARD STOP:**
- Active IP litigation (as defendant)
- SEC investigation
- Regulatory enforcement action
- Key IP owned by founder personally (not company)

**YELLOW CAUTION:**
- Messy cap table (100+ shareholders)
- Outstanding investor disputes
- Missing incorporation documents
- IP assignment not complete
- Previous company IP unclear
- Non-standard legal structure

**MONITOR:**
- International structure complexity
- Outstanding option pool issues
- SAFE/convertible stack
- Unexercised advisor grants

### 6. Traction Red Flags

**HARD STOP:**
- Fake customers (fabricated logos)
- Inflated metrics (counting free trials as customers)
- Revenue is actually founder's other company

**YELLOW CAUTION:**
- Metrics declining and hidden
- All revenue from one customer
- "Pilots" extending beyond 6 months
- Letters of intent but no conversions
- High churn (>10% monthly)
- NPS significantly negative

**MONITOR:**
- Slow growth (but honest about it)
- Long time to first customer
- Heavy discounting to acquire
- Free tier cannibalization

## Investigation Protocol

When yellow flag detected:

1. **Document** - Note the concern and source
2. **Research** - Gather more information
3. **Ask** - Direct question to founders
4. **Verify** - Cross-reference claims
5. **Assess** - Upgrade/downgrade severity
6. **Decide** - Proceed, pause, or pass

## Red Flag Combinations

Some flags are worse together:

| Flag 1 | Flag 2 | Combined Effect |
|--------|--------|-----------------|
| First-time founders | No technical co-founder | YELLOW → Concern |
| Pivoted 3x | Previous down round | YELLOW → Serious |
| High burn | Declining metrics | YELLOW → Hard Stop |
| CEO part-time | Co-founder departed | YELLOW → Hard Stop |

## False Positives

Don't flag these without more context:
- Young founders (many successes were 20s)
- Unusual backgrounds (domain expertise matters more)
- Small current team (normal for Pre-Seed)
- No revenue yet (acceptable for Pre-Seed)
- Previous failed startup (if learning evident)
- Competitive market (may indicate opportunity)

## Presenting Concerns Diplomatically

**To Investment Committee:**
- State facts, not judgments
- Provide source for each concern
- Suggest investigation path
- Recommend despite concerns if warranted

**To Founders (if asking):**
- Frame as clarifying questions
- Don't accuse, inquire
- Give benefit of doubt initially
- Document responses carefully

## Red Flag Summary Template

```
# Red Flag Assessment: [Company]

## Summary
- Hard Stops: [count]
- Yellow Cautions: [count]
- Monitors: [count]
- Overall: PROCEED / INVESTIGATE / PASS

## Hard Stops
[None / List with details]

## Yellow Cautions
| Category | Flag | Source | Status |
|----------|------|--------|--------|
| Team | [flag] | [source] | Investigating |

## Monitors
- [flag 1]
- [flag 2]

## Investigation Plan
1. [item to verify]
2. [question to ask]

## Recommendation
[Proceed with caution / Pause for investigation / Pass]
```

## Integration with DD

Red flag detection feeds into:
1. **research_analyst** - Initial flag identification
2. **due_diligence_coordinator** - Investigation orchestration
3. **investment_memo** - Risk section
4. **ranking_specialist** - Confidence adjustment
