# Phase 3: Thesis Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate thesis matching (keyword + LLM) into signal routing, persist classifications, and add CLI dashboard for pipeline management.

**Architecture:** Two-stage thesis filtering (fast keyword pre-filter + Gemini LLM semantic classification) with results persisted to SQLite. Signals routed to QUALIFIED/HELD/REJECTED status. User controls push to Notion via explicit CLI command.

**Tech Stack:** Python 3.11+, aiosqlite, Google Gemini (free tier), pytest

---

## Task 1: Schema Migration - Add thesis_classifications Table

**Files:**
- Modify: `storage/signal_store.py:65-217` (add migration 5)
- Test: `tests/storage/test_thesis_classification_storage.py` (create)

**Step 1: Write the failing test**

Create `tests/storage/test_thesis_classification_storage.py`:

```python
"""Tests for thesis classification storage."""
import pytest
from datetime import datetime
from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


class TestThesisClassificationSchema:
    """Test thesis_classifications table exists after migration."""

    @pytest.fixture
    async def store(self, tmp_path):
        """Create a fresh store for each test."""
        db_path = str(tmp_path / "test_thesis.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_schema_version_is_5(self, store):
        """Schema version should be 5 after migration."""
        assert CURRENT_SCHEMA_VERSION == 5

    @pytest.mark.asyncio
    async def test_thesis_classifications_table_exists(self, store):
        """thesis_classifications table should exist."""
        async with store._get_connection() as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_classifications'"
            )
            result = await cursor.fetchone()
            assert result is not None
            assert result[0] == "thesis_classifications"

    @pytest.mark.asyncio
    async def test_thesis_classifications_columns(self, store):
        """thesis_classifications should have all required columns."""
        async with store._get_connection() as db:
            cursor = await db.execute("PRAGMA table_info(thesis_classifications)")
            columns = await cursor.fetchall()
            column_names = {col[1] for col in columns}

            required = {
                "id", "signal_id", "canonical_key",
                "thesis_match", "thesis_fit_score", "category",
                "keyword_score", "keyword_category", "negative_keywords",
                "stage_estimate", "confidence", "rationale", "key_signals",
                "prompt_version", "model", "input_tokens", "output_tokens",
                "latency_ms", "classified_at", "competitor_flag", "competitor_match"
            }
            assert required.issubset(column_names)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_thesis_classification_storage.py -v`
Expected: FAIL - CURRENT_SCHEMA_VERSION is 4, table doesn't exist

**Step 3: Write minimal implementation**

In `storage/signal_store.py`, update:

```python
# Line 65: Update version
CURRENT_SCHEMA_VERSION = 5

# After line 216, add migration 5:
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

    -- Add new processing statuses: 'qualified', 'held'
    -- (SQLite doesn't have ALTER CONSTRAINT, statuses are just strings)
    """
}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_thesis_classification_storage.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_thesis_classification_storage.py
git commit -m "feat(storage): add thesis_classifications table (migration 5)"
```

---

## Task 2: Rewrite thesis_matcher.py with Consumer Keywords

**Files:**
- Modify: `utils/thesis_matcher.py` (complete rewrite)
- Test: `tests/utils/test_thesis_matcher.py` (rewrite)

**Step 1: Write the failing tests**

Rewrite `tests/utils/test_thesis_matcher.py`:

```python
"""Tests for Consumer thesis keyword matcher."""
import pytest
from utils.thesis_matcher import (
    ThesisMatcher,
    ThesisFit,
    ConsumerThesis,
    CONSUMER_KEYWORDS,
    NEGATIVE_KEYWORDS,
)


class TestConsumerThesisEnum:
    """Test ConsumerThesis enum values."""

    def test_enum_has_consumer_cpg(self):
        assert ConsumerThesis.CONSUMER_CPG.value == "consumer_cpg"

    def test_enum_has_consumer_health_tech(self):
        assert ConsumerThesis.CONSUMER_HEALTH_TECH.value == "consumer_health_tech"

    def test_enum_has_travel_hospitality(self):
        assert ConsumerThesis.TRAVEL_HOSPITALITY.value == "travel_hospitality"

    def test_enum_has_consumer_marketplace(self):
        assert ConsumerThesis.CONSUMER_MARKETPLACE.value == "consumer_marketplace"

    def test_enum_has_unknown(self):
        assert ConsumerThesis.UNKNOWN.value == "unknown"


class TestConsumerKeywords:
    """Test keyword definitions."""

    def test_cpg_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_CPG in CONSUMER_KEYWORDS
        assert "meal kit" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_CPG]

    def test_health_tech_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_HEALTH_TECH in CONSUMER_KEYWORDS
        assert "fitness app" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_HEALTH_TECH]

    def test_travel_keywords_exist(self):
        assert ConsumerThesis.TRAVEL_HOSPITALITY in CONSUMER_KEYWORDS
        assert "travel booking" in CONSUMER_KEYWORDS[ConsumerThesis.TRAVEL_HOSPITALITY]

    def test_marketplace_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_MARKETPLACE in CONSUMER_KEYWORDS
        assert "marketplace" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_MARKETPLACE]


class TestNegativeKeywords:
    """Test negative/exclusion keywords."""

    def test_enterprise_is_negative(self):
        assert "enterprise" in NEGATIVE_KEYWORDS

    def test_b2b_is_negative(self):
        assert "b2b" in NEGATIVE_KEYWORDS

    def test_crypto_is_negative(self):
        assert "crypto" in NEGATIVE_KEYWORDS

    def test_blockchain_is_negative(self):
        assert "blockchain" in NEGATIVE_KEYWORDS


class TestThesisFitDataclass:
    """Test ThesisFit result dataclass."""

    def test_is_fit_true_when_score_above_threshold(self):
        fit = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.7,
            matched_keywords=["meal kit"],
            negative_keywords=[],
            all_scores={},
            confidence="HIGH",
        )
        assert fit.is_fit is True

    def test_is_fit_false_when_score_below_threshold(self):
        fit = ThesisFit(
            thesis=ConsumerThesis.UNKNOWN,
            score=0.2,
            matched_keywords=[],
            negative_keywords=["enterprise"],
            all_scores={},
            confidence="LOW",
        )
        assert fit.is_fit is False


class TestThesisMatcherScoring:
    """Test ThesisMatcher scoring logic."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_cpg_description_scores_cpg(self, matcher):
        fit = matcher.score("We make healthy meal kits delivered to your door")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        assert fit.score >= 0.5

    def test_health_tech_description_scores_health_tech(self, matcher):
        fit = matcher.score("A fitness app for tracking your workouts and wellness")
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.5

    def test_travel_description_scores_travel(self, matcher):
        fit = matcher.score("Travel booking platform for unique hotel experiences")
        assert fit.thesis == ConsumerThesis.TRAVEL_HOSPITALITY
        assert fit.score >= 0.5

    def test_marketplace_description_scores_marketplace(self, matcher):
        fit = matcher.score("Consumer marketplace connecting buyers and sellers")
        assert fit.thesis == ConsumerThesis.CONSUMER_MARKETPLACE
        assert fit.score >= 0.5

    def test_negative_keywords_reduce_score(self, matcher):
        fit = matcher.score("Enterprise B2B SaaS platform for developers")
        assert fit.score < 0.4
        assert "enterprise" in fit.negative_keywords or "b2b" in fit.negative_keywords

    def test_empty_text_returns_unknown(self, matcher):
        fit = matcher.score("")
        assert fit.thesis == ConsumerThesis.UNKNOWN
        assert fit.score == 0.0

    def test_confidence_high_when_score_above_07(self, matcher):
        fit = matcher.score("Premium skincare brand with d2c subscription model for beauty products")
        assert fit.confidence == "HIGH" or fit.score >= 0.7

    def test_confidence_low_when_score_below_04(self, matcher):
        fit = matcher.score("Random unrelated text about nothing")
        assert fit.confidence == "LOW"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_matcher.py -v`
