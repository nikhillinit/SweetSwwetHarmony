# Collector Inventory + Legal Sign-off (Phase 0, task p0.2)

**Status:** Design — Phase 0 sign-off
**Date:** 2026-04-06
**Plan reference:** Red-team v2 Phase 0 task `p0.2`

## Purpose

For each new collector proposed by the strategy document, document:

1. Data source legality
2. API key requirement and whether we have it
3. Expected daily/weekly volume
4. Overlap with existing collectors
5. Keep / drop / merge decision for v1 (Phase 0 → Phase 3)

This document also re-affirms the operating posture for **existing**
collectors that the strategy document re-frames (e.g. ATS as
enrichment-only, HN as ambient corroboration only).

## New collectors

### 1. Shadow CT-log collector (`analytics/shadow_collectors/ct_log.py`)

| Attribute | Value |
|---|---|
| **Status** | Phase 0 — shadow scaffold built (`p0.7`) |
| **Data source** | crt.sh (free), Cloudflare CT, public CT logs (RFC 6962) |
| **Legality** | Public by RFC. Zero legal exposure. |
| **API key** | None required |
| **Rate budget** | 60 req/hr (crt.sh recommends < 1 req/sec) |
| **Expected volume** | ~500 certs/run, 1 run/day → ~500 candidate domains/day |
| **Overlap** | Complements `domain_whois.py` (which queries RDAP for already-known domains). CT logs surface NEW domains; RDAP enriches them. |
| **Decision** | **KEEP for Phase 3 promotion.** First-wave shadow collector. |
| **Promotion path** | Backtest against Phase 0 labelled cohort → 7-day shadow run → 14-day regret check → governance promotion |

### 2. Shadow DNS fingerprint collector (`analytics/shadow_collectors/dns_fingerprint.py`)

| Attribute | Value |
|---|---|
| **Status** | Phase 0 — shadow scaffold built (`p0.8`) |
| **Data source** | Public DNS (MX, TXT, NS records) |
| **Legality** | Public infrastructure. No legal issue. |
| **API key** | None — uses any DNS resolver |
| **Rate budget** | 600 lookups/hr |
| **Expected volume** | Inputs come from CT-log + domain_whois discoveries; ~200 domains/run, 1 run/day |
| **Overlap** | **Logical extension of `collectors/domain_whois.py`** — that collector does RDAP only; this adds DNS-record fingerprinting on top. The strategy document called these "DNS fingerprint" enrichment. In Phase 3 promotion, fold into `domain_whois.py` as a second pass. |
| **Decision** | **KEEP. Promote into `collectors/domain_whois.py` as an extension, not a separate file.** The shadow collector exists in the analytics tree only because Phase 0 forbids touching production code. |

### 3. Shadow GitHub negative-space collector (`analytics/shadow_collectors/gh_negative_space.py`)

| Attribute | Value |
|---|---|
| **Status** | Phase 0 — shadow scaffold built (`p0.9`); requires `data/shadow/founder_watchlist.csv` (`p0.6`) |
| **Data source** | GitHub REST API: `/users/<username>/events/public`, `/users/<username>/repos` |
| **Legality** | Public GitHub data, allowed by GitHub ToS for authenticated rate-limited access. Per-founder opt-out is not currently supported (no formal mechanism in the API), so the watchlist is restricted to founders linked to companies already in our pipeline (i.e., founders whose data we already store via existing collectors). |
| **API key** | `GITHUB_TOKEN` (already configured in `.env`) |
| **Rate budget** | **2000 req/hr** (40% of `GITHUB_TOKEN` 5000/hr cap, leaving headroom for production `github.py` and `github_activity.py`) |
| **Expected volume** | ≤500 founders × ~3 calls/founder = ~1500 calls per scan; 1 scan/day |
| **Overlap** | None — `github.py` watches trending repos, `github_activity.py` watches founder commit cadence. Negative-space watches the *absence* of activity plus org-membership changes, which neither does. |
| **Hard prerequisite** | `scripts/build_founder_watchlist.py` must produce a non-empty `data/shadow/founder_watchlist.csv` BEFORE this collector is permitted to run. The collector refuses to issue API calls with an empty watchlist (verified by test). |
| **Decision** | **KEEP for Phase 3 promotion**, conditional on watchlist availability. |

### 4. Founder watchlist (LinkedIn-powered) — DEFERRED

| Attribute | Value |
|---|---|
| **Status** | DEFERRED — blocked on Proxycurl key + LinkedIn ToS review |
| **Data source** | LinkedIn (currently disabled) |
| **API key** | `PROXYCURL_API_KEY` — **MISSING** in `.env` |
| **Decision** | **DEFER until Proxycurl is funded AND legal review of LinkedIn ToS for "watch mode" use is complete.** The shadow GH negative-space collector (`p0.9`) already covers part of this surface using GitHub-only data, with a watchlist sourced from existing `founders` table rows (no LinkedIn dependency for the v1 path). |
| **Re-evaluation trigger** | Either (a) Proxycurl key acquired, or (b) shadow GH negative-space delivers <30% TP rate after 30 days, indicating we need richer signals |

