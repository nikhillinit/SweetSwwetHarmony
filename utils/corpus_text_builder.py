"""Shared text builder for TF-IDF corpus (case-law + exemplars).

This function MUST be used in both corpus building and runtime retrieval
to prevent training/serving skew. Any change here requires corpus rebuild.

Mirrors the pattern in utils/ml_text_builder.py for the ML classifier.

Usage:
    from utils.corpus_text_builder import build_corpus_text

    # In build_case_law_corpus.py:
    text = build_corpus_text(company_name, raw_data, schema_row)

    # In CaseLawRetriever.find_similar():
    text = build_corpus_text(company_name, raw_data, schema_row)
"""

from __future__ import annotations

import json
import re
from typing import Optional


def build_corpus_text(
    company_name: str,
    raw_data: str | dict,
    schema_row: Optional[dict] = None,
) -> str:
    """Deterministic text construction for TF-IDF similarity.

    This function MUST be used in both corpus building and runtime retrieval
    to prevent training/serving skew. Any change here requires corpus rebuild.

    Args:
        company_name: Company name from signals table.
        raw_data: JSON string or dict from signals.raw_data.
        schema_row: Optional dict from functional_schemas table.

    Returns:
        Concatenated, whitespace-normalized text for TF-IDF.
        Returns empty string if no meaningful text can be constructed.
    """
    parts = [company_name or ""]

    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data or "{}")
        except (json.JSONDecodeError, TypeError):
            raw_data = {}

    if isinstance(raw_data, dict):
        parts.append(raw_data.get("description", "") or "")
        parts.append(raw_data.get("title", "") or "")

    if schema_row:
        parts.append(schema_row.get("problem_solved_text", "") or "")
        parts.append(schema_row.get("customer_text", "") or "")
        parts.append(schema_row.get("customer_archetype", "") or "")

    text = " ".join(p for p in parts if p).strip()
    return _normalize_corpus_text(text)


def _normalize_corpus_text(text: str) -> str:
    """Normalize text for TF-IDF corpus.

    Collapses whitespace, lowercases. Keeps punctuation for TF-IDF
    (unlike ML builder which strips more aggressively).
    """
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text)
    return normalized.lower().strip()