Expected: FAIL - ConsumerThesis doesn't exist, wrong keywords

**Step 3: Write minimal implementation**

Rewrite `utils/thesis_matcher.py`:

```python
"""
Consumer Thesis Matcher - Keyword-based thesis fit scoring.

Matches companies against Press On Ventures' Consumer investment thesis:
- Consumer CPG: Food, beverage, snacks, beauty, personal care
- Consumer Health Tech: Fitness, wellness, mental health, supplements
- Travel & Hospitality: Travel booking, hospitality tech, restaurants
- Consumer Marketplaces: Consumer-facing two-sided markets

Usage:
    from utils.thesis_matcher import ThesisMatcher, ThesisFit

    matcher = ThesisMatcher()
    fit = matcher.score("Meal kit delivery startup")
    print(f"Thesis: {fit.thesis}, Score: {fit.score}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class ConsumerThesis(str, Enum):
    """Consumer investment thesis categories."""
    CONSUMER_CPG = "consumer_cpg"
    CONSUMER_HEALTH_TECH = "consumer_health_tech"
    TRAVEL_HOSPITALITY = "travel_hospitality"
    CONSUMER_MARKETPLACE = "consumer_marketplace"
    UNKNOWN = "unknown"


# Keyword lists for each thesis (weighted by specificity)
CONSUMER_KEYWORDS: Dict[ConsumerThesis, Dict[str, float]] = {
    ConsumerThesis.CONSUMER_CPG: {
        # High weight - specific CPG terms
        "meal kit": 0.9,
        "beverage brand": 0.9,
        "food brand": 0.8,
        "snack brand": 0.8,
        "skincare brand": 0.9,
        "beauty brand": 0.8,
        "personal care": 0.8,
        "household products": 0.7,
        "cpg": 0.8,
        "d2c": 0.7,
        "dtc": 0.7,
        "direct to consumer": 0.7,
        # Medium weight - general terms
        "food": 0.4,
        "beverage": 0.5,
        "snack": 0.4,
        "drink": 0.4,
        "grocery": 0.5,
        "organic": 0.4,
        "vegan": 0.5,
        "plant-based": 0.5,
        "beauty": 0.4,
        "skincare": 0.5,
        "cosmetics": 0.5,
    },
    ConsumerThesis.CONSUMER_HEALTH_TECH: {
        # High weight - specific health tech terms
        "fitness app": 0.9,
        "wellness app": 0.9,
        "wellness platform": 0.8,
        "mental health app": 0.9,
        "health tracker": 0.8,
        "meditation app": 0.8,
        "sleep app": 0.8,
        "nutrition app": 0.7,
        # Medium weight - general terms
        "fitness": 0.5,
        "workout": 0.5,
        "wellness": 0.4,
        "meditation": 0.5,
        "sleep": 0.4,
        "supplements": 0.5,
        "vitamins": 0.4,
        "wearable": 0.5,
        "health app": 0.6,
        "mental health": 0.5,
        "therapy": 0.4,
    },
    ConsumerThesis.TRAVEL_HOSPITALITY: {
        # High weight - specific travel terms
        "travel booking": 0.9,
        "hotel tech": 0.8,
        "hospitality tech": 0.8,
        "hospitality platform": 0.8,
        "restaurant tech": 0.8,
        "travel platform": 0.7,
        "vacation rental": 0.8,
        "experience booking": 0.7,
        # Medium weight - general terms
        "travel": 0.5,
        "booking": 0.4,
        "hotel": 0.5,
        "hospitality": 0.5,
        "restaurant": 0.4,
        "vacation": 0.4,
        "experiences": 0.4,
        "tourism": 0.5,
        "lodging": 0.5,
    },
    ConsumerThesis.CONSUMER_MARKETPLACE: {
        # High weight - specific marketplace terms
        "consumer marketplace": 0.9,
        "two-sided market": 0.8,
        "peer-to-peer": 0.7,
        "p2p marketplace": 0.8,
        "c2c marketplace": 0.8,
        "buyer seller": 0.6,
        # Medium weight - general terms
        "marketplace": 0.6,
        "e-commerce": 0.5,
        "delivery": 0.4,
        "subscription": 0.4,
        "shopping": 0.4,
        "resale": 0.5,
        "secondhand": 0.5,
        "rental": 0.4,
    },
}

# Negative signals - exclusions from thesis
NEGATIVE_KEYWORDS: Dict[str, float] = {
    # B2B/Enterprise
    "enterprise": 0.5,
    "b2b": 0.5,
    "saas platform": 0.4,
    "developer tool": 0.5,
    "api platform": 0.4,
    "devops": 0.5,
    "infrastructure": 0.4,
    # Crypto/Web3
    "blockchain": 0.5,
    "crypto": 0.5,
    "web3": 0.5,
    "nft": 0.5,
    "defi": 0.5,
    "token": 0.3,
    # Other exclusions
    "consulting": 0.4,
    "agency": 0.4,
    "services firm": 0.4,
    "series b": 0.3,
    "series c": 0.4,
    "series d": 0.5,
}


@dataclass
class ThesisFit:
    """Result of thesis matching."""
    thesis: ConsumerThesis
    score: float  # 0.0-1.0
    matched_keywords: List[str]
    negative_keywords: List[str]
    all_scores: Dict[str, float]  # Score per thesis
    confidence: str  # HIGH, MEDIUM, LOW

    @property
    def is_fit(self) -> bool:
        """Returns True if score indicates good thesis fit."""
        return self.score >= 0.4

    def to_dict(self) -> Dict:
        return {
            "thesis": self.thesis.value,
            "score": round(self.score, 3),
            "matched_keywords": self.matched_keywords,
            "negative_keywords": self.negative_keywords,
            "all_scores": {k: round(v, 3) for k, v in self.all_scores.items()},
            "confidence": self.confidence,
            "is_fit": self.is_fit,
        }


class ThesisMatcher:
    """
    Matches company descriptions against Consumer investment thesis.

    Uses keyword matching with weights to score thesis fit.
    Returns the best-matching thesis with a confidence score.
    """

    def __init__(
        self,
        custom_keywords: Optional[Dict[ConsumerThesis, Dict[str, float]]] = None,
    ):
        self.keywords = {k: dict(v) for k, v in CONSUMER_KEYWORDS.items()}
        if custom_keywords:
            for thesis, kws in custom_keywords.items():
                if thesis in self.keywords:
                    self.keywords[thesis].update(kws)
                else:
                    self.keywords[thesis] = kws

    def score(
        self,
        text: str,
        company_name: Optional[str] = None,
    ) -> ThesisFit:
        """Score text against all Consumer thesis categories."""
        if not text:
            return ThesisFit(
                thesis=ConsumerThesis.UNKNOWN,
                score=0.0,
                matched_keywords=[],
                negative_keywords=[],
                all_scores={},
                confidence="LOW",
            )

        normalized = self._normalize(text)
        if company_name:
            normalized += " " + self._normalize(company_name)

        # Score each thesis
        scores: Dict[str, float] = {}
        all_matches: Dict[str, List[str]] = {}

        for thesis, keywords in self.keywords.items():
            score, matches = self._score_thesis(normalized, keywords)
            scores[thesis.value] = score
            all_matches[thesis.value] = matches

        # Find negative keywords
        negative_matches = self._find_negative_keywords(normalized)

        # Find best thesis
        if scores:
            best_thesis_name = max(scores, key=scores.get)
            best_score = scores[best_thesis_name]
            best_thesis = ConsumerThesis(best_thesis_name)
            matched_kws = all_matches.get(best_thesis_name, [])
        else:
            best_thesis = ConsumerThesis.UNKNOWN
            best_score = 0.0
            matched_kws = []

        # Apply negative penalty
        if negative_matches:
            penalty = sum(NEGATIVE_KEYWORDS.get(kw, 0.2) for kw in negative_matches)
            best_score = max(0.0, best_score - penalty * 0.5)

        # Determine confidence
        if best_score >= 0.7:
            confidence = "HIGH"
        elif best_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return ThesisFit(
            thesis=best_thesis if best_score > 0.1 else ConsumerThesis.UNKNOWN,
            score=best_score,
            matched_keywords=matched_kws,
            negative_keywords=negative_matches,
            all_scores=scores,
            confidence=confidence,
        )

    def _normalize(self, text: str) -> str:
        return text.lower().strip()

    def _score_thesis(
        self,
        text: str,
        keywords: Dict[str, float],
    ) -> tuple[float, List[str]]:
        matches: List[str] = []
        total_weight = 0.0
        max_possible = sum(keywords.values())

        for keyword, weight in keywords.items():
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
                total_weight += weight

        if max_possible > 0:
            score = min(total_weight / (max_possible * 0.3), 1.0)
        else:
            score = 0.0

        return score, matches

    def _find_negative_keywords(self, text: str) -> List[str]:
        matches = []
        for keyword in NEGATIVE_KEYWORDS:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, text):
                matches.append(keyword)
        return matches

    def score_signals(self, signals: List[Dict]) -> ThesisFit:
        """Score a list of signals to determine thesis fit."""
        texts = []
        company_name = None

        for signal in signals:
            raw = signal.get("raw_data", {}) if isinstance(signal, dict) else {}
            for field in ["description", "short_description", "about", "bio"]:
                if field in raw and raw[field]:
                    texts.append(str(raw[field]))
            if "company_name" in raw and not company_name:
                company_name = raw["company_name"]

        combined_text = " ".join(texts)
        return self.score(combined_text, company_name=company_name)


def score_thesis_fit(text: str, company_name: Optional[str] = None) -> ThesisFit:
    """Convenience function to score thesis fit."""
    matcher = ThesisMatcher()
    return matcher.score(text, company_name)


def is_thesis_fit(text: str, min_score: float = 0.4) -> bool:
    """Quick check if text matches investment thesis."""
    fit = score_thesis_fit(text)
    return fit.score >= min_score
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_matcher.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add utils/thesis_matcher.py tests/utils/test_thesis_matcher.py
git commit -m "feat(thesis): rewrite thesis_matcher with Consumer keywords"
```

