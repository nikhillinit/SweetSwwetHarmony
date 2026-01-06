# Thesis Matching

## Purpose

This skill defines how to evaluate companies against Press On Ventures' investment thesis. Apply these criteria when determining thesis fit.

## Press On Ventures Thesis

### Core Focus Areas

#### 1. Healthtech (40% of portfolio target)
**In Scope:**
- Digital health platforms
- Medical devices (non-pharma)
- Health data infrastructure
- Remote patient monitoring
- Clinical workflow tools
- Mental health tech
- Preventive care platforms

**Out of Scope:**
- Pure pharma/drug development
- Medical research without product
- Consumer wellness (vitamins, fitness)
- Insurance tech (unless B2B infrastructure)

#### 2. Cleantech (35% of portfolio target)
**In Scope:**
- Energy storage & batteries
- Grid optimization software
- Carbon capture technology
- Sustainable materials
- EV infrastructure
- Industrial decarbonization
- Climate data/analytics

**Out of Scope:**
- Pure solar/wind installation
- Consumer sustainability apps
- Carbon offset marketplaces only
- Real estate energy efficiency

#### 3. AI Infrastructure (25% of portfolio target)
**In Scope:**
- ML/AI development tools
- Data pipeline infrastructure
- Model serving/deployment
- AI observability/monitoring
- Vector databases
- LLM tooling & frameworks
- AI security/governance

**Out of Scope:**
- Pure AI applications (use AI, don't build AI infra)
- Consumer AI products
- Chatbot-only companies
- AI consulting services

## Stage Fit

### Sweet Spot: Pre-Seed to Seed+
| Stage | Typical Raise | Check Size | Fit |
|-------|---------------|------------|-----|
| Pre-Seed | $250K-$1M | $250K-$500K | Good |
| Seed | $1M-$4M | $500K-$1.5M | Best |
| Seed+ | $4M-$8M | $1M-$3M | Good |
| Series A | $8M-$20M | - | Too late |

### Stage Signals
- **Pre-Seed**: Incorporation, initial product, 1-2 founders
- **Seed**: MVP live, early customers, small team (3-8)
- **Seed+**: Revenue traction, product-market fit signals
- **Too Late**: Series A raised, 20+ employees, >$1M ARR

## Geography

### Primary Markets (1.0x)
- United States (all states)
- United Kingdom

### Secondary Markets (0.85x)
- Canada
- Western Europe (Germany, France, Netherlands)
- Israel

### Requires Exception (0.5x)
- Other regions - need exceptional circumstances

## Scoring Rubric

### Sector Match (40% of thesis score)
| Match Level | Score | Criteria |
|-------------|-------|----------|
| Core Thesis | 1.00 | Directly in Healthtech/Cleantech/AI Infra |
| Adjacent | 0.70 | Related but not core (e.g., biotech tools) |
| Tangential | 0.40 | Some overlap (e.g., general B2B SaaS) |
| No Match | 0.00 | Consumer, fintech, gaming, etc. |

### Stage Match (30% of thesis score)
| Match Level | Score | Criteria |
|-------------|-------|----------|
| Sweet Spot | 1.00 | Seed, raising $1M-$4M |
| Good Fit | 0.80 | Pre-Seed or Seed+ |
| Stretch | 0.50 | Early Pre-Seed or late Seed+ |
| No Fit | 0.00 | Series A+ |

### Geography Match (15% of thesis score)
| Match Level | Score | Criteria |
|-------------|-------|----------|
| Primary | 1.00 | US or UK |
| Secondary | 0.85 | Canada, W. Europe, Israel |
| Other | 0.50 | Elsewhere with strong ties to US/UK |

### Check Size Match (15% of thesis score)
| Match Level | Score | Criteria |
|-------------|-------|----------|
| Ideal | 1.00 | Round allows $500K-$3M check |
| Acceptable | 0.70 | Round allows $250K-$500K or $3M-$5M |
| Stretch | 0.40 | Outside normal range |

## Thesis Score Calculation

```
thesis_score = (
    sector_score * 0.40 +
    stage_score * 0.30 +
    geography_score * 0.15 +
    check_size_score * 0.15
)
```

### Routing Based on Thesis Score
| Score | Action |
|-------|--------|
| 0.70+ | Strong thesis fit - prioritize |
| 0.50-0.69 | Moderate fit - include if signals strong |
| 0.30-0.49 | Weak fit - only if exceptional signals |
| <0.30 | No fit - filter out |

## Deep Dive Questions by Sector

### Healthtech
- Is this FDA-regulated? What pathway?
- B2B (health systems) or B2C (patients)?
- What's the reimbursement model?
- Clinical validation status?

### Cleantech
- Hardware or software focus?
- What's the deployment timeline?
- Regulatory requirements?
- Unit economics at scale?

### AI Infrastructure
- Who is the buyer (ML engineer, data scientist, platform)?
- Open source component?
- What's the moat beyond the model?
- Integration complexity?

## Thesis Mismatch Patterns

### Hard No's
- Consumer social apps
- Crypto/Web3 (unless infrastructure)
- Gaming
- Adtech
- Real estate (unless cleantech angle)
- Series B+ companies

### Soft No's (need exceptional signals)
- Pure consulting/services
- Single-customer dependency
- Regulated industries without clear path
- Hardware without software moat

### Adjacent Areas (case by case)
- Fintech for healthcare payments
- Biotech tools (not therapeutics)
- Enterprise SaaS with AI/ML focus
- Climate fintech

## Output Template

```
Company: [Name]
Sector Assessment:
  - Primary: [Healthtech/Cleantech/AI Infra/Other]
  - Fit Level: [Core/Adjacent/Tangential/None]
  - Sector Score: [0.00-1.00]

Stage Assessment:
  - Estimated Stage: [Pre-Seed/Seed/Seed+/Series A+]
  - Raise Amount: [$X]
  - Stage Score: [0.00-1.00]

Geography Assessment:
  - Location: [Country/Region]
  - Market Tier: [Primary/Secondary/Other]
  - Geography Score: [0.00-1.00]

Check Size Assessment:
  - Round Size: [$X]
  - Viable Check: [$X-$Y]
  - Check Score: [0.00-1.00]

THESIS SCORE: [0.00-1.00]
FIT LEVEL: [Strong/Moderate/Weak/None]
RECOMMENDATION: [Prioritize/Include/Exception Only/Filter]
```
