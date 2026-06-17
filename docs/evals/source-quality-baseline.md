# Source-Specific Quality Baseline

**Date:** 2026-06-17
**DB:** signals.db (612 rows, schema_version=53)
**Window:** 365 days (90-day window returns 0 results — all labeled signals predate the 90-day cutoff; most recent labeled signal is 2026-03-01)
**Command:** `python -m ops.cli quality --db signals.db stats --days 365 --min-labeled 1`

## Per-Source FP Rates

| source_api | labeled | fp | tp | unsure | decided | fp_rate |
|------------|---------|----|----|--------|---------|---------|
| github | 2 | 2 | 0 | 0 | 2 | 100.00% |
| product_hunt | 2 | 2 | 0 | 0 | 2 | 100.00% |
| hacker_news | 157 | 151 | 2 | 4 | 153 | 98.69% |
| rss_feeds | 28 | 20 | 7 | 1 | 27 | 74.07% |
| news_api | 7 | 5 | 2 | 0 | 7 | 71.43% |
| greenhouse_jobs | 11 | 6 | 5 | 0 | 11 | 54.55% |
| ashby_jobs | 4 | 1 | 3 | 0 | 4 | 25.00% |

**Overall:** labeled=211, decided=206, fp=187, tp=19, fp_rate=90.78%

### Sources below min_labeled=10 (excluded from default stats output)

| source_api | labeled | decided | note |
|------------|---------|---------|------|
| github | 2 | 2 | Too small to rank |
| product_hunt | 2 | 2 | Too small to rank |
| news_api | 7 | 7 | Too small to rank |
| ashby_jobs | 4 | 4 | Too small to rank |

## Findings

- **Highest FP source:** `hacker_news` at 98.69% (153 decided signals — high-confidence measurement)
- **Lowest FP source:** `ashby_jobs` at 25.00% (4 decided signals — small sample, low confidence)
- **Only sources above min_labeled=10 with reliable signal:** `hacker_news` (153), `rss_feeds` (27), `greenhouse_jobs` (11)
- `greenhouse_jobs` (54.55% FP) and `rss_feeds` (74.07% FP) are the only sources with both reasonable sample size and materially lower FP rate than hacker_news
- `ashby_jobs` (25.00% FP, 4 samples) looks promising but needs more labeled volume before acting on it
- The 90-day window returns zero results — all labeled data predates 2026-03-19. Use `--days 365` for this corpus.

## Tuning actions taken

- [x] Added `--source-api` filter to `quality stats` CLI (P1-3-B)
- [ ] No tuning rules changed — this is the measurement-only baseline
- [ ] `hacker_news` suppression or LLM-only routing is a candidate for P1-4+ based on 98.69% FP

## Golden set additions

Signals added to `tests/fixtures/thesis_llm_golden_set.jsonl` from this session:
- None (expansion deferred to next tuning session per P1-3-B scope)