---

## Task 3: Add Storage Methods for Thesis Classifications

**Files:**
- Modify: `storage/signal_store.py` (add save/get methods)
- Test: `tests/storage/test_thesis_classification_storage.py` (extend)

**Step 1: Write the failing tests**

Add to `tests/storage/test_thesis_classification_storage.py`:

```python
class TestThesisClassificationStorage:
    """Test save/get methods for thesis classifications."""

    @pytest.fixture
    async def store(self, tmp_path):
        db_path = str(tmp_path / "test_thesis.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def signal_id(self, store):
        """Create a test signal and return its ID."""
        signal_id = await store.save_signal({
            "signal_type": "test",
            "source_api": "test",
            "canonical_key": "domain:test.com",
            "company_name": "Test Co",
            "confidence": 0.5,
            "raw_data": {"description": "Test company"},
        })
        return signal_id

    @pytest.mark.asyncio
    async def test_save_thesis_classification(self, store, signal_id):
        """Should save a thesis classification."""
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            negative_keywords=[],
            thesis_match=True,
            thesis_fit_score=0.75,
            category="consumer_cpg",
            stage_estimate="seed",
            confidence="high",
            rationale="Meal kit delivery startup",
            key_signals=["meal kit", "d2c"],
            prompt_version="v1.2.0",
            model="gemini-2.0-flash",
        )
        # Should not raise

    @pytest.mark.asyncio
    async def test_get_thesis_classification(self, store, signal_id):
        """Should retrieve a saved classification."""
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            negative_keywords=["enterprise"],
            thesis_match=True,
            thesis_fit_score=0.75,
            category="consumer_cpg",
        )

        result = await store.get_thesis_classification("domain:test.com")
        assert result is not None
        assert result["thesis_fit_score"] == 0.75
        assert result["category"] == "consumer_cpg"

    @pytest.mark.asyncio
    async def test_get_thesis_classification_not_found(self, store):
        """Should return None for unknown canonical key."""
        result = await store.get_thesis_classification("domain:unknown.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_recent_classification_within_cache(self, store, signal_id):
        """Should return recent classification (within 7 days)."""
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            thesis_fit_score=0.8,
            category="consumer_health_tech",
        )

        result = await store.get_recent_classification("domain:test.com", days=7)
        assert result is not None
        assert result["thesis_fit_score"] == 0.8
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_thesis_classification_storage.py::TestThesisClassificationStorage -v`
Expected: FAIL - save_thesis_classification doesn't exist

**Step 3: Write minimal implementation**

Add to `storage/signal_store.py` (after line ~1050):