### 5. Team-fracture aggregation — DEFERRED

| Attribute | Value |
|---|---|
| **Status** | DEFERRED |
| **Data source** | Cross-source PII joins (LinkedIn departures, GitHub org changes, etc.) |
| **Legality** | **Requires explicit legal review.** Cross-source PII correlation is the territory the project's own governance prohibitions explicitly call out (Sybil-adjacent territory). |
| **Decision** | **DEFER until legal review produces a clean data-source story.** No prototype work in Phase 0–3. |

### 6. Dependency-injection founder-repo heuristics — DEFERRED

| Attribute | Value |
|---|---|
| **Status** | DEFERRED |
| **Engineering cost** | Heavy (parses repo dependency graphs) |
| **Marginal yield** | Unclear — no evidence it correlates with consumer-startup signal |
| **Decision** | **Treat as a research project, not infrastructure.** Low priority but not zero. Revisit only after Phase 5. |

## Re-framed existing collectors (NOT new builds, but role changes)

### A. ATS / `collectors/job_postings.py` — enrichment-only

The strategy document specifies (and this inventory re-affirms): ATS data
should be **targeted enrichment** for already-surfaced candidates, not a
brute-force discovery source.

- **Current behaviour:** runs as a regular collector, scans Greenhouse / Lever boards
- **Target behaviour (Phase 1+):** triggered by an existing company appearing in another collector; ATS check enriches that company's `company_files` row with hiring evidence
- **Phase 0 action:** none. Document the intent. Code change is post-2026-04-19.
- **Why:** the production v1.6.0 thesis classifier already addresses the FP problem ATS discovery would have created. Restricting ATS to enrichment is defence-in-depth.

### B. HN / ArXiv / RSS / News / PH — demoted to ambient corroboration

Per the evidence ontology (`p0.1`), these sources are classified as
`AMBIENT_CORROBORATION` and **never sole-qualify** a company-episode for the
shadow tier-2 surface.

- **Production behaviour (unchanged in Phase 0):** they continue to write to
  `signals` and reach the thesis classifier. `LLM_THESIS_MODE=active` already
  filters HN at 100% correctness in production (per memory).
- **Shadow ladder behaviour (Phase 1+):** ambient sources contribute weight
  but never initiate a tier-2 promotion in the shadow sidecar.
- **Phase 0 action:** none in production. The demotion is encoded in
  `analytics/evidence_ontology.AMBIENT_ONLY_SOURCES` and exercised by
  `evaluate_shadow_tier`.

### C. `collectors/linkedin.py` — paused, not removed

| Attribute | Value |
|---|---|
| **Current** | Disabled (no `PROXYCURL_API_KEY`) |
| **Decision** | Keep the file. Do not delete. Re-enable conditional on key acquisition. |

## Governance prohibitions (re-affirmed for Phase 0+)

These are the load-bearing constraints from `.claude/rules/invariants.md` that
every new collector spec must respect. Listed here so the next reviewer cannot
miss them:

1. **No SMTP probing.** Do not attempt to verify email accounts by connecting to MX servers.
2. **No brute-force ATS enumeration.** Greenhouse / Lever access is restricted to known boards via `collectors/job_postings.py` discovery, not URL-iteration.
3. **No Sybil community scraping.** Discord / Telegram / Reddit are not in the v1 collector roster and require explicit governance review before being added.
4. **All external access via the internal MCP server boundary.** Shadow collectors that hit external APIs must do so through the existing `discovery_engine.mcp_server` proxy when promoted to production. (Phase 0 shadow collectors are exempt because they write only to the shadow store and run only in supervised batches.)
5. **Schema preflight before any Notion operation.** Shadow collectors never reach Notion, so this constraint is vacuously satisfied.

## Sign-off summary (Phase 0)

| Collector | Phase 0 status | Phase 3 plan |
|---|---|---|
| Shadow CT-log | Built (`p0.7`) | Promotion candidate #1 |
| Shadow DNS fingerprint | Built (`p0.8`) | Fold into `domain_whois.py` |
| Shadow GH negative-space | Built (`p0.9`), needs watchlist | Promotion candidate #2 |
| Founder watchlist (LinkedIn) | **DEFERRED** | Re-evaluate when Proxycurl funded |
| Team fracture | **DEFERRED** | Blocked on legal review |
| DI heuristics | **DEFERRED** | Research project only |
| ATS as enrichment-only | Documented | Code change post-2026-04-19 |
| HN/Arxiv/RSS/News/PH demotion | Encoded in evidence ontology | No production change in Phase 0 |
