# Discovery KPI Baseline (Phase 0, task p0.10)

**Computed at:** 2026-04-06T19:06:07.414107+00:00
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
   `analytics.kg_bridge.class_for_signal_row`, which defers to
   `verification.evidence_families.get_family()` (the production-
   authoritative classifier). No schema migration. The result will change
   when new collectors land in the ontology table or when
   `verification/evidence_families.py` adds new (signal_type, source_api)
   mappings.

## KPI 5 classifier provenance (E3)

KPI 5 (cross-source convergence) is computed using the
**`production_evidence_family`** path:

- `production_evidence_family` *(default after E3)*: per-signal
  classification via `analytics.kg_bridge.class_for_signal_row(signal_type,
  source_api)`, which defers to the production-authoritative
  `verification.evidence_families.get_family()`. This path uses BOTH
  `signals.signal_type` and `signals.source_api`, so it correctly handles
  source-API overrides for ambiguous signal types and collapses
  `linkedin_company` (web_presence) and `incorporation` (regulatory) into
  the same INFRASTRUCTURE_INTENT discovery class.

- `source_api_only` *(pre-E3 path; preserved for the source-shape branch
  at line ~380 because `company_files.source_apis` has only source-api
  strings, no signal_type)*: classification via
  `analytics.evidence_ontology.classify_source_api(source_api)`. This path
  is what the `promoted_sole_ambient_count` and
  `promoted_with_any_discovery_class` numbers above are computed under,
  because the source-shape branch operates on a list of source-api strings
  with no signal_type available.

This means the report mixes two classifiers by design: the headline KPI 5
uses the production classifier; the source-shape distribution uses the
simpler source-api map. The two classifiers agree on most cases but
disagree where the production taxonomy distinguishes signals that the
simple map collapses (or vice versa). The disagreement is documented in
`artifacts/red-team-execution/phase0/kg-enhancement-design.md` §4.

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