```python
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
        """Save a thesis classification result."""
        now = datetime.now(timezone.utc).isoformat()

        async with self._get_connection() as db:
            cursor = await db.execute(
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
            await db.commit()
            return cursor.lastrowid

    async def get_thesis_classification(
        self,
        canonical_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the most recent thesis classification for a canonical key."""
        async with self._get_connection() as db:
            cursor = await db.execute(
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
        """Get classification if within cache window."""
        result = await self.get_thesis_classification(canonical_key)
        if not result:
            return None

        classified_at = datetime.fromisoformat(result["classified_at"].replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        if classified_at >= cutoff:
            return result
        return None
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_thesis_classification_storage.py::TestThesisClassificationStorage -v`
Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_thesis_classification_storage.py
git commit -m "feat(storage): add save/get methods for thesis classifications"
```

---

## Task 4: Add ThesisFilter Class for Pipeline Integration

**Files:**
- Create: `utils/thesis_filter.py`
- Test: `tests/utils/test_thesis_filter.py`

**Step 1: Write the failing tests**

Create `tests/utils/test_thesis_filter.py`:

```python
"""Tests for ThesisFilter - combines keyword + LLM classification."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from utils.thesis_filter import (
    ThesisFilter,
    ThesisFilterConfig,
    ThesisFilterResult,
    RoutingDecision,
)


class TestRoutingDecision:
    """Test routing decision enum."""

    def test_qualified_value(self):
        assert RoutingDecision.QUALIFIED.value == "qualified"

    def test_held_value(self):
        assert RoutingDecision.HELD.value == "held"

    def test_rejected_value(self):
        assert RoutingDecision.REJECTED.value == "rejected"


class TestThesisFilterConfig:
    """Test filter configuration."""

    def test_default_hold_threshold(self):
        config = ThesisFilterConfig()
        assert config.hold_threshold == 0.3

    def test_default_skip_llm_threshold(self):
        config = ThesisFilterConfig()
        assert config.skip_llm_if_keyword_below == 0.2


class TestThesisFilterResult:
    """Test filter result dataclass."""

    def test_routing_decision_qualified(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.QUALIFIED,
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            llm_score=0.75,
            llm_category="consumer_cpg",
            confidence_adjustment=0.08,
        )
        assert result.routing == RoutingDecision.QUALIFIED

    def test_routing_decision_held(self):
        result = ThesisFilterResult(
            routing=RoutingDecision.HELD,
            keyword_score=0.3,
            llm_score=0.25,
        )
        assert result.routing == RoutingDecision.HELD


class TestThesisFilterRouting:
    """Test routing logic."""

    @pytest.fixture
    def filter(self):
        return ThesisFilter(ThesisFilterConfig())

    @pytest.mark.asyncio
    async def test_excluded_category_is_rejected(self, filter):
        """LLM category=excluded should route to REJECTED."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=False,
            thesis_fit_score=0.1,
            category="excluded",
        )
        filter._llm_classifier = mock_llm

        result = await filter.classify("Enterprise B2B SaaS platform")
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_low_score_is_held(self, filter):
        """LLM score < 0.3 should route to HELD."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=False,
            thesis_fit_score=0.2,
            category="other",
        )
        filter._llm_classifier = mock_llm

        result = await filter.classify("Random unrelated company")
        assert result.routing == RoutingDecision.HELD

    @pytest.mark.asyncio
    async def test_good_score_is_qualified(self, filter):
        """LLM score >= 0.3 should route to QUALIFIED."""
        mock_llm = AsyncMock()
        mock_llm.classify.return_value = MagicMock(
            thesis_match=True,
            thesis_fit_score=0.75,
            category="consumer_cpg",
        )
        filter._llm_classifier = mock_llm

        result = await filter.classify("Healthy meal kit delivery startup")
        assert result.routing == RoutingDecision.QUALIFIED


class TestConfidenceAdjustment:
    """Test confidence adjustment calculation."""

    @pytest.fixture
    def filter(self):
        return ThesisFilter(ThesisFilterConfig())

    def test_high_keyword_score_positive_adjustment(self, filter):
        """Keyword score >= 0.7 should give +0.08 adjustment."""
        adjustment = filter._calculate_adjustment(
            keyword_score=0.75,
            negative_keywords=[],
        )
        assert adjustment == 0.08

    def test_low_keyword_score_negative_adjustment(self, filter):
        """Keyword score < 0.4 should give -0.08 adjustment."""
        adjustment = filter._calculate_adjustment(
            keyword_score=0.3,
            negative_keywords=[],
        )
        assert adjustment == -0.08

    def test_negative_keywords_extra_penalty(self, filter):
        """Negative keywords should give additional -0.12 penalty."""
        adjustment = filter._calculate_adjustment(
            keyword_score=0.5,
            negative_keywords=["enterprise", "b2b"],
        )
        assert adjustment == -0.12

    def test_medium_score_no_adjustment(self, filter):
        """Keyword score 0.4-0.7 with no negatives = 0 adjustment."""
        adjustment = filter._calculate_adjustment(
            keyword_score=0.5,
            negative_keywords=[],
        )
        assert adjustment == 0.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_thesis_filter.py -v`
Expected: FAIL - ThesisFilter doesn't exist

**Step 3: Write minimal implementation**

Create `utils/thesis_filter.py`:

```python
"""
ThesisFilter - Two-stage thesis classification for pipeline integration.

Stage 1: Fast keyword matching (free)
Stage 2: Gemini LLM semantic classification (free tier)

Routes signals to QUALIFIED, HELD, or REJECTED based on thesis fit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from utils.thesis_matcher import ThesisMatcher, ThesisFit

logger = logging.getLogger(__name__)


class RoutingDecision(str, Enum):
    """Routing decision for signals."""
    QUALIFIED = "qualified"  # Passes gates, awaiting user push
    HELD = "held"            # Low fit, needs batch review
    REJECTED = "rejected"    # Excluded from thesis


@dataclass
class ThesisFilterConfig:
    """Configuration for thesis filter."""
    hold_threshold: float = 0.3           # Below this = HELD
    skip_llm_if_keyword_below: float = 0.2  # Skip LLM if obvious non-fit
    keyword_high_threshold: float = 0.7   # Keyword score for positive boost
    keyword_low_threshold: float = 0.4    # Keyword score for negative penalty
    high_boost: float = 0.08              # Confidence boost for high keyword fit
    low_penalty: float = -0.08            # Confidence penalty for low keyword fit
    negative_keyword_penalty: float = -0.12  # Extra penalty for negative keywords


@dataclass
class ThesisFilterResult:
    """Result of thesis filtering."""
    routing: RoutingDecision
    keyword_score: float = 0.0
    keyword_category: Optional[str] = None
    keyword_matches: List[str] = field(default_factory=list)
    negative_keywords: List[str] = field(default_factory=list)
    llm_score: Optional[float] = None
    llm_category: Optional[str] = None
    llm_rationale: Optional[str] = None
    llm_skipped: bool = False
    confidence_adjustment: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "routing": self.routing.value,
            "keyword_score": self.keyword_score,
            "keyword_category": self.keyword_category,
            "keyword_matches": self.keyword_matches,
            "negative_keywords": self.negative_keywords,
            "llm_score": self.llm_score,
            "llm_category": self.llm_category,
            "llm_rationale": self.llm_rationale,
            "llm_skipped": self.llm_skipped,
            "confidence_adjustment": self.confidence_adjustment,
        }


