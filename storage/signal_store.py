"""
Signal Storage Layer for Discovery Engine

Provides persistent SQLite storage for signals with:
- Deduplication via canonical keys
- Processing state tracking
- Notion suppression cache
- Migration support
- Connection pooling via aiosqlite

Tables:
  - signals: Raw signals from collectors
  - signal_processing: Processing state and Notion linkage
  - suppression_cache: Local cache of Notion DB to avoid duplicate pushes
  - schema_migrations: Track applied migrations

Usage:
    store = SignalStore("signals.db")
    await store.initialize()

    # Save a signal
    signal_id = await store.save_signal({
        "signal_type": "github_spike",
        "source_api": "github",
        "canonical_key": "domain:acme.ai",
        "company_name": "Acme Inc",
        "confidence": 0.85,
        "raw_data": {...}
    })

    # Check for duplicates
    is_dup = await store.is_duplicate("domain:acme.ai")

    # Get pending signals
    pending = await store.get_pending_signals()

    # Mark as pushed
    await store.mark_pushed(signal_id, notion_page_id="abc-123")
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncIterator, TYPE_CHECKING

import aiosqlite

from storage.migrations.quality_tables import QUALITY_TABLES_DDL
from storage.migrations.v27_audit_log import AUDIT_LOG_DDL
from storage.migrations.v28_canonical_identity import V28_CANONICAL_IDENTITY_DDL
from storage.migrations.v29_review_queue import V29_REVIEW_QUEUE_DDL
from storage.migrations.v30_pipeline_identity_stats import V30_PIPELINE_IDENTITY_STATS_DDL
from storage.migrations.v31_batch_publish import V31_BATCH_PUBLISH_DDL
from storage.migrations.v32_functional_schema import V32_FUNCTIONAL_SCHEMA_DDL
from storage.migrations.v33_case_law import V33_CASE_LAW_DDL
from storage.migrations.v34_exemplars import V34_EXEMPLARS_DDL
from storage.migrations.v35_platform_hardening import V35_PLATFORM_HARDENING_DDL
from storage.migrations.v36_wave1_triage import V36_WAVE1_TRIAGE_DDL
from storage.migrations.v37_ach_analyses import V37_ACH_ANALYSES_DDL
from storage.migrations.v38_wave2_shadow_canary import V38_WAVE2_SHADOW_CANARY_DDL
from storage.migrations.v39_active_hunter import V39_ACTIVE_HUNTER_DDL
from storage.migrations.v40_merge_lifecycle import V40_MERGE_LIFECYCLE_DDL
from storage.migrations.v41_drift_monitoring import V41_DRIFT_MONITORING_DDL
from storage.migrations.v42_evidence_family import V42_EVIDENCE_FAMILY_DDL
from storage.migrations.v43_canonical_key_v2 import V43_CANONICAL_KEY_V2_DDL
from storage.migrations.v44_dns_promotion_aliases import V44_DNS_PROMOTION_ALIASES_DDL
from storage.migrations.v45_evidence_key import V45_EVIDENCE_KEY_DDL
from storage.migrations.v46_evidence_key_unique import V46_EVIDENCE_KEY_UNIQUE_DDL
from storage.migrations.v47_governance_triggers import V47_GOVERNANCE_TRIGGERS_DDL
from storage.migrations.v48_shadow_log_metrics import V48_SHADOW_LOG_METRICS_DDL

if TYPE_CHECKING:
    from workflows.pipeline import PipelineStats, CollectorMetrics
    from utils.exit_predictor import ExitPrediction
    from storage.entity_identity_store import EntityIdentityStore, StrongKeyBinding

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA VERSION
# =============================================================================

CURRENT_SCHEMA_VERSION = 48

# SQL for creating tables (migrations applied in order)
MIGRATIONS = {
    1: """
    -- Signals table: raw signals from collectors
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_type TEXT NOT NULL,
        source_api TEXT NOT NULL,
        canonical_key TEXT NOT NULL,
        company_name TEXT,
        confidence REAL NOT NULL,
        raw_data TEXT NOT NULL,  -- JSON
        detected_at TEXT NOT NULL,  -- ISO 8601
        created_at TEXT NOT NULL,  -- ISO 8601

        -- Indexes for fast lookups
        UNIQUE(canonical_key, signal_type, source_api, detected_at)
    );

    CREATE INDEX IF NOT EXISTS idx_signals_canonical_key ON signals(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_signals_signal_type ON signals(signal_type);
    CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
    CREATE INDEX IF NOT EXISTS idx_signals_detected_at ON signals(detected_at);

    -- Signal processing: track what's been pushed/rejected
    CREATE TABLE IF NOT EXISTS signal_processing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        status TEXT NOT NULL,  -- 'pending', 'queued', 'pushed', 'rejected'
        notion_page_id TEXT,
        processed_at TEXT,  -- ISO 8601
        error_message TEXT,
        metadata TEXT,  -- JSON for extra context
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_processing_signal_id ON signal_processing(signal_id);
    CREATE INDEX IF NOT EXISTS idx_processing_status ON signal_processing(status);
    CREATE INDEX IF NOT EXISTS idx_processing_notion_page_id ON signal_processing(notion_page_id);

    -- Suppression cache: local copy of what's in Notion
    CREATE TABLE IF NOT EXISTS suppression_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,
        notion_page_id TEXT NOT NULL,
        status TEXT NOT NULL,  -- Notion status: Source, Tracking, etc.
        company_name TEXT,
        cached_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        metadata TEXT  -- JSON for extra Notion fields
    );

    CREATE INDEX IF NOT EXISTS idx_suppression_canonical_key ON suppression_cache(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_suppression_notion_page_id ON suppression_cache(notion_page_id);
    CREATE INDEX IF NOT EXISTS idx_suppression_expires_at ON suppression_cache(expires_at);

    -- Schema migrations tracking
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT
    );
    """,
    2: """
    -- Pipeline runs: track pipeline execution metrics
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        started_at TEXT NOT NULL,  -- ISO 8601
        completed_at TEXT,  -- ISO 8601
        duration_seconds REAL,

        -- Collector stats
        collectors_run INTEGER NOT NULL DEFAULT 0,
        collectors_succeeded INTEGER NOT NULL DEFAULT 0,
        collectors_failed INTEGER NOT NULL DEFAULT 0,
        signals_collected INTEGER NOT NULL DEFAULT 0,

        -- Storage stats
        signals_stored INTEGER NOT NULL DEFAULT 0,
        signals_deduplicated INTEGER NOT NULL DEFAULT 0,

        -- Verification stats
        signals_processed INTEGER NOT NULL DEFAULT 0,
        signals_auto_push INTEGER NOT NULL DEFAULT 0,
        signals_needs_review INTEGER NOT NULL DEFAULT 0,
        signals_held INTEGER NOT NULL DEFAULT 0,
        signals_rejected INTEGER NOT NULL DEFAULT 0,

        -- Notion stats
        prospects_created INTEGER NOT NULL DEFAULT 0,
        prospects_updated INTEGER NOT NULL DEFAULT 0,
        prospects_skipped INTEGER NOT NULL DEFAULT 0,

        -- Errors and health
        errors TEXT,  -- JSON array
        health_report TEXT,  -- JSON object

        created_at TEXT NOT NULL  -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_run_id ON pipeline_runs(run_id);
    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at ON pipeline_runs(started_at);
    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_created_at ON pipeline_runs(created_at);
    """
    ,
    3: """
    -- Notion outbox: durable queue for Notion writes
    CREATE TABLE IF NOT EXISTS notion_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotency_key TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,  -- 'pending', 'sent', 'failed'
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_outbox_status ON notion_outbox(status);
    CREATE INDEX IF NOT EXISTS idx_outbox_next_attempt ON notion_outbox(next_attempt_at);
    CREATE INDEX IF NOT EXISTS idx_outbox_created_at ON notion_outbox(created_at);
    """,
    4: """
    -- Collector metrics: per-collector timing and API stats
    CREATE TABLE IF NOT EXISTS collector_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        collector_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        duration_seconds REAL,
        signals_found INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        api_calls INTEGER NOT NULL DEFAULT 0,
        rate_limit_hits INTEGER NOT NULL DEFAULT 0,
        retries INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        error_messages TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_collector_metrics_run_id ON collector_metrics(run_id);
    CREATE INDEX IF NOT EXISTS idx_collector_metrics_collector ON collector_metrics(collector_name);
    CREATE INDEX IF NOT EXISTS idx_collector_metrics_started_at ON collector_metrics(started_at);
    """,
    5: """
    -- Thesis classifications: persist LLM classification results
    CREATE TABLE IF NOT EXISTS thesis_classifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        canonical_key TEXT NOT NULL,

        -- Keyword matcher results (stage 1)
        keyword_score REAL,
        keyword_category TEXT,
        negative_keywords TEXT,  -- JSON array

        -- LLM classifier results (stage 2)
        thesis_match BOOLEAN,
        thesis_fit_score REAL,
        category TEXT,
        stage_estimate TEXT,
        confidence TEXT,
        rationale TEXT,
        key_signals TEXT,  -- JSON array

        -- Audit trail
        prompt_version TEXT,
        model TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms INTEGER,

        -- Competitor detection
        competitor_flag BOOLEAN DEFAULT 0,
        competitor_match TEXT,  -- JSON: matched portfolio company

        classified_at TEXT NOT NULL,  -- ISO 8601

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_thesis_class_signal_id ON thesis_classifications(signal_id);
    CREATE INDEX IF NOT EXISTS idx_thesis_class_canonical ON thesis_classifications(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_thesis_class_category ON thesis_classifications(category);
    CREATE INDEX IF NOT EXISTS idx_thesis_class_classified_at ON thesis_classifications(classified_at);
    """,
    6: """
    -- Exit predictions: store heuristic exit prediction results
    CREATE TABLE IF NOT EXISTS exit_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,

        -- Component scores (0-1 each)
        thesis_fit REAL NOT NULL,
        founder_score REAL NOT NULL,
        traction_score REAL NOT NULL,
        funding_score REAL NOT NULL,
        velocity_score REAL NOT NULL,
        age_score REAL NOT NULL,
        investor_centrality REAL NOT NULL,  -- Stubbed at 0.5 in Phase 1
        patent_count REAL NOT NULL,  -- Stubbed at 0 in Phase 1

        -- Computed outputs
        deal_quality_score REAL NOT NULL,
        percentile_rank INTEGER,  -- NULL until nightly batch
        exit_probability REAL NOT NULL,
        confidence TEXT NOT NULL,  -- high, medium, low
        recommendation TEXT NOT NULL,  -- source, tracking, hold, pass

        -- Placeholders for Phase 3
        exit_timeline TEXT DEFAULT 'unknown',
        exit_type_probabilities TEXT,  -- JSON

        -- Evidence trail
        evidence TEXT,  -- JSON array of ExitEvidence

        -- Metadata
        model_version TEXT NOT NULL,
        predicted_at TEXT NOT NULL,  -- ISO 8601
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_exit_pred_canonical ON exit_predictions(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_exit_pred_deal_quality ON exit_predictions(deal_quality_score DESC);
    CREATE INDEX IF NOT EXISTS idx_exit_pred_recommendation ON exit_predictions(recommendation);
    CREATE INDEX IF NOT EXISTS idx_exit_pred_percentile ON exit_predictions(percentile_rank);
    """,
    7: """
    -- =============================================================================
    -- CLAIM LEDGER (KG-Lite) - Sprint 2
    -- =============================================================================
    -- Truth-maintenance backbone for the knowledge graph.
    -- Enables: "Why do we think target_customer = X?" with evidence chain.

    -- Controlled vocabulary for predicates
    CREATE TABLE IF NOT EXISTS predicates (
        name TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        data_type TEXT NOT NULL DEFAULT 'text',  -- text, numeric, enum, json
        units TEXT,
        decay_rate_days INTEGER,  -- How quickly claims go stale
        source_priority_weights TEXT,  -- JSON: source -> weight mapping
        description TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    -- Seed initial predicates for core claims
    INSERT OR IGNORE INTO predicates (name, display_name, data_type, description) VALUES
        ('problem_solved', 'Problem Solved', 'text', 'What problem does this company solve?'),
        ('target_customer', 'Target Customer', 'text', 'Who is the primary customer?'),
        ('business_model', 'Business Model', 'enum', 'How does the company make money?'),
        ('stage', 'Stage', 'enum', 'Company stage: Pre-Seed, Seed, Series A, etc.'),
        ('traction_metric', 'Traction Metric', 'numeric', 'Key traction number (users, revenue, etc.)'),
        ('founding_date', 'Founding Date', 'text', 'When was the company founded?'),
        ('location', 'Location', 'text', 'Primary company location'),
        ('industry', 'Industry', 'text', 'Industry or sector'),
        ('funding_raised', 'Funding Raised', 'numeric', 'Total funding raised in USD'),
        ('employee_count', 'Employee Count', 'numeric', 'Approximate employee count'),
        ('company_name', 'Company Name', 'text', 'Official company name'),
        ('website', 'Website', 'text', 'Company website URL'),
        ('description', 'Description', 'text', 'Company description or tagline');

    -- Raw extractions from any source (assertions before canonicalization)
    CREATE TABLE IF NOT EXISTS claim_extractions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
        entity_key TEXT NOT NULL,  -- Canonical key for the entity

        -- Extraction details
        extractor_name TEXT NOT NULL,  -- e.g., 'website_profiler', 'sec_edgar_parser'
        extractor_version TEXT,
        predicate_hint TEXT,  -- Which predicate this might map to

        -- Raw extracted content
        raw_text TEXT NOT NULL,
        source_snippet TEXT,  -- Verbatim evidence quote
        start_offset INTEGER,  -- For highlighting in source
        end_offset INTEGER,

        -- Provenance
        source_url TEXT,
        extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_extraction_entity ON claim_extractions(entity_key);
    CREATE INDEX IF NOT EXISTS idx_extraction_signal ON claim_extractions(source_signal_id);
    CREATE INDEX IF NOT EXISTS idx_extraction_predicate ON claim_extractions(predicate_hint);
    CREATE INDEX IF NOT EXISTS idx_extraction_extractor ON claim_extractions(extractor_name);

    -- Canonicalized claims (the "current truth" about an entity)
    CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_key TEXT NOT NULL,
        predicate TEXT NOT NULL REFERENCES predicates(name),

        -- Value (with type flexibility)
        value TEXT NOT NULL,
        value_type TEXT DEFAULT 'text',  -- text, numeric, json
        value_num REAL,  -- For numeric predicates
        value_json TEXT,  -- For complex values

        -- Confidence and status
        confidence REAL NOT NULL DEFAULT 0.5,
        status TEXT NOT NULL DEFAULT 'active',  -- active, stale, conflicting, retracted
        status_updated_at TEXT,
        status_reason TEXT,

        -- Temporal tracking
        last_supported_at TEXT,  -- Last time evidence was found
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,

        -- Ensure unique claims per entity/predicate/value combination
        UNIQUE(entity_key, predicate, value)
    );

    CREATE INDEX IF NOT EXISTS idx_claims_entity ON claims(entity_key);
    CREATE INDEX IF NOT EXISTS idx_claims_predicate ON claims(predicate);
    CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
    CREATE INDEX IF NOT EXISTS idx_claims_confidence ON claims(confidence DESC);
    CREATE INDEX IF NOT EXISTS idx_claims_entity_predicate ON claims(entity_key, predicate);

    -- Many-to-many evidence linkage (claim <-> extractions)
    CREATE TABLE IF NOT EXISTS claim_evidence (
        claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
        extraction_id INTEGER NOT NULL REFERENCES claim_extractions(id) ON DELETE CASCADE,
        evidence_weight REAL DEFAULT 1.0,  -- How strongly this extraction supports the claim
        linked_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (claim_id, extraction_id)
    );

    CREATE INDEX IF NOT EXISTS idx_evidence_claim ON claim_evidence(claim_id);
    CREATE INDEX IF NOT EXISTS idx_evidence_extraction ON claim_evidence(extraction_id);

    -- Current claims view: deterministic tie-breaking for conflicting claims
    -- Returns only the "winning" claim per entity/predicate pair
    CREATE VIEW IF NOT EXISTS current_claims AS
    SELECT
        c.*,
        (
            SELECT COUNT(*)
            FROM claims c2
            WHERE c2.entity_key = c.entity_key
              AND c2.predicate = c.predicate
              AND c2.status = 'active'
        ) as competing_claims,
        (
            SELECT COUNT(*)
            FROM claim_evidence ce
            WHERE ce.claim_id = c.id
        ) as evidence_count
    FROM claims c
    WHERE c.status = 'active'
      AND c.id = (
          SELECT c3.id
          FROM claims c3
          WHERE c3.entity_key = c.entity_key
            AND c3.predicate = c.predicate
            AND c3.status = 'active'
          ORDER BY
              c3.confidence DESC,
              c3.last_supported_at DESC,
              c3.id DESC
          LIMIT 1
      );
    """,
    8: """
    -- =============================================================================
    -- SIMILAR COMPANIES (Sprint 4) - Embedding-based similarity search
    -- =============================================================================
    -- Hybrid FTS+embedding approach: FTS5 for candidate retrieval, embeddings for ranking.

    -- FTS index for company profiles (Stage 1: candidate retrieval)
    CREATE VIRTUAL TABLE IF NOT EXISTS company_profiles_fts USING fts5(
        canonical_key UNINDEXED,  -- Join key, not searched
        company_name,
        searchable_text,          -- Combined profile text for keyword matching
        category,                 -- For optional narrowing
        business_model,           -- For soft boost
        tokenize='porter unicode61'
    );

    -- Embedding cache table (Stage 2: semantic ranking)
    CREATE TABLE IF NOT EXISTS company_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL,
        embedding_kind TEXT NOT NULL DEFAULT 'profile_v1',  -- Future-proof for multi-facet

        -- Embedding data
        embedding BLOB NOT NULL,              -- numpy float32 bytes (768 dims * 4 bytes = 3KB)
        embedding_model TEXT NOT NULL,        -- e.g., 'text-embedding-004'
        embedding_version TEXT NOT NULL,      -- e.g., 'v1' (bump on template change)

        -- Staleness detection
        source_text_hash TEXT NOT NULL,       -- SHA256 of input text
        source_text_preview TEXT,             -- First 512 chars for debugging

        -- Timestamps
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT,

        -- Ensure one embedding per company/kind/model/version
        UNIQUE (canonical_key, embedding_kind, embedding_model, embedding_version)
    );

    CREATE INDEX IF NOT EXISTS idx_embeddings_key ON company_embeddings(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON company_embeddings(source_text_hash);
    CREATE INDEX IF NOT EXISTS idx_embeddings_kind ON company_embeddings(embedding_kind);
    """,
    9: """
    -- =============================================================================
    -- INVESTOR MATCHING (Sprint 5) - Portfolio forensics-based investor matching
    -- =============================================================================
    -- Infers investor thesis from observed portfolio behavior, not marketing.
    -- Enables: "Which investors have backed similar companies?" with evidence trail.

    -- 9.1: Core investor entity
    CREATE TABLE IF NOT EXISTS investors (
        id TEXT PRIMARY KEY,                    -- investor:sequoia_capital
        canonical_key TEXT NOT NULL UNIQUE,     -- Same as id, explicit for FK
        name TEXT NOT NULL,
        investor_type TEXT DEFAULT 'vc',        -- vc|angel|accelerator|corporate|family_office
        website_domain TEXT,
        hq_country TEXT,
        hq_city TEXT,
        founded_year INTEGER,
        aum_usd REAL,                           -- Assets under management
        source TEXT NOT NULL,                   -- crunchbase|curated_json|sec_edgar
        source_ref TEXT,                        -- URL or file path
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_investors_type ON investors(investor_type);
    CREATE INDEX IF NOT EXISTS idx_investors_source ON investors(source);
    CREATE INDEX IF NOT EXISTS idx_investors_country ON investors(hq_country);

    -- 9.2: Portfolio edges (investor -> company relationships)
    -- Links to existing signals/claims via company_key
    CREATE TABLE IF NOT EXISTS investor_portfolios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        investor_id TEXT NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
        company_key TEXT NOT NULL,              -- canonical key: domain:acme.ai
        relationship_type TEXT NOT NULL,        -- led|participated|followed_on|acquired|advisor
        round_type TEXT,                        -- pre_seed|seed|series_a|series_b|bridge|unknown
        round_date TEXT,                        -- ISO date YYYY-MM-DD
        investment_usd REAL,                    -- Amount if known
        ownership_pct REAL,                     -- Ownership if known
        is_lead INTEGER DEFAULT 0,              -- 1 if led round
        source TEXT NOT NULL,                   -- crunchbase|curated_json|sec_edgar
        source_ref TEXT,
        confidence REAL NOT NULL DEFAULT 0.5,   -- 0-1
        -- FK to existing claim_extractions for evidence trail
        extraction_id INTEGER REFERENCES claim_extractions(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(investor_id, company_key, round_type, round_date)
    );
    CREATE INDEX IF NOT EXISTS idx_investor_portfolios_investor ON investor_portfolios(investor_id);
    CREATE INDEX IF NOT EXISTS idx_investor_portfolios_company ON investor_portfolios(company_key);
    CREATE INDEX IF NOT EXISTS idx_investor_portfolios_round ON investor_portfolios(round_type);
    CREATE INDEX IF NOT EXISTS idx_investor_portfolios_date ON investor_portfolios(round_date);

    -- 9.3: Investor profile claims (inferred from portfolio behavior)
    -- Reuses existing predicates table pattern
    CREATE TABLE IF NOT EXISTS investor_profile_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        investor_id TEXT NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
        predicate TEXT NOT NULL,                -- sector_preference|stage_preference|geo_preference|check_size_range
        value TEXT NOT NULL,                    -- fintech|seed|US|100000-500000
        confidence REAL NOT NULL,               -- 0-1
        lift_score REAL,                        -- Log-odds vs global baseline
        support_count INTEGER NOT NULL,         -- Portfolio companies supporting this
        support_evidence TEXT,                  -- JSON array of {company_key, extraction_id}
        status TEXT DEFAULT 'active',           -- active|stale|retracted
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(investor_id, predicate, value)
    );
    CREATE INDEX IF NOT EXISTS idx_investor_claims_investor ON investor_profile_claims(investor_id);
    CREATE INDEX IF NOT EXISTS idx_investor_claims_predicate ON investor_profile_claims(predicate);
    CREATE INDEX IF NOT EXISTS idx_investor_claims_status ON investor_profile_claims(status);
    CREATE INDEX IF NOT EXISTS idx_investor_claims_lift ON investor_profile_claims(lift_score DESC);

    -- 9.4: Cached investor profiles (denormalized for fast matching)
    CREATE TABLE IF NOT EXISTS investor_profiles (
        investor_id TEXT PRIMARY KEY REFERENCES investors(id) ON DELETE CASCADE,
        thesis_embedding BLOB,                  -- float32[768] numpy array
        embedding_model TEXT DEFAULT 'text-embedding-004',
        embedding_version INTEGER DEFAULT 1,
        source_text_hash TEXT,                  -- SHA256 for staleness detection
        stage_distribution TEXT,                -- JSON: {"seed":0.45,"series_a":0.35}
        sector_distribution TEXT,               -- JSON: {"fintech":0.28,"health":0.14}
        geo_distribution TEXT,                  -- JSON: {"US":0.70,"UK":0.18}
        check_size_p10_usd REAL,
        check_size_median_usd REAL,
        check_size_p90_usd REAL,
        lead_rate REAL,                         -- Fraction of led rounds
        portfolio_count INTEGER NOT NULL DEFAULT 0,
        active_claim_count INTEGER NOT NULL DEFAULT 0,
        is_cold_start INTEGER DEFAULT 1,        -- 1 if portfolio_count < 3
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- 9.5: Manual investor preferences (overrides inferred claims)
    CREATE TABLE IF NOT EXISTS investor_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        investor_id TEXT NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
        preference_type TEXT NOT NULL,          -- include|exclude|boost|penalize|hard_no
        predicate TEXT NOT NULL,                -- sector|stage|geo|min_revenue|max_valuation
        value TEXT NOT NULL,
        weight REAL DEFAULT 1.0,                -- Scoring weight
        reason TEXT,                            -- Analyst note or source
        source TEXT NOT NULL,                   -- manual|partner_request|policy
        created_by TEXT,                        -- User who added
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_investor_prefs_investor ON investor_preferences(investor_id);
    CREATE INDEX IF NOT EXISTS idx_investor_prefs_type ON investor_preferences(preference_type);

    -- 9.6: Global baselines for lift calculation
    -- Stores P(predicate=value) across global population for normalization
    CREATE TABLE IF NOT EXISTS global_baselines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        predicate TEXT NOT NULL,                -- sector|stage|geo|business_model
        value TEXT NOT NULL,                    -- fintech|seed|US
        global_probability REAL NOT NULL,       -- P(value) across all companies
        sample_size INTEGER NOT NULL,           -- N companies in sample
        sample_source TEXT NOT NULL,            -- crunchbase_2y|portfolio_all|signals_30d
        computed_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT,                        -- Optional TTL
        UNIQUE(predicate, value, sample_source)
    );
    CREATE INDEX IF NOT EXISTS idx_global_baselines_predicate ON global_baselines(predicate, value);
    CREATE INDEX IF NOT EXISTS idx_global_baselines_source ON global_baselines(sample_source);

    -- 9.7: FTS5 index for investor profile search
    CREATE VIRTUAL TABLE IF NOT EXISTS investor_profile_fts USING fts5(
        investor_id,
        investor_name,
        claim_text,                             -- Concatenated: "sector:fintech stage:seed geo:US"
        content='',
        tokenize='unicode61'
    );

    -- 9.8: Investor match results (cached)
    CREATE TABLE IF NOT EXISTS investor_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_key TEXT NOT NULL,              -- Startup being matched
        investor_id TEXT NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
        match_score REAL NOT NULL,              -- Combined score 0-1
        fts_score REAL,                         -- BM25 component
        embedding_score REAL,                   -- Cosine similarity component
        constraint_score REAL,                  -- Preference match component
        explanation TEXT NOT NULL,              -- JSON array of match reasons
        evidence TEXT,                          -- JSON array of supporting portfolio examples
        rank INTEGER,                           -- Position in result list
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(company_key, investor_id)
    );
    CREATE INDEX IF NOT EXISTS idx_investor_matches_company ON investor_matches(company_key);
    CREATE INDEX IF NOT EXISTS idx_investor_matches_score ON investor_matches(match_score DESC);
    CREATE INDEX IF NOT EXISTS idx_investor_matches_investor ON investor_matches(investor_id);

    -- =============================================================================
    -- EVALUATION & CALIBRATION (Sprint 6) - Gold set and drift detection
    -- =============================================================================

    -- 9.9: Gold set for evaluation
    CREATE TABLE IF NOT EXISTS gold_set_companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,
        company_name TEXT NOT NULL,
        category TEXT NOT NULL,                 -- core_sector|long_tail|ambiguous|hard_negative
        annotator_1 TEXT,
        annotator_2 TEXT,
        tie_breaker TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_gold_set_category ON gold_set_companies(category);

    -- 9.10: Gold set labels (human annotations)
    CREATE TABLE IF NOT EXISTS gold_set_labels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES gold_set_companies(id) ON DELETE CASCADE,
        predicate TEXT NOT NULL,                -- problem|customer|sector|stage|geo
        label_type TEXT NOT NULL,               -- exact|partial|incorrect|abstain
        gold_value TEXT,                        -- Ground truth value
        annotator TEXT NOT NULL,
        confidence TEXT DEFAULT 'high',         -- high|medium|low
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(company_id, predicate, annotator)
    );
    CREATE INDEX IF NOT EXISTS idx_gold_labels_company ON gold_set_labels(company_id);
    CREATE INDEX IF NOT EXISTS idx_gold_labels_predicate ON gold_set_labels(predicate);

    -- 9.11: Gold set investor labels
    CREATE TABLE IF NOT EXISTS gold_set_investor_labels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES gold_set_companies(id) ON DELETE CASCADE,
        investor_id TEXT NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
        relevance TEXT NOT NULL,                -- relevant|partial|irrelevant
        annotator TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(company_id, investor_id, annotator)
    );
    CREATE INDEX IF NOT EXISTS idx_gold_investor_labels_company ON gold_set_investor_labels(company_id);

    -- 9.12: Evaluation runs
    CREATE TABLE IF NOT EXISTS evaluation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        run_type TEXT NOT NULL,                 -- extraction|similarity|investor_match
        model_version TEXT NOT NULL,
        embedding_version TEXT,
        gold_set_version TEXT NOT NULL,
        metrics TEXT NOT NULL,                  -- JSON: {f1, precision, recall, abstention_rate, ...}
        config TEXT,                            -- JSON: run configuration
        baseline_run_id INTEGER REFERENCES evaluation_runs(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_eval_runs_type ON evaluation_runs(run_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_eval_runs_run_id ON evaluation_runs(run_id);

    -- 9.13: Drift alerts
    CREATE TABLE IF NOT EXISTS drift_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL,               -- extraction_f1_drop|abstention_spike|similarity_recall_drop|confidence_collapse
        severity TEXT NOT NULL,                 -- red|yellow
        metric_name TEXT NOT NULL,
        baseline_value REAL NOT NULL,
        current_value REAL NOT NULL,
        threshold REAL NOT NULL,
        delta REAL,                             -- current - baseline
        evaluation_run_id INTEGER REFERENCES evaluation_runs(id),
        acknowledged INTEGER DEFAULT 0,
        acknowledged_by TEXT,
        acknowledged_at TEXT,
        slack_notified INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_drift_alerts_unacked ON drift_alerts(acknowledged, severity, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_drift_alerts_type ON drift_alerts(alert_type);

    -- Seed investor-specific predicates
    INSERT OR IGNORE INTO predicates (name, display_name, data_type, description) VALUES
        ('sector_preference', 'Sector Preference', 'text', 'Investor preferred sector from portfolio analysis'),
        ('stage_preference', 'Stage Preference', 'enum', 'Investor preferred stage from portfolio analysis'),
        ('geo_preference', 'Geography Preference', 'text', 'Investor preferred geography from portfolio analysis'),
        ('check_size_range', 'Check Size Range', 'text', 'Typical investment amount range'),
        ('lead_preference', 'Lead Preference', 'enum', 'Whether investor typically leads or follows');
    """,
    10: """
    -- =============================================================================
    -- MONITORING SUBSYSTEM (Sprint 7) - Website change tracking
    -- =============================================================================
    -- "Layer on Top" monitoring: watches, snapshots, diffs, alerts
    -- Enables: "What changed on this company's website since we last checked?"

    -- Add functional_profile predicate (required for ClaimStore FK)
    INSERT OR IGNORE INTO predicates (name, display_name, data_type, description)
    VALUES ('functional_profile', 'Functional Profile', 'json', 'LLM-derived functional profile summary');

    -- Watches: what URLs to monitor for changes
    -- Note: UNIQUE(canonical_key, watch_type, url) allows multiple pages per company
    CREATE TABLE IF NOT EXISTS watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL,
        url TEXT NOT NULL,
        watch_type TEXT NOT NULL,  -- 'website', 'portfolio', 'linkedin_about'
        interval_seconds INTEGER DEFAULT 86400,

        -- Operational state (debounce/cooldown)
        last_checked_at TEXT,
        last_snapshot_id INTEGER,
        consecutive_failures INTEGER DEFAULT 0,
        backoff_until TEXT,
        cooldown_until TEXT,
        consecutive_low_sev_hits INTEGER DEFAULT 0,
        last_low_sev_at TEXT,

        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(canonical_key, watch_type, url)
    );

    CREATE INDEX IF NOT EXISTS idx_watches_canonical ON watches(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_watches_active ON watches(active);
    CREATE INDEX IF NOT EXISTS idx_watches_due ON watches(active, backoff_until, last_checked_at);

    -- Snapshots: immutable fetch history
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL REFERENCES watches(id),
        fetched_at TEXT NOT NULL,
        status_code INTEGER,

        -- URL chain (for redirect detection)
        requested_url TEXT NOT NULL,
        final_url TEXT,
        final_host TEXT,

        -- Content fingerprints
        page_state TEXT,  -- live|coming_soon|blocked|error|unknown
        content_hash TEXT NOT NULL,
        text_length INTEGER NOT NULL DEFAULT 0,
        text_content_preview TEXT,  -- first 500 chars for debugging

        -- Embedding reference (for semantic drift)
        embedding_key TEXT,  -- e.g. "snapshot:123"

        -- Error tracking
        error TEXT,

        metadata_json TEXT  -- headers, timing, etc.
    );

    CREATE INDEX IF NOT EXISTS idx_snapshots_watch_time ON snapshots(watch_id, fetched_at DESC);
    CREATE INDEX IF NOT EXISTS idx_snapshots_content ON snapshots(content_hash);

    -- Diffs: computed differences between snapshots
    CREATE TABLE IF NOT EXISTS diffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL REFERENCES watches(id),
        old_snapshot_id INTEGER REFERENCES snapshots(id),
        new_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
        created_at TEXT NOT NULL,

        -- Severity scoring
        severity_score REAL NOT NULL,
        severity_components_json TEXT,
        semantic_drift REAL,  -- 1 - cosine_similarity (NULL if not computable)

        -- Denormalized flags for fast filtering
        has_redirect INTEGER DEFAULT 0,
        has_state_change INTEGER DEFAULT 0,
        has_text_change INTEGER DEFAULT 0,

        diff_summary_json TEXT  -- {"added_chars": N, "removed_chars": M, "content_delta": 0.3}
    );

    CREATE INDEX IF NOT EXISTS idx_diffs_watch_time ON diffs(watch_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_diffs_severity ON diffs(severity_score DESC);

    -- Monitoring alerts (separate from drift_alerts which is for model regression)
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL REFERENCES watches(id),
        diff_id INTEGER REFERENCES diffs(id),
        alert_reason TEXT NOT NULL,  -- 'high_severity', 'host_changed', 'status_410', etc.
        severity_score REAL NOT NULL,

        -- Acknowledgement workflow
        acknowledged INTEGER DEFAULT 0,
        acknowledged_by TEXT,
        acknowledged_at TEXT,

        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        payload_json TEXT  -- flexible extra context
    );

    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_unacked
    ON monitoring_alerts(acknowledged, created_at DESC);

    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_watch
    ON monitoring_alerts(watch_id, created_at DESC);

    -- Canonical key aliases (for redirect/rebrand tracking)
    CREATE TABLE IF NOT EXISTS canonical_key_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_key TEXT NOT NULL,
        new_key TEXT NOT NULL,
        reason TEXT,  -- 'redirect', 'rebrand', 'merge'
        detected_at TEXT NOT NULL,
        UNIQUE(old_key, new_key)
    );

    CREATE INDEX IF NOT EXISTS idx_aliases_old ON canonical_key_aliases(old_key);
    CREATE INDEX IF NOT EXISTS idx_aliases_new ON canonical_key_aliases(new_key);

    -- Monitoring configuration (singleton)
    CREATE TABLE IF NOT EXISTS monitoring_config (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        config_json TEXT NOT NULL
    );

    -- Insert default config
    INSERT OR IGNORE INTO monitoring_config (id, config_json) VALUES (1, json('{
        "action_threshold": 0.20,
        "profile_threshold": 0.60,
        "alert_threshold": 0.80,
        "debounce_window_hours": 72,
        "debounce_count": 2,
        "cooldown_hours": 24,
        "semantic_drift_threshold": 0.85,
        "weight_content": 0.3,
        "weight_semantic": 0.4,
        "weight_state": 0.15,
        "weight_redirect": 0.15,
        "max_snapshots_per_watch": 10,
        "max_diff_age_days": 90
    }'));

    -- Monitoring runs (metrics)
    CREATE TABLE IF NOT EXISTS monitoring_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        duration_seconds REAL,

        watches_checked INTEGER DEFAULT 0,
        snapshots_taken INTEGER DEFAULT 0,
        diffs_computed INTEGER DEFAULT 0,
        high_severity_events INTEGER DEFAULT 0,
        profile_updates_triggered INTEGER DEFAULT 0,

        errors_json TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_monitoring_runs_started ON monitoring_runs(started_at DESC);
    """,
    11: """
    -- =============================================================================
    -- MONITORING v2.4 ENHANCEMENTS - Spec hardening
    -- =============================================================================
    -- Adds: outbox event routing, hasher_version, failure classification, watch_events

    -- 11.1: Extend notion_outbox for event routing
    -- Note: SQLite requires adding columns one at a time
    ALTER TABLE notion_outbox ADD COLUMN event_type TEXT NOT NULL DEFAULT 'notion_push';
    ALTER TABLE notion_outbox ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5;

    -- Index for efficient event-type routing in outbox workers
    CREATE INDEX IF NOT EXISTS idx_outbox_event_due
    ON notion_outbox(event_type, status, next_attempt_at, created_at);

    -- 11.2: Add hasher_version to snapshots for maintenance diff detection
    ALTER TABLE snapshots ADD COLUMN hasher_version TEXT NOT NULL DEFAULT 'v1';

    -- Index for recent-hash guard queries
    CREATE INDEX IF NOT EXISTS idx_snapshots_hash_version
    ON snapshots(watch_id, content_hash, hasher_version, fetched_at DESC);

    -- 11.3: Add failure tracking columns to watches
    ALTER TABLE watches ADD COLUMN last_failure_category TEXT;
    ALTER TABLE watches ADD COLUMN last_failure_error TEXT;
    ALTER TABLE watches ADD COLUMN deactivated_reason TEXT;
    ALTER TABLE watches ADD COLUMN updated_at TEXT;

    -- 11.4: Create watch_events audit log table
    CREATE TABLE IF NOT EXISTS watch_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
        occurred_at TEXT NOT NULL,
        event_type TEXT NOT NULL,  -- fetch_started, fetch_success, fetch_failed, snapshot_recorded, diff_calculated, alert_created, profile_update_enqueued, deactivated
        event_json TEXT,  -- Additional context: {category, error, snapshot_id, diff_id, severity, etc.}
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_watch_events_watch ON watch_events(watch_id, occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_watch_events_type ON watch_events(event_type, occurred_at DESC);

    -- 11.5: Add config_hash to monitoring_config for run linkage
    ALTER TABLE monitoring_config ADD COLUMN config_hash TEXT;

    -- 11.6: Add config_hash and status to monitoring_runs
    ALTER TABLE monitoring_runs ADD COLUMN config_hash TEXT;
    ALTER TABLE monitoring_runs ADD COLUMN status TEXT DEFAULT 'running';  -- running, completed, failed
    """,
    12: """
    -- =============================================================================
    -- MONITORING v3.1 - Slack Notification Tracking
    -- =============================================================================
    -- Adds slack_notified columns to monitoring_alerts for dispatch deduplication

    -- 12.1: Add Slack notification tracking to monitoring_alerts
    ALTER TABLE monitoring_alerts ADD COLUMN slack_notified INTEGER DEFAULT 0;
    ALTER TABLE monitoring_alerts ADD COLUMN slack_notified_at TEXT;

    -- Index for efficient unnotified alert queries
    CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_slack
    ON monitoring_alerts(slack_notified, acknowledged, created_at DESC);
    """,
    13: """
    -- =============================================================================
    -- UX OVERHAUL - Company State & Inbox Management
    -- =============================================================================
    -- Adds company_state for inbox workflow, company_actions for audit log,
    -- and token_nonces for magic link security.
    -- NOTE: notion_outbox already exists (Migration 3) - not recreated here.

    -- 13.1: Company State (The Inbox Truth)
    -- Tracks company status through inbox workflow: inbox -> tracking -> passed/pipeline_requested
    CREATE TABLE IF NOT EXISTS company_state (
        canonical_key TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'inbox',  -- inbox, tracking, passed, pipeline_requested, funded
        owner TEXT,  -- GP who claimed the company
        last_action_at TEXT DEFAULT (datetime('now')),
        pass_reason TEXT,  -- Why company was passed
        notion_page_id TEXT,  -- Link to Notion if pushed
        snoozed_until TEXT  -- For snooze functionality
    );
    CREATE INDEX IF NOT EXISTS idx_company_state_status ON company_state(status);
    CREATE INDEX IF NOT EXISTS idx_company_state_owner ON company_state(owner);
    CREATE INDEX IF NOT EXISTS idx_company_state_snoozed ON company_state(snoozed_until);

    -- 13.2: Company Actions (Audit Log)
    -- Every action taken on a company is logged here for full audit trail
    CREATE TABLE IF NOT EXISTS company_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at TEXT DEFAULT (datetime('now')),
        canonical_key TEXT NOT NULL,
        action TEXT NOT NULL,  -- track, pass, pipeline, snooze, unsnooze, note
        actor TEXT,  -- Who took the action
        metadata_json TEXT  -- Extra context (pass_reason, note text, etc.)
    );
    CREATE INDEX IF NOT EXISTS idx_company_actions_key ON company_actions(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_company_actions_occurred ON company_actions(occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_company_actions_actor ON company_actions(actor);

    -- 13.3: Magic Link Token Nonces
    -- One-time-use tokens for email action links (security)
    CREATE TABLE IF NOT EXISTS token_nonces (
        nonce TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL,
        action TEXT NOT NULL,  -- track, pass, view
        created_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT,  -- Token expiration
        used INTEGER DEFAULT 0  -- 1 = consumed
    );
    CREATE INDEX IF NOT EXISTS idx_token_nonces_key ON token_nonces(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_token_nonces_expires ON token_nonces(expires_at);

    -- 13.4: Add created_by column to notion_outbox for audit
    ALTER TABLE notion_outbox ADD COLUMN created_by TEXT;
    """,
    14: """
    -- =============================================================================
    -- PHASE 1: FINANCE PREDICATES - PDF Profiler Support
    -- =============================================================================
    -- Adds finance-specific predicates to the claims ledger for PDF extraction.
    -- Includes display_name for all predicates.

    -- 14.1: Finance predicates for Data Room Profiler
    INSERT OR IGNORE INTO predicates (name, display_name, data_type, units, description) VALUES
        ('burn_rate_usd_monthly', 'Monthly Burn Rate', 'numeric', 'USD/month', 'Monthly cash burn rate'),
        ('runway_months', 'Runway', 'numeric', 'months', 'Months of runway remaining'),
        ('cash_on_hand_usd', 'Cash on Hand', 'numeric', 'USD', 'Current cash balance'),
        ('valuation_pre_money_usd', 'Pre-Money Valuation', 'numeric', 'USD', 'Pre-money valuation'),
        ('valuation_post_money_usd', 'Post-Money Valuation', 'numeric', 'USD', 'Post-money valuation'),
        ('round_size_usd', 'Round Size', 'numeric', 'USD', 'Current round size'),
        ('cap_table_snapshot', 'Cap Table', 'json', NULL, 'Cap table snapshot as JSON');
    """,
    15: """
    -- =============================================================================
    -- PHASE 2: DISCOVERY CACHE - Curated Discovery Support
    -- =============================================================================
    -- Adds discovery cache tables for problem-based search with 24hr TTL.
    -- Enables cache-first re-runnable discovery and thesis audit trail.

    -- 15.1: Discovery runs tracking
    CREATE TABLE IF NOT EXISTS discovery_runs (
        run_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        source TEXT NOT NULL,  -- 'tavily', 'manual', etc.
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT,
        metadata TEXT  -- JSON
    );

    CREATE INDEX IF NOT EXISTS idx_discovery_runs_query ON discovery_runs(query);
    CREATE INDEX IF NOT EXISTS idx_discovery_runs_expires_at ON discovery_runs(expires_at);

    -- 15.2: Discovery candidates with thesis audit columns
    CREATE TABLE IF NOT EXISTS discovery_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES discovery_runs(run_id),
        url TEXT NOT NULL,
        canonical_key TEXT,
        -- Thesis audit trail (for ALL candidates)
        keyword_score REAL,
        keyword_category TEXT,
        negative_keywords TEXT,  -- JSON array
        llm_score REAL,
        llm_category TEXT,
        llm_rationale TEXT,
        routing TEXT,  -- qualified, held, rejected
        rejection_reason TEXT,
        -- Metadata
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_disc_cand_run ON discovery_candidates(run_id);
    CREATE INDEX IF NOT EXISTS idx_disc_cand_routing ON discovery_candidates(routing);
    CREATE INDEX IF NOT EXISTS idx_disc_cand_canonical_key ON discovery_candidates(canonical_key);
    """,
    16: """
    -- =============================================================================
    -- COMMAND CENTER PHASE 0 - Core Infrastructure for Dashboard
    -- =============================================================================
    -- Adds: entity_snapshots (metadata), entity_alerts, entity_stages, jobs
    -- For content storage, see blob_store.py (content-addressable blob storage)

    -- 16.1: Entity Snapshots (metadata only, content in blob store)
    -- Immutable history of entity state captures
    CREATE TABLE IF NOT EXISTS entity_snapshots (
        id TEXT PRIMARY KEY,  -- UUID
        entity_key TEXT NOT NULL,  -- canonical key
        source TEXT NOT NULL,  -- collector name or 'manual'
        url TEXT,  -- source URL if applicable

        -- Content fingerprint (actual content in blob store)
        content_hash TEXT NOT NULL,  -- SHA256, references blob store
        content_size INTEGER NOT NULL DEFAULT 0,

        -- Extracted structured data
        extracted_json TEXT,  -- JSON of key-value pairs
        diff_summary TEXT,  -- "what changed" text from previous snapshot
        significance_score REAL DEFAULT 0.0,  -- 0-1, how material is the change

        -- Retention
        retention_tier TEXT DEFAULT 'hot',  -- hot, warm, cold
        captured_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_entity_snapshots_key ON entity_snapshots(entity_key);
    CREATE INDEX IF NOT EXISTS idx_entity_snapshots_hash ON entity_snapshots(content_hash);
    CREATE INDEX IF NOT EXISTS idx_entity_snapshots_captured ON entity_snapshots(captured_at DESC);
    CREATE INDEX IF NOT EXISTS idx_entity_snapshots_tier ON entity_snapshots(retention_tier);

    -- 16.2: Entity Alerts (changes requiring review)
    CREATE TABLE IF NOT EXISTS entity_alerts (
        id TEXT PRIMARY KEY,  -- UUID
        entity_key TEXT NOT NULL,
        snapshot_id TEXT REFERENCES entity_snapshots(id),

        -- Alert details
        alert_type TEXT NOT NULL,  -- 'field_change', 'new_signal', 'anomaly', 'stale_data'
        severity TEXT NOT NULL,  -- 'low', 'medium', 'high', 'critical'
        summary TEXT NOT NULL,  -- Human-readable summary

        -- Review workflow
        status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'accepted', 'rejected', 'snoozed'
        reviewed_by TEXT,
        reviewed_at TEXT,
        snooze_until TEXT,

        -- Metadata
        metadata_json TEXT,  -- Additional context
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_entity_alerts_key ON entity_alerts(entity_key);
    CREATE INDEX IF NOT EXISTS idx_entity_alerts_status ON entity_alerts(status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_entity_alerts_severity ON entity_alerts(severity, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_entity_alerts_snooze ON entity_alerts(snooze_until);

    -- 16.3: Entity Stages (unified stage management)
    -- Consolidates stage tracking with audit trail
    CREATE TABLE IF NOT EXISTS entity_stages (
        id TEXT PRIMARY KEY,  -- UUID
        entity_key TEXT NOT NULL,
        stage TEXT NOT NULL,  -- Inbox, Tracking, Review, Meeting, Diligence, IC, Won, Lost, Passed
        owner TEXT,  -- Assigned GP
        notes TEXT,  -- Markdown notes
        next_step TEXT,  -- Next action item
        due_date TEXT,  -- ISO date

        -- Optimistic locking
        _version INTEGER DEFAULT 1,

        -- Notion sync
        notion_synced INTEGER DEFAULT 0,
        notion_page_id TEXT,

        -- Audit
        changed_by TEXT,
        changed_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    -- One active stage per entity
    CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_stages_key ON entity_stages(entity_key);
    CREATE INDEX IF NOT EXISTS idx_entity_stages_stage ON entity_stages(stage);
    CREATE INDEX IF NOT EXISTS idx_entity_stages_owner ON entity_stages(owner);
    CREATE INDEX IF NOT EXISTS idx_entity_stages_due ON entity_stages(due_date);

    -- 16.4: Entity Stage History (audit trail)
    CREATE TABLE IF NOT EXISTS entity_stage_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_key TEXT NOT NULL,
        old_stage TEXT,
        new_stage TEXT NOT NULL,
        old_owner TEXT,
        new_owner TEXT,
        reason TEXT,  -- Why the change was made
        changed_by TEXT,
        changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_entity_stage_history_key ON entity_stage_history(entity_key);
    CREATE INDEX IF NOT EXISTS idx_entity_stage_history_time ON entity_stage_history(changed_at DESC);

    -- 16.5: Jobs (long-running background operations)
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,  -- UUID
        job_type TEXT NOT NULL,  -- 'collect', 'process', 'sync', 'backup', 'import'
        status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'running', 'completed', 'failed', 'cancelled'

        -- Configuration
        params_json TEXT,  -- Job parameters

        -- Progress
        progress_pct INTEGER DEFAULT 0,
        progress_message TEXT,

        -- Results
        result_json TEXT,  -- Job output
        error_message TEXT,

        -- Timing
        started_at TEXT,
        completed_at TEXT,

        -- Audit
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

    -- 16.6: Job Logs (streaming output)
    CREATE TABLE IF NOT EXISTS job_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        level TEXT NOT NULL,  -- 'debug', 'info', 'warning', 'error'
        message TEXT NOT NULL,
        logged_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, logged_at);

    -- 16.7: Saved Searches
    CREATE TABLE IF NOT EXISTS saved_searches (
        id TEXT PRIMARY KEY,  -- UUID
        name TEXT NOT NULL,
        query TEXT NOT NULL,  -- Search query
        filters_json TEXT,  -- Applied filters

        -- Tracking
        last_run_at TEXT,
        last_result_count INTEGER,

        -- Audit
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_saved_searches_name ON saved_searches(name);

    -- 16.8: Collections (manual groupings)
    CREATE TABLE IF NOT EXISTS collections (
        id TEXT PRIMARY KEY,  -- UUID
        name TEXT NOT NULL,
        description TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(name);

    -- 16.9: Collection Members
    CREATE TABLE IF NOT EXISTS collection_members (
        collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        entity_key TEXT NOT NULL,
        added_by TEXT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (collection_id, entity_key)
    );

    CREATE INDEX IF NOT EXISTS idx_collection_members_entity ON collection_members(entity_key);

    -- 16.10: User Sessions (for dashboard auth)
    CREATE TABLE IF NOT EXISTS user_sessions (
        id TEXT PRIMARY KEY,  -- Session ID
        user_id TEXT NOT NULL,
        user_email TEXT NOT NULL,
        user_role TEXT NOT NULL DEFAULT 'readonly',  -- 'gp', 'analyst', 'readonly'
        ip_address TEXT,
        user_agent TEXT,
        expires_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions(expires_at);
    """,
    17: """
    -- =============================================================================
    -- EVALUATION HARNESS - Chain-of-Thought Reasoning Storage
    -- =============================================================================
    -- Add reasoning_trace column to thesis_classifications for evaluation harness
    -- Stores chain-of-thought reasoning from LLM classifications

    ALTER TABLE thesis_classifications ADD COLUMN reasoning_trace TEXT;
    -- JSON object containing:
    -- - cot_enabled: boolean
    -- - reasoning_steps: array of reasoning steps
    -- - self_critique: optional critique response
    -- - evaluation_model: model used if different from classifier

    ALTER TABLE thesis_classifications ADD COLUMN cot_enabled INTEGER DEFAULT 0;
    -- Whether chain-of-thought was used for this classification
    """,
    18: """
    -- =============================================================================
    -- COMMUNITY SENTIMENT TRACKING (Phase A: Community Signals)
    -- =============================================================================
    -- Stores aggregated sentiment data from community sources (Reddit, Telegram, Discord).
    -- Enables: confidence boosts/penalties based on community buzz.

    -- Community sentiment aggregates per canonical key + source
    CREATE TABLE IF NOT EXISTS community_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL,
        source TEXT NOT NULL,              -- 'reddit', 'telegram', 'discord'

        -- Sentiment metrics
        mention_count INTEGER NOT NULL DEFAULT 0,
        unique_authors INTEGER NOT NULL DEFAULT 0,
        avg_sentiment_score REAL,          -- -1.0 to 1.0
        sentiment_label TEXT,              -- 'positive', 'negative', 'neutral'

        -- Distribution
        positive_ratio REAL,               -- 0.0 to 1.0
        negative_ratio REAL,
        neutral_ratio REAL,

        -- Confidence impact
        confidence_boost REAL DEFAULT 0.0, -- -0.15 to +0.10

        -- Keywords detected (for audit)
        top_keywords TEXT,                 -- JSON array of most common keywords

        -- Time window
        window_start TEXT,                 -- ISO 8601
        window_end TEXT,                   -- ISO 8601

        -- Timestamps
        analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT,

        -- One row per canonical_key + source (upsert pattern)
        UNIQUE(canonical_key, source)
    );

    CREATE INDEX IF NOT EXISTS idx_community_sent_key ON community_sentiment(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_community_sent_source ON community_sentiment(source);
    CREATE INDEX IF NOT EXISTS idx_community_sent_boost ON community_sentiment(confidence_boost);
    CREATE INDEX IF NOT EXISTS idx_community_sent_label ON community_sentiment(sentiment_label);

    -- Individual community mentions (for audit trail)
    CREATE TABLE IF NOT EXISTS community_mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT,                -- May be NULL if company not yet identified
        source TEXT NOT NULL,              -- 'reddit', 'telegram', 'discord'
        source_id TEXT NOT NULL,           -- Platform-specific ID

        -- Content metadata (NO body text per compliance)
        title TEXT,                        -- Post/message title (if any)
        url TEXT,                          -- Link to source
        author TEXT,                       -- Username (may be anonymized)

        -- Sentiment
        sentiment_score REAL,              -- -1.0 to 1.0
        sentiment_label TEXT,              -- 'positive', 'negative', 'neutral'
        sentiment_method TEXT,             -- 'heuristic' or 'ollama'
        keywords_found TEXT,               -- JSON array

        -- Context
        subreddit TEXT,                    -- For Reddit
        channel_name TEXT,                 -- For Telegram/Discord
        engagement_score INTEGER,          -- Upvotes, reactions, etc.

        -- Timestamps
        posted_at TEXT,                    -- When originally posted
        detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        -- Dedupe
        UNIQUE(source, source_id)
    );

    CREATE INDEX IF NOT EXISTS idx_mentions_key ON community_mentions(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_mentions_source ON community_mentions(source);
    CREATE INDEX IF NOT EXISTS idx_mentions_detected ON community_mentions(detected_at);
    CREATE INDEX IF NOT EXISTS idx_mentions_posted ON community_mentions(posted_at);
    CREATE INDEX IF NOT EXISTS idx_mentions_sentiment ON community_mentions(sentiment_label);
    """,
    19: """
    -- =============================================================================
    -- PHASE G: SYNTHESIS ENHANCEMENT - Entity Resolution & LLM Arbitration
    -- =============================================================================
    -- Implements stable entity identity via alias store and LLM conflict resolution.
    -- Enables: audit-grade provenance, deterministic entity merging, cost-controlled LLM.

    -- Entity aliases: maps strong keys to stable entity IDs (no ID rotation)
    CREATE TABLE IF NOT EXISTS entity_aliases (
        strong_key TEXT PRIMARY KEY,          -- e.g., "domain:acme.com", "reg:companies_house:12345"
        entity_id TEXT NOT NULL,              -- Stable entity identifier (sha256[:16] of canonical key)
        created_at TEXT NOT NULL,             -- ISO 8601
        source_signal_id INTEGER,             -- Signal that first introduced this key
        source_key TEXT                       -- Source API that provided this key
    );

    CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity_id
        ON entity_aliases(entity_id);

    -- Entity migrations: tracks when entity IDs are merged
    CREATE TABLE IF NOT EXISTS entity_migrations (
        from_entity_id TEXT NOT NULL,         -- Entity ID being retired
        to_entity_id TEXT NOT NULL,           -- Entity ID that absorbs it
        merged_at TEXT NOT NULL,              -- ISO 8601
        merge_reason TEXT,                    -- e.g., "alias_collision_merge", "manual_merge"
        PRIMARY KEY (from_entity_id, to_entity_id, merged_at)
    );

    CREATE INDEX IF NOT EXISTS idx_entity_migrations_to
        ON entity_migrations(to_entity_id);
    CREATE INDEX IF NOT EXISTS idx_entity_migrations_from
        ON entity_migrations(from_entity_id);

    -- LLM decisions: cache for conflict arbitration (policy-versioned)
    CREATE TABLE IF NOT EXISTS llm_decisions (
        cache_key TEXT PRIMARY KEY,           -- sha256 of policy_version|field_name|conflict_type|candidates_signature
        policy_version TEXT NOT NULL,         -- e.g., "g_v1.0" - cache invalidates on policy change
        field_name TEXT NOT NULL,             -- Field this decision applies to
        conflict_type TEXT NOT NULL,          -- VALUE_MISMATCH, AUTHORITY_TIE, etc.
        prompt_hash TEXT NOT NULL,            -- sha256 of prompt (privacy: don't store raw prompt)
        decision_json TEXT NOT NULL,          -- JSON: {chosen_index, decision_reason, decision_rule}
        created_at TEXT NOT NULL              -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_llm_decisions_policy
        ON llm_decisions(policy_version);
    CREATE INDEX IF NOT EXISTS idx_llm_decisions_field
        ON llm_decisions(field_name);
    """,
    20: """
    -- =============================================================================
    -- PHASE G SPRINT 2: IDENTITY RESOLUTION - Weak Aliases & Blocking Index
    -- =============================================================================
    -- Extends entity resolution with weak/fuzzy matching via blocking-first approach.
    -- Preserves existing tables: entity_aliases, entity_migrations, llm_decisions (migration 19).

    -- 20.1: Weak key aliases (name variants, fuzzy-derived)
    -- Separate from strong key aliases in entity_aliases (migration 19)
    CREATE TABLE IF NOT EXISTS entity_key_aliases (
        alias_key TEXT PRIMARY KEY,               -- e.g., "name_norm:acme", "name_loc:acme:london"
        entity_id TEXT NOT NULL,                  -- Stable entity identifier
        alias_type TEXT NOT NULL,                 -- 'name_norm', 'name_loc', 'fuzzy_derived'
        confidence REAL NOT NULL DEFAULT 0.8,     -- How confident we are in this alias
        source TEXT,                              -- e.g., 'sec_edgar', 'companies_house', 'fuzzy_match'
        expires_at TEXT,                          -- Fuzzy aliases expire after 30 days
        archived_at TEXT,                         -- NULL = active, set to archive
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_entity_key_aliases_entity
        ON entity_key_aliases(entity_id);
    CREATE INDEX IF NOT EXISTS idx_entity_key_aliases_type
        ON entity_key_aliases(alias_type);
    CREATE INDEX IF NOT EXISTS idx_entity_key_aliases_active
        ON entity_key_aliases(archived_at) WHERE archived_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_entity_key_aliases_expires
        ON entity_key_aliases(expires_at) WHERE expires_at IS NOT NULL;

    -- 20.2: Blocking index for efficient fuzzy candidate retrieval
    -- Constrains fuzzy matching to tokens that share blocking tokens
    CREATE TABLE IF NOT EXISTS entity_blocking_index (
        blocking_token TEXT NOT NULL,             -- e.g., "acme", "AKM" (metaphone)
        token_type TEXT NOT NULL,                 -- 'first', 'meta', 'tld3', 'trigram'
        entity_id TEXT NOT NULL,                  -- Entity this token belongs to
        alias_key TEXT NOT NULL,                  -- Which alias generated this token
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (blocking_token, token_type, entity_id, alias_key)
    );

    CREATE INDEX IF NOT EXISTS idx_blocking_token_lookup
        ON entity_blocking_index(blocking_token, token_type);
    CREATE INDEX IF NOT EXISTS idx_blocking_entity
        ON entity_blocking_index(entity_id);
    CREATE INDEX IF NOT EXISTS idx_blocking_alias
        ON entity_blocking_index(alias_key);
    """,
    21: """
    -- =============================================================================
    -- PHASE G SPRINT 2: BI-TEMPORAL CLAIM FACTS (SCD-2)
    -- =============================================================================
    -- Implements SCD-Type-2 fact storage with authority tiers.
    -- Separate from migration 7 KG-lite (claims, claim_extractions, claim_evidence).
    -- Enables: "What was the company name on 2024-01-15?" with full history.

    -- 21.1: Bi-temporal claim facts
    CREATE TABLE IF NOT EXISTS claim_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT NOT NULL,                  -- Stable entity identifier
        predicate TEXT NOT NULL,                  -- 'company_name', 'founding_date', etc.
        value_json TEXT NOT NULL,                 -- JSON-encoded value

        -- Authority and confidence
        source_tier INTEGER NOT NULL,             -- 1 (highest) to 5 (lowest)
        confidence REAL NOT NULL DEFAULT 0.5,     -- 0-1 confidence score

        -- Bi-temporal tracking (SCD-2)
        valid_from TEXT NOT NULL,                 -- When this fact became true (business time)
        valid_until TEXT,                         -- NULL = currently valid, set when superseded

        -- Observation time
        observed_at TEXT NOT NULL,                -- When we first observed this fact (system time)
        last_observed_at TEXT,                    -- Updated if same value re-observed

        -- Retraction
        is_retracted INTEGER NOT NULL DEFAULT 0,  -- 1 = explicitly retracted

        -- Evidence trail
        supporting_signal_ids TEXT,               -- JSON array of signal IDs
        source_canonical_key TEXT,                -- Which canonical key this came from

        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    -- Active facts index: fast lookup for current state
    CREATE INDEX IF NOT EXISTS idx_claim_facts_active
        ON claim_facts(entity_id, predicate)
        WHERE valid_until IS NULL AND is_retracted = 0;

    -- History lookup
    CREATE INDEX IF NOT EXISTS idx_claim_facts_entity
        ON claim_facts(entity_id, predicate, valid_from);

    -- Authority-based queries
    CREATE INDEX IF NOT EXISTS idx_claim_facts_tier
        ON claim_facts(entity_id, predicate, source_tier, observed_at DESC);

    -- Temporal queries
    CREATE INDEX IF NOT EXISTS idx_claim_facts_temporal
        ON claim_facts(entity_id, valid_from, valid_until);
    """,
    22: """
    -- =============================================================================
    -- PROGRESSIVE CONTENT PIPELINE - Per-watch configuration and HTTP caching
    -- =============================================================================
    -- Adds columns to watches table for:
    -- 1. Per-watch extraction configuration (selectors, presets, transport rules)
    -- 2. HTTP conditional request support (ETag, Last-Modified headers)

    -- 22.1: Add config_json for per-watch extraction configuration
    -- JSON blob containing: selectors, preset reference, transport rules, etc.
    ALTER TABLE watches ADD COLUMN config_json TEXT DEFAULT NULL;

    -- 22.2: Add HTTP conditional request headers for efficient polling
    -- Enables 304 Not Modified responses to skip unchanged pages
    ALTER TABLE watches ADD COLUMN last_etag TEXT DEFAULT NULL;
    ALTER TABLE watches ADD COLUMN last_modified TEXT DEFAULT NULL;

    -- 22.3: Index for finding watches with custom configurations
    CREATE INDEX IF NOT EXISTS idx_watches_config_type
        ON watches(watch_type) WHERE config_json IS NOT NULL;
    """,
    23: """
    -- =============================================================================
    -- SHADOW LOGGING INFRASTRUCTURE - Feature experimentation framework
    -- =============================================================================
    -- Enables the "build wide, activate narrow" experimentation pattern:
    -- 1. Deploy features in SHADOW mode (computed but 0 weight)
    -- 2. Log predictions without affecting routing
    -- 3. Measure correlation with outcomes over 2-3 weeks
    -- 4. Promote to ACTIVE only if lift is demonstrated

    -- 23.1: shadow_log table for storing SHADOW feature computations
    CREATE TABLE IF NOT EXISTS shadow_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_name TEXT NOT NULL,          -- e.g., 'boilerplate_defense', 'team_shape'
        canonical_key TEXT NOT NULL,         -- Company/entity identifier
        computed_value TEXT NOT NULL,        -- JSON blob of computation result
        signal_id INTEGER,                   -- Optional FK to signals table
        logged_at TEXT NOT NULL,             -- ISO 8601 timestamp

        FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE SET NULL
    );

    -- 23.2: Indexes for common query patterns
    CREATE INDEX IF NOT EXISTS idx_shadow_log_feature ON shadow_log(feature_name);
    CREATE INDEX IF NOT EXISTS idx_shadow_log_canonical_key ON shadow_log(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_shadow_log_logged_at ON shadow_log(logged_at);
    CREATE INDEX IF NOT EXISTS idx_shadow_log_feature_logged ON shadow_log(feature_name, logged_at);
    """,
    24: """
    -- =============================================================================
    -- OPS LAYER: Memory & Intelligence Subsystem
    -- =============================================================================
    -- Adds learning memory, user actions, health monitoring, and audit trail.
    -- All new tables — NO modifications to existing tables.

    -- 24.1: user_actions — User decisions on signals (approve/reject/defer/bookmark)
    CREATE TABLE IF NOT EXISTS user_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        action TEXT CHECK(action IN ('approve', 'reject', 'defer', 'bookmark')) NOT NULL,
        rejection_reason TEXT,
        rejection_notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_user_actions_signal ON user_actions(signal_id);
    CREATE INDEX IF NOT EXISTS idx_user_actions_created ON user_actions(created_at DESC);

    -- 24.2: memory_facts — Investment insight memory with confidence + lifecycle
    CREATE TABLE IF NOT EXISTS memory_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT CHECK(type IN ('constraint', 'nuance', 'example')) NOT NULL,
        content TEXT NOT NULL,
        confidence REAL CHECK(confidence >= 0 AND confidence <= 1) NOT NULL,
        source_action_id INTEGER,
        source_signal_id INTEGER,
        status TEXT CHECK(status IN ('active', 'pending', 'retired')) DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        superseded_by INTEGER,
        used_count INTEGER DEFAULT 0,
        last_used_at TIMESTAMP,
        FOREIGN KEY(source_action_id) REFERENCES user_actions(id) ON DELETE SET NULL,
        FOREIGN KEY(source_signal_id) REFERENCES signals(id) ON DELETE SET NULL,
        FOREIGN KEY(superseded_by) REFERENCES memory_facts(id) ON DELETE SET NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedupe
        ON memory_facts(source_action_id, type, content)
        WHERE source_action_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_memory_active
        ON memory_facts(type) WHERE superseded_by IS NULL AND status = 'active';
    CREATE INDEX IF NOT EXISTS idx_memory_pending
        ON memory_facts(type) WHERE superseded_by IS NULL AND status = 'pending';
    CREATE INDEX IF NOT EXISTS idx_memory_status_created
        ON memory_facts(status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_source_signal
        ON memory_facts(source_signal_id);
    CREATE INDEX IF NOT EXISTS idx_memory_used_count
        ON memory_facts(used_count DESC, last_used_at DESC);

    -- 24.3: memory_action_state — Processing state machine for extraction jobs
    CREATE TABLE IF NOT EXISTS memory_action_state (
        action_id INTEGER PRIMARY KEY,
        status TEXT CHECK(status IN ('processing', 'processed', 'no_facts', 'failed', 'failed_permanent', 'suspicious')) NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(action_id) REFERENCES user_actions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_action_state_status ON memory_action_state(status);
    CREATE INDEX IF NOT EXISTS idx_action_state_attempts ON memory_action_state(attempts);

    -- 24.4: extraction_runs — LLM extraction run metrics (cost tracking)
    CREATE TABLE IF NOT EXISTS extraction_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        decisions_processed INTEGER NOT NULL,
        facts_created INTEGER NOT NULL,
        llm_failures INTEGER NOT NULL,
        duration_seconds REAL NOT NULL,
        estimated_cost REAL DEFAULT 0.0
    );

    -- 24.5: audit_log — Full before/after state audit trail
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        operation TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id INTEGER,
        user TEXT NOT NULL,
        before_state TEXT,
        after_state TEXT,
        reason TEXT
    );

    -- 24.6: system_health — Per-component health monitoring
    CREATE TABLE IF NOT EXISTS system_health (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        component TEXT NOT NULL,
        status TEXT CHECK(status IN ('healthy', 'degraded', 'unhealthy')) NOT NULL,
        latency_ms REAL,
        error TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_health_timestamp ON system_health(timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_health_component ON system_health(component, timestamp DESC);

    -- 24.7: fact_citations — Track which facts were used in classifications
    CREATE TABLE IF NOT EXISTS fact_citations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fact_id INTEGER NOT NULL,
        signal_id INTEGER,
        cited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        context TEXT,
        FOREIGN KEY(fact_id) REFERENCES memory_facts(id) ON DELETE CASCADE,
        FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_citations_fact ON fact_citations(fact_id, cited_at DESC);
    CREATE INDEX IF NOT EXISTS idx_citations_signal ON fact_citations(signal_id);
    CREATE INDEX IF NOT EXISTS idx_citations_fact_signal ON fact_citations(fact_id, signal_id, cited_at DESC);
    """,
    25: QUALITY_TABLES_DDL,
    26: """
    -- Add disagreement_detected flag to thesis_classifications
    -- Tracks when keyword and LLM classifiers disagree on thesis fit
    -- Logic: disagreement = (keyword_score >= 0.7 AND thesis_fit_score < 0.4)
    --                    OR (keyword_score < 0.4 AND thesis_fit_score >= 0.7)
    ALTER TABLE thesis_classifications ADD COLUMN disagreement_detected BOOLEAN DEFAULT 0;
    """,
    27: AUDIT_LOG_DDL,
    28: V28_CANONICAL_IDENTITY_DDL,
    29: V29_REVIEW_QUEUE_DDL,
    30: V30_PIPELINE_IDENTITY_STATS_DDL,
    31: V31_BATCH_PUBLISH_DDL,
    32: V32_FUNCTIONAL_SCHEMA_DDL,
    33: V33_CASE_LAW_DDL,
    34: V34_EXEMPLARS_DDL,
    35: V35_PLATFORM_HARDENING_DDL,
    36: V36_WAVE1_TRIAGE_DDL,
    37: V37_ACH_ANALYSES_DDL,
    38: V38_WAVE2_SHADOW_CANARY_DDL,
    39: V39_ACTIVE_HUNTER_DDL,
    40: V40_MERGE_LIFECYCLE_DDL,
    41: V41_DRIFT_MONITORING_DDL,
    42: V42_EVIDENCE_FAMILY_DDL,
    43: V43_CANONICAL_KEY_V2_DDL,
    44: V44_DNS_PROMOTION_ALIASES_DDL,
    45: V45_EVIDENCE_KEY_DDL,
    46: V46_EVIDENCE_KEY_UNIQUE_DDL,
    47: V47_GOVERNANCE_TRIGGERS_DDL,
    48: V48_SHADOW_LOG_METRICS_DDL,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StoredSignal:
    """A signal loaded from the database"""
    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    company_name: Optional[str]
    confidence: float
    raw_data: Dict[str, Any]
    detected_at: datetime
    created_at: datetime

    # Identity (added by v28 migration)
    company_id: Optional[str] = None

    # Processing info (if joined)
    processing_status: Optional[str] = None
    notion_page_id: Optional[str] = None
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class SuppressionEntry:
    """Entry in the suppression cache"""
    canonical_key: str
    notion_page_id: str
    status: str
    company_name: Optional[str] = None
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=7))
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class CompanyState:
    """Company state in the inbox workflow"""
    canonical_key: str
    status: str  # inbox, tracking, passed, pipeline_requested, funded
    owner: Optional[str] = None
    last_action_at: Optional[datetime] = None
    pass_reason: Optional[str] = None
    notion_page_id: Optional[str] = None
    snoozed_until: Optional[datetime] = None


@dataclass
class CompanyAction:
    """Audit log entry for company actions"""
    id: int
    canonical_key: str
    action: str  # track, pass, pipeline, snooze, unsnooze, note
    occurred_at: datetime
    actor: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class TokenNonce:
    """Magic link token for secure actions"""
    nonce: str
    canonical_key: str
    action: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    used: bool = False


@dataclass
class InboxCompany:
    """Company summary for inbox view"""
    canonical_key: str
    company_name: Optional[str]
    status: str
    max_confidence: float
    signal_count: int
    sources: str  # Comma-separated source list
    first_seen: datetime
    last_seen: datetime
    owner: Optional[str] = None
    thesis_fit_score: Optional[float] = None
    vertical: Optional[str] = None


@dataclass
class EntitySnapshot:
    """Immutable snapshot of entity state (metadata only, content in blob store)."""
    id: str  # UUID
    entity_key: str
    source: str
    url: Optional[str]
    content_hash: str  # SHA256, references blob store
    content_size: int
    extracted_json: Optional[Dict[str, Any]] = None
    diff_summary: Optional[str] = None
    significance_score: float = 0.0
    retention_tier: str = "hot"
    captured_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class EntityAlert:
    """Alert for entity changes requiring review."""
    id: str  # UUID
    entity_key: str
    snapshot_id: Optional[str]
    alert_type: str  # 'field_change', 'new_signal', 'anomaly', 'stale_data'
    severity: str  # 'low', 'medium', 'high', 'critical'
    summary: str
    status: str = "pending"  # 'pending', 'accepted', 'rejected', 'snoozed'
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    snooze_until: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


@dataclass
class EntityStage:
    """Entity stage in the pipeline workflow."""
    id: str  # UUID
    entity_key: str
    stage: str  # Inbox, Tracking, Review, Meeting, Diligence, IC, Won, Lost, Passed
    owner: Optional[str] = None
    notes: Optional[str] = None
    next_step: Optional[str] = None
    due_date: Optional[str] = None
    version: int = 1
    notion_synced: bool = False
    notion_page_id: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class Job:
    """Background job for long-running operations."""
    id: str  # UUID
    job_type: str  # 'collect', 'process', 'sync', 'backup', 'import'
    status: str = "pending"  # 'pending', 'running', 'completed', 'failed', 'cancelled'
    params: Optional[Dict[str, Any]] = None
    progress_pct: int = 0
    progress_message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class JobLog:
    """Log entry for a job."""
    id: int
    job_id: str
    level: str  # 'debug', 'info', 'warning', 'error'
    message: str
    logged_at: Optional[datetime] = None


@dataclass
class SavedSearch:
    """Saved search query."""
    id: str  # UUID
    name: str
    query: str
    filters: Optional[Dict[str, Any]] = None
    last_run_at: Optional[datetime] = None
    last_result_count: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Collection:
    """Manual grouping of entities."""
    id: str  # UUID
    name: str
    description: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class UserSession:
    """User session for dashboard auth."""
    id: str  # Session ID
    user_id: str
    user_email: str
    user_role: str = "readonly"  # 'gp', 'analyst', 'readonly'
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# =============================================================================
# SIGNAL STORE
# =============================================================================

class SignalStore:
    """
    Async SQLite storage for Discovery Engine signals.

    Features:
    - Connection pooling via aiosqlite
    - Automatic schema migrations
    - Transaction support
    - JSON serialization for complex fields
    - TTL-based suppression cache
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        suppression_ttl_days: int = 7,
        identity_store: Optional[EntityIdentityStore] = None,
        use_thin_files: bool = False,
    ):
        """
        Initialize signal store.

        Args:
            db_path: Path to SQLite database file.  When None (default),
                     resolves via DISCOVERY_DB_PATH > SIGNAL_DB_PATH > "signals.db".
            suppression_ttl_days: How long to cache Notion entries before re-checking
            identity_store: Phase G EntityIdentityStore for company_id resolution
            use_thin_files: Enable thin file upsert on save_signal()
        """
        from utils.db_path_helper import resolve_db_path_env
        self.db_path = Path(resolve_db_path_env(db_path))
        self.suppression_ttl_days = suppression_ttl_days
        self._identity_store = identity_store
        self._use_thin_files = use_thin_files
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Initialize database connection and apply migrations.
        Should be called once at startup.
        """
        # Create parent directories if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to database
        self._db = await aiosqlite.connect(str(self.db_path))

        # Performance and safety PRAGMAs for Phase G Sprint 2
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA synchronous = NORMAL")
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.execute("PRAGMA foreign_keys = ON")

        # Apply migrations
        await self._apply_migrations()

        # Create FTS5 virtual table for search
        await self._create_fts_table()

        # Create filter presets table
        await self._create_filter_presets_table()

        # Create memory_facts FTS5 table and triggers (ops layer v24)
        await self._create_memory_facts_fts()

        logger.info(f"SignalStore initialized: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Context manager for transactions.

        Usage:
            async with store.transaction() as conn:
                await conn.execute(...)
                await conn.execute(...)
                # Commits on success, rolls back on exception
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        async with self._lock:
            try:
                await self._db.execute("BEGIN")
                yield self._db
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise

    @asynccontextmanager
    async def transaction_immediate(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Context manager for IMMEDIATE transactions (Phase G Sprint 2).

        Uses BEGIN IMMEDIATE to acquire a write lock immediately,
        preventing write starvation in concurrent scenarios.

        Usage:
            async with store.transaction_immediate() as conn:
                await conn.execute(...)
                # Commits on success, rolls back on exception
        """
        if not self._db:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        async with self._lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                yield self._db
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise

    # =========================================================================
    # MIGRATIONS
    # =========================================================================

    async def _apply_migrations(self) -> None:
        """Apply pending schema migrations."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Get current version
        try:
            cursor = await self._db.execute(
                "SELECT MAX(version) FROM schema_migrations"
            )
            row = await cursor.fetchone()
            current_version = row[0] if row and row[0] else 0
        except aiosqlite.OperationalError:
            # Table doesn't exist yet
            current_version = 0

        # Apply each pending migration
        for version in sorted(MIGRATIONS.keys()):
            if version <= current_version:
                continue

            logger.info(f"Applying migration v{version}...")

            async with self.transaction() as conn:
                # Execute migration SQL
                await conn.executescript(MIGRATIONS[version])

                # Record migration
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (version, applied_at, description)
                    VALUES (?, ?, ?)
                    """,
                    (
                        version,
                        datetime.now(timezone.utc).isoformat(),
                        f"Schema version {version}"
                    )
                )

            logger.info(f"Migration v{version} applied successfully")

    async def _create_fts_table(self) -> None:
        """Create FTS5 virtual table for fuzzy search."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS signals_fts USING fts5(
                signal_id UNINDEXED,
                company_name,
                searchable_text,
                vertical,
                source_api,
                tokenize='porter unicode61'
            )
        """)
        await self._db.commit()

    async def _create_filter_presets_table(self) -> None:
        """Create filter presets table for saved searches."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS filter_presets (
                preset_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                filters TEXT NOT NULL,
                schema_version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                last_used TIMESTAMP
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_presets_name ON filter_presets(name)"
        )
        await self._db.commit()

    async def _create_memory_facts_fts(self) -> None:
        """Create FTS5 virtual table and sync triggers for memory_facts (ops layer v24)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Only create if memory_facts table exists (v24 migration applied)
        cursor = await self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_facts'"
        )
        if not await cursor.fetchone():
            return

        # FTS5 virtual table
        await self._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts USING fts5(
                content,
                type UNINDEXED,
                confidence UNINDEXED,
                content='memory_facts',
                content_rowid='id',
                tokenize='porter unicode61'
            )
        """)

        # Auto-sync triggers (idempotent via IF NOT EXISTS)
        await self._db.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_facts_ai AFTER INSERT ON memory_facts BEGIN
                INSERT INTO memory_facts_fts(rowid, content, type, confidence)
                VALUES (new.id, new.content, new.type, new.confidence);
            END
        """)
        await self._db.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_facts_ad AFTER DELETE ON memory_facts BEGIN
                INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
                VALUES('delete', old.id, old.content, old.type, old.confidence);
            END
        """)
        await self._db.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_facts_au AFTER UPDATE ON memory_facts BEGIN
                INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
                VALUES('delete', old.id, old.content, old.type, old.confidence);
                INSERT INTO memory_facts_fts(rowid, content, type, confidence)
                VALUES (new.id, new.content, new.type, new.confidence);
            END
        """)

        # Rebuild FTS index to sync with any existing data
        await self._db.execute(
            "INSERT INTO memory_facts_fts(memory_facts_fts) VALUES('rebuild')"
        )
        await self._db.commit()

    # =========================================================================
    # FILTER PRESET CRUD OPERATIONS
    # =========================================================================

    async def save_filter_preset(self, name: str, filters: Dict[str, Any]) -> str:
        """Save a new filter preset."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Check for duplicate
        cursor = await self._db.execute(
            "SELECT preset_id FROM filter_presets WHERE name = ?", (name,)
        )
        if await cursor.fetchone():
            raise ValueError(f"Preset '{name}' already exists")

        preset_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        await self._db.execute("""
            INSERT INTO filter_presets (preset_id, name, filters, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (preset_id, name, json.dumps(filters), now, now))
        await self._db.commit()

        return preset_id

    async def load_filter_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a filter preset by name, updating last_used."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT * FROM filter_presets WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # Update last_used
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "UPDATE filter_presets SET last_used = ? WHERE name = ?",
            (now, name)
        )
        await self._db.commit()

        # Parse result
        columns = ['preset_id', 'name', 'filters', 'schema_version', 'created_at', 'updated_at', 'last_used']
        result = dict(zip(columns, row))
        result["filters"] = json.loads(result["filters"])
        return result

    async def list_filter_presets(self) -> List[Dict[str, Any]]:
        """List all filter presets."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT preset_id, name, created_at, last_used FROM filter_presets ORDER BY name"
        )
        rows = await cursor.fetchall()
        columns = ['preset_id', 'name', 'created_at', 'last_used']
        return [dict(zip(columns, row)) for row in rows]

    async def delete_filter_preset(self, name: str) -> None:
        """Delete a filter preset by name."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        await self._db.execute("DELETE FROM filter_presets WHERE name = ?", (name,))
        await self._db.commit()

    async def update_filter_preset(self, name: str, filters: Dict[str, Any]) -> None:
        """Update filters for an existing preset."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        await self._db.execute("""
            UPDATE filter_presets SET filters = ?, updated_at = ? WHERE name = ?
        """, (json.dumps(filters), now, name))
        await self._db.commit()

    # =========================================================================
    # FTS INDEXING
    # =========================================================================

    def _build_searchable_text(self, raw_data: dict) -> str:
        """Extract searchable text from raw_data JSON."""
        searchable_parts = []

        # Common fields to extract
        for field in ['description', 'summary', 'tagline', 'tags', 'keywords', 'category']:
            if field in raw_data:
                value = raw_data[field]
                if isinstance(value, list):
                    searchable_parts.extend(str(v) for v in value)
                else:
                    searchable_parts.append(str(value))

        return " ".join(searchable_parts)

    async def index_signal_for_search(self, signal_id: int, vertical: str = "unknown") -> None:
        """
        Add or update a signal in the FTS index.

        Args:
            signal_id: The integer ID of the signal to index
            vertical: The vertical category for the signal (e.g., "health", "fintech")
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Fetch signal data
        cursor = await self._db.execute(
            "SELECT company_name, raw_data, source_api FROM signals WHERE id = ?",
            (signal_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return

        company_name, raw_data_json, source_api = row
        raw_data = json.loads(raw_data_json) if raw_data_json else {}
        searchable_text = self._build_searchable_text(raw_data)

        # Upsert into FTS (delete then insert)
        # FTS5 doesn't support UPDATE, so we delete and re-insert
        signal_id_str = str(signal_id)
        await self._db.execute("DELETE FROM signals_fts WHERE signal_id = ?", (signal_id_str,))
        await self._db.execute(
            "INSERT INTO signals_fts (signal_id, company_name, searchable_text, vertical, source_api) VALUES (?, ?, ?, ?, ?)",
            (signal_id_str, company_name, searchable_text, vertical, source_api)
        )
        await self._db.commit()

    async def get_fts_index_stats(self) -> Dict[str, int]:
        """Get FTS index statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Count total signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        # Count indexed signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals_fts")
        indexed_signals = (await cursor.fetchone())[0]

        return {
            "total_signals": total_signals,
            "indexed_signals": indexed_signals,
            "unindexed": total_signals - indexed_signals
        }

    async def rebuild_fts_index(self) -> int:
        """Rebuild entire FTS index from signals table. Returns count indexed."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Clear existing index
        await self._db.execute("DELETE FROM signals_fts")

        # Get all signal IDs
        cursor = await self._db.execute("SELECT id FROM signals")
        signal_ids = [row[0] for row in await cursor.fetchall()]

        await self._db.commit()

        # Index each signal
        count = 0
        for signal_id in signal_ids:
            await self.index_signal_for_search(signal_id)
            count += 1

        return count

    async def search_signals_fts(
        self,
        query: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fuzzy search signals using FTS5.

        Args:
            query: Search query (supports partial matches)
            limit: Maximum results to return

        Returns:
            List of matching signals with relevance rank
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        if not query or not query.strip():
            return []

        query = query.strip()

        # Limit query length
        if len(query) > 500:
            query = query[:500]

        # Escape special FTS5 characters
        # FTS5 special chars: " * - + ( ) : ^
        safe_query = query.replace('"', ' ').replace('*', ' ').replace('-', ' ')
        safe_query = safe_query.replace('+', ' ').replace('(', ' ').replace(')', ' ')
        safe_query = safe_query.replace(':', ' ').replace('^', ' ')
        safe_query = ' '.join(safe_query.split())  # Normalize whitespace

        if not safe_query:
            return []

        # Add * for prefix matching
        fts_query = f'{safe_query}*'

        try:
            cursor = await self._db.execute("""
                SELECT
                    f.signal_id,
                    f.company_name,
                    f.vertical,
                    f.source_api,
                    s.confidence,
                    s.signal_type,
                    s.created_at,
                    bm25(signals_fts) as rank
                FROM signals_fts f
                JOIN signals s ON f.signal_id = CAST(s.id AS TEXT)
                WHERE signals_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit))

            rows = await cursor.fetchall()
            columns = ['signal_id', 'company_name', 'vertical', 'source_api', 'confidence', 'signal_type', 'created_at', 'rank']
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.warning(f"FTS search failed for query '{query[:50]}': {e}")
            return []

    async def get_filtered_signals(
        self,
        verticals: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        signal_types: Optional[List[str]] = None,
        min_confidence: Optional[float] = None,
        max_confidence: Optional[float] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get signals matching filter criteria.

        All filters are ANDed together. Empty/None filters are ignored.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        conditions = []
        params = []

        # Filter by vertical (from FTS table)
        if verticals:
            placeholders = ",".join("?" * len(verticals))
            conditions.append(f"f.vertical IN ({placeholders})")
            params.extend(verticals)

        # Filter by source
        if sources:
            placeholders = ",".join("?" * len(sources))
            conditions.append(f"s.source_api IN ({placeholders})")
            params.extend(sources)

        # Filter by signal type
        if signal_types:
            placeholders = ",".join("?" * len(signal_types))
            conditions.append(f"s.signal_type IN ({placeholders})")
            params.extend(signal_types)

        # Filter by confidence
        if min_confidence is not None:
            conditions.append("s.confidence >= ?")
            params.append(min_confidence)

        if max_confidence is not None:
            conditions.append("s.confidence <= ?")
            params.append(max_confidence)

        # Filter by date range
        if start_date:
            conditions.append("s.created_at >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("s.created_at <= ?")
            params.append(end_date)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await self._db.execute(f"""
            SELECT
                s.id as signal_id,
                s.company_name,
                s.signal_type,
                s.source_api,
                s.confidence,
                s.created_at,
                f.vertical
            FROM signals s
            JOIN signals_fts f ON CAST(s.id AS TEXT) = f.signal_id
            WHERE {where_clause}
            ORDER BY s.created_at DESC
            LIMIT ? OFFSET ?
        """, params)

        rows = await cursor.fetchall()
        columns = ['signal_id', 'company_name', 'signal_type', 'source_api', 'confidence', 'created_at', 'vertical']
        return [dict(zip(columns, row)) for row in rows]

    # =========================================================================
    # SIGNAL OPERATIONS
    # =========================================================================

    async def save_signal(
        self,
        signal_type: str,
        source_api: str,
        canonical_key: str,
        confidence: float,
        raw_data: Dict[str, Any],
        company_name: Optional[str] = None,
        detected_at: Optional[datetime] = None,
        evidence_key: Optional[str] = None,
    ) -> int:
        """
        Save a new signal to the database.

        When evidence_key is provided (strict mode):
        - SELECT-then-INSERT: checks for existing signal with same evidence_key
        - If found, returns existing signal ID (skips all post-insert operations)
        - Uses BEGIN IMMEDIATE for write serialization

        When evidence_key is None (legacy mode):
        - Falls back to tuple-based dedup (IntegrityError on exact match)

        When identity_store is configured:
        - Resolves company_id via lookup_strong_keys / entity_id_for_seed
        - Registers strong key binding
        - Processes merge pairs via cascade_merge
        - Upserts company_file if use_thin_files is enabled

        Returns the signal ID (existing if dedup hit, new if inserted).
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        if self._use_thin_files and not self._identity_store:
            raise RuntimeError(
                "use_thin_files requires Phase G identity store. "
                "Ensure entity_aliases table exists (migration 19+)."
            )

        detected_at = detected_at or datetime.now(timezone.utc)
        created_at = datetime.now(timezone.utc)

        # Classify evidence_family + canonical_key_v2 (never fail insert)
        evidence_family = None
        canonical_key_v2_val = None
        try:
            from verification.evidence_families import get_family
            evidence_family = get_family(signal_type, source_api)
        except Exception:
            logger.warning("evidence_family classification failed", exc_info=True)
        try:
            from utils.canonical_key_v2 import build_canonical_key_v2
            canonical_key_v2_val, _, _ = build_canonical_key_v2(
                raw_data=raw_data, source_api=source_api,
                signal_type=signal_type, canonical_key=canonical_key,
            )
        except Exception:
            logger.warning("canonical_key_v2 build failed", exc_info=True)
        # Fallback: evidence_family = "unknown" if classification failed
        if evidence_family is None:
            evidence_family = "unknown"

        # Evidence-key fallback: extract from raw_data if not passed by caller
        if evidence_key is None:
            try:
                from utils.evidence_key import extract_source_url_from_raw_data, compute_evidence_key
                src_url = extract_source_url_from_raw_data(raw_data)
                if src_url:
                    evidence_key = compute_evidence_key(source_api, src_url) or None
            except Exception:
                logger.warning("evidence_key extraction failed", exc_info=True)

        # Choose transaction mode: IMMEDIATE for identity store or evidence_key dedup
        if self._identity_store or evidence_key:
            tx_cm = self.transaction_immediate()
        else:
            tx_cm = self.transaction()

        async with tx_cm as conn:
            # Resolve company_id if identity store is configured
            company_id = None
            if self._identity_store:
                # Lazy import to avoid circular dependency at module level
                from storage.entity_identity_store import (
                    EntityIdentityStore,
                    StrongKeyBinding,
                )

                existing = await self._identity_store.lookup_strong_keys(
                    [canonical_key]
                )
                if canonical_key in existing:
                    company_id = existing[canonical_key]
                else:
                    company_id = EntityIdentityStore.entity_id_for_seed(
                        canonical_key
                    )

            # Strict mode: evidence_key-based dedup guard
            if evidence_key:
                existing = await conn.execute(
                    "SELECT id FROM signals WHERE evidence_key = ? LIMIT 1",
                    (evidence_key,),
                )
                existing_row = await existing.fetchone()
                if existing_row:
                    logger.debug(
                        "evidence_key dedup: skipping duplicate %s (existing id=%d)",
                        evidence_key, existing_row[0],
                    )
                    return int(existing_row[0])

            # Insert signal (with company_id if resolved)
            cursor = await conn.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at, company_id,
                    evidence_family, canonical_key_v2, evidence_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_type,
                    source_api,
                    canonical_key,
                    company_name,
                    confidence,
                    json.dumps(raw_data),
                    detected_at.isoformat(),
                    created_at.isoformat(),
                    company_id,
                    evidence_family,
                    canonical_key_v2_val,
                    evidence_key,
                ),
            )

            signal_id = cursor.lastrowid

            # Create pending processing record
            await conn.execute(
                """
                INSERT INTO signal_processing (
                    signal_id, status, created_at, updated_at
                )
                VALUES (?, 'pending', ?, ?)
                """,
                (signal_id, created_at.isoformat(), created_at.isoformat()),
            )

            # Register strong key binding + handle merges
            if self._identity_store and company_id:
                binding = StrongKeyBinding(
                    strong_key=canonical_key,
                    entity_id=company_id,
                    source_signal_id=signal_id,
                    source_key=source_api,
                )
                merge_pairs = await self._identity_store.upsert_strong_key_bindings(
                    [binding], conn
                )

                # Process merge pairs via cascade_merge
                if merge_pairs:
                    from storage.merge_cascade import cascade_merge

                    for loser, winner in merge_pairs:
                        await cascade_merge(
                            store=self,
                            winner_company_id=winner,
                            loser_company_id=loser,
                            reason="identity_merge",
                            actor="pipeline",
                            tx=conn,
                        )
                        # Update our company_id if it was the loser
                        if company_id == loser:
                            company_id = winner
                            await conn.execute(
                                "UPDATE signals SET company_id = ? WHERE id = ?",
                                (winner, signal_id),
                            )

            # Upsert company file within same transaction
            if self._use_thin_files and company_id:
                from workflows.thin_file_manager import upsert_company_file

                await upsert_company_file(
                    store=self,
                    company_id=company_id,
                    company_name=company_name,
                    canonical_key=canonical_key,
                    source_api=source_api,
                    tx=conn,
                )

        logger.debug(f"Saved signal {signal_id}: {signal_type} for {canonical_key}")
        return signal_id

    async def get_signal(self, signal_id: int) -> Optional[StoredSignal]:
        """Get a signal by ID."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at, s.company_id,
                p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.id = ?
            """,
            (signal_id,)
        )

        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_signal(row)

    async def get_pending_signals(
        self,
        limit: Optional[int] = None,
        signal_type: Optional[str] = None,
    ) -> List[StoredSignal]:
        """
        Get signals that haven't been processed yet.

        Args:
            limit: Maximum number of signals to return
            signal_type: Filter by signal type (e.g., "github_spike")
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at, s.company_id,
                p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            INNER JOIN signal_processing p ON s.id = p.signal_id
            WHERE p.status = 'pending'
        """

        params: List[Any] = []

        if signal_type:
            query += " AND s.signal_type = ?"
            params.append(signal_type)

        query += " ORDER BY s.detected_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [self._row_to_signal(row) for row in rows]

    async def get_signals_for_company(
        self,
        canonical_key: str,
    ) -> List[StoredSignal]:
        """Get all signals for a company (by canonical key)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.id, s.signal_type, s.source_api, s.canonical_key,
                s.company_name, s.confidence, s.raw_data,
                s.detected_at, s.created_at, s.company_id,
                p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.canonical_key = ?
            ORDER BY s.detected_at DESC
            """,
            (canonical_key,)
        )

        rows = await cursor.fetchall()
        return [self._row_to_signal(row) for row in rows]

    async def get_signals_for_company_by_name(self, company_name: str) -> List[Dict[str, Any]]:
        """Get all signals for a specific company by name."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute("""
            SELECT
                s.id as signal_id,
                s.company_name,
                s.signal_type,
                s.source_api,
                s.confidence,
                s.created_at,
                s.raw_data,
                f.vertical
            FROM signals s
            LEFT JOIN signals_fts f ON CAST(s.id AS TEXT) = f.signal_id
            WHERE s.company_name = ?
            ORDER BY s.created_at DESC
        """, (company_name,))

        rows = await cursor.fetchall()
        columns = ['signal_id', 'company_name', 'signal_type', 'source_api', 'confidence', 'created_at', 'raw_data', 'vertical']
        return [dict(zip(columns, row)) for row in rows]

    async def is_duplicate(
        self,
        canonical_key: str,
        signal_type: Optional[str] = None,
        source_api: Optional[str] = None,
        detected_at: Optional[datetime] = None,
        evidence_key: Optional[str] = None,
    ) -> bool:
        """Check for duplicate signals.

        Contract:
        - Multi-source convergence is allowed: same canonical_key across
          different (signal_type, source_api) is NOT a duplicate.
        - "Duplicate" means same evidence identity, not "same company."

        When evidence_key is provided, checks by evidence_key first
        (fast path, uses partial index).

        When signal_type, source_api, and detected_at are all provided,
        checks for an exact-tuple match — allowing different sources to
        contribute signals for the same company.

        When only canonical_key is provided (legacy), falls back to
        blanket canonical-key check.

        Returns True if matching signals exist, False otherwise.

        NOTE: This check runs outside the save_signal() transaction,
        so a concurrent insert between is_duplicate() and save_signal()
        could cause a false negative. Data correctness is preserved by
        the SELECT-then-INSERT guard inside save_signal()'s transaction.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Fast path: evidence_key-based dedup
        if evidence_key:
            cursor = await self._db.execute(
                "SELECT 1 FROM signals WHERE evidence_key = ? LIMIT 1",
                (evidence_key,),
            )
            if await cursor.fetchone():
                return True

        if signal_type is not None and source_api is not None and detected_at is not None:
            # Exact-tuple check: same source + type + time = true duplicate
            detected_str = detected_at.isoformat() if isinstance(detected_at, datetime) else str(detected_at)
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM signals WHERE canonical_key = ? AND signal_type = ? AND source_api = ? AND detected_at = ?",
                (canonical_key, signal_type, source_api, detected_str),
            )
        else:
            # Legacy blanket check (used by suppression-cache callers, etc.)
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM signals WHERE canonical_key = ?",
                (canonical_key,),
            )
        row = await cursor.fetchone()
        return row[0] > 0 if row else False

    # =========================================================================
    # PROCESSING STATE
    # =========================================================================

    async def mark_pushed(
        self,
        signal_id: int,
        notion_page_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as successfully pushed to Notion."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'pushed',
                    notion_page_id = ?,
                    processed_at = ?,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    notion_page_id,
                    now,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as pushed (Notion: {notion_page_id})")

    async def mark_rejected(
        self,
        signal_id: int,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as rejected (won't be pushed)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'rejected',
                    processed_at = ?,
                    error_message = ?,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    now,
                    reason,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as rejected: {reason}")

    async def mark_held(
        self,
        signal_id: int,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as held (blocked by configuration, not a content rejection)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'held',
                    processed_at = ?,
                    error_message = ?,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    now,
                    reason,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as held: {reason}")

    async def mark_queued(
        self,
        signal_id: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a signal as queued for Notion write."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE signal_processing
                SET status = 'queued',
                    notion_page_id = NULL,
                    processed_at = ?,
                    error_message = NULL,
                    metadata = ?,
                    updated_at = ?
                WHERE signal_id = ?
                """,
                (
                    now,
                    json.dumps(metadata) if metadata else None,
                    now,
                    signal_id,
                )
            )

        logger.info(f"Marked signal {signal_id} as queued for Notion")

    async def get_processing_stats(self) -> Dict[str, int]:
        """Get counts by processing status."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*)
            FROM signal_processing
            GROUP BY status
            """
        )

        rows = await cursor.fetchall()
        return {status: count for status, count in rows}

    async def update_signal_status(
        self,
        canonical_key: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        Update processing status for signals matching canonical key.

        Args:
            canonical_key: The canonical key to match
            status: New status ('pending', 'qualified', 'held', 'rejected', 'pushed')
            error_message: Optional error/reason message

        Returns:
            True if any signals were updated
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE signal_processing
                SET status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE signal_id IN (
                    SELECT id FROM signals WHERE canonical_key = ?
                )
                """,
                (status, error_message, now, canonical_key),
            )
            return cursor.rowcount > 0

    async def get_signals_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
    ) -> List[StoredSignal]:
        """
        Get signals with a specific processing status.

        Args:
            status: Status to filter by
            limit: Maximum number to return

        Returns:
            List of StoredSignal objects
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT s.id, s.signal_type, s.source_api, s.canonical_key,
                   s.company_name, s.confidence, s.raw_data,
                   s.detected_at, s.created_at, s.company_id,
                   p.status, p.notion_page_id, p.processed_at, p.error_message
            FROM signals s
            JOIN signal_processing p ON s.id = p.signal_id
            WHERE p.status = ?
            ORDER BY s.created_at DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor = await self._db.execute(query, (status,))
        rows = await cursor.fetchall()

        return [self._row_to_signal(row) for row in rows]

    async def get_status_counts(self) -> Dict[str, int]:
        """
        Get counts of signals by processing status.

        Returns:
            Dict mapping status to count, e.g. {"pending": 5, "qualified": 10}
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*) as count
            FROM signal_processing
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_signals_for_analytics(
        self,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Fetch signals with thesis and processing data for analytics dashboard.

        Uses a 4-way LEFT JOIN to combine:
        - signals: Core signal data
        - thesis_classifications: Thesis fit and category
        - signals_fts: Vertical information
        - signal_processing: Processing status

        Args:
            days: Number of days to look back (default 30). Use large value for all time.

        Returns:
            List of dicts with: signal_id, company_name, canonical_key, source_api,
            confidence, detected_at, vertical, category, thesis_fit_score,
            competitor_flag, processing_status
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.id as signal_id,
                s.company_name,
                s.canonical_key,
                s.source_api,
                s.confidence,
                s.detected_at,
                COALESCE(fts.vertical, 'Unknown') as vertical,
                COALESCE(tc.category, 'Unclassified') as category,
                COALESCE(tc.thesis_fit_score, 0) as thesis_fit_score,
                COALESCE(tc.competitor_flag, 0) as competitor_flag,
                COALESCE(sp.status, 'pending') as processing_status
            FROM signals s
            LEFT JOIN thesis_classifications tc ON s.id = tc.signal_id
            LEFT JOIN signals_fts fts ON CAST(s.id AS TEXT) = fts.signal_id
            LEFT JOIN signal_processing sp ON s.id = sp.signal_id
            WHERE s.detected_at >= datetime('now', '-' || ? || ' days')
            ORDER BY s.detected_at DESC
            LIMIT 5000
            """,
            (days,)
        )

        rows = await cursor.fetchall()
        columns = [
            "signal_id", "company_name", "canonical_key", "source_api",
            "confidence", "detected_at", "vertical", "category",
            "thesis_fit_score", "competitor_flag", "processing_status"
        ]

        return [dict(zip(columns, row)) for row in rows]

    # =========================================================================
    # SHADOW LOGGING
    # =========================================================================

    async def log_shadow_computation(
        self,
        feature_name: str,
        canonical_key: str,
        computed_value: Dict[str, Any],
        signal_id: Optional[int] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Log a SHADOW feature computation for later analysis.

        SHADOW features are computed but have 0 weight - they don't affect
        routing/scoring. Logging allows measuring correlation with outcomes
        before promoting to ACTIVE.

        Args:
            feature_name: Feature identifier (e.g., 'boilerplate_defense')
            canonical_key: Company/entity identifier
            computed_value: Computation result (will be JSON serialized)
            signal_id: Optional FK to link to a specific signal
            metrics: Optional per-computation metrics dict with keys:
                latency_ms, upstream_data_version, missingness_reason,
                api_calls_made, error

        Returns:
            The shadow_log ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO shadow_log (
                    feature_name, canonical_key, computed_value, signal_id, logged_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    feature_name,
                    canonical_key,
                    json.dumps(computed_value),
                    signal_id,
                    now,
                )
            )
            log_id = cursor.lastrowid

            if metrics:
                await conn.execute(
                    """
                    INSERT INTO shadow_log_metrics (
                        shadow_log_id, latency_ms, upstream_data_version,
                        missingness_reason, api_calls_made, error
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        log_id,
                        metrics.get("latency_ms"),
                        metrics.get("upstream_data_version"),
                        metrics.get("missingness_reason"),
                        metrics.get("api_calls_made", 0),
                        metrics.get("error"),
                    ),
                )

        logger.debug(f"Logged SHADOW computation: {feature_name} for {canonical_key}")
        return log_id

    async def get_shadow_logs(
        self,
        feature_name: Optional[str] = None,
        canonical_key: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve shadow computation logs with optional filters.

        Args:
            feature_name: Filter by feature
            canonical_key: Filter by company/entity
            since: Filter by timestamp (logs after this time)
            limit: Max results (default 100)

        Returns:
            List of log entries as dicts
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT id, feature_name, canonical_key, computed_value, signal_id, logged_at
            FROM shadow_log
            WHERE 1=1
        """
        params: List[Any] = []

        if feature_name:
            query += " AND feature_name = ?"
            params.append(feature_name)

        if canonical_key:
            query += " AND canonical_key = ?"
            params.append(canonical_key)

        if since:
            query += " AND logged_at > ?"
            params.append(since.isoformat())

        query += " ORDER BY logged_at DESC LIMIT ?"
        params.append(limit)

        async with self.transaction() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "feature_name": row[1],
                "canonical_key": row[2],
                "computed_value": json.loads(row[3]) if row[3] else {},
                "signal_id": row[4],
                "logged_at": datetime.fromisoformat(row[5]) if row[5] else None,
            })

        return results

    async def count_shadow_logs(
        self,
        feature_name: Optional[str] = None,
    ) -> int:
        """
        Count shadow computation logs.

        Args:
            feature_name: Optional filter by feature

        Returns:
            Count of logs
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = "SELECT COUNT(*) FROM shadow_log"
        params: List[Any] = []

        if feature_name:
            query += " WHERE feature_name = ?"
            params.append(feature_name)

        async with self.transaction() as conn:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()

        return row[0] if row else 0

    async def get_shadow_correlation_report(
        self,
        feature_name: str,
        outcome_field: str = "processing_status",
        days: int = 7,
    ) -> Dict[str, Any]:
        """
        Generate correlation report for a shadow feature.

        Analyzes how shadow feature values correlate with signal outcomes
        to help decide if feature should be promoted to ACTIVE.

        Args:
            feature_name: Feature to analyze
            outcome_field: Field to use for outcomes (default: processing_status)
            days: Number of days to analyze (default: 7)

        Returns:
            Report dict with:
            - feature_name: The feature analyzed
            - total_logs: Count of logs in period
            - period_start/end: Time range analyzed
            - value_distribution: Counts of different computed values
            - outcome_distribution: Counts of different outcomes
            - outcome_by_value: Cross-tabulation of values vs outcomes
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=days)

        report: Dict[str, Any] = {
            "feature_name": feature_name,
            "total_logs": 0,
            "period_start": period_start.isoformat(),
            "period_end": now.isoformat(),
            "value_distribution": {},
            "outcome_distribution": {},
            "outcome_by_value": {},
        }

        # Get shadow logs for the period
        logs = await self.get_shadow_logs(
            feature_name=feature_name,
            since=period_start,
            limit=10000,  # Reasonable limit for analysis
        )

        report["total_logs"] = len(logs)

        if not logs:
            return report

        # Analyze value distribution
        match_true = 0
        match_false = 0
        for log in logs:
            val = log.get("computed_value", {})
            if isinstance(val, dict) and "match" in val:
                if val["match"]:
                    match_true += 1
                else:
                    match_false += 1

        if match_true > 0 or match_false > 0:
            report["value_distribution"] = {
                "match_true": match_true,
                "match_false": match_false,
            }

        # Get outcome distribution for linked signals
        signal_ids = [log["signal_id"] for log in logs if log.get("signal_id")]
        if signal_ids:
            async with self.transaction() as conn:
                placeholders = ",".join("?" * len(signal_ids))
                cursor = await conn.execute(
                    f"""
                    SELECT signal_id, status
                    FROM signal_processing
                    WHERE signal_id IN ({placeholders})
                    """,
                    signal_ids
                )
                outcomes = {row[0]: row[1] for row in await cursor.fetchall()}

            # Count outcome distribution
            outcome_counts: Dict[str, int] = {}
            for status in outcomes.values():
                outcome_counts[status] = outcome_counts.get(status, 0) + 1
            report["outcome_distribution"] = outcome_counts

            # Cross-tabulate: for each value type, what outcomes do we see?
            outcome_by_value: Dict[str, Dict[str, int]] = {}
            for log in logs:
                signal_id = log.get("signal_id")
                if signal_id and signal_id in outcomes:
                    val = log.get("computed_value", {})
                    if isinstance(val, dict) and "match" in val:
                        key = "match_true" if val["match"] else "match_false"
                        if key not in outcome_by_value:
                            outcome_by_value[key] = {}
                        outcome = outcomes[signal_id]
                        outcome_by_value[key][outcome] = outcome_by_value[key].get(outcome, 0) + 1
            report["outcome_by_value"] = outcome_by_value

        return report

    async def mark_processing_status(
        self,
        signal_id: int,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Mark a signal's processing status.

        Creates or updates the signal_processing record.

        Args:
            signal_id: The signal ID
            status: Processing status (pending, pushed, rejected, etc.)
            metadata: Optional extra context
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            # Check if record exists
            cursor = await conn.execute(
                "SELECT id FROM signal_processing WHERE signal_id = ?",
                (signal_id,)
            )
            existing = await cursor.fetchone()

            if existing:
                # Update existing
                await conn.execute(
                    """
                    UPDATE signal_processing
                    SET status = ?, updated_at = ?, metadata = COALESCE(?, metadata)
                    WHERE signal_id = ?
                    """,
                    (status, now, json.dumps(metadata) if metadata else None, signal_id)
                )
            else:
                # Insert new
                await conn.execute(
                    """
                    INSERT INTO signal_processing (signal_id, status, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (signal_id, status, now, now, json.dumps(metadata) if metadata else None)
                )

    # =========================================================================
    # NOTION OUTBOX
    # =========================================================================

    async def enqueue_notion_write(
        self,
        idempotency_key: str,
        payload: Dict[str, Any],
        event_type: str = "notion_push",
    ) -> int:
        """
        Queue an event in the outbox table.

        Args:
            idempotency_key: Unique key to prevent duplicate processing
            payload: Event payload (will be JSON serialized)
            event_type: Event type for routing (default: notion_push)

        Returns:
            The outbox ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO notion_outbox (
                    idempotency_key, payload_json, status, event_type,
                    attempts, created_at, updated_at
                )
                VALUES (?, ?, 'pending', ?, 0, ?, ?)
                """,
                (
                    idempotency_key,
                    json.dumps(payload),
                    event_type,
                    now,
                    now,
                )
            )

            cursor = await conn.execute(
                "SELECT id FROM notion_outbox WHERE idempotency_key = ?",
                (idempotency_key,)
            )
            row = await cursor.fetchone()
            outbox_id = row[0]

        logger.info(f"Enqueued {event_type}: {outbox_id} ({idempotency_key})")
        return outbox_id

    async def get_pending_outbox(
        self,
        limit: int = 50,
        max_attempts: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Get pending outbox entries that are due for processing.

        Respects:
        - next_attempt_at: Only returns items where next_attempt_at <= now (or NULL)
        - max_attempts: Excludes items that have exceeded retry limit
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            SELECT id, idempotency_key, payload_json, status, attempts,
                   next_attempt_at, last_error, created_at, updated_at
            FROM notion_outbox
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
              AND attempts < ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (now, max_attempts, limit)
        )

        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "idempotency_key": row[1],
                "payload": json.loads(row[2]),
                "status": row[3],
                "attempts": row[4],
                "next_attempt_at": row[5],
                "last_error": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            }
            for row in rows
        ]

    async def mark_outbox_sent(
        self,
        outbox_id: int,
    ) -> None:
        """Mark an outbox entry as sent."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE notion_outbox
                SET status = 'sent',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, outbox_id)
            )

        logger.info(f"Marked outbox {outbox_id} as sent")

    async def mark_outbox_failed(
        self,
        outbox_id: int,
        error: str,
        backoff_seconds: float = 60.0,
    ) -> None:
        """
        Mark an outbox entry as failed with retry scheduling.

        Keeps status='pending' so item will be retried after backoff.
        Increments attempts counter for retry limiting.

        Args:
            outbox_id: The outbox entry ID
            error: Error message to store
            backoff_seconds: Seconds to wait before next retry attempt
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        next_attempt = (now + timedelta(seconds=backoff_seconds)).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                UPDATE notion_outbox
                SET status = 'pending',
                    last_error = ?,
                    attempts = attempts + 1,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error, next_attempt, now.isoformat(), outbox_id)
            )

        logger.info(f"Outbox {outbox_id} failed, retry after {backoff_seconds}s: {error}")

    async def claim_due_outbox(
        self,
        event_type: str = "notion_push",
        limit: int = 10,
        stale_processing_ttl_minutes: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Atomically claim due outbox entries for processing.

        Uses BEGIN IMMEDIATE to prevent concurrent claims. Sets status='processing'
        in a short transaction, then returns claimed rows for processing outside
        the transaction.

        This is the v2.4 pattern for exactly-once processing:
        1. claim_due_outbox() - atomic claim (short txn)
        2. Process work outside transaction
        3. mark_outbox_sent() or mark_outbox_failed() - finalize

        Args:
            event_type: Event type to claim (filters by event_type column)
            limit: Maximum entries to claim
            stale_processing_ttl_minutes: Reclaim 'processing' entries older than this

        Returns:
            List of claimed outbox entries (already set to 'processing')
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        stale_threshold = (now - timedelta(minutes=stale_processing_ttl_minutes)).isoformat()

        claimed = []

        # Use BEGIN IMMEDIATE for write lock
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            # First, reclaim stale 'processing' entries (TTL expired)
            await self._db.execute(
                """
                UPDATE notion_outbox
                SET status = 'pending',
                    updated_at = ?
                WHERE status = 'processing'
                  AND event_type = ?
                  AND updated_at < ?
                """,
                (now_iso, event_type, stale_threshold)
            )

            # Select entries to claim
            cursor = await self._db.execute(
                """
                SELECT id, idempotency_key, payload_json, attempts,
                       next_attempt_at, last_error, created_at, max_attempts
                FROM notion_outbox
                WHERE status = 'pending'
                  AND event_type = ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND attempts < max_attempts
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (event_type, now_iso, limit)
            )
            rows = await cursor.fetchall()

            if rows:
                # Claim them by setting status='processing'
                ids = [row[0] for row in rows]
                placeholders = ",".join("?" * len(ids))
                await self._db.execute(
                    f"""
                    UPDATE notion_outbox
                    SET status = 'processing',
                        updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now_iso, *ids)
                )

                # Build result list
                for row in rows:
                    claimed.append({
                        "id": row[0],
                        "idempotency_key": row[1],
                        "payload": json.loads(row[2]),
                        "attempts": row[3],
                        "next_attempt_at": row[4],
                        "last_error": row[5],
                        "created_at": row[6],
                        "max_attempts": row[7],
                        "event_type": event_type,
                    })

            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

        if claimed:
            logger.info(f"Claimed {len(claimed)} {event_type} entries for processing")
        return claimed

    async def finalize_outbox(
        self,
        outbox_id: int,
        success: bool,
        error: Optional[str] = None,
        backoff_seconds: float = 60.0,
    ) -> None:
        """
        Finalize an outbox entry after processing.

        For success: marks as 'sent'
        For failure: increments attempts, schedules retry (keeps as 'pending'),
                     or marks as 'failed' if max_attempts reached.

        Args:
            outbox_id: The outbox entry ID
            success: Whether processing succeeded
            error: Error message (for failures)
            backoff_seconds: Seconds to wait before retry (for failures)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)

        if success:
            await self._db.execute(
                """
                UPDATE notion_outbox
                SET status = 'sent',
                    updated_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), outbox_id)
            )
            await self._db.commit()
            logger.info(f"Outbox {outbox_id} finalized as sent")
        else:
            next_attempt = (now + timedelta(seconds=backoff_seconds)).isoformat()

            # Check if max_attempts reached
            cursor = await self._db.execute(
                "SELECT attempts, max_attempts FROM notion_outbox WHERE id = ?",
                (outbox_id,)
            )
            row = await cursor.fetchone()
            if row:
                attempts, max_attempts = row[0], row[1]
                new_attempts = attempts + 1

                if new_attempts >= max_attempts:
                    # Max attempts reached - mark as permanently failed
                    await self._db.execute(
                        """
                        UPDATE notion_outbox
                        SET status = 'failed',
                            last_error = ?,
                            attempts = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (error, new_attempts, now.isoformat(), outbox_id)
                    )
                    logger.warning(f"Outbox {outbox_id} permanently failed after {new_attempts} attempts: {error}")
                else:
                    # Schedule retry
                    await self._db.execute(
                        """
                        UPDATE notion_outbox
                        SET status = 'pending',
                            last_error = ?,
                            attempts = ?,
                            next_attempt_at = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (error, new_attempts, next_attempt, now.isoformat(), outbox_id)
                    )
                    logger.info(f"Outbox {outbox_id} failed (attempt {new_attempts}), retry after {backoff_seconds}s")

            await self._db.commit()

    # =========================================================================
    # SUPPRESSION CACHE
    # =========================================================================

    async def update_suppression_cache(
        self,
        entries: List[SuppressionEntry],
    ) -> int:
        """
        Bulk update suppression cache from Notion sync.
        Returns number of entries updated.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        count = 0

        async with self.transaction() as conn:
            for entry in entries:
                await conn.execute(
                    """
                    INSERT INTO suppression_cache (
                        canonical_key, notion_page_id, status, company_name,
                        cached_at, expires_at, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_key) DO UPDATE SET
                        notion_page_id = excluded.notion_page_id,
                        status = excluded.status,
                        company_name = excluded.company_name,
                        cached_at = excluded.cached_at,
                        expires_at = excluded.expires_at,
                        metadata = excluded.metadata
                    """,
                    (
                        entry.canonical_key,
                        entry.notion_page_id,
                        entry.status,
                        entry.company_name,
                        entry.cached_at.isoformat(),
                        entry.expires_at.isoformat(),
                        json.dumps(entry.metadata) if entry.metadata else None,
                    )
                )
                count += 1

        logger.info(f"Updated {count} suppression cache entries")
        return count

    async def check_suppression(
        self,
        canonical_key: str,
    ) -> Optional[SuppressionEntry]:
        """
        Check if a canonical key is in the suppression cache.
        Returns None if not found or expired.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            SELECT
                canonical_key, notion_page_id, status, company_name,
                cached_at, expires_at, metadata
            FROM suppression_cache
            WHERE canonical_key = ? AND expires_at > ?
            """,
            (canonical_key, now)
        )

        row = await cursor.fetchone()
        if not row:
            return None

        return SuppressionEntry(
            canonical_key=row[0],
            notion_page_id=row[1],
            status=row[2],
            company_name=row[3],
            cached_at=datetime.fromisoformat(row[4]),
            expires_at=datetime.fromisoformat(row[5]),
            metadata=json.loads(row[6]) if row[6] else None,
        )

    async def clean_expired_cache(self) -> int:
        """
        Remove expired entries from suppression cache.
        Returns number of entries removed.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM suppression_cache WHERE expires_at <= ?",
                (now,)
            )
            count = cursor.rowcount

        if count > 0:
            logger.info(f"Cleaned {count} expired suppression cache entries")

        return count

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def _row_to_signal(self, row: tuple) -> StoredSignal:
        """Convert database row to StoredSignal object."""
        # Helper to ensure timezone-aware datetimes
        def parse_datetime(dt_str: str) -> datetime:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return StoredSignal(
            id=row[0],
            signal_type=row[1],
            source_api=row[2],
            canonical_key=row[3],
            company_name=row[4],
            confidence=row[5],
            raw_data=json.loads(row[6]),
            detected_at=parse_datetime(row[7]),
            created_at=parse_datetime(row[8]),
            company_id=row[9] if len(row) > 9 else None,
            processing_status=row[10] if len(row) > 10 else None,
            notion_page_id=row[11] if len(row) > 11 else None,
            processed_at=parse_datetime(row[12]) if len(row) > 12 and row[12] else None,
            error_message=row[13] if len(row) > 13 else None,
        )

    async def get_stats(self) -> Dict[str, Any]:
        """Get overall database statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Signal counts by type
        cursor = await self._db.execute(
            """
            SELECT signal_type, COUNT(*)
            FROM signals
            GROUP BY signal_type
            """
        )
        signal_counts = dict(await cursor.fetchall())

        # Processing stats
        processing_stats = await self.get_processing_stats()

        # Suppression cache stats
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM suppression_cache WHERE expires_at > ?",
            (datetime.now(timezone.utc).isoformat(),)
        )
        active_cache_entries = (await cursor.fetchone())[0]

        # Total signals
        cursor = await self._db.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        return {
            "total_signals": total_signals,
            "signals_by_type": signal_counts,
            "processing_status": processing_stats,
            "active_suppression_entries": active_cache_entries,
            "database_path": str(self.db_path),
        }

    # =========================================================================
    # PIPELINE METRICS
    # =========================================================================

    async def save_pipeline_run(self, stats: PipelineStats) -> str:
        """
        Save pipeline run metrics to database.

        Args:
            stats: PipelineStats object from a pipeline run

        Returns:
            run_id: UUID string for this pipeline run
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Serialize errors list to JSON
        errors_json = json.dumps(stats.errors) if stats.errors else None

        # Serialize health_report to JSON if present
        health_json = None
        if stats.health_report:
            health_json = json.dumps(stats.health_report.to_dict())

        # Serialize identity/sweep stats as JSON
        identity_stats = {
            "sweep_promoted": getattr(stats, "sweep_promoted", 0),
            "sweep_evaluated": getattr(stats, "sweep_evaluated", 0),
            "sweep_pages": getattr(stats, "sweep_pages", 0),
            "sweep_error": getattr(stats, "sweep_error", None),
        }
        identity_json = json.dumps(identity_stats)

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, started_at, completed_at, duration_seconds,
                    collectors_run, collectors_succeeded, collectors_failed, signals_collected,
                    signals_stored, signals_deduplicated,
                    signals_processed, signals_auto_push, signals_needs_review,
                    signals_held, signals_rejected,
                    prospects_created, prospects_updated, prospects_skipped,
                    errors, health_report, identity_stats, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stats.started_at.isoformat(),
                    stats.completed_at.isoformat() if stats.completed_at else None,
                    stats.duration_seconds,
                    stats.collectors_run,
                    stats.collectors_succeeded,
                    stats.collectors_failed,
                    stats.signals_collected,
                    stats.signals_stored,
                    stats.signals_deduplicated,
                    stats.signals_processed,
                    stats.signals_auto_push,
                    stats.signals_needs_review,
                    stats.signals_held,
                    stats.signals_rejected,
                    stats.prospects_created,
                    stats.prospects_updated,
                    stats.prospects_skipped,
                    errors_json,
                    health_json,
                    identity_json,
                    now,
                )
            )

        logger.info(f"Saved pipeline run {run_id} (duration: {stats.duration_seconds}s)")
        return run_id

    async def get_pipeline_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent pipeline runs in reverse chronological order.

        Args:
            limit: Maximum number of runs to return (default 10)

        Returns:
            List of pipeline run dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                run_id, started_at, completed_at, duration_seconds,
                collectors_run, collectors_succeeded, collectors_failed, signals_collected,
                signals_stored, signals_deduplicated,
                signals_processed, signals_auto_push, signals_needs_review,
                signals_held, signals_rejected,
                prospects_created, prospects_updated, prospects_skipped,
                errors, health_report
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        )

        rows = await cursor.fetchall()
        return [self._row_to_pipeline_run(row) for row in rows]

    async def get_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific pipeline run by ID.

        Args:
            run_id: UUID of the pipeline run

        Returns:
            Pipeline run dictionary or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                run_id, started_at, completed_at, duration_seconds,
                collectors_run, collectors_succeeded, collectors_failed, signals_collected,
                signals_stored, signals_deduplicated,
                signals_processed, signals_auto_push, signals_needs_review,
                signals_held, signals_rejected,
                prospects_created, prospects_updated, prospects_skipped,
                errors, health_report
            FROM pipeline_runs
            WHERE run_id = ?
            """,
            (run_id,)
        )

        row = await cursor.fetchone()
        if not row:
            return None

        return self._row_to_pipeline_run(row)

    def _row_to_pipeline_run(self, row: tuple) -> Dict[str, Any]:
        """Convert database row to pipeline run dictionary."""
        return {
            "run_id": row[0],
            "started_at": row[1],
            "completed_at": row[2],
            "duration_seconds": row[3],
            "collectors_run": row[4],
            "collectors_succeeded": row[5],
            "collectors_failed": row[6],
            "signals_collected": row[7],
            "signals_stored": row[8],
            "signals_deduplicated": row[9],
            "signals_processed": row[10],
            "signals_auto_push": row[11],
            "signals_needs_review": row[12],
            "signals_held": row[13],
            "signals_rejected": row[14],
            "prospects_created": row[15],
            "prospects_updated": row[16],
            "prospects_skipped": row[17],
            "errors": json.loads(row[18]) if row[18] else [],
            "health_report": json.loads(row[19]) if row[19] else None,
        }

    async def save_collector_metrics(self, run_id: str, metrics: "CollectorMetrics") -> None:
        """
        Save collector metrics for a pipeline run.

        Args:
            run_id: Pipeline run ID to associate with
            metrics: CollectorMetrics object with timing and API stats
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        error_messages_json = json.dumps(metrics.error_messages) if metrics.error_messages else None

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO collector_metrics (
                    run_id, collector_name, started_at, completed_at, duration_seconds,
                    signals_found, status, api_calls, rate_limit_hits, retries,
                    errors, error_messages, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metrics.collector_name,
                    metrics.started_at.isoformat(),
                    metrics.completed_at.isoformat() if metrics.completed_at else None,
                    metrics.duration_seconds,
                    metrics.signals_found,
                    metrics.status,
                    metrics.api_calls,
                    metrics.rate_limit_hits,
                    metrics.retries,
                    metrics.errors,
                    error_messages_json,
                    now,
                )
            )

        logger.debug(f"Saved metrics for collector {metrics.collector_name} (run: {run_id})")

    async def get_collector_metrics(
        self,
        run_id: Optional[str] = None,
        collector_name: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query collector metrics with optional filters.

        Args:
            run_id: Filter to specific pipeline run
            collector_name: Filter to specific collector
            limit: Maximum results (default 100)

        Returns:
            List of collector metrics dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                run_id, collector_name, started_at, completed_at, duration_seconds,
                signals_found, status, api_calls, rate_limit_hits, retries,
                errors, error_messages
            FROM collector_metrics
            WHERE 1=1
        """
        params = []

        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if collector_name:
            query += " AND collector_name = ?"
            params.append(collector_name)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "run_id": row[0],
                "collector_name": row[1],
                "started_at": row[2],
                "completed_at": row[3],
                "duration_seconds": row[4],
                "signals_found": row[5],
                "status": row[6],
                "api_calls": row[7],
                "rate_limit_hits": row[8],
                "retries": row[9],
                "errors": row[10],
                "error_messages": json.loads(row[11]) if row[11] else [],
            }
            for row in rows
        ]

    # =========================================================================
    # THESIS CLASSIFICATION STORAGE
    # =========================================================================

    async def save_thesis_classification(
        self,
        signal_id: int,
        canonical_key: str,
        keyword_score: Optional[float] = None,
        keyword_category: Optional[str] = None,
        negative_keywords: Optional[List[str]] = None,
        thesis_match: Optional[bool] = None,
        thesis_fit_score: Optional[float] = None,
        category: Optional[str] = None,
        stage_estimate: Optional[str] = None,
        confidence: Optional[str] = None,
        rationale: Optional[str] = None,
        key_signals: Optional[List[str]] = None,
        prompt_version: Optional[str] = None,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        competitor_flag: bool = False,
        competitor_match: Optional[Dict] = None,
        cot_enabled: bool = False,
        reasoning_trace: Optional[Dict] = None,
    ) -> int:
        """
        Save a thesis classification result.

        Args:
            signal_id: ID of the signal being classified
            canonical_key: Canonical key for the company
            keyword_score: Score from keyword matcher (stage 1)
            keyword_category: Category from keyword matcher
            negative_keywords: List of negative keywords found
            thesis_match: Whether LLM determined thesis match
            thesis_fit_score: Fit score from LLM (0-1)
            category: Thesis category from LLM
            stage_estimate: Estimated funding stage
            confidence: LLM confidence level
            rationale: LLM's explanation for the classification
            key_signals: Key signals identified by LLM
            prompt_version: Version of the prompt used
            model: LLM model name
            input_tokens: Input token count
            output_tokens: Output token count
            latency_ms: API call latency in milliseconds
            competitor_flag: Whether a competitor was detected
            competitor_match: Details of matched portfolio company
            cot_enabled: Whether chain-of-thought reasoning was used
            reasoning_trace: Chain-of-thought reasoning trace (JSON)

        Returns:
            The inserted row ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        # Compute disagreement flag (Phase 9 Quality Ops)
        # Disagreement = (keyword says yes, LLM says no) OR (keyword says no, LLM says yes)
        disagreement_detected = 0
        if keyword_score is not None and thesis_fit_score is not None:
            if (keyword_score >= 0.7 and thesis_fit_score < 0.4) or \
               (keyword_score < 0.4 and thesis_fit_score >= 0.7):
                disagreement_detected = 1

        async with self.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO thesis_classifications (
                    signal_id, canonical_key,
                    keyword_score, keyword_category, negative_keywords,
                    thesis_match, thesis_fit_score, category,
                    stage_estimate, confidence, rationale, key_signals,
                    prompt_version, model, input_tokens, output_tokens, latency_ms,
                    competitor_flag, competitor_match,
                    cot_enabled, reasoning_trace,
                    disagreement_detected,
                    classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    canonical_key,
                    keyword_score,
                    keyword_category,
                    json.dumps(negative_keywords) if negative_keywords else None,
                    thesis_match,
                    thesis_fit_score,
                    category,
                    stage_estimate,
                    confidence,
                    rationale,
                    json.dumps(key_signals) if key_signals else None,
                    prompt_version,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    competitor_flag,
                    json.dumps(competitor_match) if competitor_match else None,
                    1 if cot_enabled else 0,
                    json.dumps(reasoning_trace) if reasoning_trace else None,
                    disagreement_detected,
                    now,
                ),
            )
            return cursor.lastrowid

    async def get_thesis_classification(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent thesis classification for a canonical key.

        Args:
            canonical_key: The canonical key to look up

        Returns:
            Dictionary with classification details or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT signal_id, canonical_key,
                   keyword_score, keyword_category, negative_keywords,
                   thesis_match, thesis_fit_score, category,
                   stage_estimate, confidence, rationale, key_signals,
                   prompt_version, model, input_tokens, output_tokens, latency_ms,
                   competitor_flag, competitor_match,
                   disagreement_detected,
                   classified_at
            FROM thesis_classifications
            WHERE canonical_key = ?
            ORDER BY classified_at DESC, id DESC
            LIMIT 1
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            "signal_id": row[0],
            "canonical_key": row[1],
            "keyword_score": row[2],
            "keyword_category": row[3],
            "negative_keywords": json.loads(row[4]) if row[4] else [],
            "thesis_match": bool(row[5]) if row[5] is not None else None,
            "thesis_fit_score": row[6],
            "category": row[7],
            "stage_estimate": row[8],
            "confidence": row[9],
            "rationale": row[10],
            "key_signals": json.loads(row[11]) if row[11] else [],
            "prompt_version": row[12],
            "model": row[13],
            "input_tokens": row[14],
            "output_tokens": row[15],
            "latency_ms": row[16],
            "competitor_flag": bool(row[17]),
            "competitor_match": json.loads(row[18]) if row[18] else None,
            "disagreement_detected": bool(row[19]),
            "classified_at": row[20],
        }

    async def get_recent_classification(
        self,
        canonical_key: str,
        days: int = 7,
    ) -> Optional[Dict[str, Any]]:
        """
        Get classification if within cache window.

        Args:
            canonical_key: The canonical key to look up
            days: Number of days to consider as "recent" (default 7)

        Returns:
            Dictionary with classification details or None if not found or expired
        """
        result = await self.get_thesis_classification(canonical_key)
        if not result:
            return None

        classified_at_str = result["classified_at"]
        # Handle both formats: with and without timezone
        if classified_at_str.endswith("Z"):
            classified_at_str = classified_at_str[:-1] + "+00:00"
        elif "+" not in classified_at_str and not classified_at_str.endswith("Z"):
            # Assume UTC if no timezone info
            classified_at_str = classified_at_str + "+00:00"

        classified_at = datetime.fromisoformat(classified_at_str)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        if classified_at >= cutoff:
            return result
        return None

    # =========================================================================
    # FUNCTIONAL SCHEMA STORAGE
    # =========================================================================

    async def save_functional_schema(self, schema: Dict[str, Any]) -> int:
        """Save a new functional schema for a company.

        Guards:
        - Verifies all signal_ids in evidence_signal_ids belong to company_id
        - Schema rows are immutable once created (extract-once, Phase 2)

        Returns the new row ID.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        company_id = schema["company_id"]

        # Validate evidence_signal_ids belong to this company_id
        evidence_ids = schema.get("evidence_signal_ids") or []
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            cursor = await self._db.execute(
                f"SELECT id FROM signals WHERE id IN ({placeholders}) AND company_id != ?",
                [*evidence_ids, company_id],
            )
            bad_rows = await cursor.fetchall()
            if bad_rows:
                bad_ids = [r[0] for r in bad_rows]
                raise ValueError(
                    f"evidence_signal_ids {bad_ids} do not belong to company_id {company_id}"
                )

        # Determine next schema_version for this company
        cursor = await self._db.execute(
            "SELECT COALESCE(MAX(schema_version), 0) FROM functional_schemas WHERE company_id = ?",
            (company_id,),
        )
        row = await cursor.fetchone()
        next_version = row[0] + 1

        import json as _json

        cursor = await self._db.execute(
            """INSERT INTO functional_schemas (
                company_id, schema_version,
                problem_solved_text, customer_text, approach_text,
                customer_archetype, problem_archetypes,
                schema_confidence, is_advisory,
                evidence_signal_ids, extraction_model, extraction_prompt_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                company_id,
                next_version,
                schema.get("problem_solved_text"),
                schema.get("customer_text"),
                schema.get("approach_text"),
                schema.get("customer_archetype"),
                _json.dumps(schema.get("problem_archetypes")) if schema.get("problem_archetypes") else None,
                schema.get("schema_confidence"),
                1 if schema.get("is_advisory") else 0,
                _json.dumps(evidence_ids) if evidence_ids else None,
                schema.get("extraction_model"),
                schema.get("extraction_prompt_version"),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_active_schema(self, company_id: str) -> Optional[Dict[str, Any]]:
        """Get the current active schema for a company. Returns None if no schema exists."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """SELECT id, company_id, schema_version,
                      problem_solved_text, customer_text, approach_text,
                      customer_archetype, problem_archetypes,
                      schema_confidence, is_advisory,
                      evidence_signal_ids, extraction_model, extraction_prompt_version,
                      is_active, superseded_by, created_at
               FROM functional_schemas
               WHERE company_id = ? AND is_active = 1
               ORDER BY schema_version DESC LIMIT 1""",
            (company_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        import json as _json

        return {
            "id": row[0],
            "company_id": row[1],
            "schema_version": row[2],
            "problem_solved_text": row[3],
            "customer_text": row[4],
            "approach_text": row[5],
            "customer_archetype": row[6],
            "problem_archetypes": _json.loads(row[7]) if row[7] else None,
            "schema_confidence": row[8],
            "is_advisory": bool(row[9]),
            "evidence_signal_ids": _json.loads(row[10]) if row[10] else None,
            "extraction_model": row[11],
            "extraction_prompt_version": row[12],
            "is_active": bool(row[13]),
            "superseded_by": row[14],
            "created_at": row[15],
        }

    async def get_schema_history(self, company_id: str) -> List[Dict[str, Any]]:
        """Get all schema versions for a company (audit trail), ordered by version."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """SELECT id, company_id, schema_version,
                      problem_solved_text, customer_text, approach_text,
                      customer_archetype, problem_archetypes,
                      schema_confidence, is_advisory,
                      evidence_signal_ids, extraction_model, extraction_prompt_version,
                      is_active, superseded_by, created_at
               FROM functional_schemas
               WHERE company_id = ?
               ORDER BY schema_version ASC""",
            (company_id,),
        )
        rows = await cursor.fetchall()

        import json as _json

        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "company_id": row[1],
                "schema_version": row[2],
                "problem_solved_text": row[3],
                "customer_text": row[4],
                "approach_text": row[5],
                "customer_archetype": row[6],
                "problem_archetypes": _json.loads(row[7]) if row[7] else None,
                "schema_confidence": row[8],
                "is_advisory": bool(row[9]),
                "evidence_signal_ids": _json.loads(row[10]) if row[10] else None,
                "extraction_model": row[11],
                "extraction_prompt_version": row[12],
                "is_active": bool(row[13]),
                "superseded_by": row[14],
                "created_at": row[15],
            })
        return results

    async def has_active_schema(self, company_id: str) -> bool:
        """Quick check if company already has an active schema (pipeline skip logic)."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT 1 FROM functional_schemas WHERE company_id = ? AND is_active = 1 LIMIT 1",
            (company_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    # =========================================================================
    # EXIT PREDICTIONS
    # =========================================================================

    async def store_exit_prediction(
        self,
        prediction: "ExitPrediction",
    ) -> int:
        """
        Store or update an exit prediction.

        Uses UPSERT to handle updates for existing canonical keys.

        Args:
            prediction: ExitPrediction dataclass from exit_predictor.py

        Returns:
            ID of the inserted/updated row
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Serialize evidence and exit_type_probabilities to JSON
        evidence_json = json.dumps(
            [{"signal_id": e.signal_id, "factor": e.factor, "value": e.value}
             for e in prediction.evidence]
        ) if prediction.evidence else None

        exit_type_json = json.dumps(prediction.exit_type_probabilities) \
            if prediction.exit_type_probabilities else None

        cursor = await self._db.execute(
            """
            INSERT INTO exit_predictions (
                canonical_key, thesis_fit, founder_score, traction_score,
                funding_score, velocity_score, age_score, investor_centrality,
                patent_count, deal_quality_score, percentile_rank,
                exit_probability, confidence, recommendation,
                exit_timeline, exit_type_probabilities, evidence,
                model_version, predicted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                thesis_fit = excluded.thesis_fit,
                founder_score = excluded.founder_score,
                traction_score = excluded.traction_score,
                funding_score = excluded.funding_score,
                velocity_score = excluded.velocity_score,
                age_score = excluded.age_score,
                investor_centrality = excluded.investor_centrality,
                patent_count = excluded.patent_count,
                deal_quality_score = excluded.deal_quality_score,
                exit_probability = excluded.exit_probability,
                confidence = excluded.confidence,
                recommendation = excluded.recommendation,
                exit_timeline = excluded.exit_timeline,
                exit_type_probabilities = excluded.exit_type_probabilities,
                evidence = excluded.evidence,
                model_version = excluded.model_version,
                predicted_at = excluded.predicted_at
            """,
            (
                prediction.canonical_key,
                prediction.thesis_fit,
                prediction.founder_score,
                prediction.traction_score,
                prediction.funding_score,
                prediction.velocity_score,
                prediction.age_score,
                prediction.investor_centrality,
                prediction.patent_count,
                prediction.deal_quality_score,
                prediction.percentile_rank,
                prediction.exit_probability,
                prediction.confidence,
                prediction.recommendation,
                prediction.exit_timeline,
                exit_type_json,
                evidence_json,
                prediction.model_version,
                prediction.predicted_at.isoformat(),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def get_exit_prediction(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get exit prediction for a canonical key.

        Args:
            canonical_key: The canonical key to look up

        Returns:
            Dictionary with prediction details or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, thesis_fit, founder_score, traction_score,
                   funding_score, velocity_score, age_score, investor_centrality,
                   patent_count, deal_quality_score, percentile_rank,
                   exit_probability, confidence, recommendation,
                   exit_timeline, exit_type_probabilities, evidence,
                   model_version, predicted_at, created_at
            FROM exit_predictions
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            "id": row[0],
            "canonical_key": row[1],
            "thesis_fit": row[2],
            "founder_score": row[3],
            "traction_score": row[4],
            "funding_score": row[5],
            "velocity_score": row[6],
            "age_score": row[7],
            "investor_centrality": row[8],
            "patent_count": row[9],
            "deal_quality_score": row[10],
            "percentile_rank": row[11],
            "exit_probability": row[12],
            "confidence": row[13],
            "recommendation": row[14],
            "exit_timeline": row[15],
            "exit_type_probabilities": json.loads(row[16]) if row[16] else {},
            "evidence": json.loads(row[17]) if row[17] else [],
            "model_version": row[18],
            "predicted_at": row[19],
            "created_at": row[20],
        }

    async def get_all_exit_predictions(
        self,
        order_by: str = "deal_quality_score DESC",
    ) -> List[Dict[str, Any]]:
        """
        Get all exit predictions ordered by specified column.

        Args:
            order_by: SQL ORDER BY clause (default: deal_quality_score DESC)

        Returns:
            List of prediction dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Validate order_by to prevent SQL injection
        allowed_columns = {
            "deal_quality_score", "exit_probability", "predicted_at",
            "created_at", "canonical_key", "percentile_rank",
        }
        order_parts = order_by.split()
        if order_parts[0] not in allowed_columns:
            order_by = "deal_quality_score DESC"

        cursor = await self._db.execute(
            f"""
            SELECT id, canonical_key, thesis_fit, founder_score, traction_score,
                   funding_score, velocity_score, age_score, investor_centrality,
                   patent_count, deal_quality_score, percentile_rank,
                   exit_probability, confidence, recommendation,
                   exit_timeline, exit_type_probabilities, evidence,
                   model_version, predicted_at, created_at
            FROM exit_predictions
            ORDER BY {order_by}
            """
        )
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "canonical_key": row[1],
                "thesis_fit": row[2],
                "founder_score": row[3],
                "traction_score": row[4],
                "funding_score": row[5],
                "velocity_score": row[6],
                "age_score": row[7],
                "investor_centrality": row[8],
                "patent_count": row[9],
                "deal_quality_score": row[10],
                "percentile_rank": row[11],
                "exit_probability": row[12],
                "confidence": row[13],
                "recommendation": row[14],
                "exit_timeline": row[15],
                "exit_type_probabilities": json.loads(row[16]) if row[16] else {},
                "evidence": json.loads(row[17]) if row[17] else [],
                "model_version": row[18],
                "predicted_at": row[19],
                "created_at": row[20],
            }
            for row in rows
        ]

    async def update_exit_prediction_percentile(
        self,
        canonical_key: str,
        percentile_rank: int,
    ) -> bool:
        """
        Update the percentile rank for an exit prediction.

        Called by the nightly batch job.

        Args:
            canonical_key: The canonical key to update
            percentile_rank: Computed percentile (1-99)

        Returns:
            True if updated, False if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            UPDATE exit_predictions
            SET percentile_rank = ?
            WHERE canonical_key = ?
            """,
            (percentile_rank, canonical_key),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # INVESTOR MATCHING METHODS (Sprint 5)
    # =========================================================================

    async def save_investor(
        self,
        investor_id: str,
        name: str,
        source: str,
        investor_type: str = "vc",
        website_domain: Optional[str] = None,
        hq_country: Optional[str] = None,
        hq_city: Optional[str] = None,
        founded_year: Optional[int] = None,
        aum_usd: Optional[float] = None,
        source_ref: Optional[str] = None,
    ) -> str:
        """
        Save or update an investor entity.

        Args:
            investor_id: Canonical key (e.g., "investor:sequoia_capital")
            name: Display name
            source: Data source (crunchbase, curated_json, sec_edgar)
            investor_type: vc, angel, accelerator, corporate, family_office
            website_domain: Optional website
            hq_country: Headquarters country
            hq_city: Headquarters city
            founded_year: Year founded
            aum_usd: Assets under management
            source_ref: Source URL or file path

        Returns:
            The investor_id
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """
            INSERT INTO investors (
                id, canonical_key, name, investor_type, website_domain,
                hq_country, hq_city, founded_year, aum_usd, source, source_ref,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                investor_type = excluded.investor_type,
                website_domain = excluded.website_domain,
                hq_country = excluded.hq_country,
                hq_city = excluded.hq_city,
                founded_year = excluded.founded_year,
                aum_usd = excluded.aum_usd,
                source = excluded.source,
                source_ref = excluded.source_ref,
                updated_at = excluded.updated_at
            """,
            (
                investor_id, investor_id, name, investor_type, website_domain,
                hq_country, hq_city, founded_year, aum_usd, source, source_ref,
                now, now
            ),
        )
        await self._db.commit()
        return investor_id

    async def save_portfolio_entry(
        self,
        investor_id: str,
        company_key: str,
        relationship_type: str,
        source: str,
        round_type: Optional[str] = None,
        round_date: Optional[str] = None,
        investment_usd: Optional[float] = None,
        ownership_pct: Optional[float] = None,
        is_lead: bool = False,
        confidence: float = 0.5,
        source_ref: Optional[str] = None,
        extraction_id: Optional[int] = None,
    ) -> int:
        """
        Save a portfolio entry (investor -> company relationship).

        Args:
            investor_id: The investor's canonical key
            company_key: The company's canonical key
            relationship_type: led, participated, followed_on, acquired, advisor
            source: Data source
            round_type: pre_seed, seed, series_a, etc.
            round_date: ISO date YYYY-MM-DD
            investment_usd: Investment amount
            ownership_pct: Ownership percentage
            is_lead: Whether investor led the round
            confidence: Confidence score 0-1
            source_ref: Source URL or file
            extraction_id: FK to claim_extractions for evidence

        Returns:
            The portfolio entry ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO investor_portfolios (
                investor_id, company_key, relationship_type, round_type,
                round_date, investment_usd, ownership_pct, is_lead,
                source, source_ref, confidence, extraction_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(investor_id, company_key, round_type, round_date) DO UPDATE SET
                relationship_type = excluded.relationship_type,
                investment_usd = excluded.investment_usd,
                ownership_pct = excluded.ownership_pct,
                is_lead = excluded.is_lead,
                confidence = excluded.confidence,
                source_ref = excluded.source_ref,
                extraction_id = excluded.extraction_id,
                updated_at = excluded.updated_at
            """,
            (
                investor_id, company_key, relationship_type, round_type,
                round_date, investment_usd, ownership_pct, 1 if is_lead else 0,
                source, source_ref, confidence, extraction_id,
                now, now
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def save_investor_profile_claim(
        self,
        investor_id: str,
        predicate: str,
        value: str,
        confidence: float,
        support_count: int,
        lift_score: Optional[float] = None,
        support_evidence: Optional[List[Dict[str, Any]]] = None,
        status: str = "active",
    ) -> int:
        """
        Save an inferred profile claim for an investor.

        Args:
            investor_id: The investor's canonical key
            predicate: sector_preference, stage_preference, geo_preference, etc.
            value: The predicate value
            confidence: Confidence score 0-1
            support_count: Number of portfolio companies supporting this
            lift_score: Log-odds vs global baseline
            support_evidence: JSON array of {company_key, extraction_id}
            status: active, stale, retracted

        Returns:
            The claim ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        evidence_json = json.dumps(support_evidence) if support_evidence else None

        cursor = await self._db.execute(
            """
            INSERT INTO investor_profile_claims (
                investor_id, predicate, value, confidence, lift_score,
                support_count, support_evidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(investor_id, predicate, value) DO UPDATE SET
                confidence = excluded.confidence,
                lift_score = excluded.lift_score,
                support_count = excluded.support_count,
                support_evidence = excluded.support_evidence,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                investor_id, predicate, value, confidence, lift_score,
                support_count, evidence_json, status, now, now
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def save_global_baseline(
        self,
        predicate: str,
        value: str,
        global_probability: float,
        sample_size: int,
        sample_source: str,
        expires_at: Optional[str] = None,
    ) -> int:
        """
        Save a global baseline probability for lift calculation.

        Args:
            predicate: sector, stage, geo, business_model
            value: The predicate value
            global_probability: P(value) across all companies
            sample_size: Number of companies in sample
            sample_source: crunchbase_2y, portfolio_all, signals_30d
            expires_at: Optional expiry time

        Returns:
            The baseline ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO global_baselines (
                predicate, value, global_probability, sample_size,
                sample_source, computed_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(predicate, value, sample_source) DO UPDATE SET
                global_probability = excluded.global_probability,
                sample_size = excluded.sample_size,
                computed_at = excluded.computed_at,
                expires_at = excluded.expires_at
            """,
            (predicate, value, global_probability, sample_size, sample_source, now, expires_at),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def get_global_baseline(
        self,
        predicate: str,
        value: str,
        sample_source: str = "crunchbase_2y",
    ) -> Optional[float]:
        """
        Get the global baseline probability for a predicate/value pair.

        Returns:
            The global probability, or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT global_probability
            FROM global_baselines
            WHERE predicate = ? AND value = ? AND sample_source = ?
            """,
            (predicate, value, sample_source),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def save_investor_match(
        self,
        company_key: str,
        investor_id: str,
        match_score: float,
        explanation: List[str],
        rank: int,
        fts_score: Optional[float] = None,
        embedding_score: Optional[float] = None,
        constraint_score: Optional[float] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """
        Save an investor match result.

        Args:
            company_key: The startup's canonical key
            investor_id: The matched investor
            match_score: Combined match score 0-1
            explanation: List of match reasons
            rank: Position in result list
            fts_score: BM25 component
            embedding_score: Cosine similarity component
            constraint_score: Preference match component
            evidence: Supporting portfolio examples

        Returns:
            The match ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO investor_matches (
                company_key, investor_id, match_score, fts_score,
                embedding_score, constraint_score, explanation, evidence,
                rank, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_key, investor_id) DO UPDATE SET
                match_score = excluded.match_score,
                fts_score = excluded.fts_score,
                embedding_score = excluded.embedding_score,
                constraint_score = excluded.constraint_score,
                explanation = excluded.explanation,
                evidence = excluded.evidence,
                rank = excluded.rank,
                created_at = excluded.created_at
            """,
            (
                company_key, investor_id, match_score, fts_score,
                embedding_score, constraint_score, json.dumps(explanation),
                json.dumps(evidence) if evidence else None, rank, now
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def get_investor_matches(
        self,
        company_key: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get investor matches for a company.

        Args:
            company_key: The company's canonical key
            limit: Maximum results to return

        Returns:
            List of match dicts ordered by rank
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                im.company_key, im.investor_id, im.match_score,
                im.fts_score, im.embedding_score, im.constraint_score,
                im.explanation, im.evidence, im.rank, im.created_at,
                i.name as investor_name, i.investor_type, i.hq_country
            FROM investor_matches im
            JOIN investors i ON im.investor_id = i.id
            WHERE im.company_key = ?
            ORDER BY im.rank ASC
            LIMIT ?
            """,
            (company_key, limit),
        )
        rows = await cursor.fetchall()

        return [
            {
                "company_key": row[0],
                "investor_id": row[1],
                "match_score": row[2],
                "fts_score": row[3],
                "embedding_score": row[4],
                "constraint_score": row[5],
                "explanation": json.loads(row[6]) if row[6] else [],
                "evidence": json.loads(row[7]) if row[7] else [],
                "rank": row[8],
                "created_at": row[9],
                "investor_name": row[10],
                "investor_type": row[11],
                "hq_country": row[12],
            }
            for row in rows
        ]

    async def get_investor_portfolio(
        self,
        investor_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Get all portfolio entries for an investor.

        Args:
            investor_id: The investor's canonical key

        Returns:
            List of portfolio entry dicts
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                id, investor_id, company_key, relationship_type, round_type,
                round_date, investment_usd, ownership_pct, is_lead, source,
                source_ref, confidence, extraction_id, created_at, updated_at
            FROM investor_portfolios
            WHERE investor_id = ?
            ORDER BY round_date DESC
            """,
            (investor_id,),
        )
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "investor_id": row[1],
                "company_key": row[2],
                "relationship_type": row[3],
                "round_type": row[4],
                "round_date": row[5],
                "investment_usd": row[6],
                "ownership_pct": row[7],
                "is_lead": bool(row[8]),
                "source": row[9],
                "source_ref": row[10],
                "confidence": row[11],
                "extraction_id": row[12],
                "created_at": row[13],
                "updated_at": row[14],
            }
            for row in rows
        ]

    async def get_investor_profile_claims(
        self,
        investor_id: str,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        """
        Get profile claims for an investor.

        Args:
            investor_id: The investor's canonical key
            status: Filter by status (active, stale, retracted)

        Returns:
            List of claim dicts
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                id, investor_id, predicate, value, confidence, lift_score,
                support_count, support_evidence, status, created_at, updated_at
            FROM investor_profile_claims
            WHERE investor_id = ? AND status = ?
            ORDER BY lift_score DESC
            """,
            (investor_id, status),
        )
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "investor_id": row[1],
                "predicate": row[2],
                "value": row[3],
                "confidence": row[4],
                "lift_score": row[5],
                "support_count": row[6],
                "support_evidence": json.loads(row[7]) if row[7] else [],
                "status": row[8],
                "created_at": row[9],
                "updated_at": row[10],
            }
            for row in rows
        ]

    # =========================================================================
    # EVALUATION & DRIFT DETECTION METHODS (Sprint 6)
    # =========================================================================

    async def save_evaluation_run(
        self,
        run_id: str,
        run_type: str,
        model_version: str,
        gold_set_version: str,
        metrics: Dict[str, Any],
        embedding_version: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        baseline_run_id: Optional[int] = None,
    ) -> int:
        """
        Save an evaluation run with metrics.

        Args:
            run_id: Unique run identifier
            run_type: extraction, similarity, investor_match
            model_version: Model version used
            gold_set_version: Gold set version evaluated against
            metrics: Dict of metric values (f1, precision, recall, etc.)
            embedding_version: Optional embedding version
            config: Optional run configuration
            baseline_run_id: Optional reference to baseline run

        Returns:
            The run ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            INSERT INTO evaluation_runs (
                run_id, run_type, model_version, embedding_version,
                gold_set_version, metrics, config, baseline_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, run_type, model_version, embedding_version,
                gold_set_version, json.dumps(metrics),
                json.dumps(config) if config else None,
                baseline_run_id, now
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def save_drift_alert(
        self,
        alert_type: str,
        severity: str,
        metric_name: str,
        baseline_value: float,
        current_value: float,
        threshold: float,
        evaluation_run_id: Optional[int] = None,
    ) -> int:
        """
        Save a drift alert.

        Args:
            alert_type: extraction_f1_drop, abstention_spike, etc.
            severity: red or yellow
            metric_name: Name of the drifting metric
            baseline_value: Expected/historical value
            current_value: Current measured value
            threshold: Threshold that was breached
            evaluation_run_id: Optional link to eval run

        Returns:
            The alert ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        delta = current_value - baseline_value

        cursor = await self._db.execute(
            """
            INSERT INTO drift_alerts (
                alert_type, severity, metric_name, baseline_value,
                current_value, threshold, delta, evaluation_run_id,
                acknowledged, slack_notified, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (
                alert_type, severity, metric_name, baseline_value,
                current_value, threshold, delta, evaluation_run_id, now
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def get_unacknowledged_drift_alerts(
        self,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get unacknowledged drift alerts.

        Args:
            severity: Optional filter by severity (red, yellow)
            limit: Maximum alerts to return

        Returns:
            List of alert dicts ordered by severity and time
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                id, alert_type, severity, metric_name, baseline_value,
                current_value, threshold, delta, evaluation_run_id,
                acknowledged, acknowledged_by, acknowledged_at,
                slack_notified, created_at
            FROM drift_alerts
            WHERE acknowledged = 0
        """
        params: List[Any] = []

        if severity:
            query += " AND severity = ?"
            params.append(severity)

        query += " ORDER BY CASE severity WHEN 'red' THEN 0 ELSE 1 END, created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "alert_type": row[1],
                "severity": row[2],
                "metric_name": row[3],
                "baseline_value": row[4],
                "current_value": row[5],
                "threshold": row[6],
                "delta": row[7],
                "evaluation_run_id": row[8],
                "acknowledged": bool(row[9]),
                "acknowledged_by": row[10],
                "acknowledged_at": row[11],
                "slack_notified": bool(row[12]),
                "created_at": row[13],
            }
            for row in rows
        ]

    async def acknowledge_drift_alert(
        self,
        alert_id: int,
        acknowledged_by: str,
    ) -> bool:
        """
        Acknowledge a drift alert.

        Args:
            alert_id: The alert ID
            acknowledged_by: User who acknowledged

        Returns:
            True if updated, False if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """
            UPDATE drift_alerts
            SET acknowledged = 1, acknowledged_by = ?, acknowledged_at = ?
            WHERE id = ?
            """,
            (acknowledged_by, now, alert_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # THESIS EVALUATION METHODS
    # =========================================================================

    async def save_thesis_evaluation(
        self,
        run_id: str,
        evaluator_type: str,
        dataset_path: str,
        accuracy: float,
        per_class_metrics: Dict[str, Any],
        confusion_matrix: Dict[str, Dict[str, int]],
        latency_ms: Optional[int] = None,
        token_usage: Optional[Dict[str, int]] = None,
        errors: Optional[List[str]] = None,
    ) -> int:
        """
        Save a thesis classification evaluation run.

        Uses existing evaluation_runs table with run_type='thesis_keyword' or 'thesis_llm'.

        Args:
            run_id: Unique run identifier
            evaluator_type: 'keyword' or 'llm'
            dataset_path: Path to dataset used
            accuracy: Overall accuracy (0-1)
            per_class_metrics: Dict of {class: {precision, recall, f1, support}}
            confusion_matrix: Dict of {actual: {predicted: count}}
            latency_ms: Total evaluation time in ms
            token_usage: Optional {input_tokens, output_tokens} for LLM
            errors: Optional list of error messages

        Returns:
            Database row ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        run_type = f"thesis_{evaluator_type}"

        # Combine metrics into single JSON
        metrics = {
            "accuracy": accuracy,
            "per_class": per_class_metrics,
            "confusion_matrix": confusion_matrix,
        }

        # Config stores additional metadata
        config = {
            "dataset_path": dataset_path,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
            "errors": errors,
        }

        cursor = await self._db.execute(
            """
            INSERT INTO evaluation_runs (
                run_id, run_type, model_version, embedding_version,
                gold_set_version, metrics, config, baseline_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_type,
                evaluator_type,  # Use evaluator_type as model_version
                None,  # No embedding version for thesis eval
                dataset_path,  # Use dataset_path as gold_set_version
                json.dumps(metrics),
                json.dumps(config),
                None,  # No baseline for now
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def get_thesis_evaluations(
        self,
        evaluator_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get recent thesis evaluation runs.

        Args:
            evaluator_type: Optional filter ('keyword', 'llm', or None for both)
            limit: Maximum runs to return

        Returns:
            List of evaluation run dicts with metrics
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                id, run_id, run_type, model_version,
                gold_set_version, metrics, config, created_at
            FROM evaluation_runs
            WHERE run_type LIKE 'thesis_%'
        """
        params: List[Any] = []

        if evaluator_type:
            query += " AND run_type = ?"
            params.append(f"thesis_{evaluator_type}")

        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        results = []
        for row in rows:
            metrics = json.loads(row[5]) if row[5] else {}
            config = json.loads(row[6]) if row[6] else {}

            results.append({
                "id": row[0],
                "run_id": row[1],
                "run_type": row[2],
                "evaluator_type": row[2].replace("thesis_", ""),
                "model_version": row[3],
                "dataset_path": row[4],
                "accuracy": metrics.get("accuracy"),
                "per_class_metrics": metrics.get("per_class", {}),
                "confusion_matrix": metrics.get("confusion_matrix", {}),
                "latency_ms": config.get("latency_ms"),
                "token_usage": config.get("token_usage"),
                "errors": config.get("errors", []),
                "created_at": row[7],
            })

        return results

    async def get_thesis_baseline(
        self,
        evaluator_type: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get most recent evaluation as baseline for comparison.

        Args:
            evaluator_type: 'keyword' or 'llm'

        Returns:
            Most recent evaluation run or None
        """
        results = await self.get_thesis_evaluations(
            evaluator_type=evaluator_type,
            limit=1,
        )
        return results[0] if results else None

    # =========================================================================
    # COMPANY STATE METHODS (Migration 13)
    # =========================================================================

    async def upsert_company_state(
        self,
        canonical_key: str,
        status: str,
        owner: Optional[str] = None,
        pass_reason: Optional[str] = None,
        notion_page_id: Optional[str] = None,
        snoozed_until: Optional[datetime] = None,
    ) -> None:
        """
        Insert or update company state.

        Args:
            canonical_key: The company's canonical key
            status: New status (inbox, tracking, passed, pipeline_requested, funded)
            owner: GP who owns this company
            pass_reason: Reason for passing (if status='passed')
            notion_page_id: Notion page ID if pushed
            snoozed_until: Snooze end datetime
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        snoozed_str = snoozed_until.isoformat() if snoozed_until else None

        await self._db.execute(
            """
            INSERT INTO company_state (canonical_key, status, owner, last_action_at, pass_reason, notion_page_id, snoozed_until)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                status = excluded.status,
                owner = COALESCE(excluded.owner, company_state.owner),
                last_action_at = excluded.last_action_at,
                pass_reason = COALESCE(excluded.pass_reason, company_state.pass_reason),
                notion_page_id = COALESCE(excluded.notion_page_id, company_state.notion_page_id),
                snoozed_until = excluded.snoozed_until
            """,
            (canonical_key, status, owner, now, pass_reason, notion_page_id, snoozed_str),
        )
        await self._db.commit()

    async def get_company_state(self, canonical_key: str) -> Optional[CompanyState]:
        """
        Get company state by canonical key.

        Args:
            canonical_key: The company's canonical key

        Returns:
            CompanyState or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT canonical_key, status, owner, last_action_at, pass_reason, notion_page_id, snoozed_until
            FROM company_state
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return CompanyState(
            canonical_key=row[0],
            status=row[1],
            owner=row[2],
            last_action_at=datetime.fromisoformat(row[3]) if row[3] else None,
            pass_reason=row[4],
            notion_page_id=row[5],
            snoozed_until=datetime.fromisoformat(row[6]) if row[6] else None,
        )

    async def get_inbox_companies(
        self,
        status: str = "inbox",
        min_confidence: float = 0.0,
        limit: int = 100,
        offset: int = 0,
    ) -> List[InboxCompany]:
        """
        Get companies for inbox view with aggregated signal data.

        Args:
            status: Filter by status (inbox, tracking, passed)
            min_confidence: Minimum confidence score
            limit: Max results
            offset: Pagination offset

        Returns:
            List of InboxCompany objects
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Complex query joining signals with company_state
        cursor = await self._db.execute(
            """
            SELECT
                s.canonical_key,
                MAX(s.company_name) as company_name,
                COALESCE(cs.status, 'inbox') as status,
                MAX(s.confidence) as max_confidence,
                COUNT(DISTINCT s.id) as signal_count,
                GROUP_CONCAT(DISTINCT s.source_api) as sources,
                MIN(s.created_at) as first_seen,
                MAX(s.created_at) as last_seen,
                cs.owner,
                MAX(tc.thesis_fit_score) as thesis_fit_score,
                MAX(tc.category) as vertical
            FROM signals s
            LEFT JOIN company_state cs ON s.canonical_key = cs.canonical_key
            LEFT JOIN thesis_classifications tc ON s.canonical_key = tc.canonical_key
            WHERE COALESCE(cs.status, 'inbox') = ?
              AND s.confidence >= ?
            GROUP BY s.canonical_key
            ORDER BY max_confidence DESC, signal_count DESC
            LIMIT ? OFFSET ?
            """,
            (status, min_confidence, limit, offset),
        )
        rows = await cursor.fetchall()

        return [
            InboxCompany(
                canonical_key=row[0],
                company_name=row[1],
                status=row[2],
                max_confidence=row[3],
                signal_count=row[4],
                sources=row[5] or "",
                first_seen=datetime.fromisoformat(row[6]) if row[6] else datetime.now(timezone.utc),
                last_seen=datetime.fromisoformat(row[7]) if row[7] else datetime.now(timezone.utc),
                owner=row[8],
                thesis_fit_score=row[9],
                vertical=row[10],
            )
            for row in rows
        ]

    async def log_company_action(
        self,
        canonical_key: str,
        action: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Log an action taken on a company.

        Args:
            canonical_key: The company's canonical key
            action: Action type (track, pass, pipeline, snooze, unsnooze, note)
            actor: Who performed the action
            metadata: Extra context (pass_reason, note text, etc.)

        Returns:
            The action ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata) if metadata else None

        cursor = await self._db.execute(
            """
            INSERT INTO company_actions (occurred_at, canonical_key, action, actor, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (now, canonical_key, action, actor, metadata_json),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_company_actions(
        self,
        canonical_key: str,
        limit: int = 50,
    ) -> List[CompanyAction]:
        """
        Get action history for a company.

        Args:
            canonical_key: The company's canonical key
            limit: Max results

        Returns:
            List of CompanyAction objects (most recent first)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, action, occurred_at, actor, metadata_json
            FROM company_actions
            WHERE canonical_key = ?
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (canonical_key, limit),
        )
        rows = await cursor.fetchall()

        return [
            CompanyAction(
                id=row[0],
                canonical_key=row[1],
                action=row[2],
                occurred_at=datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
                actor=row[4],
                metadata=json.loads(row[5]) if row[5] else None,
            )
            for row in rows
        ]

    async def reserve_token_nonce(
        self,
        nonce: str,
        canonical_key: str,
        action: str,
        expires_in_days: int = 7,
    ) -> None:
        """
        Reserve a nonce for a magic link token.

        Args:
            nonce: The unique nonce value
            canonical_key: Company this token is for
            action: Action this token permits (track, pass, view)
            expires_in_days: Token validity period
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=expires_in_days)).isoformat()

        await self._db.execute(
            """
            INSERT INTO token_nonces (nonce, canonical_key, action, created_at, expires_at, used)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (nonce, canonical_key, action, now.isoformat(), expires_at),
        )
        await self._db.commit()

    async def consume_token_nonce(self, nonce: str) -> Optional[TokenNonce]:
        """
        Atomically consume a token nonce (one-time use).

        Args:
            nonce: The nonce to consume

        Returns:
            TokenNonce if valid and unused, None if invalid/expired/used
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        # Atomically mark as used and return if successful
        cursor = await self._db.execute(
            """
            UPDATE token_nonces
            SET used = 1
            WHERE nonce = ?
              AND used = 0
              AND (expires_at IS NULL OR expires_at > ?)
            RETURNING nonce, canonical_key, action, created_at, expires_at, used
            """,
            (nonce, now),
        )
        row = await cursor.fetchone()
        await self._db.commit()

        if not row:
            return None

        return TokenNonce(
            nonce=row[0],
            canonical_key=row[1],
            action=row[2],
            created_at=datetime.fromisoformat(row[3]) if row[3] else datetime.now(timezone.utc),
            expires_at=datetime.fromisoformat(row[4]) if row[4] else None,
            used=bool(row[5]),
        )

    async def get_company_by_key(self, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        Get aggregated company data by canonical key.

        Args:
            canonical_key: The company's canonical key

        Returns:
            Dict with company data or None
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT
                s.canonical_key,
                MAX(s.company_name) as company_name,
                MAX(s.confidence) as max_confidence,
                COUNT(DISTINCT s.id) as signal_count,
                GROUP_CONCAT(DISTINCT s.source_api) as sources,
                MIN(s.created_at) as first_seen,
                MAX(s.created_at) as last_seen,
                MAX(tc.thesis_fit_score) as thesis_fit_score,
                MAX(tc.category) as vertical,
                MAX(tc.rationale) as one_liner
            FROM signals s
            LEFT JOIN thesis_classifications tc ON s.canonical_key = tc.canonical_key
            WHERE s.canonical_key = ?
            GROUP BY s.canonical_key
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # Extract website from canonical key if domain-based
        website = None
        if canonical_key.startswith("domain:"):
            website = f"https://{canonical_key[7:]}"

        return {
            "canonical_key": row[0],
            "company_name": row[1],
            "max_confidence": row[2],
            "signal_count": row[3],
            "sources": row[4],
            "first_seen": row[5],
            "last_seen": row[6],
            "thesis_fit_score": row[7],
            "vertical": row[8],
            "one_liner": row[9],
            "website": website,
        }

    # =========================================================================
    # COMMUNITY SENTIMENT STORAGE
    # =========================================================================

    async def save_community_mention(
        self,
        source: str,
        source_id: str,
        canonical_key: Optional[str] = None,
        title: Optional[str] = None,
        url: Optional[str] = None,
        author: Optional[str] = None,
        sentiment_score: Optional[float] = None,
        sentiment_label: Optional[str] = None,
        sentiment_method: Optional[str] = None,
        keywords_found: Optional[List[str]] = None,
        subreddit: Optional[str] = None,
        channel_name: Optional[str] = None,
        engagement_score: Optional[int] = None,
        posted_at: Optional[datetime] = None,
    ) -> int:
        """
        Save a community mention (Reddit, Telegram, Discord post).

        Args:
            source: Platform name ('reddit', 'telegram', 'discord')
            source_id: Platform-specific post/message ID
            canonical_key: Company canonical key (if identified)
            title: Post/message title
            url: Link to source
            author: Username (may be anonymized)
            sentiment_score: Sentiment score (-1.0 to 1.0)
            sentiment_label: 'positive', 'negative', 'neutral'
            sentiment_method: 'heuristic' or 'ollama'
            keywords_found: List of sentiment keywords detected
            subreddit: Reddit subreddit name
            channel_name: Telegram/Discord channel name
            engagement_score: Upvotes/reactions/etc.
            posted_at: Original post timestamp

        Returns:
            Inserted row ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        posted_at_str = posted_at.isoformat() if posted_at else None

        cursor = await self._db.execute(
            """
            INSERT OR REPLACE INTO community_mentions (
                source, source_id, canonical_key,
                title, url, author,
                sentiment_score, sentiment_label, sentiment_method,
                keywords_found,
                subreddit, channel_name, engagement_score,
                posted_at, detected_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_id,
                canonical_key,
                title,
                url,
                author,
                sentiment_score,
                sentiment_label,
                sentiment_method,
                json.dumps(keywords_found) if keywords_found else None,
                subreddit,
                channel_name,
                engagement_score,
                posted_at_str,
                now,
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def save_community_sentiment(
        self,
        canonical_key: str,
        source: str,
        mention_count: int,
        unique_authors: int,
        avg_sentiment_score: float,
        sentiment_label: str,
        positive_ratio: float,
        negative_ratio: float,
        neutral_ratio: float,
        confidence_boost: float,
        top_keywords: Optional[List[str]] = None,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> int:
        """
        Save aggregated community sentiment for a company.

        Uses upsert pattern: updates if exists, inserts if not.

        Args:
            canonical_key: Company canonical key
            source: Platform name ('reddit', 'telegram', 'discord')
            mention_count: Total number of mentions
            unique_authors: Number of unique authors
            avg_sentiment_score: Average sentiment (-1.0 to 1.0)
            sentiment_label: Overall label ('positive', 'negative', 'neutral')
            positive_ratio: Ratio of positive mentions (0.0 to 1.0)
            negative_ratio: Ratio of negative mentions
            neutral_ratio: Ratio of neutral mentions
            confidence_boost: Calculated confidence boost (-0.15 to +0.10)
            top_keywords: Most common sentiment keywords
            window_start: Start of analysis time window
            window_end: End of analysis time window

        Returns:
            Inserted/updated row ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        window_start_str = window_start.isoformat() if window_start else None
        window_end_str = window_end.isoformat() if window_end else None

        cursor = await self._db.execute(
            """
            INSERT INTO community_sentiment (
                canonical_key, source,
                mention_count, unique_authors,
                avg_sentiment_score, sentiment_label,
                positive_ratio, negative_ratio, neutral_ratio,
                confidence_boost, top_keywords,
                window_start, window_end,
                analyzed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key, source) DO UPDATE SET
                mention_count = excluded.mention_count,
                unique_authors = excluded.unique_authors,
                avg_sentiment_score = excluded.avg_sentiment_score,
                sentiment_label = excluded.sentiment_label,
                positive_ratio = excluded.positive_ratio,
                negative_ratio = excluded.negative_ratio,
                neutral_ratio = excluded.neutral_ratio,
                confidence_boost = excluded.confidence_boost,
                top_keywords = excluded.top_keywords,
                window_start = excluded.window_start,
                window_end = excluded.window_end,
                analyzed_at = excluded.analyzed_at,
                updated_at = excluded.updated_at
            """,
            (
                canonical_key,
                source,
                mention_count,
                unique_authors,
                avg_sentiment_score,
                sentiment_label,
                positive_ratio,
                negative_ratio,
                neutral_ratio,
                confidence_boost,
                json.dumps(top_keywords) if top_keywords else None,
                window_start_str,
                window_end_str,
                now,
                now,
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_community_sentiment(
        self,
        canonical_key: str,
        source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get community sentiment for a company.

        Args:
            canonical_key: Company canonical key
            source: Optional specific source (returns all if None)

        Returns:
            Dictionary with sentiment data or None if not found
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        if source:
            cursor = await self._db.execute(
                """
                SELECT canonical_key, source,
                       mention_count, unique_authors,
                       avg_sentiment_score, sentiment_label,
                       positive_ratio, negative_ratio, neutral_ratio,
                       confidence_boost, top_keywords,
                       window_start, window_end,
                       analyzed_at
                FROM community_sentiment
                WHERE canonical_key = ? AND source = ?
                """,
                (canonical_key, source),
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT canonical_key, source,
                       mention_count, unique_authors,
                       avg_sentiment_score, sentiment_label,
                       positive_ratio, negative_ratio, neutral_ratio,
                       confidence_boost, top_keywords,
                       window_start, window_end,
                       analyzed_at
                FROM community_sentiment
                WHERE canonical_key = ?
                ORDER BY analyzed_at DESC
                """,
                (canonical_key,),
            )

        row = await cursor.fetchone()
        if not row:
            return None

        return {
            "canonical_key": row[0],
            "source": row[1],
            "mention_count": row[2],
            "unique_authors": row[3],
            "avg_sentiment_score": row[4],
            "sentiment_label": row[5],
            "positive_ratio": row[6],
            "negative_ratio": row[7],
            "neutral_ratio": row[8],
            "confidence_boost": row[9],
            "top_keywords": json.loads(row[10]) if row[10] else [],
            "window_start": row[11],
            "window_end": row[12],
            "analyzed_at": row[13],
        }

    async def get_all_community_sentiment(
        self,
        canonical_key: str,
    ) -> List[Dict[str, Any]]:
        """
        Get community sentiment from all sources for a company.

        Args:
            canonical_key: Company canonical key

        Returns:
            List of sentiment dictionaries from each source
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT canonical_key, source,
                   mention_count, unique_authors,
                   avg_sentiment_score, sentiment_label,
                   positive_ratio, negative_ratio, neutral_ratio,
                   confidence_boost, top_keywords,
                   window_start, window_end,
                   analyzed_at
            FROM community_sentiment
            WHERE canonical_key = ?
            ORDER BY source
            """,
            (canonical_key,),
        )

        rows = await cursor.fetchall()
        return [
            {
                "canonical_key": row[0],
                "source": row[1],
                "mention_count": row[2],
                "unique_authors": row[3],
                "avg_sentiment_score": row[4],
                "sentiment_label": row[5],
                "positive_ratio": row[6],
                "negative_ratio": row[7],
                "neutral_ratio": row[8],
                "confidence_boost": row[9],
                "top_keywords": json.loads(row[10]) if row[10] else [],
                "window_start": row[11],
                "window_end": row[12],
                "analyzed_at": row[13],
            }
            for row in rows
        ]

    async def get_aggregate_community_boost(
        self,
        canonical_key: str,
    ) -> float:
        """
        Get total confidence boost from all community sources.

        Args:
            canonical_key: Company canonical key

        Returns:
            Sum of confidence boosts (capped at +0.10 / -0.15)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT COALESCE(SUM(confidence_boost), 0.0)
            FROM community_sentiment
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        )
        row = await cursor.fetchone()
        total_boost = row[0] if row else 0.0

        # Cap the total boost
        return max(-0.15, min(0.10, total_boost))

    async def get_community_mentions(
        self,
        canonical_key: str,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get individual community mentions for a company.

        Args:
            canonical_key: Company canonical key
            source: Optional specific source
            limit: Maximum number of mentions to return

        Returns:
            List of mention dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        if source:
            cursor = await self._db.execute(
                """
                SELECT id, canonical_key, source, source_id,
                       title, url, author,
                       sentiment_score, sentiment_label, sentiment_method,
                       keywords_found,
                       subreddit, channel_name, engagement_score,
                       posted_at, detected_at
                FROM community_mentions
                WHERE canonical_key = ? AND source = ?
                ORDER BY detected_at DESC, id DESC
                LIMIT ?
                """,
                (canonical_key, source, limit),
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT id, canonical_key, source, source_id,
                       title, url, author,
                       sentiment_score, sentiment_label, sentiment_method,
                       keywords_found,
                       subreddit, channel_name, engagement_score,
                       posted_at, detected_at
                FROM community_mentions
                WHERE canonical_key = ?
                ORDER BY detected_at DESC, id DESC
                LIMIT ?
                """,
                (canonical_key, limit),
            )

        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "canonical_key": row[1],
                "source": row[2],
                "source_id": row[3],
                "title": row[4],
                "url": row[5],
                "author": row[6],
                "sentiment_score": row[7],
                "sentiment_label": row[8],
                "sentiment_method": row[9],
                "keywords_found": json.loads(row[10]) if row[10] else [],
                "subreddit": row[11],
                "channel_name": row[12],
                "engagement_score": row[13],
                "posted_at": row[14],
                "detected_at": row[15],
            }
            for row in rows
        ]


# =============================================================================
# CONTEXT MANAGER FOR EASY USAGE
# =============================================================================

@asynccontextmanager
async def signal_store(
    db_path: str | Path | None = None,
    **kwargs
) -> AsyncIterator[SignalStore]:
    """
    Context manager for SignalStore that handles initialization and cleanup.

    When db_path is None, resolves via DISCOVERY_DB_PATH > SIGNAL_DB_PATH > "signals.db".

    Usage:
        async with signal_store() as store:
            await store.save_signal(...)
    """
    store = SignalStore(db_path, **kwargs)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

async def example_usage():
    """Demonstrate signal store usage."""

    # Use context manager for automatic cleanup
    async with signal_store("example_signals.db") as store:
        # Save a signal
        signal_id = await store.save_signal(
            signal_type="github_spike",
            source_api="github",
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            confidence=0.85,
            raw_data={
                "repo": "acme/awesome-ml",
                "stars": 1500,
                "recent_stars": 200,
                "topics": ["ai", "machine-learning"],
            }
        )
        print(f"Saved signal: {signal_id}")

        # Check for duplicates
        is_dup = await store.is_duplicate("domain:acme.ai")
        print(f"Is duplicate: {is_dup}")

        # Get pending signals
        pending = await store.get_pending_signals(limit=10)
        print(f"Pending signals: {len(pending)}")

        # Get signals for a company
        company_signals = await store.get_signals_for_company("domain:acme.ai")
        print(f"Signals for Acme: {len(company_signals)}")
        for sig in company_signals:
            print(f"  - {sig.signal_type} ({sig.confidence:.2f}) from {sig.source_api}")

        # Mark as pushed
        await store.mark_pushed(
            signal_id,
            notion_page_id="notion-abc-123",
            metadata={"status": "Source", "confidence": 0.85}
        )

        # Update suppression cache
        entries = [
            SuppressionEntry(
                canonical_key="domain:acme.ai",
                notion_page_id="notion-abc-123",
                status="Source",
                company_name="Acme Inc",
            )
        ]
        await store.update_suppression_cache(entries)

        # Check suppression
        suppressed = await store.check_suppression("domain:acme.ai")
        if suppressed:
            print(f"Suppressed: {suppressed.company_name} (Notion: {suppressed.notion_page_id})")

        # Get stats
        stats = await store.get_stats()
        print("\nDatabase stats:")
        print(f"  Total signals: {stats['total_signals']}")
        print(f"  By type: {stats['signals_by_type']}")
        print(f"  Processing status: {stats['processing_status']}")
        print(f"  Active cache entries: {stats['active_suppression_entries']}")


if __name__ == "__main__":
    # Run example
    import asyncio
    asyncio.run(example_usage())
