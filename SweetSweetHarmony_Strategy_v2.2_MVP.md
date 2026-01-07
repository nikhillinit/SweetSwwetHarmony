# SweetSweetHarmony: Consumer Deal Discovery Engine
## Strategy Proposal v2.2 - Lean MVP Edition

**Version:** 2.2  
**Last Updated:** January 6, 2026  
**Status:** Ready for Implementation  
**Author:** Investment Team  

---

## Document Change Log

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | -- | Initial proposal |
| 2.0 | 2026-01-06 | Integrated critical fixes from technical review |
| 2.1 | 2026-01-06 | Corrected encoding, costs, thesis scope, added Serper backup |
| 2.2 | 2026-01-06 | **Lean MVP:** Notion-as-UI, LLM classifier, process-and-discard Reddit, simplified architecture |

---

## Executive Summary

### What Changed in v2.2

This revision reduces scope by **50-60 hours** while preserving core value:

| Change | Hours Saved | Risk Reduced |
|--------|-------------|--------------|
| Notion-as-UI (remove custom Web UI) | 24-28h | Eliminates SQLite concurrency issues |
| LLM classifier (replace weighted scoring) | 8-10h | Removes dependency on uncollected data |
| Process-and-discard Reddit | 2-4h | Eliminates purge job complexity |
| Single search provider (Tavily only) | 2-3h | Simplifies codebase |
| Fixed content hash scheme | 1h | Prevents edge-case dedup bugs |

### Revised Metrics

| Metric | v2.1 | v2.2 MVP | Change |
|--------|------|----------|--------|
| Total Hours | 148-182h | 98-122h | -50-60h |
| Timeline (25h/wk) | 6-7.5 weeks | 4-5 weeks | -2-2.5 weeks |
| Monthly Cost | $5-10 | $10-18 | +$5-8 (LLM classification) |
| SQLite Concurrency Risk | Medium | None | Eliminated |
| Compliance Complexity | Medium | Low | Simplified |

### What This System Does

1. **Collects signals** from HN, BevNet RSS, USPTO trademarks, Reddit (links only)
2. **Filters candidates** using hard disqualifiers + LLM classification
3. **Pushes qualified leads** to Notion Inbox for human review
4. **Captures feedback** from Notion status changes
5. **Provides on-demand research** via /research command

### What This System Does NOT Do (Deferred)

