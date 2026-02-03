"""
Negative Keyword Policy schema validation and typed representation.

Phase 0B-1: Provides validation and typed objects for negative_keyword_policy.yaml.
Phase 0B-2: ThesisMatcher scoring will consume NegativeKeywordPolicy for scoring.

Usage:
    from utils.negative_keyword_policy import (
        validate_negative_keyword_policy,
        NegativeKeywordPolicy,
        NegativeKeywordCategory,
    )

    # Validate policy config
    result = validate_negative_keyword_policy(config)
    if not result.valid:
        raise RuntimeError(f"Invalid policy: {result.errors}")

    # Parse into typed object
    policy = NegativeKeywordPolicy.from_config(config)
    for keyword, entry in policy.keywords.items():
        print(f"{keyword}: weight={entry.weight}, category={entry.category}")
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union


class NegativeKeywordCategory(str, Enum):
    """Categories for negative keywords.

    Groups negative keywords by type for analysis and configuration.
    """

    B2B_ENTERPRISE = "B2B_ENTERPRISE"
    CRYPTO_WEB3 = "CRYPTO_WEB3"
    SERVICES = "SERVICES"
    STAGES = "STAGES"
    EDUCATIONAL = "EDUCATIONAL"
    DEVTOOLS = "DEVTOOLS"


@dataclass
class ValidationResult:
    """Result of policy schema validation.

    Attributes:
        valid: True if policy passes validation
        errors: List of error messages (validation failures)
        warnings: List of warning messages (non-blocking issues)
    """

    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class NegativeKeywordEntry:
    """Single keyword entry with weight and category.

    Attributes:
        keyword: The negative keyword string
        weight: Penalty weight (0.0-1.0)
        category: Category grouping for the keyword
    """

    keyword: str
    weight: float
    category: NegativeKeywordCategory


@dataclass
class NegativeKeywordPolicy:
    """Typed representation of the negative keyword policy.

    Parsed from YAML, ready for Phase 0B-2 scoring consumption.

    Attributes:
        version: Policy version string (coerced from int/float)
        schema: Schema identifier for compatibility checks
        keywords: Dict mapping keyword strings to NegativeKeywordEntry objects
        description: Optional policy description
    """

    version: str
    schema: str
    keywords: Dict[str, NegativeKeywordEntry] = field(default_factory=dict)
    description: Optional[str] = None

    @classmethod
    def from_config(cls, config: dict) -> "NegativeKeywordPolicy":
        """Parse validated config dict into typed policy object.

        Args:
            config: Validated policy config dict

        Returns:
            NegativeKeywordPolicy with typed keyword entries

        Note:
            Assumes config has been validated. Does not re-validate.
        """
        keywords: Dict[str, NegativeKeywordEntry] = {}

        for kw, entry in config.get("negative_keywords", {}).items():
            keywords[kw] = NegativeKeywordEntry(
                keyword=kw,
                weight=float(entry["weight"]),
                category=NegativeKeywordCategory(entry["category"]),
            )

        return cls(
            version=str(config.get("version", "")),
            schema=config.get("schema", ""),
            keywords=keywords,
            description=config.get("description"),
        )


def validate_negative_keyword_policy(policy: dict) -> ValidationResult:
    """Validate negative keyword policy schema.

    Required keys:
    - version: str|int|float (coerced to str, must exist)
    - schema: str (required, format identifier for compatibility)
    - negative_keywords: dict (required, can be empty)

    Each keyword entry must have:
    - weight: float, 0.0 <= weight <= 1.0
    - category: str in NegativeKeywordCategory enum

    Args:
        policy: Policy config dict to validate

    Returns:
        ValidationResult with valid=True/False, errors, and warnings

    Example:
        >>> result = validate_negative_keyword_policy({
        ...     "version": "1.0",
        ...     "schema": "negative_keyword_policy_v1",
        ...     "negative_keywords": {
        ...         "enterprise": {"weight": 0.5, "category": "B2B_ENTERPRISE"}
        ...     }
        ... })
        >>> result.valid
        True
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Check required top-level fields
    if "version" not in policy or policy["version"] is None:
        errors.append("Missing required field: 'version'")

    if "schema" not in policy or policy["schema"] is None:
        errors.append("Missing required field: 'schema'")

    if "negative_keywords" not in policy:
        errors.append("Missing required field: 'negative_keywords'")

    # If negative_keywords is present, validate each entry
    negative_keywords = policy.get("negative_keywords")
    if negative_keywords is not None and isinstance(negative_keywords, dict):
        valid_categories = {cat.value for cat in NegativeKeywordCategory}

        for keyword, entry in negative_keywords.items():
            # Check for non-lowercase keyword (warning, not error)
            if keyword != keyword.lower():
                warnings.append(
                    f"Keyword '{keyword}' is not lowercase. "
                    f"Consider using '{keyword.lower()}' for consistency."
                )

            # Validate entry is a dict
            if not isinstance(entry, dict):
                errors.append(f"Keyword '{keyword}': entry must be a dict, got {type(entry).__name__}")
                continue

            # Check required entry fields
            if "weight" not in entry:
                errors.append(f"Keyword '{keyword}': missing required field 'weight'")
            else:
                weight = entry["weight"]
                if not isinstance(weight, (int, float)):
                    errors.append(f"Keyword '{keyword}': weight must be a number, got {type(weight).__name__}")
                elif weight < 0.0 or weight > 1.0:
                    errors.append(f"Keyword '{keyword}': weight {weight} out of bounds [0.0, 1.0]")

            if "category" not in entry:
                errors.append(f"Keyword '{keyword}': missing required field 'category'")
            else:
                category = entry["category"]
                if category not in valid_categories:
                    errors.append(
                        f"Keyword '{keyword}': invalid category '{category}'. "
                        f"Valid: {sorted(valid_categories)}"
                    )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
