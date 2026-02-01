---
name: deal-sourcing-agent
description: Run the Discovery Engine pipeline to find new consumer companies. Use when the user asks to "find deals", "source companies", "run the pipeline", "discover startups", or "search for prospects" in CPG, health tech, travel, or marketplaces.
user-invocable: true
allowed-tools: [Bash, Read]
license: MIT
compatibility: claude-code >= 1.0.0
metadata:
  version: 1.0.0
  category: workflow-automation
  author: Press On Ventures
---

# Deal Sourcing Agent

Transform CLI-based discovery into a guided, conversational workflow for sourcing consumer companies.

## When to Use This Skill

This skill activates when you want to:
- Find new investment prospects
- Run the discovery pipeline
- Source companies in specific sectors (CPG, health tech, travel, marketplaces)
- Review and push qualified signals to Notion CRM

**Trigger phrases:**
- "Help me find new deals"
- "Run the discovery pipeline"
- "Source companies in [sector]"
- "What new prospects do we have?"
- "Check for new SEC filings"

## Quick Start

**Simplest invocation:**
```
User: "Find me some new deals"
```

The skill will guide you through 6 steps: Configuration → Collection → Processing → Review → Push → Health Check.

## Workflow

### Step 1: Configuration
Ask which collectors to run and which sectors to focus on.

**Collector Presets:**
| Preset | Collectors | Duration | Best For |
|--------|-----------|----------|----------|
| Fast | github, sec_edgar, companies_house | ~2 min | Quick daily scan |
| All | 16 collectors | ~10 min | Comprehensive weekly search |
| Custom | User selects | Varies | Specific signal types |

**Validation Gate:** User confirms configuration before proceeding.

### Step 2: Collection
Execute collectors and report results.

```bash
python run_pipeline.py collect --collectors <preset>
```

**Output:** Signals collected, duplicates found, collector status (✓/✗)

**Decision Point:**
- A) Proceed to processing
- B) Adjust collectors
- C) Cancel

### Step 3: Processing
Run verification gate and thesis filter on collected signals.

```bash
python run_pipeline.py process
```

**Output:**
- Qualified signals (ready for push)
- Held signals (need review)
- Rejected signals (excluded by thesis)

**Decision Point:**
- A) Review qualified signals
- B) Push all qualified to Notion
- C) Review held signals
- D) Show metrics

### Step 4: Review (Optional)
Display top qualified signals with confidence scores.

```bash
python run_pipeline.py pipeline qualified --limit 20
```

**Output:** Table with company name, canonical key, confidence, signal types, why now.

**Decision Point:**
- A) Push all to Notion
- B) Filter further
- C) Exclude specific companies

### Step 5: Push to Notion
Sync qualified signals to Notion CRM.

```bash
# Dry run preview
python run_pipeline.py pipeline push --dry-run

# Actual push (after confirmation)
python run_pipeline.py pipeline push --confirm
```

**Output:**
- Prospects created (Status: "Source" or "Tracking")
- Prospects updated
- Prospects skipped (duplicates)
- Notion URLs

### Step 6: Health Check
Run post-execution diagnostics and recommend next steps.

```bash
python run_pipeline.py health --json
```

**Output:**
- Component health (Database, APIs, cache)
- Anomaly detection results
- Rate limit status
- Recommendations (review held signals, schedule next run)

## Collector Guide

See [references/collector-guide.md](references/collector-guide.md) for complete list of 16 collectors, API requirements, and signal strengths.

**Fast Preset Collectors:**
- **github** - Trending repos, spike detection (0.5-0.7 confidence)
- **sec_edgar** - SEC Form D filings (0.6-0.8 confidence)
- **companies_house** - UK incorporations (0.6-0.8 confidence)

## Error Handling

### Missing API Keys
If a collector fails due to missing credentials:

```
⚠ Missing API Key: GITHUB_TOKEN

Setup:
1. Generate token at: https://github.com/settings/tokens
2. Add to .env file: GITHUB_TOKEN=ghp_xxx
3. Restart skill

Alternative: Run other collectors without GitHub
```

### Rate Limits
If rate limited:

```
⚠ GitHub Rate Limit Exceeded

Resets at: 2026-01-31 14:30 UTC (in 42 minutes)

Options:
  A) Wait 42 minutes, then retry
  B) Run other collectors
  C) Use authenticated token (increases limit)
```

### Zero Qualified Signals
If no signals pass verification:

```
ℹ No Qualified Signals

Results: 0 qualified, 12 held, 3 rejected

Next Steps:
  A) Review held signals
  B) Adjust thesis filters
  C) Run different collectors
```

For complete troubleshooting guide, see [references/troubleshooting.md](references/troubleshooting.md).

## Notion Schema

The pipeline pushes to Notion with these fields:
- **Status:** "Source" (multi-source, high confidence) or "Tracking" (single source)
- **Discovery ID:** Unique identifier for tracking
- **Canonical Key:** Deduplication key (domain:example.com)
- **Confidence Score:** 0.0-1.0 routing score
- **Signal Types:** Multi-select (github, sec_edgar, etc.)
- **Why Now:** Narrative explaining timing

See [references/notion-schema.md](references/notion-schema.md) for complete schema and routing logic.

## Examples

### Basic Usage
[See examples/basic-usage.md](examples/basic-usage.md)

User: "Find me some new deals"
→ Guided through Fast preset (2 min)
→ 12 qualified signals pushed to Notion

### Sector-Specific
[See examples/sector-specific.md](examples/sector-specific.md)

User: "Source consumer CPG companies"
→ Collectors filtered by CPG keywords
→ Thesis filter emphasizes food/beverage/beauty

### Advanced Options
[See examples/advanced-options.md](examples/advanced-options.md)

- Run single collector
- Dry-run mode for testing
- View detailed metrics
- Import from CSV

## Common Scenarios

| Scenario | Command Flow |
|----------|--------------|
| Daily quick scan | Fast preset → Process → Push |
| Weekly deep dive | All preset → Review held → Push selected |
| Sector focus | Custom collectors → Filter by keywords |
| Debug low signals | Metrics → Health → Adjust collectors |

## Related Skills

- **thesis_matching.md** - Investment thesis evaluation criteria
- **founder_evaluation.md** - Founder intelligence assessment
- **signal_quality.md** - Quality tiers and freshness scoring

## Technical Details

For pipeline architecture, stage-by-stage flow, and `PipelineStats` schema, see [references/pipeline-architecture.md](references/pipeline-architecture.md).

## Success Criteria

**You'll know this skill is working when:**
- Time to first qualified signal < 3 minutes
- You complete workflow without errors
- Qualified signals appear in Notion CRM
- You understand what was found and why

**Metrics:**
- Typical batch: 5-20 qualified signals
- Collector success rate: >85%
- False positive rate: <15%
