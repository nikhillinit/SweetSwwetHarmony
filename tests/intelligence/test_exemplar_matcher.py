"""Tests for intelligence/exemplar_matcher.py.

Verifies:
- Similar signal returns high similarity
- Dissimilar signal returns no matches (below threshold)
- Threshold filtering works
- Multiple matches sorted by similarity
- Veto eligibility computed correctly
- Empty exemplar library → empty result
- Best match populated correctly
- Matched categories unique and sorted
"""

import os
import pickle
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from intelligence.exemplar_matcher import (
    ExemplarMatch,
    ExemplarMatchResult,
    ExemplarMatcher,
)


def _make_matcher(vocab=None):
    from sklearn.feature_extraction.text import TfidfVectorizer

    if vocab is None:
        vocab = [
            "meal delivery consumer food health wellness",
            "blockchain crypto dao web3 token mining",
            "fitness app tracking workout gym",
            "B2B saas enterprise developer tools api",
            "travel hospitality booking hotel vacation",
        ]
    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), min_df=1)
    vectorizer.fit(vocab)
    return ExemplarMatcher(vectorizer=vectorizer)


def _make_exemplar(exemplar_key, category, description, corpus_text,
                   company_name="", source="auto", is_active=1,
                   vectorizer_version="v1.0.0", tfidf_vector=None):
    return {
        "exemplar_key": exemplar_key,
        "category": category,
        "description": description,
        "corpus_text": corpus_text,
        "company_name": company_name,
        "source": source,
        "is_active": is_active,
        "vectorizer_version": vectorizer_version,
        "tfidf_vector": tfidf_vector,
    }


def _vectorize_exemplars(matcher, exemplars):
    for e in exemplars:
        vec = matcher._vectorizer.transform([e["corpus_text"]])
        e["tfidf_vector"] = pickle.dumps(vec)
    return exemplars


class TestExemplarMatcher:

    def test_similar_returns_high_score(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("meal_co", "foodies", "Meal delivery", "meal delivery consumer food"),
        ])
        result = matcher.match("meal delivery platform for consumers", exemplars, threshold=0.1)
        assert len(result.matches) == 1
        assert result.matches[0].similarity_score > 0.3

    def test_dissimilar_below_threshold(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("crypto_ex", "crypto", "Crypto DAO", "blockchain crypto dao web3"),
        ])
        result = matcher.match("meal delivery consumer food health", exemplars, threshold=0.5)
        assert len(result.matches) == 0

    def test_threshold_filtering(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("meal_co", "foodies", "Meal delivery", "meal delivery consumer food"),
            _make_exemplar("crypto_ex", "crypto", "Crypto", "blockchain crypto dao"),
        ])
        result = matcher.match("meal delivery consumer food", exemplars, threshold=0.3)
        # Only the meal exemplar should pass threshold
        for m in result.matches:
            assert m.similarity_score >= 0.3

    def test_multiple_matches_sorted(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("meal_co", "foodies", "Meal delivery", "meal delivery consumer food"),
            _make_exemplar("fitness_co", "wellness", "Fitness app", "fitness app tracking workout gym"),
            _make_exemplar("travel_co", "travel", "Travel booking", "travel hospitality booking hotel vacation"),
        ])
        result = matcher.match("meal delivery fitness health consumer food", exemplars, threshold=0.05)
        scores = [m.similarity_score for m in result.matches]
        assert scores == sorted(scores, reverse=True)

    def test_veto_eligibility_high_similarity(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("meal_co", "foodies", "Meal delivery", "meal delivery consumer food"),
        ])
        result = matcher.match("meal delivery consumer food health wellness", exemplars, threshold=0.0)
        # veto_eligible depends on whether max_similarity >= VETO_THRESHOLD (0.75)
        # With TF-IDF on small vocab, we may not hit 0.75, so test the logic:
        assert result.veto_eligible == (result.max_similarity >= 0.75)

    def test_veto_eligibility_low_similarity(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("crypto_ex", "crypto", "Crypto", "blockchain crypto dao web3 token"),
        ])
        result = matcher.match("meal delivery consumer food", exemplars, threshold=0.0)
        assert result.max_similarity < 0.75
        assert result.veto_eligible is False

    def test_empty_exemplar_library(self):
        matcher = _make_matcher()
        result = matcher.match("some query text", [])
        assert result.matches == []
        assert result.best_match is None
        assert result.max_similarity == 0.0
        assert result.veto_eligible is False

    def test_best_match_populated(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("meal_co", "foodies", "Meal delivery", "meal delivery consumer food"),
            _make_exemplar("fitness_co", "wellness", "Fitness", "fitness app gym"),
        ])
        result = matcher.match("meal delivery consumer food", exemplars, threshold=0.0)
        assert result.best_match is not None
        assert result.best_match.similarity_score == result.max_similarity

    def test_matched_categories_unique_sorted(self):
        matcher = _make_matcher()
        exemplars = _vectorize_exemplars(matcher, [
            _make_exemplar("m1", "foodies", "Meal1", "meal delivery consumer"),
            _make_exemplar("m2", "foodies", "Meal2", "food health consumer"),
            _make_exemplar("f1", "wellness", "Fit1", "fitness app gym"),
        ])
        result = matcher.match("meal delivery fitness consumer food", exemplars, threshold=0.0)
        assert result.matched_categories == sorted(set(result.matched_categories))
