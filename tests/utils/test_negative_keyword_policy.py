"""
Tests for negative keyword policy schema validation (Phase 0B-1).

Tests:
1. Schema validation (required fields, types, bounds)
2. Content completeness (YAML matches NEGATIVE_KEYWORDS)
3. Category mapping verification
4. NegativeKeywordPolicy typed object creation
"""

import pytest
from pathlib import Path


# Expected category mapping - locks groupings for all 58 keywords
# This is the source of truth for which category each keyword belongs to
# Note: bare 'token' removed per ADR; context-qualified 'crypto token'/'nft token' added
EXPECTED_CATEGORIES = {
    # B2B/Enterprise (13 keywords)
    "enterprise": "B2B_ENTERPRISE",
    "b2b": "B2B_ENTERPRISE",
    "saas platform": "B2B_ENTERPRISE",
    "developer tool": "B2B_ENTERPRISE",
    "api platform": "B2B_ENTERPRISE",
    "api management": "B2B_ENTERPRISE",
    "devops": "B2B_ENTERPRISE",
    "infrastructure": "B2B_ENTERPRISE",
    "logistics platform": "B2B_ENTERPRISE",
    "logistics": "B2B_ENTERPRISE",
    "data platform": "B2B_ENTERPRISE",
    "sdk": "B2B_ENTERPRISE",
    "production management tool": "B2B_ENTERPRISE",
    # Crypto/Web3 (7 keywords — bare 'token' removed, context-qualified only)
    "blockchain": "CRYPTO_WEB3",
    "crypto": "CRYPTO_WEB3",
    "web3": "CRYPTO_WEB3",
    "nft": "CRYPTO_WEB3",
    "defi": "CRYPTO_WEB3",
    "crypto token": "CRYPTO_WEB3",
    "nft token": "CRYPTO_WEB3",
    # Services (3 keywords)
    "consulting": "SERVICES",
    "agency": "SERVICES",
    "services firm": "SERVICES",
    # Stages (4 keywords)
    "series b": "STAGES",
    "series c": "STAGES",
    "series d": "STAGES",
    "aggregator": "STAGES",
    # Educational (10 keywords)
    "boilerplate": "EDUCATIONAL",
    "starter": "EDUCATIONAL",
    "template": "EDUCATIONAL",
    "tutorial": "EDUCATIONAL",
    "workshop": "EDUCATIONAL",
    "course": "EDUCATIONAL",
    "homework": "EDUCATIONAL",
    "assignment": "EDUCATIONAL",
    "example": "EDUCATIONAL",
    "demo repo": "EDUCATIONAL",
    # DevTools (21 keywords)
    "cli": "DEVTOOLS",
    "library": "DEVTOOLS",
    "framework": "DEVTOOLS",
    "plugin": "DEVTOOLS",
    "linter": "DEVTOOLS",
    "log aggregation": "DEVTOOLS",
    "mysql": "DEVTOOLS",
    "vector rendering": "DEVTOOLS",
    "compression algorithm": "DEVTOOLS",
    "embedded scheduler": "DEVTOOLS",
    "mcp server": "DEVTOOLS",
    "password system": "DEVTOOLS",
    "legal research": "DEVTOOLS",
    "stock movements": "DEVTOOLS",
    "benchmark for llms": "DEVTOOLS",
    "ship python to aws": "DEVTOOLS",
    "tabular data": "DEVTOOLS",
    "floor plans": "DEVTOOLS",
    "skills marketplace": "DEVTOOLS",
    "data-centric ai": "DEVTOOLS",
    "sentiment on ai": "DEVTOOLS",
}


