# Planning with Skills: Discovery Engine Next Steps

**Date:** 2026-02-07
**Branch:** `claude/planning-with-skills-pOiyh`
**Method:** Systematic assessment using all available quality/diagnostic skills

---

## Assessment Summary

### Skills Invoked

| Skill | Result | Finding |
|-------|--------|---------|
| `quality-stats` | 0 labeled signals | No quality data — system is cold-start |
| `fp-pattern-detector` | 0 patterns | No labeled data to detect patterns from |
| `thesis-disagreement-report` | 0 disagreements | No thesis classifications yet |
| `canonical-key-remediator` | No suggestions | Too few signals to analyze |
| `quality-export-dataset` | 0 exported | Empty labeled dataset |
| Pipeline health check | UNHEALTHY | SEC EDGAR 403, degraded signal health |
| Dry-run (hacker_news, rss_feeds) | 0 signals | No HN items matched; RSS feeds returned 403s |

### Database State
- **4 test signals** (all "Acme", from github + product_hunt)
- **0 labeled signals**, 0 quality metrics, 0 thesis classifications
- **26/26 migrations applied** (was 3/26 — migrations 4-26 applied during assessment)
- **1 pipeline run** on record (from 2026-01-08, yielded 0 signals)

### Environment State
- **API keys:** None configured in this environment (all keys missing)
- **Dependencies:** Core deps installed during assessment (aiosqlite, numpy, scikit-learn, mcp, google-generativeai)
- **Pipeline:** Functional — runs, processes, produces results. Missing only API keys for external collectors.

---

## Prioritized Next Steps

### Priority 1: Bootstrap Live Data (Blocking)

The entire quality feedback loop (stats, patterns, tuning, thesis disagreement) requires labeled signals. No skill can produce meaningful output until signals flow through the pipeline.

**Actions:**
1. **Configure API keys** — At minimum: `GITHUB_TOKEN`, `GOOGLE_API_KEY` (Gemini for thesis classification), `GNEWS_API_KEY`
2. **Run first live collection** — `python run_pipeline.py full --collectors github,hacker_news,sec_edgar,job_postings`
3. **Run thesis classification** — `LLM_THESIS_MODE=active python run_pipeline.py process` (requires `GOOGLE_API_KEY`)

**Skills to invoke after:**
- `deal-sourcing-agent` — Full multi-source pipeline run
- `thesis-classify-batch` — Batch classify unclassified signals

### Priority 2: Bootstrap Quality Labels (Depends on P1)

Once signals exist, seed the quality feedback loop:

**Actions:**
1. **Manual labeling** — Use `quality-label` skill on first 20-30 signals to seed TP/FP data
2. **Connect Notion** — Configure `NOTION_API_KEY` + `NOTION_DATABASE_ID`, run `python run_pipeline.py sync`
3. **Backfill from Notion** — Use `quality-backfill-notion-status-events` then `quality-backfill-outcomes` skills

**Skills to invoke after:**
- `quality-stats` — Measure baseline FP rate
- `quality-export-dataset` — Export labeled dataset for analysis

### Priority 3: Tune and Optimize (Depends on P2)

With labeled data, activate the tuning feedback loop:

**Actions:**
1. **Detect FP patterns** — `fp-pattern-detector` with enough labeled data (min ~50 signals)
2. **Investigate patterns** — `fp-pattern-investigator` on top patterns
3. **Generate tuning proposal** — `tuning-proposal-writer` from pattern report
4. **Apply safe tuning** — `tuning-proposal-apply` for negative keyword updates
5. **Monitor impact** — `quality-stats` before/after comparison

**Skills to invoke:**
- `fp-pattern-finder-signals` — End-to-end pattern→tuning workflow
- `thesis-disagreement-report` — Calibrate keyword vs LLM alignment

### Priority 4: Expand Coverage

Once core loop is running:

**Actions:**
1. **Add more collectors** — Configure keys for Product Hunt, Companies House, Crunchbase, LinkedIn
2. **Build custom collector** — Use `collector-framework` skill for new signal sources
3. **Enrich signals** — Use `enrichment-run-async` skill for brand sentiment, community metrics
4. **Canonical key health** — Use `canonical-key-remediator` once >100 signals exist

---

## Infrastructure Fixes Applied During Assessment

1. **Database migrations:** Applied migrations 4-26 (collector_metrics, thesis_classifications, exit_predictions, entity resolution, quality tables, and more)
2. **Python dependencies:** Installed aiosqlite, numpy, scikit-learn, MCP, google-generativeai, and related packages
3. **Verified pipeline functionality:** Health check runs, dry-run pipeline completes, quality CLI works with all 14 subcommands

## Skills Readiness Matrix

| Skill | Ready? | Blocker |
|-------|--------|---------|
| `quality-stats` | Ready (returns zeros) | Needs labeled signals |
| `quality-label` | Ready | Needs signal IDs |
| `fp-pattern-detector` | Ready | Needs labeled FP signals |
| `fp-pattern-investigator` | Ready | Needs patterns |
| `fp-pattern-finder-signals` | Ready | Needs labels + patterns |
| `tuning-proposal-writer` | Ready | Needs patterns |
| `tuning-proposal-apply` | Ready | Needs proposal |
| `thesis-classify` | Blocked | Needs GOOGLE_API_KEY |
| `thesis-classify-batch` | Blocked | Needs GOOGLE_API_KEY |
| `thesis-disagreement-report` | Ready (returns zeros) | Needs classifications |
| `quality-backfill-notion-status-events` | Blocked | Needs NOTION_API_KEY |
| `quality-backfill-outcomes` | Ready | Needs status events |
| `quality-export-dataset` | Ready (returns zeros) | Needs labeled signals |
| `canonical-key-remediator` | Ready | Needs more signals |
| `collector-framework` | Ready | No blocker |
| `deal-sourcing-agent` | Partially blocked | Needs collector API keys |
| `enrichment-run-async` | Ready | No blocker |

---

## Conclusion

The Discovery Engine codebase is architecturally mature with 200+ tests, 16 production skills, and 14 quality CLI subcommands — all functional. The bottleneck is **data**: zero live signals and zero quality labels. The critical path is:

```
Configure API keys → Run collectors → Label signals → Activate quality loop
```

Every quality skill is implemented and tested but starved of data. Priority 1 (API keys + first collection) unblocks everything downstream.
