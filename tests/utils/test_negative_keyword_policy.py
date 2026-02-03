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


# Expected category mapping - locks groupings for all 40 keywords
# This is the source of truth for which category each keyword belongs to
EXPECTED_CATEGORIES = {
    # B2B/Enterprise (12 keywords)
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
    # Crypto/Web3 (6 keywords)
    "blockchain": "CRYPTO_WEB3",
    "crypto": "CRYPTO_WEB3",
    "web3": "CRYPTO_WEB3",
    "nft": "CRYPTO_WEB3",
    "defi": "CRYPTO_WEB3",
    "token": "CRYPTO_WEB3",
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
    # DevTools (5 keywords)
    "cli": "DEVTOOLS",
    "library": "DEVTOOLS",
    "framework": "DEVTOOLS",
    "plugin": "DEVTOOLS",
    "linter": "DEVTOOLS",
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
        assert yaml_policy.get("schema") == "negative_keyword_policy_v1", "Wrong or missing schema"
        assert "negative_keywords" in yaml_policy, "Missing negative_keywords"

    def test_keyword_count_is_40(self, yaml_policy):
        """YAML should have exactly 40 keywords."""
        from utils.thesis_matcher import NEGATIVE_KEYWORDS

        yaml_count = len(yaml_policy.get("negative_keywords", {}))
        python_count = len(NEGATIVE_KEYWORDS)

        assert yaml_count == 40, f"Expected 40 keywords, got {yaml_count}"
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

        assert category_counts["B2B_ENTERPRISE"] == 12
        assert category_counts["CRYPTO_WEB3"] == 6
        assert category_counts["SERVICES"] == 3
        assert category_counts["STAGES"] == 4
        assert category_counts["EDUCATIONAL"] == 10
        assert category_counts["DEVTOOLS"] == 5
        assert sum(category_counts.values()) == 40