class TestValidateNegativeKeywordPolicy:
    """Test validate_negative_keyword_policy function."""

    def test_valid_policy_passes(self):
        """A valid policy should pass validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"weight": 0.5, "category": "B2B_ENTERPRISE"},
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is True
        assert result.errors == []

    def test_missing_version_fails(self):
        """Missing version field should fail validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("version" in err.lower() for err in result.errors)

    def test_missing_schema_fails(self):
        """Missing schema field should fail validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "negative_keywords": {},
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("schema" in err.lower() for err in result.errors)

    def test_missing_negative_keywords_fails(self):
        """Missing negative_keywords field should fail validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("negative_keywords" in err.lower() for err in result.errors)

    def test_version_coercion_int(self):
        """Integer version should be coerced to string (no error)."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": 1,  # int, not string
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is True
        assert result.errors == []

    def test_version_coercion_float(self):
        """Float version should be coerced to string (no error)."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": 1.0,  # float, not string
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is True
        assert result.errors == []

    def test_invalid_weight_too_low(self):
        """Weight < 0 should fail validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"weight": -0.1, "category": "B2B_ENTERPRISE"},
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("weight" in err.lower() for err in result.errors)

    def test_invalid_weight_too_high(self):
        """Weight > 1 should fail validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"weight": 1.5, "category": "B2B_ENTERPRISE"},
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("weight" in err.lower() for err in result.errors)

    def test_invalid_category_fails(self):
        """Category not in enum should fail validation."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"weight": 0.5, "category": "INVALID_CATEGORY"},
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("category" in err.lower() for err in result.errors)

    def test_non_lowercase_keyword_produces_warning(self):
        """Non-lowercase keyword should produce warning (not error)."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "Enterprise": {"weight": 0.5, "category": "B2B_ENTERPRISE"},  # Capital E
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is True  # Warning, not error
        assert any("lowercase" in warn.lower() for warn in result.warnings)

    def test_unknown_extra_keys_allowed(self):
        """Unknown extra keys should be allowed (future flexibility)."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
            "description": "Some description",
            "future_field": "some value",
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is True

    def test_empty_negative_keywords_valid(self):
        """Empty negative_keywords dict should be valid (for test stubs)."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is True

    def test_missing_weight_in_entry_fails(self):
        """Missing weight in keyword entry should fail."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"category": "B2B_ENTERPRISE"},  # missing weight
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("weight" in err.lower() for err in result.errors)

    def test_missing_category_in_entry_fails(self):
        """Missing category in keyword entry should fail."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        policy = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"weight": 0.5},  # missing category
            },
        }

        result = validate_negative_keyword_policy(policy)

        assert result.valid is False
        assert any("category" in err.lower() for err in result.errors)


