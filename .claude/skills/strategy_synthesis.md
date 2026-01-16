# Strategy Synthesis

## Purpose

This skill extends the **planning-with-files** pattern for strategic analysis work. It uses the three-file system (`task_plan.md`, `findings.md`, `progress.md`) with strategy-specific phases and outputs.

## When to Use

- Competitive analysis requiring multi-source research
- Market entry strategy development
- Investment thesis synthesis
- Due diligence across multiple domains
- Any strategic decision needing structured evidence gathering

**Skip for:** Single-source summaries, simple lookups, or tasks under 3 steps.

## Core Principle

> "Context Window = RAM (volatile); Filesystem = Disk (persistent)"
>
> All strategic insights must be written to disk. Never trust memory for multi-step analysis.

## The Three-File System

| File | Purpose | Update Frequency |
|------|---------|------------------|
| `task_plan.md` | Research roadmap, phases, decisions | Before major decisions |
| `findings.md` | Evidence, sources, contradictions | Every 2 research actions |
| `progress.md` | Session log, tests, reboot context | After each work block |

## Strategy-Specific Phases

### task_plan.md Template

```markdown
# Strategy: [Research Question]

## Goal
[Specific, answerable question]

## Phase 1: Requirements ⬜
- [ ] Define success criteria
- [ ] Identify stakeholders
- [ ] Set scope boundaries
- [ ] List known constraints

## Phase 2: Research ⬜
- [ ] Inventory existing docs
- [ ] Identify external sources needed
- [ ] Map competitive landscape
- [ ] Gather quantitative data

## Phase 3: Analysis ⬜
- [ ] Apply framework (SWOT/Porter's/JTBD)
- [ ] Cross-reference sources
- [ ] Identify contradictions
- [ ] Quantify where possible

## Phase 4: Synthesis ⬜
- [ ] Draft strategic options (2-3 paths)
- [ ] Evaluate trade-offs per option
- [ ] Stress-test recommendations
- [ ] Document risks & mitigations

## Phase 5: Delivery ⬜
- [ ] Write OPTIMAL_STRATEGY.md
- [ ] Validate against success criteria
- [ ] Quality checklist complete
- [ ] Handoff ready

## Key Questions
- Q1: [Unresolved question]
- Q2: [Unresolved question]

## Decisions Made
| Decision | Rationale | Date |
|----------|-----------|------|
| [choice] | [why] | [when] |

## Errors Encountered
| Error | Resolution | Attempt # |
|-------|------------|-----------|
```

### findings.md Template

```markdown
# Research Findings: [Topic]

## Sources Reviewed
| Source | Type | Credibility | Key Insight |
|--------|------|-------------|-------------|
| [doc] | Internal/External | HIGH/MED/LOW | [finding] |

## Evidence Matrix
| Claim | Supporting Sources | Contradicting Sources | Confidence |
|-------|-------------------|----------------------|------------|
| [claim] | [sources] | [sources] | HIGH/MED/LOW |

## Quantitative Data
| Metric | Value | Source | Date |
|--------|-------|--------|------|
| [metric] | [value] | [source] | [date] |

## Contradictions Found
1. **[Topic]**: Source A says X, Source B says Y. Resolution: [approach]

## Framework Analysis
### [SWOT/Porter's/JTBD - choose one]
[Framework-specific structure]

## Open Questions
- [ ] [Question needing more research]
```

### progress.md Template

```markdown
# Session Log: [Date]

## 5-Question Reboot Check
1. **Where am I?** [Current phase]
2. **Where am I going?** [Next milestone]
3. **What's the goal?** [Research question]
4. **What have I learned?** [Key insights so far]
5. **What have I done?** [Actions completed]

## Timeline
| Time | Action | Result |
|------|--------|--------|
| [time] | [action] | [outcome] |

## Files Modified
- [filepath]: [change summary]

## Error Log
| Error | Cause | Resolution | Attempt |
|-------|-------|------------|---------|
```

## Critical Rules

### The 2-Action Rule
After every 2 search/view operations, **immediately** update `findings.md`. Never trust context window for research.

### Read Before Deciding
Before any major decision or phase transition, re-read `task_plan.md` to prevent attention manipulation.

### The 3-Strike Protocol
1. **Strike 1**: Diagnose and fix
2. **Strike 2**: Try alternative approach
3. **Strike 3**: Broader rethinking, escalate if still blocked

Log all attempts in `task_plan.md` Errors table.

## Final Deliverable: OPTIMAL_STRATEGY.md

```markdown
# [Strategy Title]

## Executive Summary
[1 paragraph: Recommendation FIRST, then key supporting evidence, then primary risk]

## Key Findings
1. [Quantified finding with source]
2. [Quantified finding with source]
3. [Quantified finding with source]

## Strategic Options

### Option A: [Name]
- **Description**: [What this path entails]
- **Pros**: [Benefits]
- **Cons**: [Drawbacks]
- **Confidence**: [HIGH/MED/LOW]

### Option B: [Name]
[Same structure]

### Option C: [Name] (if applicable)
[Same structure]

## Recommendation
**[Specific action]** because:
1. [Reason with evidence]
2. [Reason with evidence]
3. [Reason with evidence]

## Risks & Mitigations
| Risk | Severity | Mitigation | Residual Risk |
|------|----------|------------|---------------|
| [risk] | RED/YELLOW | [plan] | Accept/Monitor |

## Next Steps
| Action | Owner | Timeframe |
|--------|-------|-----------|
| [action] | [who] | [when] |

---
*Sources: See findings.md for complete evidence matrix*
```

## Quality Checklist

Before finalizing OPTIMAL_STRATEGY.md:
- [ ] All 5 phases in task_plan.md complete
- [ ] findings.md has 3+ sources per major claim
- [ ] Contradictory evidence explicitly addressed
- [ ] Recommendations are specific and actionable
- [ ] Risks are honest (not buried or minimized)
- [ ] Next steps have owners and timeframes
- [ ] Executive summary leads with recommendation

## Anti-Patterns

| Pattern | Problem | Fix |
|---------|---------|-----|
| Confirmation bias | Only citing supporting evidence | Mandate "Contradicting Sources" column |
| Analysis paralysis | Endless Phase 2 | Set source limit upfront |
| Vague recommendations | "Consider" language | Require specific actions |
| Source-free claims | Assertions without evidence | Reject any claim without source |
| Silent retry | Repeating failed approaches | 3-Strike Protocol with logging |
| Memory trust | Not writing to disk | Enforce 2-Action Rule |

## Example: Good vs Bad

### Good Strategy Output
> **Recommendation: Enter UK market via Tesco partnership in Q3.**
>
> Three sources support this: (1) Mintel UK ready meals report shows 8% YoY growth vs 2% US (HIGH confidence), (2) Tesco investor deck confirms 27% grocery share with D2C infrastructure, (3) Competitor scan found no dominant premium frozen player.
>
> Risk: Brexit supply chain friction (YELLOW). Mitigation: Local manufacturing commitment within 18 months.

### Bad Strategy Output
> The UK market looks promising and there are several options to consider. Further research may be needed to determine the optimal path forward.

*Problems: No specific recommendation, no sources cited, vague language, no quantification*