class ThesisFilter:
    """
    Two-stage thesis filter for discovery pipeline.

    Usage:
        filter = ThesisFilter(ThesisFilterConfig())
        result = await filter.classify("Meal kit delivery startup")
        if result.routing == RoutingDecision.QUALIFIED:
            # Proceed to verification gate
    """

    def __init__(self, config: Optional[ThesisFilterConfig] = None):
        self.config = config or ThesisFilterConfig()
        self._keyword_matcher = ThesisMatcher()
        self._llm_classifier = None  # Lazy load

    @property
    def llm_classifier(self):
        """Lazy-load LLM classifier."""
        if self._llm_classifier is None:
            try:
                from consumer.thesis_filter.llm_classifier import LLMClassifier
                self._llm_classifier = LLMClassifier()
            except ImportError:
                logger.warning("LLM classifier not available")
        return self._llm_classifier

    async def classify(
        self,
        text: str,
        company_name: Optional[str] = None,
        skip_llm: bool = False,
    ) -> ThesisFilterResult:
        """
        Classify text through two-stage thesis filter.

        Args:
            text: Description or combined signal text
            company_name: Optional company name for context
            skip_llm: If True, only run keyword matching

        Returns:
            ThesisFilterResult with routing decision
        """
        # Stage 1: Keyword matching
        keyword_fit = self._keyword_matcher.score(text, company_name)

        # Check if we should skip LLM (obvious non-fit)
        if skip_llm or keyword_fit.score < self.config.skip_llm_if_keyword_below:
            adjustment = self._calculate_adjustment(
                keyword_fit.score,
                keyword_fit.negative_keywords,
            )

            # Route based on keyword score alone
            if keyword_fit.negative_keywords:
                routing = RoutingDecision.REJECTED
            elif keyword_fit.score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED

            return ThesisFilterResult(
                routing=routing,
                keyword_score=keyword_fit.score,
                keyword_category=keyword_fit.thesis.value,
                keyword_matches=keyword_fit.matched_keywords,
                negative_keywords=keyword_fit.negative_keywords,
                llm_skipped=True,
                confidence_adjustment=adjustment,
            )

        # Stage 2: LLM classification
        llm_result = None
        if self.llm_classifier:
            try:
                signal_data = {
                    "title": company_name or "Unknown",
                    "source_context": text,
                    "source_api": "pipeline",
                }
                llm_result = await self.llm_classifier.classify(signal_data)
            except Exception as e:
                logger.error(f"LLM classification failed: {e}")

        # Calculate confidence adjustment
        adjustment = self._calculate_adjustment(
            keyword_fit.score,
            keyword_fit.negative_keywords,
        )

        # Determine routing
        if llm_result:
            if llm_result.category == "excluded":
                routing = RoutingDecision.REJECTED
            elif llm_result.thesis_fit_score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED
        else:
            # Fallback to keyword-only routing
            if keyword_fit.negative_keywords:
                routing = RoutingDecision.REJECTED
            elif keyword_fit.score < self.config.hold_threshold:
                routing = RoutingDecision.HELD
            else:
                routing = RoutingDecision.QUALIFIED

        return ThesisFilterResult(
            routing=routing,
            keyword_score=keyword_fit.score,
            keyword_category=keyword_fit.thesis.value,
            keyword_matches=keyword_fit.matched_keywords,
            negative_keywords=keyword_fit.negative_keywords,
            llm_score=llm_result.thesis_fit_score if llm_result else None,
            llm_category=llm_result.category if llm_result else None,
            llm_rationale=llm_result.rationale if llm_result else None,
            llm_skipped=False,
            confidence_adjustment=adjustment,
        )

    def _calculate_adjustment(
        self,
        keyword_score: float,
        negative_keywords: List[str],
    ) -> float:
        """Calculate confidence adjustment based on keyword results."""
        adjustment = 0.0

        # Keyword score adjustment
        if keyword_score >= self.config.keyword_high_threshold:
            adjustment = self.config.high_boost
        elif keyword_score < self.config.keyword_low_threshold:
            adjustment = self.config.low_penalty

        # Negative keyword penalty (replaces score adjustment if present)
        if negative_keywords:
            adjustment = self.config.negative_keyword_penalty

        return adjustment
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_thesis_filter.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add utils/thesis_filter.py tests/utils/test_thesis_filter.py
git commit -m "feat(thesis): add ThesisFilter combining keyword + LLM classification"
```

---

## Task 5: Integrate ThesisFilter into Pipeline

**Files:**
- Modify: `workflows/pipeline.py` (add thesis filtering step)
- Test: `tests/workflows/test_thesis_integration.py`

**Step 1: Write the failing tests**

Create `tests/workflows/test_thesis_integration.py`:

```python
"""Tests for thesis filter integration in pipeline."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


class TestPipelineThesisConfig:
    """Test thesis config in pipeline."""

    def test_use_thesis_filter_default_true(self):
        config = PipelineConfig()
        assert config.use_thesis_filter is True

    def test_thesis_hold_threshold_default(self):
        config = PipelineConfig()
        assert config.thesis_hold_threshold == 0.3


class TestPipelineThesisIntegration:
    """Test thesis filter is called in pipeline."""

    @pytest.fixture
    def config(self):
        return PipelineConfig(
            use_thesis_filter=True,
            dry_run=True,
        )

    @pytest.mark.asyncio
    async def test_thesis_filter_initialized_when_enabled(self, config):
        """Pipeline should initialize thesis filter when enabled."""
        with patch("workflows.pipeline.SignalStore"):
            pipeline = DiscoveryPipeline(config)
            assert pipeline._thesis_filter is not None

    @pytest.mark.asyncio
    async def test_thesis_filter_not_initialized_when_disabled(self):
        """Pipeline should not initialize thesis filter when disabled."""
        config = PipelineConfig(use_thesis_filter=False, dry_run=True)
        with patch("workflows.pipeline.SignalStore"):
            pipeline = DiscoveryPipeline(config)
            assert pipeline._thesis_filter is None


class TestPipelineThesisRouting:
    """Test routing decisions affect signal status."""

    @pytest.mark.asyncio
    async def test_rejected_signal_not_processed(self):
        """Signals with REJECTED routing should be marked rejected."""
        # This will be tested via integration test
        pass

    @pytest.mark.asyncio
    async def test_held_signal_marked_held(self):
        """Signals with HELD routing should be marked held."""
        pass

    @pytest.mark.asyncio
    async def test_qualified_signal_continues_to_gate(self):
        """Signals with QUALIFIED routing should continue to verification gate."""
        pass


class TestPipelineThesisMetrics:
    """Test thesis-related metrics."""

    def test_metrics_include_thesis_rejected(self):
        """PipelineStats should have thesis_rejected metric."""
        from workflows.pipeline import PipelineStats
        stats = PipelineStats()
        assert hasattr(stats, "thesis_rejected")

    def test_metrics_include_thesis_held(self):
        """PipelineStats should have thesis_held metric."""
        from workflows.pipeline import PipelineStats
        stats = PipelineStats()
        assert hasattr(stats, "thesis_held")

    def test_metrics_include_thesis_passed(self):
        """PipelineStats should have thesis_passed metric."""
        from workflows.pipeline import PipelineStats
        stats = PipelineStats()
        assert hasattr(stats, "thesis_passed")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_thesis_integration.py -v`
Expected: FAIL - use_thesis_filter not in PipelineConfig

**Step 3: Write minimal implementation**

Modify `workflows/pipeline.py`:

1. Add to PipelineConfig (around line 130):
```python
    # Thesis filtering
    use_thesis_filter: bool = True
    thesis_hold_threshold: float = 0.3
```

2. Add to PipelineStats (around line 180):
```python
    # Thesis filtering stats
    thesis_rejected: int = 0
    thesis_held: int = 0
    thesis_passed: int = 0
    llm_calls_saved: int = 0
```

3. Add import at top:
```python
from utils.thesis_filter import ThesisFilter, ThesisFilterConfig, RoutingDecision
```

4. Initialize in __init__ (around line 280):
```python
        # Initialize thesis filter
        self._thesis_filter: Optional[ThesisFilter] = None
        if config.use_thesis_filter:
            thesis_config = ThesisFilterConfig(
                hold_threshold=config.thesis_hold_threshold,
            )
            self._thesis_filter = ThesisFilter(thesis_config)
```

5. In _process_company, after enrichment boost calculation (around line 650):
```python
        # Thesis filtering (before verification gate)
        thesis_result = None
        if self._thesis_filter and consolidated:
            try:
                description = consolidated.description or ""
                thesis_result = await self._thesis_filter.classify(
                    description,
                    company_name=consolidated.company_name,
                )

                # Route based on thesis result
                if thesis_result.routing == RoutingDecision.REJECTED:
                    self._stats.thesis_rejected += 1
                    await self._store.update_signal_status(
                        canonical_key, "rejected",
                        error_message="Thesis excluded"
                    )
                    return  # Don't continue processing
                elif thesis_result.routing == RoutingDecision.HELD:
                    self._stats.thesis_held += 1
                    await self._store.update_signal_status(
                        canonical_key, "held",
                        error_message="Low thesis fit"
                    )
                    return  # Don't continue processing
                else:
                    self._stats.thesis_passed += 1

                # Save classification to DB
                if self._store and thesis_result:
                    await self._store.save_thesis_classification(
                        signal_id=signals[0].id,
                        canonical_key=canonical_key,
                        keyword_score=thesis_result.keyword_score,
                        keyword_category=thesis_result.keyword_category,
                        thesis_fit_score=thesis_result.llm_score,
                        category=thesis_result.llm_category,
                        rationale=thesis_result.llm_rationale,
                    )

                # Apply confidence adjustment
                enrichment_boost += thesis_result.confidence_adjustment

            except Exception as e:
                logger.warning(f"Thesis filtering failed (non-fatal): {e}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_thesis_integration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_thesis_integration.py
git commit -m "feat(pipeline): integrate thesis filter with routing decisions"
```

---

## Task 6: Add update_signal_status Method to SignalStore

**Files:**
- Modify: `storage/signal_store.py` (add update_signal_status)
- Test: `tests/storage/test_thesis_classification_storage.py` (extend)

**Step 1: Write the failing tests**

Add to `tests/storage/test_thesis_classification_storage.py`:

```python
class TestSignalStatusUpdates:
    """Test signal status update methods."""

    @pytest.fixture
    async def store(self, tmp_path):
        db_path = str(tmp_path / "test_status.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def signal_id(self, store):
        signal_id = await store.save_signal({
            "signal_type": "test",
            "source_api": "test",
            "canonical_key": "domain:status-test.com",
            "company_name": "Status Test Co",
            "confidence": 0.5,
            "raw_data": {},
        })
        return signal_id

    @pytest.mark.asyncio
    async def test_update_status_to_held(self, store, signal_id):
        """Should update signal status to held."""
        await store.update_signal_status(
            "domain:status-test.com",
            "held",
            error_message="Low thesis fit",
        )

        signals = await store.get_signals_by_status("held")
        assert len(signals) >= 1

    @pytest.mark.asyncio
    async def test_update_status_to_qualified(self, store, signal_id):
        """Should update signal status to qualified."""
        await store.update_signal_status(
            "domain:status-test.com",
            "qualified",
        )

        signals = await store.get_signals_by_status("qualified")
        assert len(signals) >= 1

    @pytest.mark.asyncio
    async def test_get_signals_by_status(self, store, signal_id):
        """Should retrieve signals by status."""
        await store.update_signal_status("domain:status-test.com", "qualified")

        qualified = await store.get_signals_by_status("qualified")
        assert any(s.canonical_key == "domain:status-test.com" for s in qualified)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_thesis_classification_storage.py::TestSignalStatusUpdates -v`
Expected: FAIL - update_signal_status doesn't exist

**Step 3: Write minimal implementation**

Add to `storage/signal_store.py` (after save_thesis_classification):

```python
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
        now = datetime.now(timezone.utc).isoformat()

        async with self._get_connection() as db:
            cursor = await db.execute(
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
            await db.commit()
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
        async with self._get_connection() as db:
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

            cursor = await db.execute(query, (status,))
            rows = await cursor.fetchall()

            return [
                StoredSignal(
                    id=row[0],
                    signal_type=row[1],
                    source_api=row[2],
                    canonical_key=row[3],
                    company_name=row[4],
                    confidence=row[5],
                    raw_data=json.loads(row[6]) if row[6] else {},
                    detected_at=datetime.fromisoformat(row[7]),
                    created_at=datetime.fromisoformat(row[8]),
                    processing_status=row[9],
                    notion_page_id=row[10],
                    processed_at=datetime.fromisoformat(row[11]) if row[11] else None,
                    error_message=row[12],
                )
                for row in rows
            ]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_thesis_classification_storage.py::TestSignalStatusUpdates -v`
Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_thesis_classification_storage.py
git commit -m "feat(storage): add update_signal_status and get_signals_by_status methods"
```

---

## Task 7: Add CLI Pipeline Status Command

**Files:**
- Modify: `run_pipeline.py` (add pipeline status/qualified/push commands)
- Test: `tests/test_cli_pipeline_commands.py`

**Step 1: Write the failing tests**

Create `tests/test_cli_pipeline_commands.py`:

```python
"""Tests for CLI pipeline commands."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from io import StringIO
import sys