class TestNegativeKeywordPolicyFromConfig:
    """Test NegativeKeywordPolicy.from_config method."""

    def test_creates_typed_policy(self):
        """from_config should create a typed NegativeKeywordPolicy object."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": 1.0,  # float version
            "schema": "negative_keyword_policy_v1",
            "description": "Test policy",
            "negative_keywords": {
                "enterprise": {"weight": 0.5, "category": "B2B_ENTERPRISE"},
                "crypto": {"weight": 0.5, "category": "CRYPTO_WEB3"},
            },
        }

        policy = NegativeKeywordPolicy.from_config(config)

        assert policy.version == "1.0"  # Coerced to string
        assert policy.schema == "negative_keyword_policy_v1"
        assert policy.description == "Test policy"
        assert len(policy.keywords) == 2
        assert policy.keywords["enterprise"].weight == 0.5
        assert policy.keywords["enterprise"].category.value == "B2B_ENTERPRISE"
        assert policy.keywords["crypto"].category.value == "CRYPTO_WEB3"

    def test_empty_keywords_creates_empty_dict(self):
        """Empty negative_keywords should result in empty keywords dict."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }

        policy = NegativeKeywordPolicy.from_config(config)

        assert policy.keywords == {}

    def test_missing_description_is_none(self):
        """Missing description should result in None."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }

        policy = NegativeKeywordPolicy.from_config(config)

        assert policy.description is None


class TestYAMLContentCompleteness:
    """Test that YAML contains all keywords from NEGATIVE_KEYWORDS."""

    @pytest.fixture
    def yaml_policy(self):
        """Load the actual YAML policy file."""
        import yaml

        policy_path = Path(__file__).parent.parent.parent / "config" / "v2" / "negative_keyword_policy.yaml"
        with open(policy_path) as f:
            return yaml.safe_load(f)

    def test_yaml_contains_all_python_keywords(self, yaml_policy):
        """YAML must contain all keywords from NEGATIVE_KEYWORDS."""
        from utils.thesis_matcher import NEGATIVE_KEYWORDS

        yaml_keywords = set(yaml_policy.get("negative_keywords", {}).keys())
        python_keywords = set(NEGATIVE_KEYWORDS.keys())

        # Check for exact set equality
        missing_from_yaml = python_keywords - yaml_keywords
        extra_in_yaml = yaml_keywords - python_keywords

        assert missing_from_yaml == set(), f"Keywords in Python but not YAML: {missing_from_yaml}"
        assert extra_in_yaml == set(), f"Keywords in YAML but not Python: {extra_in_yaml}"
        assert yaml_keywords == python_keywords

    def test_weights_match_python_dict(self, yaml_policy):
        """YAML weights must match NEGATIVE_KEYWORDS exactly."""
        from utils.thesis_matcher import NEGATIVE_KEYWORDS

        yaml_keywords = yaml_policy.get("negative_keywords", {})

        for keyword, python_weight in NEGATIVE_KEYWORDS.items():
            yaml_entry = yaml_keywords.get(keyword)
            assert yaml_entry is not None, f"Missing keyword in YAML: {keyword}"
            yaml_weight = yaml_entry.get("weight")
            assert yaml_weight == pytest.approx(python_weight), (
                f"Weight mismatch for '{keyword}': YAML={yaml_weight}, Python={python_weight}"
            )

    def test_categories_match_expected_mapping(self, yaml_policy):
        """Each keyword must have the expected category."""
        yaml_keywords = yaml_policy.get("negative_keywords", {})

        for keyword, expected_category in EXPECTED_CATEGORIES.items():
            yaml_entry = yaml_keywords.get(keyword)
            assert yaml_entry is not None, f"Missing keyword in YAML: {keyword}"
            yaml_category = yaml_entry.get("category")
            assert yaml_category == expected_category, (
                f"Category mismatch for '{keyword}': YAML={yaml_category}, expected={expected_category}"
            )

    def test_all_categories_are_valid_enum_values(self, yaml_policy):
        """All categories in YAML should be valid enum values."""
        from utils.negative_keyword_policy import NegativeKeywordCategory

        yaml_keywords = yaml_policy.get("negative_keywords", {})
        valid_categories = {cat.value for cat in NegativeKeywordCategory}

        for keyword, entry in yaml_keywords.items():
            category = entry.get("category")
            assert category in valid_categories, (
                f"Invalid category '{category}' for keyword '{keyword}'. "
                f"Valid: {valid_categories}"
            )

    def test_yaml_has_required_schema_fields(self, yaml_policy):
        """YAML must have version, schema, and negative_keywords."""
        assert yaml_policy.get("version") is not None, "Missing version"
        assert yaml_policy.get("schema") in (
            "negative_keyword_policy_v1",
            "negative_keyword_policy_v2",
        ), "Wrong or missing schema"
        assert "negative_keywords" in yaml_policy, "Missing negative_keywords"

    def test_keyword_count_is_58(self, yaml_policy):
        """YAML should have exactly 58 keywords (bare 'token' removed, crypto/nft token added)."""
        from utils.thesis_matcher import NEGATIVE_KEYWORDS

        yaml_count = len(yaml_policy.get("negative_keywords", {}))
        python_count = len(NEGATIVE_KEYWORDS)

        assert yaml_count == 58, f"Expected 58 keywords, got {yaml_count}"
        assert yaml_count == python_count, f"YAML ({yaml_count}) != Python ({python_count})"


class TestCategoryEnumCoverage:
    """Test that category enum is complete."""

    def test_all_expected_categories_in_enum(self):
        """All expected categories should be in the enum."""
        from utils.negative_keyword_policy import NegativeKeywordCategory

        expected = {"B2B_ENTERPRISE", "CRYPTO_WEB3", "SERVICES", "STAGES", "EDUCATIONAL", "DEVTOOLS"}
        actual = {cat.value for cat in NegativeKeywordCategory}

        assert actual == expected

    def test_category_counts(self):
        """Verify expected number of keywords per category."""
        category_counts = {}
        for category in EXPECTED_CATEGORIES.values():
            category_counts[category] = category_counts.get(category, 0) + 1

        assert category_counts["B2B_ENTERPRISE"] == 13
        assert category_counts["CRYPTO_WEB3"] == 7
        assert category_counts["SERVICES"] == 3
        assert category_counts["STAGES"] == 4
        assert category_counts["EDUCATIONAL"] == 10
        assert category_counts["DEVTOOLS"] == 21
        assert sum(category_counts.values()) == 58


# =========================================================================
# Phase 1: 3-Tier Policy Tiering + Dual-Read (v1/v2)
# =========================================================================

# Expected tier mapping — source of truth for v2 YAML
EXPECTED_TIERS = {
    # hard_reject: crypto/web3, late-stage, template/educational noise
    "blockchain": "hard_reject",
    "crypto": "hard_reject",
    "web3": "hard_reject",
    "nft": "hard_reject",
    "defi": "hard_reject",
    "crypto token": "hard_reject",
    "nft token": "hard_reject",
    "series c": "hard_reject",
    "series d": "hard_reject",
    "boilerplate": "hard_reject",
    "template": "hard_reject",
    "tutorial": "hard_reject",
    "demo repo": "hard_reject",
    "homework": "hard_reject",
    "assignment": "hard_reject",
    # hard_hold: ambiguous B2B/enterprise + early-late overlap
    "enterprise": "hard_hold",
    "b2b": "hard_hold",
    "saas platform": "hard_hold",
    "infrastructure": "hard_hold",
    "logistics platform": "hard_hold",
    "series b": "hard_hold",
    # soft: everything else
    "developer tool": "soft",
    "api platform": "soft",
    "api management": "soft",
    "devops": "soft",
    "logistics": "soft",
    "data platform": "soft",
    "sdk": "soft",
    "cli": "soft",
    "library": "soft",
    "framework": "soft",
    "plugin": "soft",
    "linter": "soft",
    "consulting": "soft",
    "agency": "soft",
    "services firm": "soft",
    "aggregator": "soft",
    "starter": "soft",
    "workshop": "soft",
    "course": "soft",
    "example": "soft",
    "log aggregation": "soft",
    "mysql": "soft",
    "vector rendering": "soft",
    "compression algorithm": "soft",
    "embedded scheduler": "soft",
    "mcp server": "soft",
    "password system": "soft",
    "legal research": "soft",
    "stock movements": "soft",
    "benchmark for llms": "soft",
    "ship python to aws": "soft",
    "tabular data": "soft",
    "floor plans": "soft",
    "skills marketplace": "soft",
    "data-centric ai": "soft",
    "sentiment on ai": "soft",
    "production management tool": "soft",
}


class TestNegativeKeywordEntryTier:
    """Phase 1: Test tier field on NegativeKeywordEntry."""

    def test_entry_has_tier_field(self):
        """NegativeKeywordEntry should have a tier field."""
        from utils.negative_keyword_policy import NegativeKeywordEntry, NegativeKeywordCategory

        entry = NegativeKeywordEntry(
            keyword="blockchain",
            weight=0.5,
            category=NegativeKeywordCategory.CRYPTO_WEB3,
            tier="hard_reject",
        )
        assert entry.tier == "hard_reject"

    def test_tier_defaults_to_soft(self):
        """Tier should default to 'soft' if not provided."""
        from utils.negative_keyword_policy import NegativeKeywordEntry, NegativeKeywordCategory

        entry = NegativeKeywordEntry(
            keyword="cli",
            weight=0.4,
            category=NegativeKeywordCategory.DEVTOOLS,
        )
        assert entry.tier == "soft"

    def test_valid_tier_values(self):
        """Tier must be one of hard_reject, hard_hold, soft."""
        from utils.negative_keyword_policy import NegativeKeywordEntry, NegativeKeywordCategory

        for tier in ("hard_reject", "hard_hold", "soft"):
            entry = NegativeKeywordEntry(
                keyword="test",
                weight=0.5,
                category=NegativeKeywordCategory.B2B_ENTERPRISE,
                tier=tier,
            )
            assert entry.tier == tier


class TestV1TierInference:
    """Phase 1: Test v1 schema → tier inference via from_config()."""

    def test_crypto_web3_infers_hard_reject(self):
        """CRYPTO_WEB3 category should infer tier=hard_reject in v1."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "blockchain": {"weight": 0.5, "category": "CRYPTO_WEB3"},
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["blockchain"].tier == "hard_reject"

    def test_b2b_enterprise_infers_hard_hold(self):
        """B2B_ENTERPRISE category should infer tier=hard_hold in v1."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "enterprise": {"weight": 0.5, "category": "B2B_ENTERPRISE"},
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["enterprise"].tier == "hard_hold"

    def test_devtools_infers_soft(self):
        """DEVTOOLS category should infer tier=soft in v1."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "cli": {"weight": 0.4, "category": "DEVTOOLS"},
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["cli"].tier == "soft"

    def test_stages_infers_hard_reject(self):
        """STAGES category should infer tier=hard_reject in v1."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "series c": {"weight": 0.4, "category": "STAGES"},
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["series c"].tier == "hard_reject"

    def test_services_infers_soft(self):
        """SERVICES category should infer tier=soft in v1."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "consulting": {"weight": 0.4, "category": "SERVICES"},
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["consulting"].tier == "soft"

    def test_educational_infers_soft(self):
        """EDUCATIONAL category should infer tier=soft in v1."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {
                "workshop": {"weight": 0.4, "category": "EDUCATIONAL"},
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["workshop"].tier == "soft"


class TestV2ExplicitTier:
    """Phase 1: Test v2 schema requires explicit tier field."""

    def test_v2_requires_explicit_tier(self):
        """v2 schema: missing tier field should cause validation error."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        config = {
            "version": "2.0",
            "schema": "negative_keyword_policy_v2",
            "negative_keywords": {
                "blockchain": {"weight": 0.5, "category": "CRYPTO_WEB3"},
                # No tier field — should fail for v2
            },
        }
        result = validate_negative_keyword_policy(config)
        assert result.valid is False
        assert any("tier" in err.lower() for err in result.errors)

    def test_v2_with_explicit_tier_passes(self):
        """v2 schema: keyword with explicit tier should pass."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        config = {
            "version": "2.0",
            "schema": "negative_keyword_policy_v2",
            "negative_keywords": {
                "blockchain": {
                    "weight": 0.5,
                    "category": "CRYPTO_WEB3",
                    "tier": "hard_reject",
                },
            },
        }
        result = validate_negative_keyword_policy(config)
        assert result.valid is True

    def test_v2_invalid_tier_value_fails(self):
        """v2 schema: invalid tier value should fail."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        config = {
            "version": "2.0",
            "schema": "negative_keyword_policy_v2",
            "negative_keywords": {
                "blockchain": {
                    "weight": 0.5,
                    "category": "CRYPTO_WEB3",
                    "tier": "ultra_reject",  # invalid
                },
            },
        }
        result = validate_negative_keyword_policy(config)
        assert result.valid is False
        assert any("tier" in err.lower() for err in result.errors)

    def test_v2_from_config_uses_explicit_tier(self):
        """v2 from_config should use explicit tier, not inference."""
        from utils.negative_keyword_policy import NegativeKeywordPolicy

        config = {
            "version": "2.0",
            "schema": "negative_keyword_policy_v2",
            "negative_keywords": {
                "blockchain": {
                    "weight": 0.5,
                    "category": "CRYPTO_WEB3",
                    "tier": "hard_reject",
                },
                "enterprise": {
                    "weight": 0.5,
                    "category": "B2B_ENTERPRISE",
                    "tier": "hard_hold",
                },
                "cli": {
                    "weight": 0.4,
                    "category": "DEVTOOLS",
                    "tier": "soft",
                },
            },
        }
        policy = NegativeKeywordPolicy.from_config(config)
        assert policy.keywords["blockchain"].tier == "hard_reject"
        assert policy.keywords["enterprise"].tier == "hard_hold"
        assert policy.keywords["cli"].tier == "soft"


