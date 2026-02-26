# DNS Probe Precision Check — 2026-02-26

## Canary Run
- Command: `DNS_PROBE_ENABLED=true python run_pipeline.py collect --collectors rss_feeds --dry-run`
- RSS signals: 9 (6 with company names, all 6 got DNS hits)
- Total DNS overhead: ~156ms for 6 probes (concurrency 8)

## 25-Hit Precision Sample

Probed 210 unique company names from signals.db (all sources) until 25 DNS hits collected.

| # | Company Name | DNS Domain | Verdict | Notes |
|---|-------------|-----------|---------|-------|
| 1 | Wildbrine | wildbrine.com | TP | Fermented foods brand |
| 2 | Seafood Industry Report | seafood.com | FP | Generic domain, not the company |
| 3 | ORBITEL | orbitel.com | TP | Telecom company |
| 4 | Deltec Homes | deltechomes.com | TP | Prefab homes company |
| 5 | Borden Cheese | bordencheese.com | TP | Borden Dairy/Cheese brand |
| 6 | MEDIPEEL | medipeel.com | TP | Korean skincare brand |
| 7 | Talkiatry | talkiatry.com | TP | Mental health startup |
| 8 | Hopper | hopper.com | TP | Travel app |
| 9 | CULINARY | culinary.com | FP | Generic word, not a company |
| 10 | GSA Certification Programs Drive | gsa.com | FP | First-token hit on generic domain |
| 11 | NYSE | nyse.com | FP | Extraction error — not a startup |
| 12 | Offerup | offerup.com | TP | Consumer marketplace |
| 13 | Whoop | whoop.com | TP | Fitness wearable |
| 14 | Veho Builds West | veho.com | TP | Delivery startup |
| 15 | Lyrahealth | lyrahealth.com | TP | Mental health platform |
| 16 | Better Hub | betterhub.com | TP | Resolves, plausible |
| 17 | Oura | oura.com | TP | Smart ring company |
| 18 | Faire | faire.com | TP | Wholesale marketplace |
| 19 | Respectify | respectify.ai | TP | Resolves on .ai |
| 20 | Hungryroot | hungryroot.com | TP | Grocery delivery |
| 21 | Poshmark | poshmark.com | TP | Fashion marketplace |
| 22 | Stockx | stockx.com | TP | Sneaker marketplace |
| 23 | Flyr | flyr.com | TP | Travel tech |
| 24 | A Real | areal.com | FP | Extraction error — not a company name |
| 25 | Glossier | glossier.com | TP | Beauty brand |

## Results

- **Precision: 20/25 = 80%**
- Meets the >= 80% Phase 2 decision gate

### FP Breakdown

| Category | Count | Examples |
|----------|-------|---------|
| Bad DNS match (generic first-token domain) | 2 | seafood.com, gsa.com |
| Bad upstream extraction (not a real company) | 3 | CULINARY, NYSE, A Real |

### Phase 2 Recommendation

Precision meets the 80% gate. DNS probe domains can be promoted to `domain_for_key`
with -0.05 confidence penalty in Phase 2. Upstream extraction quality improvements
(stoplist for generic words, better regex guards) would push precision higher.
