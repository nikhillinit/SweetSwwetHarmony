# Architecture

**Analysis Date:** 2026-04-08

## Pattern Overview

**Overall:** Plugin-based signal pipeline architecture with staged gating and multi-LLM classification.

**Key Characteristics:**
- Pluggable collectors that feed a unified signal store
- Staged verification gates (thesis filtering → verification → confidence routing)
- Notion CRM as operational checkpoint (suppression cache, batch publishing)
- Governed state transitions via environment variables and feature registry
- Async/await throughout for horizontal scalability
- Signal deduplication via canonical keys (domain, company registry, GitHub org)

## Layers

**Collection Layer:**
- Purpose: Gather raw signals from 16+ external sources (GitHub, SEC, news, jobs, etc.)
- Location: `collectors/` (base: `collectors/base.py`)
- Contains: Collector implementations (github.py, sec_edgar.py, crunchbase.py, etc.) with async HTTP, retry logic, rate limiting
- Depends on: SignalStore (for persistence), HTTP clients, API credentials
- Used by: DiscoveryPipeline.run_collectors()

**Storage Layer:**
- Purpose: Persistent signal storage with deduplication, processing state tracking, migrations
- Location: `storage/signal_store.py` (core), `storage/migrations/` (schema versioning)
- Contains: SQLite tables for signals, signal_processing, suppression_cache, audit_events, identity mappings
- Depends on: aiosqlite (async), schema migrations (v51 current)
- Used by: All pipeline stages, verification gates, Notion pusher

**Processing Layer:**
- Purpose: Apply thesis classification and confidence scoring
- Location: `workflows/pipeline.py` (orchestrator), `consumer/` (signal processing), `utils/thesis_filter.py` (routing)
- Contains: ThesisFilter (keyword + LLM gates), SignalProcessor (trigger → classify), VerificationGate (multi-source convergence)
- Depends on: Gemini LLM (optional, feature-gated), SignalStore
- Used by: NotionPusher, batch processing workflows

**Routing Layer:**
- Purpose: Gate signals for Notion push based on confidence and source count
- Location: `verification/verification_gate_v2.py`, `workflows/notion_pusher.py`
- Contains: VerificationGate (cross-source verification, founder score, velocity), Notion routing logic
- Depends on: Signal convergence heuristics, investor matching, team shape analysis
- Used by: NotionPusher.process_batch()

**Connector Layer:**
- Purpose: Interface to Notion CRM with schema validation
- Location: `connectors/notion_connector_v2.py` (v2 API), `connectors/notion_transport.py` (HTTP)
- Contains: ProspectPayload marshaling, deal status/stage enums, canonical key binding
- Depends on: Notion API (NOTION_API_KEY, NOTION_DATABASE_ID)
- Used by: NotionPusher, suppression sync, health checks

**Governance Layer:**
- Purpose: Enforce state policy transitions and write permissions
- Location: `governance/state_policies.py` (policy), `workflows/delivery_policy.py` (intent guards)
- Contains: Two-lane flag registry (env-backed vs feature experiments), permission matrix
- Depends on: Environment variables, feature_states registry
- Used by: All write operations (via assert_notion_write_allowed)

## Data Flow

**Full Pipeline Flow:**

1. **Collection** (run_pipeline.py → DiscoveryPipeline.run_collectors)
   - Load all requested collectors (e.g., github, sec_edgar, job_postings)
   - Each collector fetches signals from external source
   - Collector dedupes against suppression_cache via canonical_key lookup
   - Saves new signals to signals table with status='pending'
   - Returns collector statistics (signals_new, signals_suppressed, errors)

2. **Initial Storage** (BaseCollector → SignalStore.save_signal)
   - Extract canonical_key from signal (domain:acme.ai, companies_house:10324567, etc.)
   - Build from metadata if exact key unavailable (build_canonical_key_candidates)
   - Store signal with raw_data JSON, source_api, signal_type, detected_at
   - Check is_duplicate before saving (canonical_key + source_api)

