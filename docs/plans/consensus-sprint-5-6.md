# Consensus Architecture: Sprint 5-6 (Investor Matching & Evaluation)

**Synthesized from:** Codex Iteration 1 + Claude Critical Review

## Executive Summary

This document represents consensus on Sprint 5 (Investor Matching v1) and Sprint 6 (Evaluation + Calibration) architecture, addressing all blocking issues identified during review.

---

## PART 1: MIGRATION 9 - INVESTOR MATCHING SCHEMA

### Complete SQL (extends signal_store.py MIGRATIONS dict)

```python
# Add to storage/signal_store.py MIGRATIONS dict
9: """
    -- Migration 9: Investor Matching (Sprint 5)

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

    -- 9.2: Portfolio edges (investor → company relationships)
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
        portfolio_count INTEGER NOT NULL,
        active_claim_count INTEGER NOT NULL,
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

    -- 9.6: Global baselines for lift calculation
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

    -- 9.7: FTS5 index for investor profile search
    CREATE VIRTUAL TABLE IF NOT EXISTS investor_profile_fts USING fts5(
        investor_id,
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
""",
```

### Evidence Independence Rules

```python
# Add to storage/signal_store.py or utils/investor_matching.py

class EvidenceIndependence:
    """
    Rules for determining if two pieces of evidence are independent.

    Evidence is INDEPENDENT if ANY of these conditions are met:
    1. Different source_api (crunchbase vs sec_edgar vs linkedin)
    2. Different source_signal_id (distinct original signals)
    3. Same source but dates > 30 days apart

    Evidence is DEPENDENT (same piece of info) if ALL of these are true:
    1. Same source_api
    2. Same or missing source_signal_id
    3. Dates within 30 days
    """

    MIN_INDEPENDENT_DATE_GAP_DAYS = 30

    @staticmethod
    def are_independent(extraction_a: dict, extraction_b: dict) -> bool:
        """Check if two claim_extractions are independent evidence."""

        # Rule 1: Different source APIs = independent
        if extraction_a.get('source_api') != extraction_b.get('source_api'):
            return True

        # Rule 2: Different source signals = independent
        sig_a = extraction_a.get('source_signal_id')
        sig_b = extraction_b.get('source_signal_id')
        if sig_a and sig_b and sig_a != sig_b:
            return True

        # Rule 3: Same source but significant time gap = independent
        date_a = extraction_a.get('extracted_at')
        date_b = extraction_b.get('extracted_at')
        if date_a and date_b:
            from datetime import datetime
            try:
                dt_a = datetime.fromisoformat(date_a.replace('Z', '+00:00'))
                dt_b = datetime.fromisoformat(date_b.replace('Z', '+00:00'))
                gap_days = abs((dt_a - dt_b).days)
                if gap_days >= EvidenceIndependence.MIN_INDEPENDENT_DATE_GAP_DAYS:
                    return True
            except (ValueError, TypeError):
                pass

        # Default: dependent (same evidence repeated)
        return False

    @staticmethod
    def count_independent_evidence(extractions: list[dict]) -> int:
        """
        Count independent evidence pieces from a list of extractions.

        Uses greedy clustering: each extraction is independent if it's
        independent from ALL previously counted extractions.
        """
        if not extractions:
            return 0

        independent = [extractions[0]]

        for ext in extractions[1:]:
            # Check if independent from all previously counted
            is_new = all(
                EvidenceIndependence.are_independent(ext, prev)
                for prev in independent
            )
            if is_new:
                independent.append(ext)

        return len(independent)

    # Minimum evidence thresholds for confidence levels
    THRESHOLDS = {
        'high': 3,      # 3+ independent sources
        'medium': 2,    # 2 independent sources
        'low': 1,       # 1 source (weak)
    }
```

---

## PART 2: GLOBAL BASELINES COMPUTATION

### Nightly Batch Job Pattern

