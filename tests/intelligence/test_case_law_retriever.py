"""Tests for intelligence/case_law_retriever.py.

Verifies:
- Similar TP signal returns high similarity score
- Dissimilar signal returns low similarity
- Top-K partitioning: wins from TP, losses from FP
- Empty precedents handled gracefully
- Query with empty text returns empty result
- Max similarity per label computed correctly (max_similarity_tp, max_similarity_fp)
- Result sorting by similarity (descending)
- Recent precedent → is_stale = False
- Old precedent → is_stale = True
- Stale precedents still returned (not filtered out)
"""

import os
import pickle
import sys

import numpy as np
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from intelligence.case_law_retriever import (
    CaseLawRetriever,
    CaseLawResult,
    PrecedentMatch,
    STALE_THRESHOLD_DAYS,
)


def _make_retriever(vocab=None):
    """Create a retriever with a mock vectorizer."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    if vocab is None:
        vocab = [
            "meal delivery consumer food health wellness",
            "blockchain crypto dao web3 token mining",
            "fitness app tracking workout gym",
            "B2B saas enterprise developer tools api",
        ]
    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), min_df=1)
    vectorizer.fit(vocab)
    return CaseLawRetriever(vectorizer=vectorizer)


def _make_precedent(signal_id, canonical_key, company_name, label, corpus_text,
                    similarity_text_hash="", source_api="test", confidence=0.5,
                    label_reason="", signal_created_at="2025-06-15T10:00:00Z",
                    vectorizer_version="v1.0.0", tfidf_vector=None):
    """Create a precedent dict matching DB row format."""
    return {
        "signal_id": signal_id,
        "canonical_key": canonical_key,
        "company_name": company_name,
        "human_label": label,
        "corpus_text": corpus_text,
        "tfidf_vector": tfidf_vector,
        "similarity_text_hash": similarity_text_hash,
        "signal_created_at": signal_created_at,
        "vectorizer_version": vectorizer_version,
        "label_reason": label_reason,
        "source_api": source_api,
        "confidence": confidence,
    }


def _vectorize_precedents(retriever, precedents):
    """Pre-vectorize precedent corpus_text and store as tfidf_vector blob."""
    for p in precedents:
        vec = retriever._vectorizer.transform([p["corpus_text"]])
        p["tfidf_vector"] = pickle.dumps(vec)
    return precedents


class TestFindSimilar:
    """Tests for CaseLawRetriever.find_similar()."""

    def test_similar_tp_returns_high_score(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "domain:a.com", "MealCo", "TP", "meal delivery consumer food"),
        ])
        result = retriever.find_similar("meal delivery platform for consumers", precedents)
        assert len(result.wins) == 1
        assert result.wins[0].similarity_score > 0.3

    def test_dissimilar_returns_low_score(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "domain:a.com", "CryptoCo", "FP", "blockchain crypto dao"),
        ])
        result = retriever.find_similar("meal delivery platform for consumers", precedents)
        assert len(result.losses) == 1
        assert result.losses[0].similarity_score < 0.3

    def test_top_k_partitioning(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "WinCo", "TP", "meal delivery consumer food"),
            _make_precedent(2, "d:b", "Win2", "TP", "fitness app health wellness"),
            _make_precedent(3, "d:c", "LossCo", "FP", "blockchain crypto dao"),
            _make_precedent(4, "d:d", "Loss2", "FP", "B2B saas enterprise tools"),
        ])
        result = retriever.find_similar("meal delivery health food", precedents, top_k_wins=2, top_k_losses=2)
        assert all(m.human_label == "TP" for m in result.wins)
        assert all(m.human_label == "FP" for m in result.losses)
        assert len(result.wins) <= 2
        assert len(result.losses) <= 2

    def test_empty_precedents(self):
        retriever = _make_retriever()
        result = retriever.find_similar("some query", [])
        assert result.wins == []
        assert result.losses == []
        assert result.max_similarity_tp == 0.0
        assert result.max_similarity_fp == 0.0

    def test_empty_query_text(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "Co", "TP", "meal delivery"),
        ])
        result = retriever.find_similar("", precedents)
        assert result.wins == []
        assert result.losses == []

    def test_max_similarity_per_label(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "WinCo", "TP", "meal delivery consumer food"),
            _make_precedent(2, "d:b", "Win2", "TP", "fitness wellness health"),
            _make_precedent(3, "d:c", "LossCo", "FP", "crypto blockchain dao token"),
        ])
        result = retriever.find_similar("meal delivery health food consumer", precedents)
        # max_similarity_tp should be the highest among TP wins
        if result.wins:
            assert result.max_similarity_tp == result.wins[0].similarity_score
        # max_similarity_fp should be the highest among FP losses
        if result.losses:
            assert result.max_similarity_fp == result.losses[0].similarity_score
        # They should be different
        assert result.max_similarity_tp != result.max_similarity_fp or len(result.losses) == 0

    def test_results_sorted_descending(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "A", "TP", "meal delivery consumer"),
            _make_precedent(2, "d:b", "B", "TP", "fitness wellness health"),
            _make_precedent(3, "d:c", "C", "TP", "food app nutrition tracking"),
        ])
        result = retriever.find_similar("meal delivery food consumer", precedents, top_k_wins=3)
        scores = [m.similarity_score for m in result.wins]
        assert scores == sorted(scores, reverse=True)


class TestRecency:
    """Tests for staleness flagging (Task 3.4)."""

    def test_recent_precedent_not_stale(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "Co", "TP", "meal delivery consumer",
                           signal_created_at="2025-06-15T10:00:00Z"),
        ])
        result = retriever.find_similar("meal delivery consumer food", precedents)
        assert len(result.wins) == 1
        assert result.wins[0].is_stale is False

    def test_old_precedent_is_stale(self):
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "Co", "TP", "meal delivery consumer",
                           signal_created_at="2020-01-01T00:00:00Z"),
        ])
        result = retriever.find_similar("meal delivery consumer food", precedents)
        assert len(result.wins) == 1
        assert result.wins[0].is_stale is True

    def test_stale_precedents_still_returned(self):
        """Stale precedents should be included, not filtered out."""
        retriever = _make_retriever()
        precedents = _vectorize_precedents(retriever, [
            _make_precedent(1, "d:a", "OldCo", "TP", "meal delivery consumer",
                           signal_created_at="2019-01-01T00:00:00Z"),
            _make_precedent(2, "d:b", "NewCo", "TP", "fitness wellness health",
                           signal_created_at="2025-12-01T00:00:00Z"),
        ])
        result = retriever.find_similar("meal delivery fitness consumer", precedents, top_k_wins=5)
        assert len(result.wins) == 2
        stale_flags = {m.company_name: m.is_stale for m in result.wins}
        assert stale_flags["OldCo"] is True
        assert stale_flags["NewCo"] is False
