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

if TYPE_CHECKING:
    from workflows.pipeline import PipelineStats, CollectorMetrics
    from utils.exit_predictor import ExitPrediction

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA VERSION
# =============================================================================

CURRENT_SCHEMA_VERSION = 13

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
    """
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
        db_path: str | Path = "signals.db",
        suppression_ttl_days: int = 7,
    ):
        """
        Initialize signal store.

        Args:
            db_path: Path to SQLite database file
            suppression_ttl_days: How long to cache Notion entries before re-checking
        """
        self.db_path = Path(db_path)
        self.suppression_ttl_days = suppression_ttl_days
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

        # Enable foreign keys
        await self._db.execute("PRAGMA foreign_keys = ON")

        # Apply migrations
        await self._apply_migrations()

        # Create FTS5 virtual table for search
        await self._create_fts_table()

        # Create filter presets table
        await self._create_filter_presets_table()

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
    ) -> int:
        """
        Save a new signal to the database.

        Returns the signal ID.
        Raises IntegrityError if duplicate (same canonical_key, signal_type, source_api, detected_at).
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        detected_at = detected_at or datetime.now(timezone.utc)
        created_at = datetime.now(timezone.utc)

        async with self.transaction() as conn:
            # Insert signal
            cursor = await conn.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                )
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
                (signal_id, created_at.isoformat(), created_at.isoformat())
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
                s.detected_at, s.created_at,
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
                s.detected_at, s.created_at,
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
                s.detected_at, s.created_at,
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

    async def is_duplicate(self, canonical_key: str) -> bool:
        """
        Check if we already have signals for this canonical key.
        Returns True if any signals exist, False otherwise.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM signals WHERE canonical_key = ?",
            (canonical_key,)
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
                   s.detected_at, s.created_at,
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
            processing_status=row[9] if len(row) > 9 else None,
            notion_page_id=row[10] if len(row) > 10 else None,
            processed_at=parse_datetime(row[11]) if len(row) > 11 and row[11] else None,
            error_message=row[12] if len(row) > 12 else None,
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
                    errors, health_report, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        Returns:
            The inserted row ID
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

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
                    classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                   classified_at
            FROM thesis_classifications
            WHERE canonical_key = ?
            ORDER BY classified_at DESC
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
            "classified_at": row[19],
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


# =============================================================================
# CONTEXT MANAGER FOR EASY USAGE
# =============================================================================

@asynccontextmanager
async def signal_store(
    db_path: str | Path = "signals.db",
    **kwargs
) -> AsyncIterator[SignalStore]:
    """
    Context manager for SignalStore that handles initialization and cleanup.

    Usage:
        async with signal_store("signals.db") as store:
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
