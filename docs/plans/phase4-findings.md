# Findings & Decisions — Phase 4+ Full Roadmap

## Requirements
- Plan all 5 sub-phases of Phase 4+ (Dashboard, ACH, Active Hunter, Entity Resolution, Drift Monitoring)
- ~10 weeks estimated (Month 2+)
- Dashboard is UI overlay on existing CLI/CSV data (NOT greenfield)
- All intelligence already visible via CLI/CSV from Phases 2-3

## Research Findings

### Foundation Already Built
- **Streamlit dashboard** (`dashboard/app.py`) with views: login, health, ops_health, scheduler, cost_analysis, inbox, URL profiler, monitoring
- **API client** (`dashboard/api_client.py`) for backend communication
- **FastAPI backend** (`api/main.py`) with 8 routers: companies, actions, health, auth, entities, jobs, scheduler, public
- **Triage CLI** (run_pipeline.py): list, approve, reject, defer, detail — all data available
- **Batch publish** (workflows/batch_publisher.py): create/preview/commit/abort lifecycle
- **Intelligence layer**: case_law_retriever, exemplar_matcher, semantic_filter, functional_extractor, web3_detector
- **Entity resolution** (Phase G — DISABLED): entity_identity_store.py (669 lines), phase_g_entity_resolver.py, claim_fact_store.py
- **Health monitoring**: drift_detector.py, signal_health.py, quality stats/patterns

### Dashboard Existing Structure
```
dashboard/
  app.py              — Main Streamlit app (multi-page)
  api_client.py       — APIClient for backend
  views/
    login.py
    health.py
    ops_health.py
    scheduler.py
    cost_analysis.py
  components/
    company_card.py
    action_buttons.py
```
- Press On brand: Dark #292929, Beige #E0D8D1, White #FFFFFF, Light #F2F2F2

### Triage CLI Data Already Available
- `triage list --compact`: [ID] Company | schema summary | archetype | Sim (tp/fp) | Status
- `triage detail <id>`: Functional schema + case-law top-3 wins/losses + exemplar match + veto
- `triage approve/reject/defer <id> --reason "..."`
- CSV export: 21 columns including intelligence fields

### Entity Resolution (Phase G) Status
- Migrations 19-21 already applied (entity_aliases, entity_migrations, entity_key_aliases, entity_blocking_index, claim_facts)
- Feature flags: USE_PHASE_G_IDENTITY_RESOLUTION=false, USE_CLAIM_FACTS=false
- SHA256[:16] IDs, lexmin winner merge, transitive resolution
- Dependencies: rapidfuzz>=3.0.0, metaphone>=0.6

### Database State
- Schema v34 (34 migrations applied)
- Key tables: signals, company_files, review_items, thesis_classifications, functional_schemas, precedents, thesis_exemplars, signal_quality_metrics, publish_batches, entity_aliases

### Tech Stack Constraints
- SQLite single-writer (asyncio.Lock() for write coordination)
- No WebSockets (polling-based updates)
- Pydantic v2 for all request/response models
- pytest asyncio_mode=auto
- Windows-first (Python 3.11+)

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Keep Streamlit for dashboard | Already operational, existing patterns, no framework switch needed. Trigger migration only if latency thresholds fail. |
| Dashboard is dumb client (A1) | Single-writer SQLite constraint; all writes through FastAPI only, never import storage/* from dashboard |
| API contract-first (A2) | Cursor pagination, error envelope, idempotency keys, optimistic concurrency — defined in W0 before any endpoints |
| RBAC + audit as blocking work (A3) | UI actions (approve/reject/merge/promote) require authn/authz + immutable audit trail before shipping |
| Generic run/job abstraction (A4) | Hunter, canary, ACH, entity resolution all need start/poll/result pattern; avoid 5 custom polling implementations |
| Entity resolution BEFORE hunter (sequencing) | Identity shield must be in place before proactive sourcing generates new entities. Moved from W4→W2. |
| Canary scaffold in W0, baseline in W2 | Establish quality baseline BEFORE adding hunter/merge capabilities that change distributions |
| ACH CLI/API alongside triage (W1) | Faster value delivery; don't delay structured reasoning until separate wave |
| Write activation gated in W4 | Merge writes, hunter promotion, bulk actions enabled only after shadow validation passes gates |
| Event-sourced entity merges | Merges are sensitive; proposed→approved→applied→rolled_back lifecycle enables undo |
| Hunter bootstrap mode + negative feedback | Manual seed targets for cold start; auto-negative keywords from reviewer rejects prevent query drift |
| 4f + 4g phases added | Deployment hardening (migration rollback CI, cross-phase suites) + user validation (UAT, demos) are not optional |
| 208h estimate (was 142-156h) | Integration overhead, security, testing, UAT buffers add ~40%; 20% contingency = 250h max |

## Review Integration Log (2026-02-09)

### From Review Round 1 (Strategic)
- Added Wave 0 platform hardening (RBAC, contracts, audit, run abstraction)
- Added per-wave Definition of Done (user-facing, data, reliability, security)
- Added authn/authz as blocking prerequisite for UI actions
- Added run/job abstraction for long-running workflows
- Added optimistic concurrency + idempotency for triage endpoints
- Split 4a into read-only → state-changing releases
- Added confidence calibration / reliability diagrams to drift monitoring
- Defined label taxonomy (operator decisions vs eventual outcomes vs gold labels)
- Added merge "blast radius" preview

### From Review Round 2 (Operational)
- Resequenced: entity resolution (W2) before hunter (W3)
- Canary scaffolded left (W0) and baselined (W2) before adding capabilities
- Added hunter bootstrap mode + spend circuit breaker + negative feedback loop
- Added entity-normalized dedupe for hunter results
- Event-sourced merge lifecycle (not simple undo)
- Merge rollback drills as explicit task
- ACH versioned with `builder_version` + `inputs_hash` for reproducibility
- Tribunal must cite evidence IDs (no fabrication guardrail)
- Expanded canary strategy: stratify by archetype/collector/confidence, grow golden set
- Separated drift types: data drift, concept drift, model/heuristic drift
- Added alert workflow: ack/snooze, link to run, MTTA tracking
- Added 4f (deployment hardening) + 4g (user validation) phases
- Added 5 new test layers: contract, property-based, sandbox safety, migration downgrade, cross-phase integration
- Adjusted estimate from 142-156h to 208h + 20% contingency

## Resources
- `task_plan_v1.1.md` — Master phased plan (Phase 4+ section at line 356)
- `findings_v1.1.md` — Architectural analysis
- `architectural_review_v1.1.md` — Senior architect review
- `docs/plans/identity-charter.md` — Entity identity decisions
- `dashboard/app.py` — Existing dashboard entry point
- `api/main.py` — FastAPI backend entry point