- Custom Web UI (use Notion instead)
- Weighted multi-signal scoring (use LLM instead)
- Search API failover (single provider sufficient for MVP)
- Reddit full-body storage (process and discard)
- Accelerator batch scraping (manual for now)

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Thesis Filter Specification](#2-thesis-filter-specification)
3. [Data Schema](#3-data-schema)
4. [Collector Specifications](#4-collector-specifications)
5. [Notion Integration](#5-notion-integration)
6. [AI Research Agent](#6-ai-research-agent)
7. [Phased Roadmap](#7-phased-roadmap)
8. [Compliance and Risk](#8-compliance-and-risk)
9. [Cost Analysis](#9-cost-analysis)
10. [Testing Strategy](#10-testing-strategy)
11. [Decision Log](#11-decision-log)
12. [Appendices](#12-appendices)

---

## 1. Architecture

### 1.1 High-Level Architecture (Headless Pipeline)

```
+------------------------------------------------------------------+
|                      SEEDER LAYER (Python)                        |
|  +-------------+  +-------------+  +-------------+  +-----------+ |
|  | HN Algolia  |  | BevNet RSS  |  | USPTO TM    |  | Reddit    | |
|  | (non-SLA)   |  | (reliable)  |  | (bulk data) |  | (links)   | |
|  +------+------+  +------+------+  +------+------+  +-----+-----+ |
+---------+----------------+----------------+---------------+-------+
          |                |                |               |
          v                v                v               v
+------------------------------------------------------------------+
|                    INTELLIGENCE LAYER (Python)                    |
|                                                                   |
|  +------------------+    +------------------+    +-------------+  |
|  | Dedup Engine     |    | Thesis Filter    |    | AI Research |  |
|  | (source_api +    | -> | (Hard disqual +  | -> | Agent       |  |
|  |  source_id hash) |    |  LLM classify)   |    | (on-demand) |  |
|  +------------------+    +------------------+    +-------------+  |
+------------------------------+------------------------------------+
                               |
                               v
+------------------------------------------------------------------+
|                     STORAGE LAYER (Python-only access)            |
|  +--------------------------------------------------------------+ |
|  |  SQLite (/data/discovery.db)                                 | |
|  |  - signals (deduped, links only, no full body)               | |
|  |  - companies (canonical)                                      | |
|  |  - user_actions (synced from Notion)                         | |
|  |  - collector_runs (health monitoring)                        | |
|  |  - llm_classifications (audit trail)                         | |
|  +--------------------------------------------------------------+ |
+------------------------------+------------------------------------+
                               |
                               v
+------------------------------------------------------------------+
|                     NOTION CRM (UI Layer)                         |
|                                                                   |
|  +------------------+         +------------------+                 |
|  | Discovery Inbox  |  --->   | Deal Pipeline    |                 |
|  | - New leads      |         | - Qualified      |                 |
|  | - Pending review |         | - In progress    |                 |
|  | - Status prop    |         | - Closed         |                 |
|  | - Reject reason  |         |                  |                 |
|  +------------------+         +------------------+                 |
|           ^                                                       |
|           |                                                       |
|  Python Poller: reads status changes, writes user_actions         |
+------------------------------------------------------------------+
```

### 1.2 Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **Python-only pipeline** | Eliminates SQLite concurrency issues from mixed Node/Python |
| **Notion-as-UI** | Zero custom UI code; reviewers use familiar tool |
| **LLM classification** | Works on raw text; doesn't require specific data fields |
| **Process-and-discard** | No compliance-sensitive data storage |
| **Single search provider** | Batch tool can retry if provider is down |

### 1.3 Data Flow

```
1. COLLECT
   Seeder runs (scheduled) --> Raw signals with URLs/titles
   
2. DEDUP
   Check content_hash --> Skip if duplicate, update last_seen_at
   
3. FILTER (Stage 1: Hard Disqualifiers)
   Check keywords --> AUTO_REJECT if B2B/biotech/cannabis/etc.
   
4. CLASSIFY (Stage 2: LLM)
   Send to GPT-4o-mini --> {score, category, rationale, confidence}
   Store classification in llm_classifications table
   
5. ROUTE
   score >= 0.5 --> Push to Notion Inbox (status: "Pending Review")
   score < 0.5  --> Store as rejected, don't push to Notion
   
6. REVIEW (Human in Notion)
   Reviewer sets Status: "Approved" | "Rejected"
   If rejected, sets Rejection Reason property
   
7. FEEDBACK CAPTURE
   Python poller reads Notion changes
   Writes to user_actions table
   Updates suppression rules (optional)
```

---

## 2. Thesis Filter Specification

### 2.1 Overview

The thesis filter uses a **two-stage approach**:

1. **Hard Disqualifiers** (deterministic, fast, free)
2. **LLM Classifier** (handles gray area, ~$0.002/signal)

This replaces the weighted scoring model from v2.1, which required data fields we don't actually collect.

### 2.2 Stage 1: Hard Disqualifiers

```python
# thesis_filter/hard_disqualifiers.py

HARD_DISQUALIFIERS = {
    # B2B / Enterprise (immediate reject)
    "b2b": ["b2b saas", "enterprise software", "api platform", "developer tools",
            "devops", "infrastructure", "salesforce", "hubspot integration"],
    
    # Biotech / Pharma (immediate reject)
    "biotech": ["biotech", "pharmaceutical", "clinical trial", "fda approval",
                "medical device", "healthcare software", "drug discovery"],
    
    # Cannabis (regulatory complexity)
    "cannabis": ["cannabis", "marijuana", "thc", "cbd", "dispensary"],
    
    # Other exclusions
    "excluded": ["consulting firm", "agency services", "staffing", 
                 "real estate", "cryptocurrency", "nft", "web3"]
}

HARD_DISQUALIFIER_FLAT = []
for category, terms in HARD_DISQUALIFIERS.items():
    HARD_DISQUALIFIER_FLAT.extend(terms)


def check_hard_disqualifiers(text: str) -> tuple[bool, str | None]:
    """
    Check if text contains any hard disqualifiers.
    
    Returns:
        (is_disqualified, matched_term)
    """
    text_lower = text.lower()
    
    for term in HARD_DISQUALIFIER_FLAT:
        if term in text_lower:
            # Find which category it belongs to
            for category, terms in HARD_DISQUALIFIERS.items():
                if term in terms:
                    return True, f"{category}:{term}"
            return True, term
    
    return False, None
```

### 2.3 Stage 2: LLM Classifier

```python
# thesis_filter/llm_classifier.py

from openai import OpenAI
from typing import Dict, Any
import json

CLASSIFIER_SYSTEM_PROMPT = """You are a venture capital analyst evaluating companies for a consumer-focused fund.

The fund invests in:
- Consumer Health Tech (smart devices, health monitoring, wellness tech)
- Consumer CPG (packaged food, beverages, snacks, personal care)
- Travel & Hospitality (travel booking, hospitality ops, tourism tech)
- Consumer Marketplaces (community platforms, peer-to-peer services)

Stage focus: Pre-seed to Series A, <$10M raised, <3 years old

You will receive a signal (title + URL + source context) about a potential company.

Respond with JSON only:
{
    "company_name": "extracted or inferred company name",
    "thesis_fit_score": 0.0-1.0,
    "category": "consumer_health_tech|consumer_cpg|travel_hospitality|consumer_marketplace|adjacent|not_consumer",
    "stage_estimate": "pre_seed|seed|series_a|later_stage|unknown",
    "rationale": "1-2 sentence explanation",
    "confidence": "high|medium|low",
    "key_signals": ["list", "of", "detected", "signals"]
}

Scoring guide:
- 0.8-1.0: Strong fit (clear consumer company in target category with traction signals)
- 0.6-0.8: Good fit (consumer company, may lack traction signals)
- 0.4-0.6: Possible fit (consumer-adjacent or unclear)
- 0.2-0.4: Weak fit (likely not a match but not obviously excluded)
- 0.0-0.2: Poor fit (wrong category, wrong stage, or excluded type)

Be concise. Focus on investment relevance."""

CLASSIFIER_PROMPT_VERSION = "v2.2.1"


class LLMClassifier:
    def __init__(self):
        self.client = OpenAI()
        self.model = "gpt-4o-mini"
    
    def classify(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Classify a signal using LLM.
        
        Args:
            signal_data: {title, url, source_api, raw_context}
        
        Returns:
            Classification result with score, category, rationale
        """
        user_prompt = f"""Evaluate this signal:

Title: {signal_data.get('title', 'N/A')}
URL: {signal_data.get('url', 'N/A')}
Source: {signal_data.get('source_api', 'unknown')}
Context: {signal_data.get('raw_context', 'N/A')[:500]}

Respond with JSON classification."""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,  # Low temperature for consistency
            response_format={"type": "json_object"},
            max_tokens=300
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Add metadata for audit trail
        result["prompt_version"] = CLASSIFIER_PROMPT_VERSION
        result["model"] = self.model
        result["tokens_used"] = response.usage.total_tokens
        
        return result
```

### 2.4 Combined Filter Pipeline

```python
# thesis_filter/pipeline.py

from .hard_disqualifiers import check_hard_disqualifiers
from .llm_classifier import LLMClassifier
from typing import Dict, Any
from enum import Enum

class FilterResult(Enum):
    AUTO_REJECT = "auto_reject"      # Hard disqualifier matched
    LLM_REJECT = "llm_reject"        # LLM score < 0.5
    LLM_REVIEW = "llm_review"        # LLM score >= 0.5, needs human review
    LLM_AUTO_APPROVE = "llm_auto"    # LLM score >= 0.85, high confidence

REVIEW_THRESHOLD = 0.50
AUTO_APPROVE_THRESHOLD = 0.85


class ThesisFilterPipeline:
    def __init__(self):
        self.llm_classifier = LLMClassifier()
    
    def filter(self, signal_data: Dict[str, Any]) -> tuple[FilterResult, Dict[str, Any]]:
        """
        Run signal through thesis filter pipeline.
        
        Returns:
            (result_type, classification_data)
        """
        # Combine text fields for analysis
        text = f"{signal_data.get('title', '')} {signal_data.get('raw_context', '')}"
        
        # Stage 1: Hard disqualifiers (free, fast)
        is_disqualified, matched_term = check_hard_disqualifiers(text)
        
        if is_disqualified:
            return FilterResult.AUTO_REJECT, {
                "thesis_fit_score": 0.0,
                "category": "excluded",
                "rationale": f"Hard disqualifier matched: {matched_term}",
                "confidence": "high",
                "filter_stage": "hard_disqualifier"
            }
        
        # Stage 2: LLM classification (~$0.002 per call)
        classification = self.llm_classifier.classify(signal_data)
        classification["filter_stage"] = "llm_classifier"
        
        score = classification.get("thesis_fit_score", 0.0)
        confidence = classification.get("confidence", "low")
        
        # Route based on score and confidence
        if score >= AUTO_APPROVE_THRESHOLD and confidence == "high":
            return FilterResult.LLM_AUTO_APPROVE, classification
        elif score >= REVIEW_THRESHOLD:
            return FilterResult.LLM_REVIEW, classification
        else:
            return FilterResult.LLM_REJECT, classification
```

### 2.5 Classification Audit Trail

All LLM classifications are stored for debugging and prompt improvement:

```sql
CREATE TABLE llm_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    
    -- Classification result
    thesis_fit_score REAL NOT NULL,
    category TEXT,
    stage_estimate TEXT,
    rationale TEXT,
    confidence TEXT,
    key_signals JSONB,
    
    -- Audit fields
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_used INTEGER,
    raw_response JSONB,
    
    -- Result
    filter_result TEXT NOT NULL,  -- 'auto_reject', 'llm_reject', 'llm_review', 'llm_auto'
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_llm_class_signal ON llm_classifications(signal_id);
CREATE INDEX idx_llm_class_score ON llm_classifications(thesis_fit_score);
CREATE INDEX idx_llm_class_version ON llm_classifications(prompt_version);
```

---

## 3. Data Schema

### 3.1 Core Tables

```sql
-- =====================================================
-- SIGNALS TABLE
-- Stores raw signals from all collectors (links only, no full body)
-- =====================================================
CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Source identification (used for idempotency)
    source_api TEXT NOT NULL,           -- 'reddit', 'hn', 'bevnet_rss', 'uspto_tm'
    source_id TEXT NOT NULL,            -- Original ID from source (immutable)
    signal_type TEXT NOT NULL,          -- 'mention', 'launch', 'trademark', etc.
    
    -- Idempotency (FIXED in v2.2: use source identifiers only)
    content_hash TEXT NOT NULL,         -- SHA256(source_api|source_id)[:32]
    
    -- Core content (links only - no full body storage)
    title TEXT,
    url TEXT,
    source_context TEXT,                -- Brief excerpt or summary (max 500 chars)
    raw_metadata JSONB,                 -- Source-specific metadata (scores, dates)
    
    -- Entity reference (nullable until matched)
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    extracted_company_name TEXT,        -- From LLM or regex
    
    -- Filter result
    filter_result TEXT,                 -- 'auto_reject', 'llm_reject', 'llm_review', 'llm_auto'
    filter_stage TEXT,                  -- 'hard_disqualifier', 'llm_classifier'
    
    -- Review status
    status TEXT DEFAULT 'pending',      -- 'pending', 'in_notion', 'approved', 'rejected'
    notion_page_id TEXT,                -- If pushed to Notion
    
    -- Timestamps
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Idempotency constraint (FIXED: based on immutable source IDs)
CREATE UNIQUE INDEX idx_signals_idempotent 
    ON signals(source_api, source_id);

CREATE INDEX idx_signals_status ON signals(status);
CREATE INDEX idx_signals_filter ON signals(filter_result);
CREATE INDEX idx_signals_company ON signals(company_id);


-- =====================================================
-- COMPANIES TABLE
-- Canonical company records
-- =====================================================
CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Canonical identification
    canonical_key TEXT UNIQUE NOT NULL, -- Normalized: lowercase(name) + domain
    
    -- Core fields
    name TEXT NOT NULL,
    domain TEXT,
    description TEXT,
    
    -- Classification (from LLM)
    category TEXT,
    stage TEXT,
    
    -- Aggregated data
    headquarters_location TEXT,
    founding_year INTEGER,
    
    -- Status
    overall_status TEXT DEFAULT 'candidate',
    notion_page_id TEXT,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_companies_status ON companies(overall_status);


-- =====================================================
-- USER_ACTIONS TABLE
-- Tracks human decisions (synced from Notion)
-- =====================================================
CREATE TABLE user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- What was acted on
    signal_id INTEGER REFERENCES signals(id) ON DELETE CASCADE,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    notion_page_id TEXT,
    
    -- Action details
    action TEXT NOT NULL,               -- 'approve', 'reject', 'defer'
    
    -- For rejections
    rejection_reason TEXT,              -- From Notion property
    rejection_notes TEXT,
    
    -- Context
    thesis_score_at_action REAL,
    
    -- Sync metadata
    synced_from_notion_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_actions_signal ON user_actions(signal_id);
CREATE INDEX idx_user_actions_action ON user_actions(action, created_at);


-- =====================================================
-- COLLECTOR_RUNS TABLE
-- Health monitoring
-- =====================================================
CREATE TABLE collector_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    collector_name TEXT NOT NULL,
    
    status TEXT NOT NULL,               -- 'success', 'partial_failure', 'failure'
    signals_found INTEGER DEFAULT 0,
    signals_new INTEGER DEFAULT 0,
    
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    duration_seconds REAL,
    
    error_message TEXT,
    
    api_calls_made INTEGER DEFAULT 0
);

CREATE INDEX idx_collector_runs_name ON collector_runs(collector_name, started_at);


-- =====================================================
-- COST_TRACKING TABLE
-- API usage monitoring
-- =====================================================
CREATE TABLE cost_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    service TEXT NOT NULL,              -- 'openai', 'tavily', 'notion'
    operation TEXT NOT NULL,            -- 'classify', 'search', 'create_page'
    
    units_consumed INTEGER DEFAULT 1,
    estimated_cost_usd REAL,
    
    triggered_by TEXT,
    related_signal_id INTEGER,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cost_tracking_service ON cost_tracking(service, created_at);
```

### 3.2 Idempotency Implementation (FIXED)

```python
# utils/deduplication.py

import hashlib
from typing import Dict, Any, Tuple

def generate_content_hash(signal_data: Dict[str, Any]) -> str:
    """
    Generate stable fingerprint for signal deduplication.
    
    FIXED in v2.2:
    - Uses only immutable source identifiers
    - Longer hash (32 chars instead of 16)
    - No dependency on extracted entity names
    """
    # Use only immutable identifiers from the source
    fingerprint_parts = [
        signal_data.get('source_api', ''),
        signal_data.get('source_id', ''),  # Reddit post ID, HN item ID, RSS GUID
    ]
    
    fingerprint = '|'.join(str(p).strip() for p in fingerprint_parts)
    
    # Use 32 chars (128 bits) - collision-resistant for billions of records
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:32]


async def upsert_signal(session, signal_data: Dict[str, Any]) -> Tuple[int, bool]:
    """
    Insert new signal or update last_seen_at for existing.
    
    Returns:
        (signal_id, is_new)
    """
    content_hash = generate_content_hash(signal_data)
    
    # Check for existing (use unique index on source_api, source_id)
    existing = await session.execute(
        """SELECT id FROM signals 
           WHERE source_api = ? AND source_id = ?""",
        (signal_data['source_api'], signal_data['source_id'])
    )
    row = existing.fetchone()
    
    if row:
        # Update last_seen_at
        await session.execute(
            """UPDATE signals SET last_seen_at = CURRENT_TIMESTAMP, 
               updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (row[0],)
        )
        await session.commit()
        return row[0], False
    
    else:
        # Insert new signal
        cursor = await session.execute(
            """INSERT INTO signals 
               (source_api, source_id, signal_type, content_hash, title, url, 
                source_context, raw_metadata, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                signal_data['source_api'],
                signal_data['source_id'],
                signal_data.get('signal_type', 'mention'),
                content_hash,
                signal_data.get('title'),
                signal_data.get('url'),
                signal_data.get('source_context', '')[:500],  # Truncate
                json.dumps(signal_data.get('raw_metadata', {}))
            )
        )
        await session.commit()
        return cursor.lastrowid, True
```

---

## 4. Collector Specifications

### 4.1 Collector Status Matrix

| Collector | Phase | Status | Data Stored | Notes |
|-----------|-------|--------|-------------|-------|
| HN Algolia | 3 | Planned | Link + title + score | Non-SLA, graceful degradation |
| BevNet/NOSH RSS | 3 | Planned | Link + title + excerpt | Public RSS, reliable |
| USPTO Trademark | 3 | Planned | Link + mark + class | Official data, high quality |
| Reddit | 3 | Planned | Link + title only | **No body storage** (v2.2 change) |
| Product Hunt | -- | Manual | N/A | TOS prohibits commercial use |
| LinkedIn | -- | Not Planned | N/A | Use Google search via research agent |

### 4.2 Reddit Collector (Process-and-Discard)

**v2.2 Change:** No longer stores post body. Process immediately, store only link + title.

```python
# collectors/reddit_collector.py

import praw
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class RedditCollector:
    """
    Reddit collector with process-and-discard pattern.
    
    v2.2: No body storage. We extract what we need immediately
    and store only the link + title. Reviewers click through
    to Reddit if they need more context.
    
    This eliminates:
    - Compliance complexity (no 48h purge job needed)
    - Data retention concerns
    - Storage overhead
    """
    
    MONITORED_SUBREDDITS = [
        "entrepreneur",
        "smallbusiness", 
        "startups",
        "FoodStartups",
        "DTC",
        "ConsumerGoods",
    ]
    
    CONSUMER_KEYWORDS = [
        "cpg", "consumer packaged goods", "food brand", "beverage brand",
        "dtc brand", "direct to consumer", "launched product",
        "whole foods", "target", "grocery", "retail",
        "wellness", "supplements", "personal care",
    ]
    
    def __init__(self, reddit_client: praw.Reddit):
        self.reddit = reddit_client
    
    def collect_signals(self) -> List[Dict[str, Any]]:
        """
        Collect signals from monitored subreddits.
        
        Returns only links + titles + brief context (no full body).
        """
        signals = []
        
        for subreddit_name in self.MONITORED_SUBREDDITS:
            try:
                subreddit = self.reddit.subreddit(subreddit_name)
                
                for post in subreddit.new(limit=50):
                    # Quick keyword check on title only
                    if self._matches_keywords(post.title):
                        signal = self._create_signal(post, subreddit_name)
                        signals.append(signal)
                        
            except Exception as e:
                logger.error(f"Error collecting from r/{subreddit_name}: {e}")
                continue
        
        return signals
    
    def _matches_keywords(self, title: str) -> bool:
        """Check if title matches consumer keywords."""
        title_lower = title.lower()
        return any(kw in title_lower for kw in self.CONSUMER_KEYWORDS)
    
    def _create_signal(self, post, subreddit_name: str) -> Dict[str, Any]:
        """
        Create signal record - LINKS ONLY, no body.
        """
        return {
            "source_api": "reddit",
            "source_id": post.id,  # Immutable, used for dedup
            "signal_type": "mention",
            
            # Stored permanently
            "title": post.title,
            "url": f"https://reddit.com{post.permalink}",
            
            # Brief context for LLM (NOT the full body)
            "source_context": f"r/{subreddit_name} | {post.score} upvotes | {post.num_comments} comments",
            
            # Metadata
            "raw_metadata": {
                "subreddit": subreddit_name,
                "score": post.score,
                "num_comments": post.num_comments,
                "created_utc": post.created_utc,
            },
        }
```

### 4.3 HN Algolia Collector

```python
# collectors/hn_collector.py

import httpx
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class HNCollector:
    """
    Hacker News collector via Algolia API.
    
    Non-SLA dependency - built for graceful failure.
    """
    
    BASE_URL = "http://hn.algolia.com/api/v1/search"
    
    QUERIES = [
        'show hn food',
        'show hn beverage',
        'show hn wellness',
        'dtc brand launch',
        'consumer startup seed',
    ]
    
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 60]
    
    def collect_signals(self) -> List[Dict[str, Any]]:
        """
        Collect signals from HN with graceful degradation.
        """
        signals = []
        
        for query in self.QUERIES:
            try:
                results = self._search_with_retry(query)
                for hit in results.get('hits', [])[:10]:
                    signal = self._create_signal(hit, query)
                    if signal:
                        signals.append(signal)
            except Exception as e:
                logger.error(f"HN query '{query}' failed after retries: {e}")
                continue
        
        return signals
    
    def _search_with_retry(self, query: str) -> dict:
        """Search with exponential backoff."""
        import time
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = httpx.get(
                    self.BASE_URL,
                    params={"query": query, "tags": "story"},
                    timeout=10
                )
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"HN search failed (attempt {attempt+1}), retrying in {delay}s")
                    time.sleep(delay)
                else:
                    raise
        return {}
    
    def _create_signal(self, hit: dict, query: str) -> Dict[str, Any] | None:
        """Create signal from HN hit."""
        if not hit.get('objectID'):
            return None
        
        return {
            "source_api": "hn",
            "source_id": hit['objectID'],  # Immutable HN item ID
            "signal_type": "mention",
            
            "title": hit.get('title', ''),
            "url": hit.get('url') or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            
            "source_context": f"HN | {hit.get('points', 0)} points | query: {query}",
            
            "raw_metadata": {
                "points": hit.get('points', 0),
                "num_comments": hit.get('num_comments', 0),
                "author": hit.get('author'),
                "created_at": hit.get('created_at'),
            },
        }
```

### 4.4 BevNet/NOSH RSS Collector

```python
# collectors/bevnet_collector.py

import feedparser
import hashlib
from typing import List, Dict, Any


class BevNetCollector:
    """
    RSS collector for BevNet and NOSH industry news.
    """
    
    FEEDS = [
        ("bevnet", "https://www.bevnet.com/news/feed/"),
        ("nosh", "https://www.nosh.com/feed/"),
    ]
    
    def collect_signals(self) -> List[Dict[str, Any]]:
        """Collect from RSS feeds."""
        signals = []
        
        for source_name, feed_url in self.FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:
                    signal = self._create_signal(entry, source_name)
                    signals.append(signal)
            except Exception as e:
                logger.error(f"RSS feed {source_name} failed: {e}")
                continue
        
        return signals
    
    def _create_signal(self, entry: dict, source_name: str) -> Dict[str, Any]:
        """Create signal from RSS entry."""
        # Use GUID or link as source_id
        source_id = entry.get('id') or entry.get('link', '')
        if not source_id:
            source_id = hashlib.md5(entry.get('title', '').encode()).hexdigest()
        
        return {
            "source_api": f"rss_{source_name}",
            "source_id": source_id,
            "signal_type": "news",
            
            "title": entry.get('title', ''),
            "url": entry.get('link', ''),
            
            # Use summary as context (truncated)
            "source_context": entry.get('summary', '')[:500],
            
            "raw_metadata": {
                "published": entry.get('published'),
                "source": source_name,
            },
        }
```

### 4.5 USPTO Trademark Collector

```python
# collectors/uspto_collector.py

from typing import List, Dict, Any
import xml.etree.ElementTree as ET

# Nice Classification codes relevant to consumer
RELEVANT_CLASSES = {
    3: "Cosmetics, cleaning preparations",
    5: "Dietary supplements",
    29: "Meats, processed foods",
    30: "Coffee, tea, bakery goods",
    31: "Pet food",
    32: "Non-alcoholic beverages",
    33: "Alcoholic beverages",
}


class USPTOTrademarkCollector:
    """
    USPTO trademark bulk data collector.
    
    Uses daily XML files from USPTO (free public data).
    High-quality signal for new consumer brands.
    """
    
    def collect_signals(self, xml_file_path: str) -> List[Dict[str, Any]]:
        """
        Parse USPTO daily trademark XML and extract consumer-relevant filings.
        """
        signals = []
        
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        
        for tm in root.findall('.//trademark'):
            if self._is_consumer_relevant(tm):
                signal = self._create_signal(tm)
                if signal:
                    signals.append(signal)
        
        return signals
    
    def _is_consumer_relevant(self, tm: ET.Element) -> bool:
        """Check if trademark is in consumer-relevant class."""
        classes = tm.findall('.//classification/international-code')
        for cls in classes:
            try:
                code = int(cls.text)
                if code in RELEVANT_CLASSES:
                    return True
            except (ValueError, TypeError):
                continue
        return False
    
    def _create_signal(self, tm: ET.Element) -> Dict[str, Any] | None:
        """Create signal from trademark filing."""
        serial = tm.findtext('.//serial-number')
        if not serial:
            return None
        
        mark_text = tm.findtext('.//mark-text') or ''
        owner = tm.findtext('.//owner/party-name') or ''
        
        # Get classes
        classes = []
        for cls in tm.findall('.//classification/international-code'):
            try:
                code = int(cls.text)
                if code in RELEVANT_CLASSES:
                    classes.append(f"{code}: {RELEVANT_CLASSES[code]}")
            except (ValueError, TypeError):
                continue
        
        return {
            "source_api": "uspto_tm",
            "source_id": serial,
            "signal_type": "trademark",
            
            "title": f"TM: {mark_text}",
            "url": f"https://tsdr.uspto.gov/#caseNumber={serial}&caseSearchType=US_APPLICATION&caseType=DEFAULT",
            
            "source_context": f"Owner: {owner} | Classes: {', '.join(classes)}",
            
            "raw_metadata": {
                "serial_number": serial,
                "mark_text": mark_text,
                "owner": owner,
                "classes": classes,
            },
        }
```

---

## 5. Notion Integration

### 5.1 Notion Database Schema

**Discovery Inbox Database Properties:**

| Property | Type | Options | Purpose |
|----------|------|---------|---------|
| Name | Title | -- | Company/signal name |
| Status | Select | New, Reviewing, Approved, Rejected | Review workflow |
| Rejection Reason | Select | not_consumer, wrong_category, too_early, too_late, insufficient_info, other | Feedback capture |
| Notes | Text | -- | Free-form reviewer notes |
| Source | Select | hn, reddit, bevnet, nosh, uspto_tm | Signal origin |
| URL | URL | -- | Link to original |
| Thesis Score | Number | 0.0-1.0 | From LLM classifier |
| Category | Select | consumer_cpg, consumer_health_tech, travel, marketplace, other | From LLM |
| Signal ID | Number | -- | Internal reference |
| Created | Date | -- | When added |

### 5.2 Push to Notion

```python
# notion/pusher.py

from notion_client import Client
from ratelimit import limits, sleep_and_retry
import os
import logging

logger = logging.getLogger(__name__)


class NotionPusher:
    """
    Push qualified leads to Notion Inbox.
    
    Rate limited to 2 req/sec (Notion limit is 3/sec).
    """
    
    def __init__(self):
        self.client = Client(auth=os.environ["NOTION_API_KEY"])
        self.database_id = os.environ["NOTION_INBOX_DATABASE_ID"]
    
    @sleep_and_retry
    @limits(calls=2, period=1)
    def push_lead(self, signal_data: dict, classification: dict) -> str:
        """
        Push a qualified lead to Notion.
        
        Returns:
            Notion page ID
        """
        properties = {
            "Name": {"title": [{"text": {"content": classification.get("company_name", signal_data.get("title", "Unknown"))[:100]}}]},
            "Status": {"select": {"name": "New"}},
            "Source": {"select": {"name": signal_data.get("source_api", "unknown")}},
            "URL": {"url": signal_data.get("url")},
            "Thesis Score": {"number": classification.get("thesis_fit_score", 0.0)},
            "Category": {"select": {"name": classification.get("category", "other")}},
            "Signal ID": {"number": signal_data.get("id", 0)},
        }
        
        # Add rationale to page content
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Rationale: {classification.get('rationale', 'N/A')}"}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph", 
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"Key signals: {', '.join(classification.get('key_signals', []))}"}}]
                }
            }
        ]
        
        response = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=properties,
            children=children
        )
        
        logger.info(f"Pushed to Notion: {response['id']}")
        return response["id"]
```

### 5.3 Poll Notion for Decisions

```python
# notion/poller.py

from notion_client import Client
from datetime import datetime, timedelta
import os
import logging

logger = logging.getLogger(__name__)


class NotionPoller:
    """
    Poll Notion for status changes and sync to user_actions.
    
    Runs periodically (e.g., every 5 minutes) to capture reviewer decisions.
    """
    
    def __init__(self, db_session):
        self.client = Client(auth=os.environ["NOTION_API_KEY"])
        self.database_id = os.environ["NOTION_INBOX_DATABASE_ID"]
        self.db = db_session
    
    async def poll_and_sync(self, since_minutes: int = 10) -> int:
        """
        Poll Notion for recently updated pages and sync decisions.
        
        Returns:
            Number of actions synced
        """
        # Query recently modified pages
        since = datetime.utcnow() - timedelta(minutes=since_minutes)
        
        response = self.client.databases.query(
            database_id=self.database_id,
            filter={
                "property": "Status",
                "select": {
                    "does_not_equal": "New"
                }
            },
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}]
        )
        
        synced = 0
        for page in response.get("results", []):
            last_edited = page.get("last_edited_time")
            if last_edited:
                edited_dt = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
                if edited_dt < since.replace(tzinfo=edited_dt.tzinfo):
                    continue  # Too old
            
            if await self._sync_page_decision(page):
                synced += 1
        
        logger.info(f"Synced {synced} decisions from Notion")
        return synced
    
    async def _sync_page_decision(self, page: dict) -> bool:
        """
        Sync a single page's decision to user_actions.
        """
        page_id = page["id"]
        props = page["properties"]
        
        # Extract data
        status = props.get("Status", {}).get("select", {}).get("name")
        rejection_reason = props.get("Rejection Reason", {}).get("select", {}).get("name")
        notes = self._extract_text(props.get("Notes", {}))
        signal_id = props.get("Signal ID", {}).get("number")
        thesis_score = props.get("Thesis Score", {}).get("number")
        
        if not signal_id:
            return False
        
        # Map status to action
        action = None
        if status == "Approved":
            action = "approve"
        elif status == "Rejected":
            action = "reject"
        else:
            return False  # Not a final decision
        
        # Check if already synced
        existing = await self.db.execute(
            "SELECT id FROM user_actions WHERE notion_page_id = ? AND action = ?",
            (page_id, action)
        )
        if existing.fetchone():
            return False  # Already synced
        
        # Insert user_action
        await self.db.execute(
            """INSERT INTO user_actions 
               (signal_id, notion_page_id, action, rejection_reason, 
                rejection_notes, thesis_score_at_action, synced_from_notion_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (signal_id, page_id, action, rejection_reason, notes, thesis_score)
        )
        
        # Update signal status
        await self.db.execute(
            "UPDATE signals SET status = ? WHERE id = ?",
            (action + "d", signal_id)  # "approved" or "rejected"
        )
        
        await self.db.commit()
        return True
    
    def _extract_text(self, prop: dict) -> str:
        """Extract text from Notion rich text property."""
        rich_text = prop.get("rich_text", [])
        if rich_text:
            return rich_text[0].get("plain_text", "")
        return ""
```

---

## 6. AI Research Agent

### 6.1 Overview

The research agent provides on-demand deep research for specific companies. It's triggered manually (via /research command), not automatically for every signal.

### 6.2 Implementation

```python
# services/research_agent.py

from openai import OpenAI
from tavily import TavilyClient
from typing import Dict, Any, Optional
import json
import os
import hashlib
from datetime import datetime, timedelta

RESEARCH_SYSTEM_PROMPT = """You are a venture capital research analyst.

Given search results about a company, generate a structured research brief.

Output JSON:
{
    "company_name": "string",
    "one_liner": "One sentence description",
    "category": "consumer_cpg|consumer_health_tech|travel_hospitality|consumer_marketplace|other",
    "stage_estimate": "pre_seed|seed|series_a|later_stage|unknown",
    "key_findings": [
        {"type": "funding|distribution|traction|team|competition", "detail": "string"}
    ],
    "red_flags": ["string"],
    "thesis_fit_assessment": "strong|moderate|weak|poor",
    "thesis_fit_rationale": "2-3 sentences",
    "recommended_next_steps": ["string"],
    "sources_used": ["url"]
}

Be concise and investment-focused."""


class ResearchAgent:
    """
    On-demand company research using search + LLM.
    
    Single provider (Tavily) for MVP - failover deferred to v2.3.
    """
    
    def __init__(self):
        self.search = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        self.llm = OpenAI()
        self.cache: Dict[str, Any] = {}
        self.CACHE_TTL_HOURS = 24
    
    def research(
        self, 
        company_name: str, 
        founder_name: Optional[str] = None,
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate research brief for a company.
        """
        # Check cache
        cache_key = self._cache_key(company_name, founder_name)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if cached['expires_at'] > datetime.utcnow():
                return cached['result']
        
        # Execute searches
        queries = [
            f'"{company_name}" funding news 2025 2026',
            f'"{company_name}" competitors',
            f'"{company_name}" retail distribution',
        ]
        if founder_name:
            queries.append(f'"{founder_name}" founder CEO background')
        
        all_results = []
        for query in queries:
            try:
                response = self.search.search(query, search_depth="basic", max_results=3)
                all_results.extend(response.get('results', []))
            except Exception as e:
                # Log but continue with partial results
                print(f"Search failed for '{query}': {e}")
        
        # Generate brief via LLM
        search_context = self._format_results(all_results)
        
        response = self.llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": f"""Research: {company_name}
                
Additional context: {context or 'None'}

Search results:
{search_context}

Generate research brief as JSON."""}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Cache result
        self.cache[cache_key] = {
            'result': result,
            'expires_at': datetime.utcnow() + timedelta(hours=self.CACHE_TTL_HOURS)
        }
        
        return result
    
    def _cache_key(self, company: str, founder: Optional[str]) -> str:
        key = f"{company}|{founder or ''}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def _format_results(self, results: list) -> str:
        formatted = []
        for i, r in enumerate(results[:12], 1):
            formatted.append(f"""
Result {i}:
URL: {r.get('url', 'N/A')}
Title: {r.get('title', 'N/A')}
Content: {r.get('content', 'N/A')[:400]}
""")
        return "\n".join(formatted)
```

### 6.3 MCP Command Interface

```python
# commands/research.py

from services.research_agent import ResearchAgent

agent = ResearchAgent()

def research_command(company_name: str, founder_name: str = None) -> str:
    """
    /research <company_name> [founder_name]
    
    Returns formatted research brief.
    """
    result = agent.research(company_name, founder_name)
    
    # Format for display
    output = f"""
## Research Brief: {result.get('company_name', company_name)}

**{result.get('one_liner', 'N/A')}**

**Category:** {result.get('category', 'unknown')}
**Stage:** {result.get('stage_estimate', 'unknown')}
**Thesis Fit:** {result.get('thesis_fit_assessment', 'unknown')}

### Key Findings
"""
    for finding in result.get('key_findings', []):
        output += f"- [{finding.get('type')}] {finding.get('detail')}\n"
    
    if result.get('red_flags'):
        output += "\n### Red Flags\n"
        for flag in result['red_flags']:
            output += f"- {flag}\n"
    
    output += f"\n### Rationale\n{result.get('thesis_fit_rationale', 'N/A')}\n"
    
    if result.get('recommended_next_steps'):
        output += "\n### Next Steps\n"
        for step in result['recommended_next_steps']:
            output += f"- {step}\n"
    
    return output
```

---

## 7. Phased Roadmap

### 7.1 Overview

| Phase | Focus | Hours | Cumulative |
|-------|-------|-------|------------|
| Phase 1 | Core Pipeline + Notion | 30-38h | 30-38h |
| Phase 2 | Intelligence (Filter + Research) | 28-34h | 58-72h |
| Phase 3 | Collectors | 26-32h | 84-104h |
| Phase 4 | Deployment | 14-18h | 98-122h |

**Timeline:** 4-5 weeks at 25 hours/week

### 7.2 Phase 1: Core Pipeline (30-38 hours)

**Objective:** Establish data flow from collectors to Notion.

| Task | Hours | Deliverables |
|------|-------|--------------|
| Database schema + migrations | 4h | SQLite schema, indexes |
| Signal deduplication (fixed hash) | 3h | content_hash on source_api+source_id |
| Notion Inbox database setup | 4h | Database schema, properties |
| Notion push (rate-limited) | 6h | Push leads to Inbox |
| Notion poller (read decisions) | 6h | Poll for status changes |
| user_actions sync | 3h | Write decisions to SQLite |
| Error handling + logging | 4h | Retries, structured logging |

**Exit Criteria:**
- [ ] Qualified leads appear in Notion Inbox
- [ ] Reviewers can set Status + Rejection Reason in Notion
- [ ] Decisions sync to SQLite user_actions table within 10 minutes
- [ ] Duplicate signals are correctly detected and skipped

### 7.3 Phase 2: Intelligence (28-34 hours)

**Objective:** Build filtering and research capabilities.

| Task | Hours | Deliverables |
|------|-------|--------------|
| Hard disqualifier list | 2h | Keyword-based fast filter |
| LLM classifier | 10h | GPT-4o-mini classification |
| Prompt versioning + audit trail | 2h | llm_classifications table |
| Tavily search integration | 3h | Rate-limited search client |
| Research agent core | 8h | /research command |
| Research caching | 3h | 24h TTL cache |

**Exit Criteria:**
- [ ] Hard disqualifiers correctly reject B2B/biotech
- [ ] LLM classifier returns structured JSON with score/category/rationale
- [ ] Classifications stored with prompt version for audit
- [ ] /research returns brief in <15 seconds
- [ ] Search costs tracked

### 7.4 Phase 3: Collectors (26-32 hours)

**Objective:** Add consumer signal sources.

| Task | Hours | Deliverables |
|------|-------|--------------|
| Collector health monitoring | 4h | collector_runs table, logging |
| HN Algolia collector | 6h | With graceful degradation |
| BevNet/NOSH RSS collector | 4h | RSS parsing |
| USPTO Trademark collector | 8h | XML parsing, class filtering |
| Reddit collector (links only) | 4h | No body storage |
| Smoke tests | 4h | Basic collector tests |

**Exit Criteria:**
- [ ] All collectors report health status
- [ ] No duplicate signals after 1 week
- [ ] Reddit stores links only (compliance verified)
- [ ] All collectors pass smoke tests

### 7.5 Phase 4: Deployment (14-18 hours)

**Objective:** Deploy to production.

| Task | Hours | Deliverables |
|------|-------|--------------|
| Fly.io configuration | 4h | fly.toml, Dockerfile |
| Volume configuration | 2h | Persistent /data |
| R2 backup setup | 3h | Nightly backups |
| Environment config | 2h | Secrets management |
| Monitoring/alerts | 3h | Uptime, error alerts |
| Smoke test in prod | 2h | End-to-end verification |

**Exit Criteria:**
- [ ] System deployed and accessible
- [ ] Backups running nightly
- [ ] Alerts configured for collector failures

---

## 8. Compliance and Risk

### 8.1 API Terms of Service

| Service | Key Restrictions | Our Approach | Risk |
|---------|-----------------|--------------|------|
| **Reddit** | Delete stored content within 48h | **No body storage** - links only | None |
| **HN Algolia** | No SLA, don't abuse | Rate limiting, graceful degradation | Low |
| **Notion** | 3 req/sec limit | 2 req/sec rate limiter | Low |
| **Tavily** | Abuse detection | 50 req/hour limit | Low |
| **USPTO** | Public data | Direct bulk download | None |

### 8.2 Technical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Fly.io data loss | High | Nightly R2 backups |
| HN Algolia outage | Low | Graceful degradation |
| Tavily quota exceeded | Medium | Cost tracking + alerts |
| LLM inconsistency | Medium | Low temperature, audit trail |

### 8.3 Data Privacy

| Data Type | Storage | Retention |
|-----------|---------|-----------|
| Reddit links | Permanent | Indefinite |
| Reddit body | **Not stored** | N/A |
| Company names | Permanent | Indefinite |
| LLM classifications | Permanent | Indefinite (audit) |
| User decisions | Permanent | Indefinite |

---

## 9. Cost Analysis

### 9.1 Monthly Operating Costs

| Category | Service | Expected Usage | Monthly Cost |
|----------|---------|----------------|--------------|
| **Compute** | Fly.io | 1 shared VM | $3-5 |
| **LLM Classification** | OpenAI GPT-4o-mini | ~500 signals × $0.002 | $1-2 |
| **Research Agent** | OpenAI + Tavily | ~50 calls | $0.50 |
| **Backups** | Cloudflare R2 | <500MB | $0 |
| **Total** | | | **$5-8** |

### 9.2 Cost Scaling

| Signals/Week | Classification Cost | Total Monthly |
|--------------|--------------------:|-------------:|
| 100 | $0.80 | $5-6 |
| 250 | $2.00 | $7-9 |
| 500 | $4.00 | $10-12 |
| 1000 | $8.00 | $15-18 |

---

## 10. Testing Strategy

### 10.1 Test Categories

| Category | Coverage | Hours |
|----------|----------|-------|
| Smoke tests | All collectors | 2h |
| Dedup tests | Hash function | 1h |
| Filter tests | Hard disqualifiers | 1h |
| Integration | End-to-end flow | 2h |

### 10.2 Key Tests

```python
# tests/test_dedup.py
def test_same_source_id_generates_same_hash():
    signal = {"source_api": "reddit", "source_id": "abc123"}
    assert generate_content_hash(signal) == generate_content_hash(signal)

def test_different_source_id_different_hash():
    s1 = {"source_api": "reddit", "source_id": "abc123"}
    s2 = {"source_api": "reddit", "source_id": "xyz789"}
    assert generate_content_hash(s1) != generate_content_hash(s2)

# tests/test_filter.py
def test_hard_disqualifier_catches_b2b():
    is_disq, term = check_hard_disqualifiers("B2B SaaS platform for enterprises")
    assert is_disq
    assert "b2b" in term.lower()

def test_consumer_passes_hard_filter():
    is_disq, _ = check_hard_disqualifiers("New DTC beverage brand launches at Whole Foods")
    assert not is_disq

# tests/test_collectors.py
def test_reddit_no_body_storage():
    collector = RedditCollector(mock_client)
    signals = collector.collect_signals()
    for signal in signals:
        assert "post_body" not in signal
        assert "temporary_content" not in signal
```

---

## 11. Decision Log

| ID | Decision | Rationale |
|----|----------|-----------|
| D001 | SQLite on Fly.io Volume | Simplicity, low cost, Python-only access |
| D002 | 48h Reddit retention | **SUPERSEDED by D014** |
| D003 | Product Hunt manual only | TOS prohibits commercial use |
| D004 | No LinkedIn scraping | Legal risk too high |
| D005 | Tavily for search | LLM-optimized results |
| D006 | Tavily + Serper failover | **SUPERSEDED by D015** |
| D007 | Seeder/Enricher separation | Clean architecture |
| D008 | Next.js + shadcn/ui | **SUPERSEDED by D012** |
| D009 | Fly.io over CF Workers | Python-native |
| D010 | Cloudflare R2 for backups | 10GB free forever |
| D011 | PatentsView deprecated | API shutdown May 2025 |
| **D012** | **Notion-as-UI (headless)** | Eliminates 28h UI build, removes SQLite concurrency |
| **D013** | **LLM classifier** | Weighted scoring required uncollected data |
| **D014** | **Process-and-discard Reddit** | No body storage, eliminates purge job |
| **D015** | **Single search provider (Tavily)** | Failover is premature optimization for batch tool |
| **D016** | **Keep USPTO trademark** | High-quality official data worth 8h investment |

---

## 12. Appendices

### Appendix A: Verification Checklist

| Item | Status |
|------|--------|
| Reddit API credentials | TODO |
| Tavily API key | TODO |
| OpenAI API key | TODO |
| Notion API key + Inbox database | TODO |
| Fly.io account | TODO |
| Cloudflare R2 bucket | TODO |

### Appendix B: Rejection Reason Taxonomy

| Code | Label |
|------|-------|
| `not_consumer` | Not Consumer |
| `wrong_category` | Wrong Category |
| `too_early` | Too Early Stage |
| `too_late` | Too Late Stage |
| `insufficient_info` | Insufficient Information |
| `duplicate` | Duplicate |
| `other` | Other |

### Appendix C: Environment Variables

```bash
# .env.example

# Reddit
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=SweetSweetHarmony/2.2

# Search
TAVILY_API_KEY=

# LLM
OPENAI_API_KEY=

# Notion
NOTION_API_KEY=
NOTION_INBOX_DATABASE_ID=

# Database
DATABASE_PATH=/data/discovery.db

# Backup
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
R2_ENDPOINT=

# Feature flags
ENABLE_REDDIT=true
ENABLE_HN=true
ENABLE_USPTO=false  # Phase 3
```

### Appendix D: Weekly Report (via Notion API)

```python
# reports/weekly.py

async def generate_weekly_report() -> str:
    """Query Notion + SQLite for weekly metrics."""
    
    # From SQLite
    new_signals = await db.execute(
        "SELECT COUNT(*) FROM signals WHERE created_at > datetime('now', '-7 days')"
    )
    
    approved = await db.execute(
        "SELECT COUNT(*) FROM user_actions WHERE action='approve' AND created_at > datetime('now', '-7 days')"
    )
    
    rejected = await db.execute(
        "SELECT COUNT(*) FROM user_actions WHERE action='reject' AND created_at > datetime('now', '-7 days')"
    )
    
    # Top rejection reasons
    reasons = await db.execute("""
        SELECT rejection_reason, COUNT(*) as cnt 
        FROM user_actions 
        WHERE action='reject' AND created_at > datetime('now', '-7 days')
        GROUP BY rejection_reason 
        ORDER BY cnt DESC 
        LIMIT 5
    """)
    
    return f"""
# Weekly Report

**New Signals:** {new_signals.scalar()}
**Approved:** {approved.scalar()}
**Rejected:** {rejected.scalar()}

## Top Rejection Reasons
{format_reasons(reasons.fetchall())}
"""
```

---

## Document End

**Next Steps:**
1. Complete verification checklist
2. Set up Notion Inbox database
3. Begin Phase 1 implementation
4. Schedule weekly progress review

---

*Document version: 2.2 MVP | Generated: January 6, 2026*
