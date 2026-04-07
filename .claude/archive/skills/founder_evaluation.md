# Founder Evaluation

## Purpose

This skill defines how to assess founder signals when evaluating early-stage companies. Strong founders can overcome weak markets; weak founders rarely succeed even in strong markets.

## Evaluation Dimensions

### 1. Domain Expertise (30% weight)

**What to Look For:**
- Years in relevant industry (5+ is strong)
- Technical depth matching product (PhD, senior engineer)
- Regulatory knowledge if applicable (FDA, energy)
- Published work, patents, speaking engagements
- Previous role at category leader

**Scoring:**
| Level | Score | Indicators |
|-------|-------|------------|
| Expert | 0.90-1.00 | 10+ years, recognized authority |
| Strong | 0.70-0.89 | 5-10 years, built relevant products |
| Moderate | 0.50-0.69 | 2-5 years, adjacent experience |
| Limited | 0.30-0.49 | <2 years, learning the domain |
| None | 0.00-0.29 | No relevant background |

### 2. Track Record (25% weight)

**What to Look For:**
- Previous startup experience (founded, early employee)
- Exit history (acquisition, IPO)
- Companies scaled (10→100, 100→1000 employees)
- Known investors backed them before
- References from respected operators

**Scoring:**
| Level | Score | Indicators |
|-------|-------|------------|
| Exceptional | 0.90-1.00 | Successful exit as founder |
| Strong | 0.70-0.89 | Founded company, raised Series A+ |
| Moderate | 0.50-0.69 | Early employee at successful startup |
| Limited | 0.30-0.49 | Corporate background only |
| None | 0.00-0.29 | First job or unclear history |

### 3. Commitment Signals (20% weight)

**What to Look For:**
- Full-time dedication (quit day job)
- Personal capital invested
- Relocation if needed
- Burned bridges (can't easily go back)
- Public commitment (announced departure)

**Scoring:**
| Level | Score | Indicators |
|-------|-------|------------|
| All-In | 0.90-1.00 | Quit job, invested savings, relocated |
| Committed | 0.70-0.89 | Full-time, some personal investment |
| Transitioning | 0.50-0.69 | Part-time but with timeline to full |
| Exploring | 0.30-0.49 | Side project, keeping day job |
| Unclear | 0.00-0.29 | No evidence of commitment |

### 4. Team Composition (15% weight)

**What to Look For:**
- Complementary skills (technical + business)
- Previous working relationship
- Equity split (roughly equal is healthiest)
- Decision-making clarity (clear CEO)
- Ability to attract talent

**Scoring:**
| Level | Score | Indicators |
|-------|-------|------------|
| Exceptional | 0.90-1.00 | Repeat co-founders, complementary, balanced |
| Strong | 0.70-0.89 | Worked together before, good balance |
| Adequate | 0.50-0.69 | New team but complementary skills |
| Concerning | 0.30-0.49 | Solo founder or skill gaps |
| Problematic | 0.00-0.29 | Conflict signals, unclear roles |

### 5. Network & Access (10% weight)

**What to Look For:**
- Investor relationships
- Customer connections in target market
- Advisor quality
- Industry relationships
- Ability to recruit

**Scoring:**
| Level | Score | Indicators |
|-------|-------|------------|
| Exceptional | 0.90-1.00 | Tier 1 VC relationships, industry leaders |
| Strong | 0.70-0.89 | Active angel/VC network, good advisors |
| Moderate | 0.50-0.69 | Some industry connections |
| Limited | 0.30-0.49 | Building network from scratch |
| None | 0.00-0.29 | No visible network |

## Founder Archetypes

### High-Conviction Archetypes

**Domain Expert Founder**
- Deep industry experience (10+ years)
- Identified problem firsthand
- Has customer relationships
- May need business co-founder
- Example: Physician building healthtech

**Repeat Founder**
- Previous startup experience
- Learned from failures/successes
- Knows how to fundraise
- Strong network
- Example: Ex-founder of acquired startup

**Technical Visionary**
- Deep technical expertise
- Unique technical insight
- May need GTM help
- Example: Ex-Google ML researcher

### Moderate-Conviction Archetypes

**Career Switcher**
- Strong corporate background
- New to startups
- High capability, learning curve
- Example: McKinsey → Founder

**Second-Time Operator**
- Early employee at successful startup
- Understands scale
- First time as founder
- Example: VP Eng at Series C startup

### Lower-Conviction Archetypes

**Idea Generator**
- Many ideas, unclear focus
- May pivot frequently
- Execution questions
- Watch for: commitment signals

**Part-Time Founder**
- Keeping day job
- Risk-averse
- May not go full-time
- Watch for: timeline to commit

## LinkedIn Profile Analysis

### Green Flags
- [ ] 5+ years in relevant industry
- [ ] Previous startup on profile
- [ ] Current role shows company as primary
- [ ] Recommendations from operators/investors
- [ ] Active posting about company/industry
- [ ] Education aligned with domain

### Yellow Flags
- [ ] Job-hopping (many <1 year roles)
- [ ] Current role still shows employer
- [ ] No startup experience
- [ ] Limited network (<500 connections in target industry)
- [ ] Profile not recently updated

### Red Flags
- [ ] "Open to work" banner
- [ ] Conflicting time commitments
- [ ] History of failed ventures without learning narrative
- [ ] Controversial past roles
- [ ] Misrepresented credentials

## GitHub Profile Analysis (for technical founders)

### Green Flags
- [ ] Active contributions (green squares)
- [ ] Repos relevant to company
- [ ] Stars on personal projects
- [ ] Contributions to notable open source
- [ ] Clean, documented code

### Yellow Flags
- [ ] Sparse recent activity
- [ ] All private repos (can't verify)
- [ ] No contributions to company repos

### Red Flags
- [ ] No GitHub despite claiming technical role
- [ ] All forked repos, no original work
- [ ] Very junior-looking contributions

## Founder Score Calculation

```
founder_score = (
    domain_expertise * 0.30 +
    track_record * 0.25 +
    commitment * 0.20 +
    team_composition * 0.15 +
    network * 0.10
)
```

## Output Template

```
Founder: [Name]
Role: [CEO/CTO/etc.]

Domain Expertise: [0.00-1.00]
  - Industry: [X years in Y]
  - Technical: [relevant skills/credentials]
  - Notes: [key observations]

Track Record: [0.00-1.00]
  - Previous: [companies/roles]
  - Outcomes: [exits/scale achieved]
  - Notes: [key observations]

Commitment: [0.00-1.00]
  - Status: [Full-time/Part-time]
  - Investment: [personal capital if known]
  - Notes: [key observations]

Team: [0.00-1.00]
  - Co-founders: [names/roles]
  - Composition: [complementary/gaps]
  - Notes: [key observations]

Network: [0.00-1.00]
  - Investors: [known relationships]
  - Advisors: [notable names]
  - Notes: [key observations]

FOUNDER SCORE: [0.00-1.00]
ARCHETYPE: [Domain Expert/Repeat/Technical/etc.]
CONFIDENCE: [High/Moderate/Low based on data availability]
```

## Integration with Overall Score

Founder score contributes 30% to overall discovery score:
```
discovery_score = (
    signal_quality * 0.40 +
    thesis_fit * 0.30 +
    founder_score * 0.30
)
```