```python
# utils/investor_profile_batch.py

"""
Investor Profile Batch Job

Nightly job to:
1. Compute global baselines from all portfolio data
2. Refresh investor profile claims with lift scores
3. Rebuild thesis embeddings
4. Update FTS index

Run via: python -m utils.investor_profile_batch
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("investor_profile_batch")


@dataclass
class BatchMetrics:
    """Metrics from batch run."""
    total_investors: int = 0
    profiles_updated: int = 0
    claims_refreshed: int = 0
    baselines_computed: int = 0
    embeddings_generated: int = 0
    cold_start_count: int = 0
    errors: List[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class InvestorProfileBatch:
    """
    Nightly batch job for investor profile maintenance.

    Mirrors similar_companies_batch.py pattern.
    """

    # Global baseline configuration
    BASELINE_SAMPLE_SOURCES = {
        'crunchbase_2y': {
            'description': 'Crunchbase companies from last 2 years',
            'query': "source = 'crunchbase' AND created_at > date('now', '-2 years')"
        },
        'portfolio_all': {
            'description': 'All companies in any investor portfolio',
            'query': "company_key IN (SELECT DISTINCT company_key FROM investor_portfolios)"
        },
    }

    BASELINE_PREDICATES = [
        'sector',       # fintech, health, cpg, etc.
        'stage',        # pre_seed, seed, series_a, etc.
        'geo',          # US, UK, EU, etc.
        'business_model', # b2c, marketplace, subscription, etc.
    ]

    # Lift threshold: only keep claims where lift > this value
    LIFT_THRESHOLD = 0.1  # log-odds units

    # Minimum portfolio size before computing claims
    MIN_PORTFOLIO_SIZE = 3

    # Cold-start threshold
    COLD_START_THRESHOLD = 3

    def __init__(self, store):
        """Initialize with SignalStore instance."""
        self.store = store
        self.metrics = BatchMetrics()

    async def run(self) -> BatchMetrics:
        """Execute full batch job."""
        logger.info("Starting investor profile batch job")

        try:
            # Step 1: Compute global baselines
            await self._compute_global_baselines()

            # Step 2: Refresh profile claims for all investors
            await self._refresh_all_profile_claims()

            # Step 3: Generate/update thesis embeddings
            await self._update_thesis_embeddings()

            # Step 4: Rebuild FTS index
            await self._rebuild_fts_index()

            self.metrics.completed_at = datetime.now(timezone.utc).isoformat()
            logger.info(f"Batch complete: {self.metrics}")

        except Exception as e:
            self.metrics.errors.append(str(e))
            logger.exception(f"Batch job failed: {e}")

        return self.metrics

    async def _compute_global_baselines(self) -> None:
        """
        Compute P(predicate=value) across the global company population.

        For each predicate (sector, stage, geo), count occurrences and
        compute probability. This is the denominator for lift calculation.
        """
        logger.info("Computing global baselines...")

        async with self.store._pool.acquire() as conn:
            for source_name, source_config in self.BASELINE_SAMPLE_SOURCES.items():
                for predicate in self.BASELINE_PREDICATES:
                    # Count total companies in sample
                    total_query = f"""
                        SELECT COUNT(DISTINCT entity_key)
                        FROM claims
                        WHERE {source_config['query']}
                    """
                    cursor = await conn.execute(total_query)
                    row = await cursor.fetchone()
                    total_n = row[0] if row else 0

                    if total_n == 0:
                        continue

                    # Count per value
                    value_query = f"""
                        SELECT value, COUNT(DISTINCT entity_key) as cnt
                        FROM claims
                        WHERE predicate = ? AND {source_config['query']}
                        GROUP BY value
                    """
                    cursor = await conn.execute(value_query, (predicate,))
                    rows = await cursor.fetchall()

                    for value, count in rows:
                        probability = count / total_n

                        # Upsert baseline
                        await conn.execute("""
                            INSERT INTO global_baselines
                                (predicate, value, global_probability, sample_size, sample_source, computed_at)
                            VALUES (?, ?, ?, ?, ?, datetime('now'))
                            ON CONFLICT(predicate, value, sample_source) DO UPDATE SET
                                global_probability = excluded.global_probability,
                                sample_size = excluded.sample_size,
                                computed_at = excluded.computed_at
                        """, (predicate, value, probability, total_n, source_name))

                        self.metrics.baselines_computed += 1

            await conn.commit()

        logger.info(f"Computed {self.metrics.baselines_computed} baselines")

    async def _compute_lift_score(
        self,
        predicate: str,
        value: str,
        investor_probability: float
    ) -> float:
        """
        Compute lift score: log( P(value|investor) / P(value|global) )

        Positive lift = investor overweights this value
        Negative lift = investor underweights this value
        """
        import math

        EPS = 1e-6  # Avoid log(0)

        async with self.store._pool.acquire() as conn:
            cursor = await conn.execute("""
                SELECT global_probability
                FROM global_baselines
                WHERE predicate = ? AND value = ? AND sample_source = 'crunchbase_2y'
            """, (predicate, value))
            row = await cursor.fetchone()

            global_prob = row[0] if row else EPS

        lift = math.log((investor_probability + EPS) / (global_prob + EPS))
        return lift

    async def _refresh_all_profile_claims(self) -> None:
        """Refresh profile claims for all investors."""
        logger.info("Refreshing investor profile claims...")

        async with self.store._pool.acquire() as conn:
            # Get all investors with portfolio data
            cursor = await conn.execute("""
                SELECT i.id, i.canonical_key, COUNT(ip.id) as portfolio_size
                FROM investors i
                LEFT JOIN investor_portfolios ip ON i.id = ip.investor_id
                GROUP BY i.id
            """)
            investors = await cursor.fetchall()

            self.metrics.total_investors = len(investors)

            for inv_id, canonical_key, portfolio_size in investors:
                is_cold_start = portfolio_size < self.COLD_START_THRESHOLD

                if is_cold_start:
                    self.metrics.cold_start_count += 1
                    continue  # Skip claim generation for cold-start

                await self._refresh_investor_claims(conn, inv_id, portfolio_size)
                self.metrics.profiles_updated += 1

            await conn.commit()

        logger.info(f"Updated {self.metrics.profiles_updated} profiles, {self.metrics.cold_start_count} cold-start")

    async def _refresh_investor_claims(self, conn, investor_id: str, portfolio_size: int) -> None:
        """Generate profile claims for a single investor from portfolio behavior."""

        for predicate in self.BASELINE_PREDICATES:
            # Count portfolio companies by predicate value
            cursor = await conn.execute("""
                SELECT c.value, COUNT(DISTINCT ip.company_key) as cnt,
                       GROUP_CONCAT(ip.company_key || ':' || COALESCE(ip.extraction_id, ''), '|') as evidence
                FROM investor_portfolios ip
                JOIN claims c ON ip.company_key = c.entity_key
                WHERE ip.investor_id = ? AND c.predicate = ? AND c.status = 'active'
                GROUP BY c.value
            """, (investor_id, predicate))
            rows = await cursor.fetchall()

            for value, count, evidence_str in rows:
                investor_prob = count / portfolio_size
                lift = await self._compute_lift_score(predicate, value, investor_prob)

                # Only keep claims with significant lift
                if lift < self.LIFT_THRESHOLD:
                    continue

                # Parse evidence
                evidence_json = []
                if evidence_str:
                    for item in evidence_str.split('|'):
                        parts = item.split(':')
                        if len(parts) >= 2:
                            evidence_json.append({
                                'company_key': parts[0],
                                'extraction_id': parts[1] if parts[1] else None
                            })

                # Upsert claim
                import json
                await conn.execute("""
                    INSERT INTO investor_profile_claims
                        (investor_id, predicate, value, confidence, lift_score, support_count, support_evidence, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'))
                    ON CONFLICT(investor_id, predicate, value) DO UPDATE SET
                        confidence = excluded.confidence,
                        lift_score = excluded.lift_score,
                        support_count = excluded.support_count,
                        support_evidence = excluded.support_evidence,
                        status = 'active',
                        updated_at = datetime('now')
                """, (
                    investor_id, predicate, value,
                    min(0.5 + lift * 0.1, 0.95),  # Convert lift to confidence
                    lift, count, json.dumps(evidence_json)
                ))

                self.metrics.claims_refreshed += 1

    async def _update_thesis_embeddings(self) -> None:
        """Generate thesis embeddings from profile claims."""
        # Similar to similar_companies_batch.py pattern
        # Build claim text → generate embedding → store
        logger.info("Updating thesis embeddings...")
        # Implementation follows company_embeddings pattern

    async def _rebuild_fts_index(self) -> None:
        """Rebuild FTS5 index from profile claims."""
        logger.info("Rebuilding FTS index...")

        async with self.store._pool.acquire() as conn:
            # Clear existing
            await conn.execute("DELETE FROM investor_profile_fts")

            # Rebuild from claims
            cursor = await conn.execute("""
                SELECT investor_id, GROUP_CONCAT(predicate || ':' || value, ' ') as claim_text
                FROM investor_profile_claims
                WHERE status = 'active'
                GROUP BY investor_id
            """)
            rows = await cursor.fetchall()

            for investor_id, claim_text in rows:
                await conn.execute("""
                    INSERT INTO investor_profile_fts (investor_id, claim_text)
                    VALUES (?, ?)
                """, (investor_id, claim_text))

            await conn.commit()
```

