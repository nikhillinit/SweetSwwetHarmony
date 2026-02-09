"""Exemplar similarity scoring against thesis exemplar library.

Matches a signal against curated TP exemplar patterns using TF-IDF
cosine similarity. Used for veto logic and promotion rules.

Phase 3 — case-law + exemplars.
"""

from __future__ import annotations

import logging
import os
import pickle
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

VETO_THRESHOLD = float(os.environ.get("EXEMPLAR_VETO_THRESHOLD", "0.75"))


@dataclass
class ExemplarMatch:
    exemplar_key: str
    category: str
    description: str
    similarity_score: float  # 0.0-1.0
    company_name: str
    source: str  # "auto", "manual", "portfolio"


@dataclass
class ExemplarMatchResult:
    matches: List[ExemplarMatch]  # All matches above threshold
    best_match: Optional[ExemplarMatch]  # Highest similarity
    max_similarity: float  # Convenience: best match score
    matched_categories: List[str]  # Unique categories matched, sorted
    vectorizer_version: str
    veto_eligible: bool  # True if max_similarity >= VETO_THRESHOLD


class ExemplarMatcher:
    """Scores signal similarity against thesis exemplar library."""

    def __init__(self, vectorizer=None, vectorizer_path: Optional[str] = None):
        if vectorizer is not None:
            self._vectorizer = vectorizer
        elif vectorizer_path:
            import joblib
            self._vectorizer = joblib.load(vectorizer_path)
        else:
            raise ValueError("Must provide either vectorizer or vectorizer_path")

    def match(
        self,
        query_text: str,
        exemplars: List[dict],
        threshold: float = 0.5,
    ) -> ExemplarMatchResult:
        """Find matching exemplars above threshold.

        Args:
            query_text: TF-IDF query text (from build_corpus_text).
            exemplars: List of dicts from thesis_exemplars table.
            threshold: Minimum similarity to report.

        Returns:
            ExemplarMatchResult with filtered, sorted matches.
        """
        empty = ExemplarMatchResult(
            matches=[], best_match=None, max_similarity=0.0,
            matched_categories=[], vectorizer_version="", veto_eligible=False,
        )

        if not query_text or not query_text.strip() or not exemplars:
            return empty

        # Transform query
        query_vec = self._vectorizer.transform([query_text])

        # Compute similarities
        from sklearn.metrics.pairwise import cosine_similarity
        import scipy.sparse as sp

        exemplar_vecs = []
        valid_exemplars = []
        for e in exemplars:
            blob = e.get("tfidf_vector")
            if blob:
                try:
                    vec = pickle.loads(blob)
                    exemplar_vecs.append(vec)
                    valid_exemplars.append(e)
                except Exception as exc:
                    logger.warning("Failed to deserialize exemplar vector: %s", exc)

        if not exemplar_vecs:
            return empty

        stacked = sp.vstack(exemplar_vecs)
        similarities = cosine_similarity(query_vec, stacked).flatten()

        # Build matches above threshold
        matches = []
        for i, e in enumerate(valid_exemplars):
            score = float(similarities[i])
            if score >= threshold:
                matches.append(ExemplarMatch(
                    exemplar_key=e["exemplar_key"],
                    category=e["category"],
                    description=e["description"],
                    similarity_score=score,
                    company_name=e.get("company_name", ""),
                    source=e.get("source", "auto"),
                ))

        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        best = matches[0] if matches else None
        max_sim = best.similarity_score if best else 0.0
        categories = sorted(set(m.category for m in matches))
        version = valid_exemplars[0].get("vectorizer_version", "") if valid_exemplars else ""

        return ExemplarMatchResult(
            matches=matches,
            best_match=best,
            max_similarity=max_sim,
            matched_categories=categories,
            vectorizer_version=version,
            veto_eligible=max_sim >= VETO_THRESHOLD,
        )

    def match_from_db(
        self,
        query_text: str,
        db_conn,
        vectorizer_version: str,
        threshold: float = 0.5,
    ) -> ExemplarMatchResult:
        """Convenience: load exemplars from DB, then match."""
        cursor = db_conn.execute(
            "SELECT * FROM thesis_exemplars WHERE vectorizer_version = ? AND is_active = 1",
            (vectorizer_version,),
        )
        columns = [d[0] for d in cursor.description]
        exemplars = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return self.match(query_text, exemplars, threshold)