class TestV1V2Roundtrip:
    """Phase 1: v1→v2 tier inference follows category-level mapping."""

    def test_v1_inferred_tiers_follow_category_map(self):
        """Load v1 config, verify tiers follow _V1_CATEGORY_TIER_MAP.

        V1 inference is category-level (approximate). V2 YAML has exact per-keyword
        tiers tested in TestYAMLV2Tiers.
        """
        from utils.negative_keyword_policy import (
            NegativeKeywordPolicy,
            _V1_CATEGORY_TIER_MAP,
            NegativeKeywordCategory,
        )

        config = {
            "version": "1.0",
            "schema": "negative_keyword_policy_v1",
            "negative_keywords": {},
        }
        for keyword, category in EXPECTED_CATEGORIES.items():
            config["negative_keywords"][keyword] = {
                "weight": 0.5,
                "category": category,
            }

        policy = NegativeKeywordPolicy.from_config(config)

        for keyword, entry in policy.keywords.items():
            expected_tier = _V1_CATEGORY_TIER_MAP.get(entry.category, "soft")
            assert entry.tier == expected_tier, (
                f"V1 inference mismatch for '{keyword}' (category={entry.category.value}): "
                f"got '{entry.tier}', expected '{expected_tier}' from category map"
            )

    def test_v1_v2_divergence_documented(self):
        """Document known divergences between v1 inference and v2 explicit tiers.

        V1 inference is lossy: categories with mixed tiers (EDUCATIONAL, STAGES)
        cannot be perfectly inferred. V2 YAML is authoritative.
        """
        from utils.negative_keyword_policy import (
            _V1_CATEGORY_TIER_MAP,
            NegativeKeywordCategory,
        )

        # These keywords have different tier in v2 vs what v1 would infer
        known_divergences = {
            # EDUCATIONAL → soft (v1), but some are hard_reject (v2)
            "boilerplate": ("soft", "hard_reject"),
            "template": ("soft", "hard_reject"),
            "tutorial": ("soft", "hard_reject"),
            "demo repo": ("soft", "hard_reject"),
            "homework": ("soft", "hard_reject"),
            "assignment": ("soft", "hard_reject"),
            # STAGES → hard_reject (v1), but series b is hard_hold (v2)
            "series b": ("hard_reject", "hard_hold"),
        }

        for keyword, (v1_inferred, v2_explicit) in known_divergences.items():
            category = EXPECTED_CATEGORIES[keyword]
            cat_enum = NegativeKeywordCategory(category)
            assert _V1_CATEGORY_TIER_MAP[cat_enum] == v1_inferred
            assert EXPECTED_TIERS[keyword] == v2_explicit
            assert v1_inferred != v2_explicit  # Confirms divergence