---

## PART 3: FEATURE FLAG + PIPELINE INTEGRATION

### Environment Variable

```python
# Add to CLAUDE.md Environment Variables section
ENABLE_INVESTOR_MATCHING=false  # Feature flag (default: disabled)
```

### Pipeline Integration (workflows/pipeline.py)

```python
# Add after exit_predictor stage (~line 850)

# Investor matching (Sprint 5)
if self.use_investor_matching:
    await self._match_investors(qualified_signals)

# New method:
async def _match_investors(self, signals: List[ProspectPayload]) -> None:
    """
    Match qualified signals to relevant investors.

    Runs after exit_predictor, adds investor recommendations.
    Feature-flagged via ENABLE_INVESTOR_MATCHING.
    """
    if not signals:
        return

    from utils.investor_matching import InvestorMatcher

    matcher = InvestorMatcher(self.store)

    for signal in signals:
        try:
            matches = await matcher.match(
                company_key=signal.canonical_key,
                company_claims=signal.claims,
                company_embedding=signal.embedding,
                top_n=10
            )

            # Store matches
            await self.store.save_investor_matches(
                signal.canonical_key,
                matches
            )

            # Add to payload for Notion push
            signal.investor_matches = matches[:5]  # Top 5 for Notion

            self.metrics['investor_matches_generated'] += 1

        except Exception as e:
            logger.warning(f"Investor matching failed for {signal.canonical_key}: {e}")
            self.metrics['investor_match_errors'] += 1

# Constructor addition:
def __init__(self, ...):
    ...
    self.use_investor_matching = os.getenv('ENABLE_INVESTOR_MATCHING', 'false').lower() == 'true'
```

