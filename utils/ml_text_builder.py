"""
Shared ML text builder for thesis classification.

Ensures identical text construction in both training and inference,
preventing training/serving skew (identified as critical risk in review).

Usage:
    from utils.ml_text_builder import build_ml_text

    # In training script:
    text = build_ml_text(description, company_name, domain_name)

    # In ThesisMatcher._compute_ml_score():
    text = build_ml_text(description, company_name, domain_name)
"""

from __future__ import annotations

import re
from typing import Optional


def build_ml_text(
    description: str,
    company_name: Optional[str] = None,
    domain_name: Optional[str] = None,
) -> str:
    """Build consistent text input for ML thesis model.

    This function MUST be used in both training and inference to prevent
    training/serving skew. Any change here requires model retraining.

    Args:
        description: Company description or README content
        company_name: Optional company name
        domain_name: Optional domain name (SLD extracted, TLD stripped)

    Returns:
        Normalized text suitable for ML model input.
        Returns empty string if no meaningful text can be constructed.
    """
    parts = []

    if description and description.strip():
        parts.append(description.strip())

    if company_name and company_name.strip():
        parts.append(company_name.strip())

    if domain_name and domain_name.strip():
        sld = _extract_sld(domain_name.strip())
        if sld:
            parts.append(sld)

    text = " ".join(parts)
    return _normalize_ml_text(text)


def _extract_sld(domain_name: str) -> Optional[str]:
    """Extract second-level domain (SLD) from a domain name.

    Examples:
        "getfitness.com" → "getfitness"
        "https://try-meals.io" → "try-meals"
        "app.example.co.uk" → "app"

    Args:
        domain_name: Raw domain string (may include protocol)

    Returns:
        SLD string or None if extraction fails
    """
    domain = domain_name.lower()

    # Remove protocol
    if "://" in domain:
        domain = domain.split("://", 1)[1]

    # Remove port
    if ":" in domain:
        domain = domain.split(":", 1)[0]

    # Remove path
    if "/" in domain:
        domain = domain.split("/", 1)[0]

    # Get first part (SLD)
    parts = domain.split(".")
    if not parts or not parts[0]:
        return None

    return parts[0]


def _normalize_ml_text(text: str) -> str:
    """Normalize text for ML model input.

    Applies the same normalization as ThesisMatcher._normalize() for
    consistency between keyword scoring and ML scoring.

    Args:
        text: Raw text

    Returns:
        Normalized lowercase text with collapsed whitespace
    """
    if not text:
        return ""
    normalized = re.sub(r"[-/_]", " ", text)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower().strip()