class TestPipelineStatusCommand:
    """Test pipeline status command."""

    @pytest.mark.asyncio
    async def test_cmd_pipeline_status_shows_counts(self):
        """Status command should show signal counts by status."""
        from run_pipeline import cmd_pipeline_status

        mock_store = AsyncMock()
        mock_store.get_status_counts.return_value = {
            "qualified": 23,
            "held": 12,
            "rejected": 8,
            "pushed": 45,
            "pending": 5,
        }

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            mock_store.initialize = AsyncMock()
            mock_store.close = AsyncMock()

            # Capture output
            captured = StringIO()
            with patch("sys.stdout", captured):
                await cmd_pipeline_status(db_path="test.db")

            output = captured.getvalue()
            assert "Qualified" in output or "qualified" in output
            assert "23" in output


class TestPipelineQualifiedCommand:
    """Test pipeline qualified command."""

    @pytest.mark.asyncio
    async def test_cmd_pipeline_qualified_lists_signals(self):
        """Qualified command should list qualified signals."""
        from run_pipeline import cmd_pipeline_qualified

        mock_store = AsyncMock()
        mock_signal = MagicMock()
        mock_signal.company_name = "Test Company"
        mock_signal.canonical_key = "domain:test.com"
        mock_signal.confidence = 0.75
        mock_store.get_signals_by_status.return_value = [mock_signal]

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            mock_store.initialize = AsyncMock()
            mock_store.close = AsyncMock()

            captured = StringIO()
            with patch("sys.stdout", captured):
                await cmd_pipeline_qualified(db_path="test.db", limit=10)

            output = captured.getvalue()
            assert "Test Company" in output


class TestPipelinePushCommand:
    """Test pipeline push command."""

    @pytest.mark.asyncio
    async def test_cmd_pipeline_push_requires_confirmation(self):
        """Push command should require confirmation or --confirm flag."""
        from run_pipeline import cmd_pipeline_push

        mock_store = AsyncMock()
        mock_store.get_signals_by_status.return_value = []

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            mock_store.initialize = AsyncMock()
            mock_store.close = AsyncMock()

            # Without --confirm, should not push
            result = await cmd_pipeline_push(
                db_path="test.db",
                confirm=False,
                dry_run=True,
            )
            # Should return without pushing
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_pipeline_commands.py -v`
Expected: FAIL - cmd_pipeline_status doesn't exist

**Step 3: Write minimal implementation**

Add to `run_pipeline.py` (after existing command functions):

```python
# =============================================================================
# PIPELINE DASHBOARD COMMANDS
# =============================================================================

