"""Tests for workflows/semantic_filter.py — Phase 3 Task 3.8."""

from __future__ import annotations

import pickle

import pytest
from sklearn.feature_extraction.text import TfidfVectorizer

from workflows.semantic_filter import (
    check_exemplar_veto,
    get_case_law_context,
    SemanticVetoResult,
    CaseLawContext,
)
from intelligence.exemplar_matcher import ExemplarMatcher
from intelligence.case_law_retriever import CaseLawRetriever


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vectorizer():
    """Shared TF-IDF vectorizer for tests."""
    v = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), min_df=1)
    v.fit([
        "healthy snacks organic food consumer product",
        "fitness wellness app health tech platform",
        "enterprise saas b2b developer tools cloud",
        "travel booking hotel hospitality marketplace",
    ])
    return v


@pytest.fixture
def exemplar_matcher(vectorizer):
    return ExemplarMatcher(vectorizer=vectorizer)


@pytest.fixture
def case_law_retriever(vectorizer):
    return CaseLawRetriever(vectorizer=vectorizer)


def _make_exemplar(vectorizer, text, key="test_exemplar", category="consumer_cpg"):
    """Create a fake exemplar dict with TF-IDF vector."""
    vec = vectorizer.transform([text])
    return {
        "exemplar_key": key,
        "category": category,
        "description": f"Test exemplar: {key}",
        "tfidf_vector": pickle.dumps(vec[0]),
        "company_name": "TestCo",
        "source": "auto",
        "vectorizer_version": "v1.0.0",
    }


def _make_precedent(vectorizer, text, label, signal_id=1, company_name="TestCo"):
    """Create a fake precedent dict with TF-IDF vector."""
    vec = vectorizer.transform([text])
    return {
        "signal_id": signal_id,
        "canonical_key": f"domain:{company_name.lower()}.com",
        "company_name": company_name,
        "human_label": label,
        "tfidf_vector": pickle.dumps(vec[0]),
        "label_reason": f"{label} label",
        "source_api": "github",
        "confidence": 0.6,
        "signal_created_at": "2025-06-01T00:00:00Z",
        "vectorizer_version": "v1.0.0",
    }


# ---------------------------------------------------------------------------
# check_exemplar_veto tests
# ---------------------------------------------------------------------------

class TestCheckExemplarVeto:
    def test_veto_active_high_similarity(self, vectorizer, exemplar_matcher):
        exemplars = [_make_exemplar(vectorizer, "healthy snacks organic food consumer product")]
        result = check_exemplar_veto(
            "healthy snacks organic food consumer product",
            exemplar_matcher, exemplars, threshold=0.5,
        )
        assert result.veto_active is True
        assert result.max_similarity > 0.5
        assert result.matched_exemplar == "test_exemplar"
        assert result.matched_category == "consumer_cpg"

    def test_veto_inactive_low_similarity(self, vectorizer, exemplar_matcher):
        exemplars = [_make_exemplar(vectorizer, "enterprise saas b2b developer tools cloud")]
        result = check_exemplar_veto(
            "healthy snacks organic food consumer product",
            exemplar_matcher, exemplars, threshold=0.75,
        )
        assert result.veto_active is False
        assert result.max_similarity < 0.75

    def test_veto_empty_query(self, exemplar_matcher):
        result = check_exemplar_veto("", exemplar_matcher, [])
        assert result.veto_active is False
        assert result.max_similarity == 0.0
        assert result.matched_exemplar is None

    def test_veto_empty_exemplars(self, exemplar_matcher):
        result = check_exemplar_veto("healthy snacks", exemplar_matcher, [])
        assert result.veto_active is False

    def test_veto_threshold_boundary(self, vectorizer, exemplar_matcher):
        # Same text → similarity ~1.0, well above any threshold
        exemplars = [_make_exemplar(vectorizer, "travel booking hotel")]
        result = check_exemplar_veto(
            "travel booking hotel",
            exemplar_matcher, exemplars, threshold=0.99,
        )
        assert result.veto_active is True

    def test_veto_returns_best_match(self, vectorizer, exemplar_matcher):
        exemplars = [
            _make_exemplar(vectorizer, "enterprise saas b2b tools", key="bad_match", category="b2b"),
            _make_exemplar(vectorizer, "healthy snacks organic consumer", key="good_match", category="cpg"),
        ]
        result = check_exemplar_veto(
            "healthy snacks organic consumer food",
            exemplar_matcher, exemplars, threshold=0.3,
        )
        assert result.veto_active is True
        assert result.matched_exemplar == "good_match"
        assert result.matched_category == "cpg"


# ---------------------------------------------------------------------------
# get_case_law_context tests
# ---------------------------------------------------------------------------

class TestGetCaseLawContext:
    def test_returns_wins_and_losses(self, vectorizer, case_law_retriever):
        precedents = [
            _make_precedent(vectorizer, "healthy organic food snacks", "TP", signal_id=1, company_name="WinCo"),
            _make_precedent(vectorizer, "healthy organic food snacks consumer", "FP", signal_id=2, company_name="LossCo"),
        ]
        ctx = get_case_law_context(
            "healthy organic food consumer product",
            case_law_retriever, precedents,
        )
        assert ctx.win_count >= 1
        assert ctx.loss_count >= 1
        assert ctx.top_win_similarity > 0
        assert ctx.top_loss_similarity > 0
        assert len(ctx.wins_summary) >= 1
        assert ctx.wins_summary[0][0] == "WinCo"

    def test_empty_query_returns_empty(self, case_law_retriever):
        ctx = get_case_law_context("", case_law_retriever, [])
        assert ctx.win_count == 0
        assert ctx.loss_count == 0
        assert ctx.wins_summary == []
        assert ctx.losses_summary == []

    def test_empty_precedents(self, case_law_retriever):
        ctx = get_case_law_context("some text", case_law_retriever, [])
        assert ctx.win_count == 0
        assert ctx.loss_count == 0

    def test_context_summary_format(self, vectorizer, case_law_retriever):
        precedents = [
            _make_precedent(vectorizer, "fitness wellness health tech", "TP", signal_id=1, company_name="FitWin"),
        ]
        ctx = get_case_law_context(
            "fitness wellness health tech platform",
            case_law_retriever, precedents,
        )
        # Summary entries are (company_name, similarity, label_reason)
        assert len(ctx.wins_summary) == 1
        name, sim, reason = ctx.wins_summary[0]
        assert name == "FitWin"
        assert isinstance(sim, float)
        assert isinstance(reason, str)