---

## PART 4: SPRINT 6 - EVALUATION SCHEMA

### Gold Set Tables

```sql
-- Add to Migration 9 (or Migration 10)

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

CREATE TABLE IF NOT EXISTS gold_set_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES gold_set_companies(id),
    predicate TEXT NOT NULL,                -- problem|customer|sector|stage|geo
    label_type TEXT NOT NULL,               -- exact|partial|incorrect|abstain
    gold_value TEXT,                        -- Ground truth value
    annotator TEXT NOT NULL,
    confidence TEXT DEFAULT 'high',         -- high|medium|low
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, predicate, annotator)
);

CREATE TABLE IF NOT EXISTS gold_set_investor_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES gold_set_companies(id),
    investor_id TEXT NOT NULL REFERENCES investors(id),
    relevance TEXT NOT NULL,                -- relevant|partial|irrelevant
    annotator TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, investor_id, annotator)
);

-- 9.10: Evaluation runs
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,                 -- extraction|similarity|investor_match
    model_version TEXT NOT NULL,
    embedding_version TEXT,
    gold_set_version TEXT NOT NULL,
    metrics TEXT NOT NULL,                  -- JSON: {f1, precision, recall, abstention_rate, ...}
    config TEXT,                            -- JSON: run configuration
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_type ON evaluation_runs(run_type, created_at DESC);

-- 9.11: Drift alerts
CREATE TABLE IF NOT EXISTS drift_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,               -- extraction_f1_drop|abstention_spike|similarity_recall_drop|confidence_collapse
    severity TEXT NOT NULL,                 -- red|yellow
    metric_name TEXT NOT NULL,
    baseline_value REAL NOT NULL,
    current_value REAL NOT NULL,
    threshold REAL NOT NULL,
    evaluation_run_id INTEGER REFERENCES evaluation_runs(id),
    acknowledged INTEGER DEFAULT 0,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drift_alerts_unacked ON drift_alerts(acknowledged, severity, created_at DESC);
```

### Drift Detection Thresholds