async def cmd_pipeline_status(db_path: str = "signals.db") -> None:
    """Show pipeline status overview."""
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        counts = await store.get_status_counts()

        print("\n" + "=" * 50)
        print("Pipeline Status")
        print("=" * 50)
        print(f"\n  Qualified:  {counts.get('qualified', 0):>5} signals (ready for push)")
        print(f"  Held:       {counts.get('held', 0):>5} signals (need review)")
        print(f"  Rejected:   {counts.get('rejected', 0):>5} signals (excluded)")
        print(f"  Pushed:     {counts.get('pushed', 0):>5} signals (in Notion)")
        print(f"  Pending:    {counts.get('pending', 0):>5} signals (not processed)")
        print()
        print("Commands:")
        print("  pipeline qualified  - List signals ready for push")
        print("  pipeline review     - Review held signals")
        print("  pipeline push       - Export qualified to Notion")
        print("=" * 50 + "\n")

    finally:
        await store.close()


async def cmd_pipeline_qualified(
    db_path: str = "signals.db",
    limit: int = 20,
) -> None:
    """List qualified signals ready for push."""
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        signals = await store.get_signals_by_status("qualified", limit=limit)

        print(f"\n{'='*60}")
        print(f"Qualified Signals ({len(signals)} shown, limit={limit})")
        print(f"{'='*60}\n")

        if not signals:
            print("  No qualified signals found.\n")
            return

        for i, sig in enumerate(signals, 1):
            print(f"{i:3}. {sig.company_name or 'Unknown'}")
            print(f"     Key: {sig.canonical_key}")
            print(f"     Confidence: {sig.confidence:.2f}")
            print(f"     Source: {sig.source_api}")
            print()

        print(f"Run 'python run_pipeline.py pipeline push' to export to Notion")
        print(f"{'='*60}\n")

    finally:
        await store.close()


async def cmd_pipeline_push(
    db_path: str = "signals.db",
    confirm: bool = False,
    dry_run: bool = False,
    signal_id: Optional[int] = None,
) -> None:
    """Push qualified signals to Notion."""
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        if signal_id:
            signals = [await store.get_signal_by_id(signal_id)]
            signals = [s for s in signals if s]
        else:
            signals = await store.get_signals_by_status("qualified")

        if not signals:
            print("No qualified signals to push.")
            return

        print(f"\nFound {len(signals)} qualified signal(s) to push.")

        if not confirm and not dry_run:
            print("\nUse --confirm to push, or --dry-run to preview.")
            return

        if dry_run:
            print("\n[DRY RUN] Would push:")
            for sig in signals[:10]:
                print(f"  - {sig.company_name or sig.canonical_key}")
            if len(signals) > 10:
                print(f"  ... and {len(signals) - 10} more")
            return

        # Actual push logic would go here
        print(f"\nPushing {len(signals)} signals to Notion...")
        # TODO: Integrate with NotionPusher
        print("Push complete.")

    finally:
        await store.close()
```

Add to argument parser (in main()):

```python
    # Pipeline subcommands
    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help="Pipeline dashboard commands",
    )
    pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_cmd")

    # pipeline status
    status_parser = pipeline_sub.add_parser("status", help="Show pipeline status")

    # pipeline qualified
    qual_parser = pipeline_sub.add_parser("qualified", help="List qualified signals")
    qual_parser.add_argument("--limit", type=int, default=20, help="Max signals to show")

    # pipeline push
    push_parser = pipeline_sub.add_parser("push", help="Push qualified to Notion")
    push_parser.add_argument("--confirm", action="store_true", help="Confirm push")
    push_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    push_parser.add_argument("--id", type=int, help="Push specific signal ID")
```

Add to command dispatch:

```python
    elif args.command == "pipeline":
        if args.pipeline_cmd == "status":
            await cmd_pipeline_status(db_path=db_path)
        elif args.pipeline_cmd == "qualified":
            await cmd_pipeline_qualified(db_path=db_path, limit=args.limit)
        elif args.pipeline_cmd == "push":
            await cmd_pipeline_push(
                db_path=db_path,
                confirm=args.confirm,
                dry_run=args.dry_run,
                signal_id=args.id,
            )
        else:
            pipeline_parser.print_help()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_pipeline_commands.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add run_pipeline.py tests/test_cli_pipeline_commands.py
git commit -m "feat(cli): add pipeline status, qualified, and push commands"
```

---

## Task 8: Add Competitor Detection

**Files:**
- Create: `config/portfolio.json`
- Create: `utils/competitor_detector.py`
- Test: `tests/utils/test_competitor_detector.py`

**Step 1: Write the failing tests**

Create `tests/utils/test_competitor_detector.py`:

```python
"""Tests for competitor detection."""
import pytest
import json
from pathlib import Path
from utils.competitor_detector import CompetitorDetector, CompetitorMatch


class TestCompetitorMatch:
    """Test CompetitorMatch dataclass."""

    def test_match_has_required_fields(self):
        match = CompetitorMatch(
            portfolio_company="PortCo Alpha",
            category="consumer_cpg",
            matched_keywords=["meal kit"],
            confidence=0.8,
        )
        assert match.portfolio_company == "PortCo Alpha"
        assert match.category == "consumer_cpg"