class TestYAMLV2Tiers:
    """Phase 1: Test that v2 YAML has correct tiers for all keywords."""

    @pytest.fixture
    def yaml_policy(self):
        """Load the actual YAML policy file."""
        import yaml

        policy_path = Path(__file__).parent.parent.parent / "config" / "v2" / "negative_keyword_policy.yaml"
        with open(policy_path) as f:
            return yaml.safe_load(f)

    def test_yaml_is_v2(self, yaml_policy):
        """YAML should be version 2.0 with v2 schema."""
        assert yaml_policy.get("version") == "2.0"
        assert yaml_policy.get("schema") == "negative_keyword_policy_v2"

    def test_yaml_tiers_match_expected(self, yaml_policy):
        """All YAML keywords should have correct tier values."""
        yaml_keywords = yaml_policy.get("negative_keywords", {})

        for keyword, expected_tier in EXPECTED_TIERS.items():
            entry = yaml_keywords.get(keyword)
            assert entry is not None, f"Missing keyword in YAML: {keyword}"
            actual_tier = entry.get("tier")
            assert actual_tier == expected_tier, (
                f"Tier mismatch for '{keyword}': YAML has '{actual_tier}', expected '{expected_tier}'"
            )

    def test_yaml_tiers_match_python_dicts(self, yaml_policy):
        """YAML tiers should agree with Python HARD_REJECT/HARD_HOLD/SOFT dicts."""
        from utils.thesis_matcher import HARD_REJECT_KEYWORDS, HARD_HOLD_KEYWORDS, SOFT_PENALTY_KEYWORDS

        yaml_keywords = yaml_policy.get("negative_keywords", {})

        for keyword in HARD_REJECT_KEYWORDS:
            entry = yaml_keywords.get(keyword)
            assert entry is not None, f"Missing in YAML: {keyword}"
            assert entry.get("tier") == "hard_reject", (
                f"'{keyword}' is HARD_REJECT in Python but tier='{entry.get('tier')}' in YAML"
            )

        for keyword in HARD_HOLD_KEYWORDS:
            entry = yaml_keywords.get(keyword)
            assert entry is not None, f"Missing in YAML: {keyword}"
            assert entry.get("tier") == "hard_hold", (
                f"'{keyword}' is HARD_HOLD in Python but tier='{entry.get('tier')}' in YAML"
            )

        for keyword in SOFT_PENALTY_KEYWORDS:
            entry = yaml_keywords.get(keyword)
            assert entry is not None, f"Missing in YAML: {keyword}"
            assert entry.get("tier") == "soft", (
                f"'{keyword}' is SOFT_PENALTY in Python but tier='{entry.get('tier')}' in YAML"
            )

    def test_yaml_validates_as_v2(self, yaml_policy):
        """YAML should pass v2 validation (including tier field check)."""
        from utils.negative_keyword_policy import validate_negative_keyword_policy

        result = validate_negative_keyword_policy(yaml_policy)
        assert result.valid is True, f"Validation errors: {result.errors}"
