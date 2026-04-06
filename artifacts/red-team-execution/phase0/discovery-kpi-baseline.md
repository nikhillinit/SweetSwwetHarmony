# Discovery KPI Baseline (Phase 0, task p0.10)

**Computed at:** 2026-04-06T18:02:30.686191+00:00
**Window:** last 90 days
**Queue size for KPI 2:** 20

## Headline numbers

| KPI | Value | Sample size |
|---|---|---|
| 1. Lead time vs first public mention (median, days) | — | 0 (with public mention) / 20 (without) |
| 2. Analyst precision at queue size 20 | 15.0% | 3/20 labelled |
| 3. Meetings booked rate | 9.8% | 58/589 Notion entries |
| 4. Pre-launch / pre-fundraise detection rate (GNews-only) | 42.1% | 8/19 TPs |
| 5. Cross-source convergence rate (≥2 non-ambient classes) | 0.0% | 0/118 promoted companies |

## Caveats

1. **KPI 4 is GNews-only.** Crunchbase data is not configured in this
   environment (no `CRUNCHBASE_API_KEY`). The pre-launch detection rate
   biases toward news-mentioned companies; it cannot detect "pre-fundraise"
   in the strict Crunchbase-funding-event sense. Adding Crunchbase is a
   Phase 1+ prerequisite for the strict version of this KPI.

2. **KPI 2 is sensitive to label availability.** With sparse
   `signal_quality_metrics`, the precision-at-queue denominator can be
   dramatically smaller than the queue size, inflating or deflating the
   headline. The labelling sprint (`p0.3`) is the primary remedy.

3. **KPI 5 uses derived classes.** Computed via
   `analytics.evidence_ontology.aggregate_company_evidence`. No schema
   migration. The result will change when new collectors land in the
   ontology table.

## Per-source signal counts (window)

| Source API | Count |
|---|---|
| arxiv | 275 |
| hacker_news | 192 |
| rss_feeds | 87 |
| manual_seed_buzz | 20 |
| news_api | 14 |
| greenhouse_jobs | 13 |
| ashby_jobs | 4 |
| lever_jobs | 3 |
| github | 2 |
| product_hunt | 2 |

**Total signals in window:** 612
**Total promoted companies:** 118

## Promoted-cohort source-shape distribution (lifetime)

This is the most interpretable number in the report. It answers: *what
combinations of sources have actually triggered the OR-based promotion
rule in `workflows/thin_file_manager.py`?*

| Source shape | Promoted count |
|---|---|
| `hacker_news` | 98 |
| `greenhouse_jobs, manual_seed` | 13 |
| `ashby_jobs, manual_seed` | 4 |
| `lever_jobs, manual_seed` | 3 |

**Sole-ambient promotions:** 98 (83.1% of all promoted)
**Promotions with any discovery class:** 20

A "discovery class" excludes both AMBIENT_CORROBORATION (popularity
signals) and ANALYST_SEED (manual analyst entries). The strategy
document's central diagnosis — that the system is over-reliant on
single-class popularity signals — is **directly verifiable from this
table**.