3. **Thesis Filtering** (DiscoveryPipeline.process_pending → ThesisFilter)
   - Stage 1 (free): Keyword matching (CONSUMER_SIGNAL_KEYWORDS vs NEGATIVE_KEYWORDS)
   - Stage 1 hard gates: Web3/crypto detection (veto), domain blacklist (veto)
   - Stage 2 (optional, LLM_THESIS_MODE=active): Gemini semantic classification
   - Routes signal to QUALIFIED, HELD, or REJECTED
   - Stores decision in signal_processing.thesis_category + prompt_version

4. **Verification Gating** (NotionPusher → VerificationGate)
   - Aggregate signals by canonical_key (multi-source convergence)
   - Score each aggregated prospect via VerificationGate.evaluate()
   - Weigh signal types (incorporation: 0.25, hiring: 0.30, etc.) by count and source diversity
   - Calculate confidence_score = sum(signal_weights) × founder_score × velocity_boost
   - Classify as UNVERIFIED (0 sources), SINGLE_SOURCE, MULTI_SOURCE, or CONFLICTING
   - Route: confidence >= 0.7 → AUTO_PUSH ("Source"), 0.4-0.7 → NEEDS_REVIEW ("Tracking"), <0.4 → HOLD

5. **Notion Push** (NotionPusher.process_batch → NotionConnector)
   - Check delivery policy (assert_notion_write_allowed with DeliveryIntent.BATCH_PUSH or AUTO_PUSH)
   - Verify Notion schema (ValidationResult confirms statuses, stages, custom properties)
   - Marshal ProspectPayload: company_name, canonical_key, confidence_score, signal_types, why_now
   - POST to Notion database, receive page_id
   - Update signal_processing.status='pushed', notion_page_id=page_id
   - Mark related signals as processed via signal_id references

6. **Suppression Cache Sync** (SuppressionSync)
   - Fetch all active entries from Notion (excluding Passed/Lost)
   - Extract canonical_key from Discovery ID property
   - Upsert to suppression_cache table with strong_key_match flag
   - Clean expired entries (>90 days old)
   - Cache refreshed on pipeline start and periodically

**State Management:**
- **Signal state**: pending → qualified/held/rejected (thesis) → pushed/rejected (delivery)
- **Processing state**: Stored in signal_processing table (notion_page_id, status, thesis_category)
- **Governance state**: Environment-backed (DELIVERY_MODE, LLM_THESIS_MODE) + feature registry (ML_ENABLEMENT, V2_ENABLEMENT)
- **Audit trail**: Every state change logged to audit_events with actor, timestamp, reason

## Key Abstractions

**Signal:**
- Purpose: Typed representation of a single discovery event (code commit spike, domain registration, etc.)
- Examples: `verification/verification_gate_v2.py:Signal`, `collectors/base.py:Signal`
- Pattern: dataclass with signal_type, source_api, canonical_key, confidence, raw_data, detected_at
- Properties: Immutable after creation; rich_data (merge of metadata + raw_data); confidence supplied by collector

**Canonical Key:**
- Purpose: Deterministic deduplication identifier independent of company website
- Examples: `domain:acme.ai`, `companies_house:10324567`, `github_org:acme-inc`
- Implementation: `utils/canonical_keys.py:build_canonical_key()` + candidates list
- Usage: Primary key for suppression_cache lookups; bridges Notion Discovery ID ↔ local signals

