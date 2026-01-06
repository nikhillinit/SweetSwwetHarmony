# Technical Due Diligence

## Purpose

This skill defines how to evaluate the technical strength of early-stage companies. Apply these frameworks when assessing technology, architecture, and technical team.

## Assessment Areas

### 1. Architecture & Scalability (Weight: 20%)

**Questions to Investigate:**
- Can the system handle 10x current load?
- What's the path to 100x?
- Where are the bottlenecks?
- Is it cloud-native or legacy?

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Proven scale, handles 100x headroom |
| 0.7-0.8 | Well-architected, clear path to 10x |
| 0.5-0.6 | Adequate for current needs, some concerns |
| 0.3-0.4 | Technical debt, scaling will require rework |
| 0.0-0.2 | Fundamental architecture issues |

**Green Flags:**
- Microservices or well-modularized monolith
- Auto-scaling infrastructure
- Database appropriate for use case
- Async processing for heavy tasks
- CDN/caching strategy

**Red Flags:**
- Single point of failure
- No database indexing strategy
- Synchronous processing of heavy tasks
- Hardcoded configuration
- No staging/test environment

### 2. Code Quality (Weight: 15%)

**If Code Access Available:**
- Test coverage %
- Linting/formatting consistency
- Documentation quality
- Dependency management
- Security scanning in CI

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | >80% test coverage, clean CI, documented |
| 0.7-0.8 | >50% coverage, consistent style |
| 0.5-0.6 | Some tests, readable code |
| 0.3-0.4 | Minimal tests, inconsistent quality |
| 0.0-0.2 | No tests, spaghetti code |

**If No Code Access:**
- Infer from GitHub public repos
- Ask about testing philosophy
- Check for open source contributions
- Review any public demos

### 3. Security Posture (Weight: 15%)

**Checklist:**
- [ ] Authentication implemented properly
- [ ] Authorization/RBAC in place
- [ ] Data encrypted at rest
- [ ] Data encrypted in transit (TLS)
- [ ] Secrets management (not in code)
- [ ] Input validation
- [ ] Rate limiting
- [ ] Audit logging

**Compliance Considerations:**
| Industry | Requirements |
|----------|--------------|
| Healthcare | HIPAA, SOC2 |
| Financial | SOC2, PCI-DSS |
| Enterprise | SOC2 minimum |
| Consumer | GDPR, CCPA |

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | SOC2 certified, security-first culture |
| 0.7-0.8 | SOC2 in progress, good practices |
| 0.5-0.6 | Basic security, awareness present |
| 0.3-0.4 | Security gaps, reactive approach |
| 0.0-0.2 | No security consideration |

### 4. Technical Team (Weight: 25%)

**CTO/Technical Lead Assessment:**
- Previous technical roles and scale
- System design experience
- Hiring and team building
- Technical decision-making
- Communication with non-technical

**Team Composition:**
| Stage | Expected Team |
|-------|---------------|
| Pre-Seed | 1-2 engineers (founder + 1) |
| Seed | 3-5 engineers |
| Seed+ | 5-10 engineers |

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | CTO built/scaled similar systems, strong team |
| 0.7-0.8 | Solid technical background, growing team |
| 0.5-0.6 | Adequate for current stage |
| 0.3-0.4 | Gaps in experience, hiring challenges |
| 0.0-0.2 | No technical leadership |

### 5. Technology Choices (Weight: 10%)

**Stack Assessment:**
- Appropriate for problem domain?
- Talent availability?
- Maintenance burden?
- Vendor lock-in risk?

**Common Stacks by Domain:**
| Domain | Typical Stack |
|--------|---------------|
| Web SaaS | React/Vue + Node/Python + PostgreSQL |
| ML/AI | Python + PyTorch/TensorFlow + Cloud ML |
| Mobile | React Native / Flutter / Native |
| Data | Python/Spark + Cloud warehouses |

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Optimal choices, easy hiring |
| 0.7-0.8 | Good choices, justified decisions |
| 0.5-0.6 | Workable but some concerns |
| 0.3-0.4 | Unusual choices, hiring difficulty |
| 0.0-0.2 | Poor choices, migration needed |

### 6. Technical Moat (Weight: 10%)

