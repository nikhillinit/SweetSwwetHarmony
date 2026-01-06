# Investment Memo Generation

## Purpose

This skill defines how to write compelling investment memos for Press On Ventures. Apply these guidelines when generating IC-ready documentation.

## Memo Structure

### 1. Executive Summary (1 paragraph)
The reader should understand the recommendation in 60 seconds.

**Include:**
- Company name and one-line description
- Stage and round size
- Recommendation: INVEST / PASS / MORE DD NEEDED
- Key reason for recommendation
- Proposed check size

**Example:**
> **Acme Health** is building AI-powered remote patient monitoring for chronic disease management. They're raising a $2.5M Seed round at $10M pre. **RECOMMEND INVEST** - strong founder-market fit (CEO is former Epic product lead with 12 years in health IT), early traction ($50K MRR, 3 health system pilots), and clear path to $500K check. Key risk: FDA clearance timeline.

### 2. Company Overview
- Founded: [date]
- Location: [city, country]
- Team size: [number]
- Website: [URL]
- Stage: [Pre-Seed / Seed / Seed+]

### 3. Market Opportunity

**TAM/SAM/SOM Framework:**
| Metric | Value | Methodology | Source |
|--------|-------|-------------|--------|
| TAM | $XB | [How calculated] | [Source] |
| SAM | $XB | [Filters applied] | [Source] |
| SOM | $XM | [Realistic capture] | [Estimate] |

**Market Dynamics:**
- Growth rate: X% CAGR
- Key drivers: [list]
- Tailwinds: [list]
- Headwinds: [list]

### 4. Product/Technology

**What They Do:**
[2-3 sentence clear description]

**Technical Moat:**
| Dimension | Assessment | Evidence |
|-----------|------------|----------|
| Proprietary tech | Strong/Moderate/Weak | [details] |
| Data advantage | Strong/Moderate/Weak | [details] |
| Network effects | Strong/Moderate/Weak | [details] |

**Product Stage:**
- [ ] Concept only
- [ ] MVP / Beta
- [ ] Production with customers
- [ ] Scaled product

### 5. Team

**Founders:**
| Name | Role | Background | Founder Score |
|------|------|------------|---------------|
| [Name] | CEO | [summary] | 0.X |
| [Name] | CTO | [summary] | 0.X |

**Key Hires:** [list if any]
**Advisors:** [notable names]
**Team Gaps:** [what's missing]

### 6. Traction/Metrics

**Stage-Appropriate Metrics:**

*Pre-Seed:*
- Product: Does it exist?
- Users: Any pilots/LOIs?
- Team: Are founders committed full-time?

*Seed:*
- Revenue: MRR/ARR
- Growth: MoM %
- Customers: Count and quality
- Retention: Early signals

*Seed+:*
- Revenue: ARR with growth trajectory
- Unit economics: CAC, LTV, payback
- Retention: Logo and net revenue
- Pipeline: Qualified opportunities

### 7. Competition

| Competitor | Funding | Positioning | Threat Level |
|------------|---------|-------------|--------------|
| [Direct 1] | $XM | [diff] | High/Med/Low |
| [Direct 2] | $XM | [diff] | High/Med/Low |
| [Indirect] | $XM | [diff] | High/Med/Low |

**Differentiation:** [How they win]

### 8. Investment Thesis

**Why This Company:**
1. [Reason 1]
2. [Reason 2]
3. [Reason 3]

**Why Now:**
[Timing factors]

**Why Us (Press On Ventures):**
- Thesis fit: [Healthtech/Cleantech/AI Infra]
- Value-add: [What we bring]
- Check size alignment: [fits $500K-$3M]

### 9. Key Risks

| Risk | Severity | Mitigation | Residual |
|------|----------|------------|----------|
| [Risk 1] | RED/YELLOW | [plan] | Accept/Monitor |
| [Risk 2] | RED/YELLOW | [plan] | Accept/Monitor |

**Deal Breakers:** [Any hard stops]

### 10. Deal Terms

- Round size: $X
- Valuation: $X pre / $X post
- Instrument: [SAFE / Priced / Convertible]
- Our check: $X for X%
- Co-investors: [names]
- Use of funds: [breakdown]

## Scoring Framework

### Overall Company Score (0.0-1.0)
```
company_score = (
    team_score * 0.30 +
    market_score * 0.25 +
    product_score * 0.20 +
    traction_score * 0.15 +
    terms_score * 0.10
)
```

### Recommendation Thresholds
| Score | Recommendation |
|-------|----------------|
| 0.75+ | Strong INVEST |
| 0.60-0.74 | INVEST with notes |
| 0.45-0.59 | MORE DD NEEDED |
| <0.45 | PASS |

## Tone & Style

**Do:**
- Be direct and opinionated
- Lead with the recommendation
- Quantify everything possible
- Acknowledge risks honestly
- Use tables for comparisons

**Don't:**
- Bury the lede
- Use vague language ("interesting", "promising")
- Ignore obvious risks
- Oversell weak points
- Write more than 3 pages for Seed

## Quality Checklist

Before submitting memo:
- [ ] Recommendation is clear in first paragraph
- [ ] All claims have sources
- [ ] TAM has multiple sources
- [ ] Team section uses founder_evaluation skill
- [ ] Risks section is honest and complete
- [ ] Terms are benchmarked
- [ ] Thesis fit is explicit
- [ ] No typos or formatting issues

## Examples

### Good Executive Summary
> **CloudMeter** provides AI-powered energy analytics for commercial buildings, reducing HVAC costs by 20-30%. Raising $3M Seed at $12M pre. **RECOMMEND INVEST $1M.** CEO built similar product at Honeywell (acquired for $50M). $80K MRR growing 25% MoM with 15 enterprise customers. Cleantech thesis fit, strong unit economics (12-month payback). Risk: long enterprise sales cycles.

### Bad Executive Summary
> CloudMeter is an interesting company in the energy space. They have a good team and are seeing nice traction. The market seems big and there could be a good opportunity here. We should probably invest if the terms work out.

*Problems: No specifics, vague language, no recommendation, no metrics*