**Collector:**
- Purpose: Pluggable source adapter with unified interface
- Base class: `collectors/base.py:BaseCollector`
- Implementations: 16+ subclasses (github.py, sec_edgar.py, job_postings.py, etc.)
- Contract: `_collect_signals() → List[Signal]`, error isolation (signal-level failures don't crash run), retry with exponential backoff
- Rate limiting: Per-collector via `utils/rate_limiter.py:get_rate_limiter()` lookup table

**VerificationGate:**
- Purpose: Multi-source signal convergence logic
- Location: `verification/verification_gate_v2.py`
- Pattern: Weighted signal scoring + founder multiplier + velocity boost → confidence_score
- Decision: AUTO_PUSH (>= 0.7), NEEDS_REVIEW (0.4-0.7), HOLD (< 0.4), REJECT (hard kill)
- Data sources: founder_score from founder_store, velocity from signal_velocity tracker, signal weights from SIGNAL_WEIGHTS dict

**Verification Status:**
- Purpose: Classify source diversity for transparency
- Values: UNVERIFIED (0 sources), SINGLE_SOURCE (1 type), MULTI_SOURCE (2+ types), CONFLICTING (contradictory)
- Usage: Reported in Notion "Signal Types" multi-select property; informs human reviewers

**Delivery Policy:**
- Purpose: Stage-gate Notion writes by environment variable
- Modes: staging_only (no writes), manual_publish (single item), batch_publish (workflows), auto_publish (all)
- Enforcement: `assert_notion_write_allowed(DeliveryIntent)` before any Notion write
- Guard locations: NotionPusher.process_batch(), manual push CLI, batch publish workflow

**MCP Server Boundary:**
- Purpose: Single trusted API surface for Claude/agent interactions
- Location: `discovery_engine/mcp_server.py`
- Prompts: run-collector, check-suppression, push-to-notion, sync-suppression-cache, validate-notion-schema
- Guard: ALLOWED_COLLECTORS whitelist; validation preflight on schema operations
- Rationale: All external tool access routes through this; prevents accidental API key leaks or Notion schema drift

## Entry Points

**CLI:**
- Location: `run_pipeline.py`
- Triggers: User runs `python run_pipeline.py <command>`
- Commands: full (collect+process+push), collect, process, sync, stats, health, metrics, triage, pipeline, import-csv, push
- Responsibilities: Arg parsing, async event loop, logging setup, error reporting

**API:**
- Location: `api/` (FastAPI app, not primary; CLI is main entry point)
- Triggers: External HTTP requests to /collectors, /process, /sync endpoints
- Responsibilities: REST marshaling, auth (if needed), status 200/503 responses

**Notion Pushes:**
- Trigger: DiscoveryPipeline.run_full_pipeline() with delivery mode >= batch_publish
- Trigger: Manual CLI `python run_pipeline.py push --signal-id <id> --confirm`
- Responsibilities: Call NotionPusher.process_batch(), respect delivery policy, update signal state

**Health Checks:**
- Location: `run_pipeline.py health` command
- Triggers: Periodic monitoring, pre-push verification
- Checks: DB connectivity, Notion schema validation, API key coverage, suppression cache staleness

## Error Handling

**Strategy:** Fail-open at collection layer (one collector failure ≠ pipeline failure), fail-closed at Notion layer (schema validation before push).

**Patterns:**
- **Collection errors**: Try/catch per signal in collector; signal-level failures logged, skipped; collector returns partial results
- **Storage errors**: sqlite3.IntegrityError on canonical_key conflicts → idempotent ignore or upsert
- **Notion errors**: Pre-flight schema validation; push errors → log, mark signal with notion_error flag, halt batch
- **LLM errors**: Classification timeout → mark as HELD (requires human review)
- **Rate limit errors**: RetryConfig.with_retry() decorator; exponential backoff with jitter; CollectorSkipError if fail-fast

## Cross-Cutting Concerns

**Logging:**
- Framework: Python logging module with handlers to stdout + optional file
- Configuration: `utils/logging_config.py`
- Pattern: Structured logs via dataclass field inclusion; one log per significant operation
- Usage: Discovery/error/warning severity; DEBUG level for HTTP/retry details

**Validation:**
- Notion schema: `utils/config_validator.py:validate_env()` at startup; preflight before push
- Canonical keys: `is_strong_key()` test for true identifiers vs weak candidates
- Signals: Required fields (signal_type, source_api, canonical_key) checked before storage
- Delivery policy: `governance/state_policies.py` rejects invalid flag transitions

**Authentication:**
- API keys: Loaded from .env (GOOGLE_API_KEY, NOTION_API_KEY, GITHUB_TOKEN, etc.)
- Credentials guard: Keys not logged; config_validator checks presence
- MCP boundary: Single point of API key usage for Claude integration

---

*Architecture analysis: 2026-04-08*
