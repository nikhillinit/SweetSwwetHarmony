# Ranking Explanation

## Purpose

This skill defines how to articulate ranking decisions transparently. Every prospect pushed to the CRM should have a clear explanation of why it scored as it did.

## Glass.AI Methodology Principles

Our ranking explanations follow these principles:

### 1. Multi-Signal Verification
- Never rely on a single source
- Corroboration increases confidence
- Conflicting signals reduce confidence
- Document all sources used

### 2. Recency Bias (Intentional)
- Recent signals weighted higher
- Decay curves applied consistently
- Stale signals explicitly noted
- "Why Now" emphasizes timeliness

### 3. Anti-Hype Mechanisms
- Social buzz alone doesn't score high
- Require substantive signals (incorporation, funding, product)
- Detect artificial inflation (bought stars, bot activity)
- Penalize hype without substance

### 4. Transparent Reasoning
- Show the math, not just the result
- Explain what would change the score
- Acknowledge uncertainty
- Cite specific evidence

## Overall Score Formula

```
discovery_score = (
    signal_quality_score * 0.40 +
    thesis_fit_score * 0.30 +
    founder_score * 0.30
)
```

### Score Interpretation
| Score Range | Confidence | Routing |
|-------------|------------|---------|
| 0.70-0.95 | HIGH | Status: "Source" |
| 0.40-0.69 | MEDIUM | Status: "Tracking" |
| 0.00-0.39 | LOW | HOLD (don't push) |

*Note: Max score is 0.95, never 1.00 (epistemic humility)*

## Confidence Level Explanations

### HIGH Confidence (0.70+)

**What it means:**
- Multiple corroborating signals from reliable sources
- Strong thesis alignment
- Founder signals present and positive
- Recent activity within freshness windows

**Typical evidence:**
- SEC Form D filing + GitHub activity + incorporation
- Or: Funding announcement + domain + founder LinkedIn
- At least 2 Tier 1/2 sources

**Action:** Push to Notion with Status: "Source"

### MEDIUM Confidence (0.40-0.69)

**What it means:**
- Some positive signals but gaps in verification
- Thesis alignment present but not perfect
- Limited founder information
- May have some stale signals

**Typical evidence:**
- Single authoritative source (e.g., Form D only)
- Or: Multiple weak sources without corroboration
- Thesis fit but stage/geography uncertainty

**Action:** Push to Notion with Status: "Tracking"

### LOW Confidence (<0.40)

**What it means:**
- Weak or stale signals
- Thesis alignment unclear
- Insufficient data to evaluate
- May have warning flags

**Typical evidence:**
- Only social media mentions
- Very old signals (beyond decay window)
- Missing key information

**Action:** HOLD - do not push yet, await more signals

## "Why Now" Field Guidance

The "Why Now" field captures the timely reason this prospect is interesting. It should be:
- 1-2 sentences maximum
- Specific and time-bound
- Actionable for the investment team

### Structure
```
[Recent event/signal] + [implication for investment opportunity]
```

### Good Examples

**Healthtech:**
> "Filed Form D for $2.5M seed round on Jan 5. Remote patient monitoring platform with FDA 510(k) pending - regulatory clearance could accelerate growth in Q2."

**Cleantech:**
> "GitHub stars jumped 400% in past 14 days. Open-source battery management system gaining developer traction ahead of their enterprise product launch."

**AI Infrastructure:**
> "Incorporated Dec 2024 with ex-OpenAI founding team. Building ML observability tools - timing aligns with enterprise AI adoption wave."

### Bad Examples (avoid these)

- "Interesting company" (not specific)
- "They raised money" (not timely)
- "Good founders" (no urgency)
- "Growing fast" (no specifics)

### Sector-Specific Templates

**Healthtech:**
> "[Regulatory milestone/funding event] positions them for [market opportunity]. [Founder credential] adds credibility in [specific domain]."

**Cleantech:**
> "[Technical milestone/policy tailwind] creates window for [technology type]. [Traction metric] suggests market validation."

**AI Infrastructure:**
> "[Product launch/open source traction] signals developer adoption. [Technical differentiation] addresses [specific pain point] in ML workflows."

## Explanation Templates

### High Confidence Explanation
```
DISCOVERY SCORE: [0.XX] (HIGH CONFIDENCE)
Status Recommendation: Source

SIGNAL QUALITY (40% weight): [0.XX]
- Primary signal: [type] from [source] ([age] days old)
- Corroboration: [additional signals]
- Multi-source boost: [1.15x/1.30x] applied
- Flags: [none / list any]

THESIS FIT (30% weight): [0.XX]
- Sector: [Healthtech/Cleantech/AI Infra] - [Core/Adjacent]
- Stage: [Pre-Seed/Seed/Seed+] - [Sweet Spot/Good/Stretch]
- Geography: [US/UK/Other] - [Primary/Secondary]
- Check size: [$X-Y range] - [Ideal/Acceptable]

FOUNDER (30% weight): [0.XX]
- [Founder name]: [key credential]
- Archetype: [Domain Expert/Repeat/etc.]
- Commitment: [evidence]

WHY NOW:
[1-2 sentence timely narrative]

WHAT WOULD CHANGE THIS SCORE:
- Higher: [additional verification that would help]
- Lower: [risks or flags to monitor]
```

### Medium Confidence Explanation
```
DISCOVERY SCORE: [0.XX] (MEDIUM CONFIDENCE)
Status Recommendation: Tracking

SIGNAL QUALITY (40% weight): [0.XX]
- Primary signal: [type] from [source]
- Gaps: [missing corroboration / stale signals]
- Verification needed: [what would help]

THESIS FIT (30% weight): [0.XX]
- Alignment: [summary of fit/gaps]
- Uncertainty: [stage unclear / geography TBD]

FOUNDER (30% weight): [0.XX]
- Available info: [what we know]
- Missing: [LinkedIn not found / limited data]

WHY NOW:
[1-2 sentence narrative, noting uncertainty]

MONITORING TRIGGERS:
- Move to Source if: [specific signal]
- Reject if: [specific concern]
```

### Rejection Explanation
```
DISCOVERY SCORE: [0.XX] (LOW / REJECTED)
Status: Do not push

REASON: [Hard kill signal / Low confidence / Thesis mismatch]

DETAILS:
- [Specific reason for rejection]
- [Evidence supporting rejection]

IF CIRCUMSTANCES CHANGE:
- [What would need to happen to reconsider]
```

## Notion Field Mapping

| Field | Source | Format |
|-------|--------|--------|
| Company Name | Signal data | Text |
| Status | Routing decision | Select |
| Confidence Score | discovery_score | Number (0.00-0.95) |
| Signal Types | Collected signals | Multi-select |
| Why Now | Ranking explanation | Text (1-2 sentences) |
| Discovery ID | Generated | Text (uuid) |
| Canonical Key | canonical_keys.py | Text (e.g., "domain:acme.ai") |

## Common Explanation Scenarios

### Scenario: High score despite single source
```
Note: While typically we require multi-source verification, this SEC Form D
filing is from an authoritative government source and contains rich data
(offering amount, industry code, principals). Single-source confidence
is appropriate for Tier 1 sources with comprehensive data.
```

### Scenario: Lower score despite strong signals
```
Note: Despite strong signals (recent funding, active GitHub), confidence
is moderated due to [thesis mismatch / founder data unavailable /
conflicting information about stage]. Monitoring for clarification.
```

### Scenario: Rejection of seemingly good company
```
Note: Although [positive signals], this prospect is rejected due to
[hard kill: company_dissolved / thesis: Series B stage / geography:
outside investment mandate]. This is a permanent/temporary filter.
```