**Types of Technical Moats:**
1. **Proprietary algorithms** - Unique IP
2. **Data moat** - Unique dataset that improves product
3. **Integration depth** - Hard to rip out
4. **Network effects** - Technical network effects
5. **Regulatory barrier** - Technical compliance

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Strong moat, 2+ years to replicate |
| 0.7-0.8 | Meaningful moat, 1 year to replicate |
| 0.5-0.6 | Some defensibility |
| 0.3-0.4 | Limited moat, 6 months to copy |
| 0.0-0.2 | No moat, commodity tech |

### 7. Technical Debt (Weight: 5%)

**Assessment Questions:**
- Is the team aware of debt?
- Is there a paydown plan?
- Does debt block features?
- What's the estimated remediation cost?

**Scoring:**
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Minimal debt, actively managed |
| 0.7-0.8 | Known debt, prioritized paydown |
| 0.5-0.6 | Moderate debt, acknowledged |
| 0.3-0.4 | Significant debt, no plan |
| 0.0-0.2 | Crushing debt, blocking progress |

## GitHub Profile Analysis

### Organization/Repo Signals

**Green Flags:**
- Active commits (recent activity)
- Multiple contributors
- Issues being closed
- CI/CD badges
- Good README
- Release tags
- Stars from real users

**Yellow Flags:**
- Single committer only
- Long gaps in activity
- Many open issues, few closed
- No CI/CD
- Minimal documentation

**Red Flags:**
- All commits in one day (fake history)
- Star patterns suggest buying
- Copied code without attribution
- Sensitive data in history

### Individual Profiles

**Strong Technical Signals:**
- Contributions to major projects
- Own projects with stars
- Consistent activity over years
- Technical blog posts
- Conference talks

## Questions to Ask Founders

### Architecture
1. "Walk me through your system architecture"
2. "What happens when traffic 10x's overnight?"
3. "What's your biggest scaling concern?"
4. "How do you handle data backup/recovery?"

### Team
1. "How do you make technical decisions?"
2. "What's your hiring bar for engineers?"
3. "How do you onboard new developers?"
4. "What would you build differently?"

### Security
1. "How do you handle authentication?"
2. "Where is customer data stored?"
3. "What's your incident response process?"
4. "Any security audits completed?"

### Process
1. "What's your deployment frequency?"
2. "How do you handle production incidents?"
3. "What's your testing philosophy?"
4. "How do you prioritize tech debt?"

## Overall Technical Score

```
technical_score = (
    architecture * 0.20 +
    code_quality * 0.15 +
    security * 0.15 +
    team * 0.25 +
    tech_choices * 0.10 +
    moat * 0.10 +
    debt * 0.05
)
```

### Interpretation
| Score | Assessment |
|-------|------------|
| 0.8+ | Exceptional - technical strength is differentiator |
| 0.6-0.8 | Solid - technical execution on track |
| 0.4-0.6 | Adequate - some concerns but manageable |
| <0.4 | Concerning - significant technical risks |

## Output Template

```
# Technical Due Diligence: [Company]

## Summary
- Technical Score: X.XX / 1.00
- Assessment: [Exceptional / Solid / Adequate / Concerning]
- Key Strength: [area]
- Key Risk: [area]

## Detailed Scores
| Area | Score | Notes |
|------|-------|-------|
| Architecture | X.X | [notes] |
| Code Quality | X.X | [notes] |
| Security | X.X | [notes] |
| Team | X.X | [notes] |
| Tech Choices | X.X | [notes] |
| Moat | X.X | [notes] |
| Tech Debt | X.X | [notes] |

## GitHub Analysis
- Org: [link]
- Activity: [assessment]
- Contributors: [count]
- Concerns: [list]

## Key Findings
1. [Finding 1]
2. [Finding 2]

## Technical Risks
- [Risk 1]: [mitigation]
- [Risk 2]: [mitigation]

## Recommendation
[Proceed / Proceed with caution / Pause / Pass]
[Reasoning]
```

## Integration Points

Technical DD feeds into:
- **investment_memo** - Product/Technology section
- **red_flag_detection** - Technical red flags
- **due_diligence_coordinator** - Overall DD checklist
- **ranking_specialist** - Technical moat assessment