```python
# utils/drift_detector.py

class DriftDetector:
    """Detect quality regressions in extraction and matching."""

    # Thresholds (from Codex proposal, validated)
    THRESHOLDS = {
        # Extraction metrics
        'extraction_f1_drop': {
            'metric': 'extraction_f1',
            'comparison': 'vs_baseline',
            'threshold': 5.0,  # 5 points drop
            'severity': 'red',
        },
        'abstention_rate_spike': {
            'metric': 'abstention_rate',
            'comparison': 'absolute',
            'threshold': 25.0,  # > 25% abstention
            'severity': 'red',
        },
        'abstention_rate_increase': {
            'metric': 'abstention_rate',
            'comparison': 'vs_baseline',
            'threshold': 8.0,  # 8 point increase
            'severity': 'yellow',
        },

        # Similarity metrics
        'top10_recall_drop': {
            'metric': 'top10_recall',
            'comparison': 'vs_baseline',
            'threshold': 7.0,  # 7 points drop
            'severity': 'red',
        },
        'top10_recall_absolute': {
            'metric': 'top10_recall',
            'comparison': 'absolute_min',
            'threshold': 60.0,  # Below 60%
            'severity': 'red',
        },

        # Confidence metrics
        'confidence_collapse': {
            'metric': 'median_confidence',
            'comparison': 'absolute_min',
            'threshold': 55.0,  # Below 55%
            'consecutive_runs': 3,  # Must fail 3x
            'severity': 'red',
        },

        # Match score drift
        'match_score_ks_test': {
            'metric': 'match_score_distribution',
            'comparison': 'ks_test',
            'threshold': 0.01,  # p < 0.01
            'severity': 'yellow',
        },
    }

    @staticmethod
    def check_threshold(
        current: float,
        baseline: float,
        threshold_config: dict
    ) -> tuple[bool, str]:
        """
        Check if metric breaches threshold.

        Returns (is_alert, reason)
        """
        comparison = threshold_config['comparison']
        threshold = threshold_config['threshold']

        if comparison == 'vs_baseline':
            diff = baseline - current
            if diff > threshold:
                return True, f"Dropped {diff:.1f} points vs baseline"

        elif comparison == 'absolute':
            if current > threshold:
                return True, f"Exceeds {threshold}% threshold"

        elif comparison == 'absolute_min':
            if current < threshold:
                return True, f"Below {threshold}% minimum"

        return False, ""
```

---

## PART 5: TEST STRATEGY

### Test Categories

| Category | Count | Focus |
|----------|-------|-------|
| Unit: Portfolio ingestion | 15 | Normalization, dedup, dates |
| Unit: Profile claims | 20 | Lift calculation, thresholds |
| Unit: Evidence independence | 10 | Independence rules |
| Unit: Matching algorithm | 15 | Scoring, explanations |
| Integration: End-to-end | 10 | Ingest → profile → match |
| Regression: Gold set | 5 | Stability on fixed cohort |
| Drift: Alert triggers | 8 | Threshold detection |

### Key Test Files

```
tests/
  utils/
    test_investor_matching.py      # Core matching tests
    test_investor_profile_batch.py # Batch job tests
    test_evidence_independence.py  # Evidence rules
    test_drift_detector.py         # Drift detection
  evaluation/
    test_gold_set.py              # Gold set management
    test_evaluator.py             # Metrics computation
  integration/
    test_investor_pipeline.py     # E2E pipeline tests
```

---

## RISKS AND MITIGATIONS

| Risk | Severity | Mitigation |
|------|----------|------------|
| Portfolio data bias (Crunchbase coverage) | HIGH | Confidence-weighted claims; curated JSON supplements |
| Cold-start investors | MEDIUM | is_cold_start flag; fallback to preferences |
| Overfitting to common sectors | MEDIUM | Lift-based filtering, require support_count >= 3 |
| Explanation quality regression | MEDIUM | Evidence independence rules; abstain if < 2 sources |
| Embedding model drift | LOW | Lock version for gold set; A/B on new models |
| FTS index corruption | LOW | Atomic rebuild via shadow table pattern |

---

## CONSENSUS SUMMARY

**Agreed Points:**
1. Migration 9 extends signal_store.py with 8 new tables + FTS5
2. Global baselines computed nightly from Crunchbase 2-year window
3. Lift score filtering (> 0.1) prevents common-sector overfitting
4. Evidence independence requires different source_api OR 30+ day gap
5. Feature-flagged via ENABLE_INVESTOR_MATCHING
6. Pipeline integration after exit_predictor stage
7. Gold set: 120-220 companies with 4 categories
8. Drift thresholds: F1 drop > 5pts, abstention > 25%

**Remaining Work:**
- Concrete Python implementation files
- Test suite implementation
- Notion field mapping for investor recommendations