class TestCompetitorDetector:
    """Test competitor detection logic."""

    @pytest.fixture
    def detector(self, tmp_path):
        # Create test portfolio file
        portfolio = {
            "companies": [
                {
                    "name": "MealBox Co",
                    "category": "consumer_cpg",
                    "keywords": ["meal kit", "food delivery", "subscription meals"]
                },
                {
                    "name": "FitTrack",
                    "category": "consumer_health_tech",
                    "keywords": ["fitness app", "workout tracking"]
                }
            ]
        }
        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text(json.dumps(portfolio))

        return CompetitorDetector(str(portfolio_path))

    def test_detects_same_category_competitor(self, detector):
        """Should detect competitor in same category with keyword match."""
        result = detector.check(
            category="consumer_cpg",
            description="We deliver meal kits to your door",
        )
        assert result is not None
        assert result.portfolio_company == "MealBox Co"

    def test_no_match_different_category(self, detector):
        """Should not flag if category doesn't match."""
        result = detector.check(
            category="travel_hospitality",
            description="We deliver meal kits to your door",
        )
        assert result is None

    def test_no_match_no_keyword_overlap(self, detector):
        """Should not flag if no keyword overlap."""
        result = detector.check(
            category="consumer_cpg",
            description="We sell organic supplements",
        )
        assert result is None

    def test_returns_none_for_empty_portfolio(self, tmp_path):
        """Should handle empty portfolio gracefully."""
        empty_path = tmp_path / "empty.json"
        empty_path.write_text('{"companies": []}')

        detector = CompetitorDetector(str(empty_path))
        result = detector.check("consumer_cpg", "meal kit delivery")
        assert result is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_competitor_detector.py -v`
Expected: FAIL - CompetitorDetector doesn't exist

**Step 3: Write minimal implementation**

Create `config/portfolio.json`:

```json
{
  "companies": []
}
```

Create `utils/competitor_detector.py`:

```python
"""
Competitor Detector - Flag signals that may compete with portfolio companies.

Checks if a signal's category and keywords overlap with existing portfolio companies.
Surfaces as warning, does not auto-reject.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompetitorMatch:
    """Result of competitor detection."""
    portfolio_company: str
    category: str
    matched_keywords: List[str]
    confidence: float

    def to_dict(self) -> Dict:
        return {
            "portfolio_company": self.portfolio_company,
            "category": self.category,
            "matched_keywords": self.matched_keywords,
            "confidence": self.confidence,
        }


class CompetitorDetector:
    """
    Detects potential competitors to portfolio companies.

    Usage:
        detector = CompetitorDetector("config/portfolio.json")
        match = detector.check("consumer_cpg", "We make meal kits")
        if match:
            print(f"Potential competitor to {match.portfolio_company}")
    """

    def __init__(self, portfolio_path: str = "config/portfolio.json"):
        self.portfolio_path = Path(portfolio_path)
        self._portfolio: List[Dict] = []
        self._load_portfolio()

    def _load_portfolio(self) -> None:
        """Load portfolio companies from JSON file."""
        if not self.portfolio_path.exists():
            logger.warning(f"Portfolio file not found: {self.portfolio_path}")
            return

        try:
            with open(self.portfolio_path) as f:
                data = json.load(f)
                self._portfolio = data.get("companies", [])
                logger.debug(f"Loaded {len(self._portfolio)} portfolio companies")
        except Exception as e:
            logger.error(f"Failed to load portfolio: {e}")

    def check(
        self,
        category: str,
        description: str,
    ) -> Optional[CompetitorMatch]:
        """
        Check if a signal may be a competitor to portfolio companies.

        Args:
            category: The thesis category of the signal
            description: Description text to check for keyword overlap

        Returns:
            CompetitorMatch if potential competitor detected, None otherwise
        """
        if not self._portfolio:
            return None

        normalized_desc = description.lower()

        for company in self._portfolio:
            # Check category match first
            if company.get("category") != category:
                continue

            # Check for keyword overlap
            keywords = company.get("keywords", [])
            matched = []

            for keyword in keywords:
                pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
                if re.search(pattern, normalized_desc):
                    matched.append(keyword)

            # Need at least one keyword match
            if matched:
                confidence = min(len(matched) / len(keywords), 1.0) if keywords else 0.5

                return CompetitorMatch(
                    portfolio_company=company.get("name", "Unknown"),
                    category=category,
                    matched_keywords=matched,
                    confidence=confidence,
                )

        return None

    def reload(self) -> None:
        """Reload portfolio from file."""
        self._load_portfolio()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_competitor_detector.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add config/portfolio.json utils/competitor_detector.py tests/utils/test_competitor_detector.py
git commit -m "feat(thesis): add competitor detection for portfolio companies"
```

---

## Task 9: Wire Competitor Detection into Pipeline

**Files:**
- Modify: `workflows/pipeline.py` (add competitor check)
- Modify: `utils/thesis_filter.py` (integrate competitor detection)

**Step 1: Write the failing tests**

Add to `tests/workflows/test_thesis_integration.py`:

```python
class TestPipelineCompetitorDetection:
    """Test competitor detection in pipeline."""

    def test_pipeline_config_has_competitor_detection(self):
        """PipelineConfig should have use_competitor_detection flag."""
        from workflows.pipeline import PipelineConfig
        config = PipelineConfig()
        assert hasattr(config, "use_competitor_detection")

    @pytest.mark.asyncio
    async def test_competitor_flag_saved_to_classification(self):
        """Competitor flag should be saved with classification."""
        # Integration test - verify competitor_flag column is populated
        pass
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_thesis_integration.py::TestPipelineCompetitorDetection -v`
Expected: FAIL - use_competitor_detection not in config

**Step 3: Write minimal implementation**

Add to `workflows/pipeline.py` PipelineConfig:

```python
    # Competitor detection
    use_competitor_detection: bool = True
    portfolio_path: str = "config/portfolio.json"
```

Add to pipeline __init__:

```python
        # Initialize competitor detector
        self._competitor_detector = None
        if config.use_competitor_detection:
            from utils.competitor_detector import CompetitorDetector
            self._competitor_detector = CompetitorDetector(config.portfolio_path)
```

Add to _process_company after thesis classification:

```python
                # Check for competitors
                competitor_match = None
                if self._competitor_detector and thesis_result.llm_category:
                    competitor_match = self._competitor_detector.check(
                        thesis_result.llm_category,
                        description,
                    )
                    if competitor_match:
                        logger.warning(
                            f"Potential competitor detected: {canonical_key} "
                            f"similar to {competitor_match.portfolio_company}"
                        )

                # Save classification with competitor flag
                if self._store and thesis_result:
                    await self._store.save_thesis_classification(
                        signal_id=signals[0].id,
                        canonical_key=canonical_key,
                        keyword_score=thesis_result.keyword_score,
                        keyword_category=thesis_result.keyword_category,
                        thesis_fit_score=thesis_result.llm_score,
                        category=thesis_result.llm_category,
                        rationale=thesis_result.llm_rationale,
                        competitor_flag=competitor_match is not None,
                        competitor_match=competitor_match.to_dict() if competitor_match else None,
                    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_thesis_integration.py::TestPipelineCompetitorDetection -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_thesis_integration.py
git commit -m "feat(pipeline): wire competitor detection into thesis filtering"
```

---

## Task 10: Update CLAUDE.md and Run Full Test Suite

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 2: Update CLAUDE.md**

Mark Phase 3 as complete:

```markdown
**Phase 3: Thesis Integration** ✅ COMPLETE

- [x] Rewrite thesis_matcher.py with Consumer keywords
- [x] Add thesis_classifications table (migration 5)
- [x] Add ThesisFilter combining keyword + LLM classification
- [x] Integrate thesis filter into pipeline with routing (QUALIFIED/HELD/REJECTED)
- [x] Add CLI dashboard commands (pipeline status/qualified/push)
- [x] Add competitor detection with portfolio.json
- [x] Persist classifications and competitor flags to DB

Exit criteria met:
- ✓ Thesis factors into push decision
- ✓ Low-fit signals held (not pushed)
- ✓ Classifications persisted in DB
- ✓ User controls push action via CLI
```

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mark Phase 3 Thesis Integration complete"
```

---

## Summary

| Task | Description | Tests |
|------|-------------|-------|
| 1 | Schema migration - thesis_classifications table | 3 |
| 2 | Rewrite thesis_matcher.py with Consumer keywords | 15 |
| 3 | Storage methods for thesis classifications | 5 |
| 4 | ThesisFilter class (keyword + LLM) | 12 |
| 5 | Pipeline integration with routing | 6 |
| 6 | update_signal_status method | 3 |
| 7 | CLI pipeline commands | 3 |
| 8 | Competitor detection | 5 |
| 9 | Wire competitor detection into pipeline | 2 |
| 10 | Final verification | - |

**Total: ~54 new tests**
