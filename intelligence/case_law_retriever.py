"""TF-IDF case-law retrieval for similar precedents.

Finds the most similar TP (win) and FP (loss) precedents for a given signal
using precomputed TF-IDF vectors in the precedents table.

Phase 3 — case-law + exemplars.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

STALE_THRESHOLD_DAYS = 365 * 3  # 3 years


@dataclass
class PrecedentMatch:
    signal_id: int
    canonical_key: str
    company_name: str
    human_label: str  # "TP" or "FP"
    similarity_score: float  # 0.0-1.0
    label_reason: str
    source_api: str
    confidence: float
    signal_created_at: str  # Original signal creation date
    is_stale: bool = False  # True if signal > 3 years old


@dataclass
class CaseLawResult:
    wins: List[PrecedentMatch]  # Top-K TP precedents
    losses: List[PrecedentMatch]  # Top-K FP precedents
    max_similarity_tp: float  # Highest similarity among TP (wins)
    max_similarity_fp: float  # Highest similarity among FP (losses)
    vectorizer_version: str
    query_text_length: int


class CaseLawRetriever:
    """Retrieves similar TP/FP precedents using TF-IDF cosine similarity."""

    def __init__(self, vectorizer=None, vectorizer_path: Optional[str] = None):
        """Load pre-trained TF-IDF vectorizer.

        Args:
            vectorizer: Pre-loaded sklearn TfidfVectorizer (for testing).
            vectorizer_path: Path to joblib-serialized vectorizer.
        """
        if vectorizer is not None:
            self._vectorizer = vectorizer
        elif vectorizer_path:
            import joblib
            self._vectorizer = joblib.load(vectorizer_path)
        else:
            raise ValueError("Must provide either vectorizer or vectorizer_path")

    def find_similar(
        self,
        query_text: str,
        precedents: List[dict],
        top_k_wins: int = 3,
        top_k_losses: int = 3,
    ) -> CaseLawResult:
        """Find top-K similar wins (TP) and losses (FP).

        Args:
            query_text: TF-IDF query text (from build_corpus_text).
            precedents: List of dicts from precedents table.
            top_k_wins: Max TP matches to return.
            top_k_losses: Max FP matches to return.

        Returns:
            CaseLawResult with partitioned wins/losses.
        """
        if not query_text or not query_text.strip():
            return CaseLawResult(
                wins=[], losses=[],
                max_similarity_tp=0.0, max_similarity_fp=0.0,
                vectorizer_version="", query_text_length=0,
            )

        if not precedents:
            return CaseLawResult(
                wins=[], losses=[],
                max_similarity_tp=0.0, max_similarity_fp=0.0,
                vectorizer_version="", query_text_length=len(query_text),
            )

        # Transform query
        query_vec = self._vectorizer.transform([query_text])

        # Compute similarities
        from sklearn.metrics.pairwise import cosine_similarity
        import scipy.sparse as sp

        precedent_vecs = []
        valid_precedents = []
        for p in precedents:
            blob = p.get("tfidf_vector")
            if blob:
                try:
                    vec = pickle.loads(blob)
                    precedent_vecs.append(vec)
                    valid_precedents.append(p)
                except Exception as e:
                    logger.warning("Failed to deserialize vector for signal %s: %s", p.get("signal_id"), e)

        if not precedent_vecs:
            return CaseLawResult(
                wins=[], losses=[],
                max_similarity_tp=0.0, max_similarity_fp=0.0,
                vectorizer_version=precedents[0].get("vectorizer_version", ""),
                query_text_length=len(query_text),
            )

        stacked = sp.vstack(precedent_vecs)
        similarities = cosine_similarity(query_vec, stacked).flatten()

        # Build matches with staleness
        now = datetime.now(timezone.utc)
        tp_matches = []
        fp_matches = []

        for i, p in enumerate(valid_precedents):
            is_stale = self._check_stale(p.get("signal_created_at"), now)
            match = PrecedentMatch(
                signal_id=p["signal_id"],
                canonical_key=p["canonical_key"],
                company_name=p.get("company_name", ""),
                human_label=p["human_label"],
                similarity_score=float(similarities[i]),
                label_reason=p.get("label_reason", ""),
                source_api=p.get("source_api", ""),
                confidence=p.get("confidence", 0.0),
                signal_created_at=p.get("signal_created_at", ""),
                is_stale=is_stale,
            )
            if p["human_label"] == "TP":
                tp_matches.append(match)
            else:
                fp_matches.append(match)

        # Sort descending by similarity
        tp_matches.sort(key=lambda m: m.similarity_score, reverse=True)
        fp_matches.sort(key=lambda m: m.similarity_score, reverse=True)

        wins = tp_matches[:top_k_wins]
        losses = fp_matches[:top_k_losses]

        return CaseLawResult(
            wins=wins,
            losses=losses,
            max_similarity_tp=wins[0].similarity_score if wins else 0.0,
            max_similarity_fp=losses[0].similarity_score if losses else 0.0,
            vectorizer_version=valid_precedents[0].get("vectorizer_version", ""),
            query_text_length=len(query_text),
        )

    def find_similar_from_db(
        self,
        query_text: str,
        db_conn,
        vectorizer_version: str,
        top_k_wins: int = 3,
        top_k_losses: int = 3,
    ) -> CaseLawResult:
        """Convenience: load precedents from DB, then find_similar.

        Note: db_conn should be a synchronous sqlite3.Connection (not aiosqlite).
        """
        cursor = db_conn.execute(
            "SELECT * FROM precedents WHERE vectorizer_version = ?",
            (vectorizer_version,),
        )
        columns = [d[0] for d in cursor.description]
        precedents = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return self.find_similar(query_text, precedents, top_k_wins, top_k_losses)

    @staticmethod
    def _check_stale(signal_created_at: Optional[str], now: datetime) -> bool:
        """Check if a precedent's original signal is stale (>3 years old)."""
        if not signal_created_at:
            return False
        try:
            created = datetime.fromisoformat(signal_created_at.replace("Z", "+00:00"))
            age_days = (now - created).days
            return age_days > STALE_THRESHOLD_DAYS
        except (ValueError, TypeError):
            return False
