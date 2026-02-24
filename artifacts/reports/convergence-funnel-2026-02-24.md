# Convergence Funnel Diagnosis — 2026-02-24

Generated after COUNT(*) write verification confirmed persistence is working
(N1=311 -> N2=317, +6 new signals, algebraic invariant holds: stored+deduped=collected).

## Stage 1: Signal Volume by Source (All Time)

| Source | Signals | % |
|--------|---------|---|
| hacker_news | 156 | 49.2% |
| arxiv | 99 | 31.2% |
| rss_feeds | 46 | 14.5% |
| news_api | 12 | 3.8% |
| product_hunt | 2 | 0.6% |
| github | 2 | 0.6% |
| **TOTAL** | **317** | |

5 collectors consistently dark (0 signals across all runs):
domain_whois, github (mostly), job_postings, sec_edgar, uspto

## Stage 2: Signal-to-Company Linkage

100% linkage rate across ALL sources. But key type quality varies:

| Source | Key Pattern | Quality |
|--------|------------|---------|
| hacker_news | `domain:xxx` (actual company domains) | GOOD |
| arxiv | `arxiv_author:xxx` (author names) | UNUSABLE for convergence |
| rss_feeds | `rss_xxx` content hashes (43/46) | UNUSABLE — only 3/46 have `name_loc:` |
| news_api | `domain:xxx` (PUBLISHER domains) | WRONG — extracts bostonglobe.com, not article subject |
| product_hunt | `domain:xxx` | OK (only 2 signals) |
| github | `github_org:xxx` | OK (only 2 signals) |

## Stage 3: Company Files

| Status | Count | Sources |
|--------|-------|---------|
| promoted | 64 | hacker_news ONLY |
| thin | 134 | arxiv (99), rss_feeds (30), news_api (5) |

## Stage 4: Multi-Source Distribution

| Source Count | Promoted Files |
|-------------|---------------|
| 1-source | 64 (100%) |
| 2+ source | 0 (0%) |

## Stage 5: Cross-Source Key Overlap

**ZERO OVERLAP** between non-HN canonical keys and promoted file keys.
No non-HN signal shares a canonical_key with any promoted company file.

## Root Cause

The convergence bottleneck is **canonical key quality**, not promotion policy or identity resolution.

1. **rss_feeds**: 93% of keys are content hashes (`rss_xxx`) — structurally can't match anything
2. **news_api**: Extracting publisher URL domains instead of article subjects
3. **arxiv**: Author-based keys — can never converge with company domain keys
4. **5 dark collectors**: Produce nothing despite running successfully

## Required Fixes (Priority Order)

1. **Fix rss_feeds key extraction** — most signals (43/46) get hash keys instead of company identifiers
2. **Fix news_api key extraction** — needs NER/entity extraction (`COMPANY_EXTRACTION_MODE=ner_active`)
3. **Diagnose dark collectors** — domain_whois, sec_edgar, job_postings, github, uspto
4. **arxiv** — consider affiliation extraction, or accept it won't contribute to convergence

## Secondary Finding: api_calls Instrumentation Gap

Every collector shows `api_calls=0` even when producing signals.
news_api shows 5-7 retries with 0 api_calls — confirmed wiring bug.
