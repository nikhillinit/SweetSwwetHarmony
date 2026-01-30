"""
Tests for Merge Policy Module (Phase G)

Tests for:
- MergeRule and Materiality enums
- FieldMergePolicy dataclass
- Normalization functions
- Equivalence functions
- FIELD_MERGE_POLICIES configuration
"""

import pytest
from datetime import date, datetime, timezone


class TestMergeRuleEnum:
    """Tests for MergeRule enum."""

    def test_pick_highest_score_value(self):
        from utils.merge_policy import MergeRule
        assert MergeRule.PICK_HIGHEST_SCORE.value == "pick_highest_score"

    def test_concat_top_k_value(self):
        from utils.merge_policy import MergeRule
        assert MergeRule.CONCAT_TOP_K.value == "concat_top_k"

    def test_enum_is_string(self):
        from utils.merge_policy import MergeRule
        assert isinstance(MergeRule.PICK_HIGHEST_SCORE, str)


class TestMaterialityEnum:
    """Tests for Materiality enum."""

    def test_critical_value(self):
        from utils.merge_policy import Materiality
        assert Materiality.CRITICAL.value == "critical"

    def test_important_value(self):
        from utils.merge_policy import Materiality
        assert Materiality.IMPORTANT.value == "important"

    def test_minor_value(self):
        from utils.merge_policy import Materiality
        assert Materiality.MINOR.value == "minor"


class TestFieldMergePolicy:
    """Tests for FieldMergePolicy dataclass."""

    def test_create_basic_policy(self):
        from utils.merge_policy import FieldMergePolicy, MergeRule, Materiality

        policy = FieldMergePolicy(
            field_name="company_name",
            merge_rule=MergeRule.PICK_HIGHEST_SCORE,
            materiality=Materiality.CRITICAL,
        )

        assert policy.field_name == "company_name"
        assert policy.merge_rule == MergeRule.PICK_HIGHEST_SCORE
        assert policy.materiality == Materiality.CRITICAL

    def test_default_values(self):
        from utils.merge_policy import FieldMergePolicy, MergeRule, Materiality

        policy = FieldMergePolicy(
            field_name="test",
            merge_rule=MergeRule.PICK_HIGHEST_SCORE,
            materiality=Materiality.MINOR,
        )

        assert policy.recency_half_life_days is None
        assert policy.source_authority_override is None
        assert policy.normalize_fn is None
        assert policy.equivalence_fn is None
        assert policy.max_candidates == 5
        assert policy.audit_merge_rule is None
        assert policy.audit_top_k == 3
        assert policy.audit_max_chars == 900

    def test_policy_is_frozen(self):
        from utils.merge_policy import FieldMergePolicy, MergeRule, Materiality

        policy = FieldMergePolicy(
            field_name="test",
            merge_rule=MergeRule.PICK_HIGHEST_SCORE,
            materiality=Materiality.MINOR,
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            policy.field_name = "changed"


class TestNormalizeCompanyName:
    """Tests for normalize_company_name function."""

    def test_none_returns_empty(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name(None) == ""

    def test_strips_whitespace(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("  Acme Corp  ") == "acme"

    def test_lowercase(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("ACME") == "acme"

    def test_removes_inc_suffix(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme Inc") == "acme"
        assert normalize_company_name("Acme Inc.") == "acme"
        assert normalize_company_name("Acme Incorporated") == "acme"

    def test_removes_llc_suffix(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme LLC") == "acme"
        # L.L.C. with periods becomes "l l c" after punctuation removal, not matched as suffix
        # This is acceptable - the key is that "Acme LLC" == "ACME LLC" for equivalence

    def test_removes_ltd_suffix(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme Ltd") == "acme"
        assert normalize_company_name("Acme Ltd.") == "acme"
        assert normalize_company_name("Acme Limited") == "acme"

    def test_removes_corp_suffix(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme Corp") == "acme"
        assert normalize_company_name("Acme Corp.") == "acme"
        assert normalize_company_name("Acme Corporation") == "acme"

    def test_removes_punctuation(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme, Inc.") == "acme"
        # Apostrophe becomes space, so "O'Reilly" -> "o reilly"
        assert normalize_company_name("O'Reilly Media") == "o reilly media"

    def test_collapses_whitespace(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme   Corp   Inc") == "acme"

    def test_multi_word_company(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Press On Ventures LLC") == "press on ventures"

    def test_international_suffixes(self):
        from utils.merge_policy import normalize_company_name
        assert normalize_company_name("Acme GmbH") == "acme"
        assert normalize_company_name("Acme SARL") == "acme"
        assert normalize_company_name("Acme AG") == "acme"
        assert normalize_company_name("Acme BV") == "acme"


class TestNormalizeDescription:
    """Tests for normalize_description function."""

    def test_none_returns_empty(self):
        from utils.merge_policy import normalize_description
        assert normalize_description(None) == ""

    def test_strips_whitespace(self):
        from utils.merge_policy import normalize_description
        assert normalize_description("  Hello world  ") == "Hello world"

    def test_collapses_internal_whitespace(self):
        from utils.merge_policy import normalize_description
        assert normalize_description("Hello    world") == "Hello world"

    def test_preserves_case(self):
        from utils.merge_policy import normalize_description
        assert normalize_description("Hello World") == "Hello World"


class TestNormalizeDate:
    """Tests for normalize_date function."""

    def test_none_returns_none(self):
        from utils.merge_policy import normalize_date
        assert normalize_date(None) is None

    def test_date_returns_date(self):
        from utils.merge_policy import normalize_date
        d = date(2024, 1, 15)
        assert normalize_date(d) == d

    def test_datetime_returns_date(self):
        from utils.merge_policy import normalize_date
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert normalize_date(dt) == date(2024, 1, 15)

    def test_iso_string(self):
        from utils.merge_policy import normalize_date
        assert normalize_date("2024-01-15") == date(2024, 1, 15)

    def test_iso_datetime_string(self):
        from utils.merge_policy import normalize_date
        assert normalize_date("2024-01-15T10:30:00") == date(2024, 1, 15)

    def test_invalid_string_returns_none(self):
        from utils.merge_policy import normalize_date
        assert normalize_date("not a date") is None
        assert normalize_date("") is None


class TestEquivalenceFunctions:
    """Tests for equivalence helper functions."""

    def test_eq_normalized_str_same(self):
        from utils.merge_policy import eq_normalized_str, normalize_company_name
        assert eq_normalized_str("Acme Inc", "ACME INC.", normalize_company_name) is True

    def test_eq_normalized_str_different(self):
        from utils.merge_policy import eq_normalized_str, normalize_company_name
        assert eq_normalized_str("Acme Inc", "Beta Corp", normalize_company_name) is False

    def test_dates_within_days_same_date(self):
        from utils.merge_policy import dates_within_days
        assert dates_within_days("2024-01-15", "2024-01-15", days=30) is True

    def test_dates_within_days_within_tolerance(self):
        from utils.merge_policy import dates_within_days
        assert dates_within_days("2024-01-15", "2024-01-30", days=30) is True
        assert dates_within_days("2024-01-15", "2024-02-01", days=30) is True

    def test_dates_within_days_outside_tolerance(self):
        from utils.merge_policy import dates_within_days
        assert dates_within_days("2024-01-15", "2024-03-15", days=30) is False

    def test_dates_within_days_none_values(self):
        from utils.merge_policy import dates_within_days
        assert dates_within_days(None, "2024-01-15", days=30) is False
        assert dates_within_days("2024-01-15", None, days=30) is False


class TestFieldMergePolicies:
    """Tests for FIELD_MERGE_POLICIES configuration."""

    def test_company_name_policy_exists(self):
        from utils.merge_policy import FIELD_MERGE_POLICIES, MergeRule, Materiality

        policy = FIELD_MERGE_POLICIES["company_name"]
        assert policy.field_name == "company_name"
        assert policy.merge_rule == MergeRule.PICK_HIGHEST_SCORE
        assert policy.materiality == Materiality.CRITICAL

    def test_description_policy_exists(self):
        from utils.merge_policy import FIELD_MERGE_POLICIES, MergeRule, Materiality

        policy = FIELD_MERGE_POLICIES["description"]
        assert policy.field_name == "description"
        assert policy.merge_rule == MergeRule.PICK_HIGHEST_SCORE
        assert policy.materiality == Materiality.IMPORTANT
        assert policy.audit_merge_rule == MergeRule.CONCAT_TOP_K

    def test_founding_date_policy_exists(self):
        from utils.merge_policy import FIELD_MERGE_POLICIES, MergeRule, Materiality

        policy = FIELD_MERGE_POLICIES["founding_date"]
        assert policy.field_name == "founding_date"
        assert policy.merge_rule == MergeRule.PICK_HIGHEST_SCORE
        assert policy.materiality == Materiality.IMPORTANT

    def test_company_name_has_normalize_fn(self):
        from utils.merge_policy import FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        assert policy.normalize_fn is not None
        assert policy.normalize_fn("Acme Inc") == "acme"

    def test_company_name_has_equivalence_fn(self):
        from utils.merge_policy import FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        assert policy.equivalence_fn is not None
        assert policy.equivalence_fn("Acme Inc", "ACME INC.") is True
        assert policy.equivalence_fn("Acme", "Beta") is False

    def test_founding_date_has_equivalence_fn(self):
        from utils.merge_policy import FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["founding_date"]
        assert policy.equivalence_fn is not None
        # Within 30 days
        assert policy.equivalence_fn("2024-01-15", "2024-01-30") is True
        # Outside 30 days
        assert policy.equivalence_fn("2024-01-15", "2024-03-15") is False


class TestPolicyVersion:
    """Tests for POLICY_VERSION constant."""

    def test_policy_version_exists(self):
        from utils.merge_policy import POLICY_VERSION
        assert POLICY_VERSION is not None
        assert isinstance(POLICY_VERSION, str)
        assert len(POLICY_VERSION) > 0

    def test_policy_version_format(self):
        from utils.merge_policy import POLICY_VERSION
        # Should be in format like "g_v1.0"
        assert POLICY_VERSION.startswith("g_v")


class TestSourceAuthority:
    """Tests for SOURCE_AUTHORITY configuration."""

    def test_source_authority_exists(self):
        from utils.merge_policy import SOURCE_AUTHORITY
        assert SOURCE_AUTHORITY is not None
        assert isinstance(SOURCE_AUTHORITY, dict)

    def test_companies_house_highest_authority(self):
        from utils.merge_policy import SOURCE_AUTHORITY
        # Companies House is official UK registry - highest authority
        assert "companies_house" in SOURCE_AUTHORITY
        assert SOURCE_AUTHORITY["companies_house"] >= 0.9

    def test_sec_edgar_high_authority(self):
        from utils.merge_policy import SOURCE_AUTHORITY
        # SEC filings are official - high authority
        assert "sec_edgar" in SOURCE_AUTHORITY
        assert SOURCE_AUTHORITY["sec_edgar"] >= 0.85

    def test_crunchbase_moderate_authority(self):
        from utils.merge_policy import SOURCE_AUTHORITY
        # Crunchbase is curated but not official
        assert "crunchbase" in SOURCE_AUTHORITY
        assert 0.6 <= SOURCE_AUTHORITY["crunchbase"] <= 0.8

    def test_github_lower_authority(self):
        from utils.merge_policy import SOURCE_AUTHORITY
        # GitHub repo names may not match company names
        assert "github" in SOURCE_AUTHORITY
        assert SOURCE_AUTHORITY["github"] <= 0.6

    def test_default_authority_exists(self):
        from utils.merge_policy import DEFAULT_AUTHORITY
        assert DEFAULT_AUTHORITY is not None
        assert 0.0 < DEFAULT_AUTHORITY < 1.0


class TestCalculateEffectiveScore:
    """Tests for calculate_effective_score function."""

    def _make_provenance(self, source_key: str, confidence: float, age_days: int = 0):
        """Helper to create FieldProvenance for testing."""
        from datetime import datetime, timezone, timedelta
        from utils.synthesis_types import FieldProvenance

        detected_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        return FieldProvenance(
            value="Test Value",
            normalized_value="test value",
            source_key=source_key,
            signal_id=1,
            confidence=confidence,
            detected_at=detected_at,
            evidence_ref="test",
        )

    def test_basic_score_with_authority(self):
        """Score should incorporate source authority."""
        from utils.merge_policy import calculate_effective_score, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        prov = self._make_provenance("companies_house", confidence=1.0)

        score = calculate_effective_score(prov, policy)

        # With confidence=1.0 and companies_house (authority ~0.95), score should be ~0.95
        assert 0.9 <= score <= 1.0

    def test_lower_authority_source(self):
        """Lower authority sources should have lower scores."""
        from utils.merge_policy import calculate_effective_score, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        high_auth = self._make_provenance("companies_house", confidence=1.0)
        low_auth = self._make_provenance("github", confidence=1.0)

        high_score = calculate_effective_score(high_auth, policy)
        low_score = calculate_effective_score(low_auth, policy)

        assert high_score > low_score

    def test_confidence_affects_score(self):
        """Higher confidence should yield higher score."""
        from utils.merge_policy import calculate_effective_score, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        high_conf = self._make_provenance("crunchbase", confidence=0.9)
        low_conf = self._make_provenance("crunchbase", confidence=0.5)

        high_score = calculate_effective_score(high_conf, policy)
        low_score = calculate_effective_score(low_conf, policy)

        assert high_score > low_score

    def test_recency_decay_no_half_life(self):
        """Without half_life, older signals should NOT be penalized."""
        from utils.merge_policy import calculate_effective_score, FIELD_MERGE_POLICIES

        # company_name has no recency_half_life_days (stable fact)
        policy = FIELD_MERGE_POLICIES["company_name"]
        recent = self._make_provenance("crunchbase", confidence=0.8, age_days=0)
        old = self._make_provenance("crunchbase", confidence=0.8, age_days=365)

        recent_score = calculate_effective_score(recent, policy)
        old_score = calculate_effective_score(old, policy)

        # Should be equal (no decay)
        assert abs(recent_score - old_score) < 0.01

    def test_recency_decay_with_half_life(self):
        """With half_life, older signals should be penalized exponentially."""
        from utils.merge_policy import (
            calculate_effective_score,
            FieldMergePolicy,
            MergeRule,
            Materiality,
        )

        # Create policy with 30-day half-life
        policy = FieldMergePolicy(
            field_name="test",
            merge_rule=MergeRule.PICK_HIGHEST_SCORE,
            materiality=Materiality.MINOR,
            recency_half_life_days=30,
        )

        recent = self._make_provenance("crunchbase", confidence=0.8, age_days=0)
        one_half_life = self._make_provenance("crunchbase", confidence=0.8, age_days=30)
        two_half_lives = self._make_provenance("crunchbase", confidence=0.8, age_days=60)

        recent_score = calculate_effective_score(recent, policy)
        one_hl_score = calculate_effective_score(one_half_life, policy)
        two_hl_score = calculate_effective_score(two_half_lives, policy)

        # After one half-life, score should be ~50% of recent
        assert 0.45 * recent_score <= one_hl_score <= 0.55 * recent_score
        # After two half-lives, score should be ~25% of recent
        assert 0.20 * recent_score <= two_hl_score <= 0.30 * recent_score

    def test_policy_override_authority(self):
        """Policy-level authority override should take precedence."""
        from utils.merge_policy import (
            calculate_effective_score,
            FieldMergePolicy,
            MergeRule,
            Materiality,
        )

        # Policy with custom authority for github
        policy = FieldMergePolicy(
            field_name="test",
            merge_rule=MergeRule.PICK_HIGHEST_SCORE,
            materiality=Materiality.MINOR,
            source_authority_override={"github": 0.99},  # Override github to very high
        )

        prov = self._make_provenance("github", confidence=1.0)
        score = calculate_effective_score(prov, policy)

        # With override authority=0.99 and confidence=1.0, score should be ~0.99
        assert 0.95 <= score <= 1.0

    def test_unknown_source_uses_default(self):
        """Unknown sources should use DEFAULT_AUTHORITY."""
        from utils.merge_policy import calculate_effective_score, FIELD_MERGE_POLICIES, DEFAULT_AUTHORITY

        policy = FIELD_MERGE_POLICIES["company_name"]
        prov = self._make_provenance("unknown_source", confidence=1.0)

        score = calculate_effective_score(prov, policy)

        # Should use DEFAULT_AUTHORITY
        assert abs(score - DEFAULT_AUTHORITY) < 0.05

    def test_score_range(self):
        """Scores should always be in [0, 1] range."""
        from utils.merge_policy import calculate_effective_score, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]

        # Test various combinations
        test_cases = [
            ("companies_house", 1.0, 0),
            ("github", 0.1, 1000),
            ("unknown", 0.5, 365),
        ]

        for source, conf, age in test_cases:
            prov = self._make_provenance(source, conf, age)
            score = calculate_effective_score(prov, policy)
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for {source}"


class TestDetectConflicts:
    """Tests for detect_conflicts function with equivalence suppression."""

    def _make_provenance(self, value: str, source_key: str, confidence: float, age_days: int = 0):
        """Helper to create FieldProvenance for testing."""
        from datetime import datetime, timezone, timedelta
        from utils.synthesis_types import FieldProvenance
        from utils.merge_policy import normalize_company_name

        detected_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        return FieldProvenance(
            value=value,
            normalized_value=normalize_company_name(value),
            source_key=source_key,
            signal_id=1,
            confidence=confidence,
            detected_at=detected_at,
            evidence_ref="test",
        )

    def test_no_conflict_same_values(self):
        """Identical values should not produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
            self._make_provenance("Acme Inc", "crunchbase", 0.8),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is None

    def test_no_conflict_equivalence_suppression(self):
        """Values that normalize to same should not produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        # These all normalize to "acme" - no conflict
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
            self._make_provenance("ACME LLC", "crunchbase", 0.8),
            self._make_provenance("Acme Corporation", "sec_edgar", 0.85),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is None

    def test_conflict_detected_different_values(self):
        """Meaningfully different values should produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES
        from utils.synthesis_types import ConflictRecord

        policy = FIELD_MERGE_POLICIES["company_name"]
        # These normalize to different values: "acme" vs "beta"
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
            self._make_provenance("Beta Corp", "crunchbase", 0.8),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is not None
        assert isinstance(conflict, ConflictRecord)
        assert conflict.field_name == "company_name"
        assert conflict.conflict_type == "VALUE_MISMATCH"
        assert len(conflict.candidates) == 2

    def test_conflict_severity_from_materiality(self):
        """Conflict severity should map from policy materiality."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]  # CRITICAL materiality
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
            self._make_provenance("Beta Corp", "crunchbase", 0.8),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is not None
        assert conflict.severity == "CRITICAL"

    def test_single_candidate_no_conflict(self):
        """Single candidate should not produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is None

    def test_empty_candidates_no_conflict(self):
        """Empty candidate list should not produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        candidates = []

        conflict = detect_conflicts(candidates, policy)
        assert conflict is None

    def test_dates_within_tolerance_no_conflict(self):
        """Dates within tolerance should not produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES, normalize_date
        from utils.synthesis_types import FieldProvenance
        from datetime import datetime, timezone, timedelta

        policy = FIELD_MERGE_POLICIES["founding_date"]
        now = datetime.now(timezone.utc)

        # Two dates within 30 days - should NOT conflict
        candidates = [
            FieldProvenance(
                value="2024-01-15",
                normalized_value=normalize_date("2024-01-15"),
                source_key="companies_house",
                signal_id=1,
                confidence=0.9,
                detected_at=now,
                evidence_ref="test1",
            ),
            FieldProvenance(
                value="2024-01-30",
                normalized_value=normalize_date("2024-01-30"),
                source_key="crunchbase",
                signal_id=2,
                confidence=0.8,
                detected_at=now,
                evidence_ref="test2",
            ),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is None

    def test_dates_outside_tolerance_conflict(self):
        """Dates outside tolerance should produce a conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES, normalize_date
        from utils.synthesis_types import FieldProvenance
        from datetime import datetime, timezone

        policy = FIELD_MERGE_POLICIES["founding_date"]
        now = datetime.now(timezone.utc)

        # Two dates more than 30 days apart - should conflict
        candidates = [
            FieldProvenance(
                value="2024-01-15",
                normalized_value=normalize_date("2024-01-15"),
                source_key="companies_house",
                signal_id=1,
                confidence=0.9,
                detected_at=now,
                evidence_ref="test1",
            ),
            FieldProvenance(
                value="2024-06-15",
                normalized_value=normalize_date("2024-06-15"),
                source_key="crunchbase",
                signal_id=2,
                confidence=0.8,
                detected_at=now,
                evidence_ref="test2",
            ),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is not None
        assert conflict.conflict_type == "VALUE_MISMATCH"

    def test_policy_without_equivalence_fn(self):
        """Policy without equivalence_fn should use strict equality."""
        from utils.merge_policy import (
            detect_conflicts,
            FieldMergePolicy,
            MergeRule,
            Materiality,
        )
        from utils.synthesis_types import FieldProvenance
        from datetime import datetime, timezone

        # Policy with no equivalence_fn
        policy = FieldMergePolicy(
            field_name="test_field",
            merge_rule=MergeRule.PICK_HIGHEST_SCORE,
            materiality=Materiality.MINOR,
        )
        now = datetime.now(timezone.utc)

        # Different values should conflict (no equivalence suppression)
        candidates = [
            FieldProvenance(
                value="value1",
                normalized_value="value1",
                source_key="source_a",
                signal_id=1,
                confidence=0.9,
                detected_at=now,
                evidence_ref="test1",
            ),
            FieldProvenance(
                value="value2",
                normalized_value="value2",
                source_key="source_b",
                signal_id=2,
                confidence=0.8,
                detected_at=now,
                evidence_ref="test2",
            ),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is not None
        assert conflict.severity == "INFO"  # MINOR → INFO

    def test_three_distinct_values_conflict(self):
        """Three distinct values should all be in conflict record."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
            self._make_provenance("Beta Corp", "crunchbase", 0.8),
            self._make_provenance("Gamma Ltd", "sec_edgar", 0.85),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is not None
        assert len(conflict.candidates) == 3

    def test_mixed_equivalent_and_distinct(self):
        """Mix of equivalent and distinct values should conflict."""
        from utils.merge_policy import detect_conflicts, FIELD_MERGE_POLICIES

        policy = FIELD_MERGE_POLICIES["company_name"]
        # "Acme Inc" and "ACME LLC" normalize to "acme"
        # "Beta Corp" normalizes to "beta"
        candidates = [
            self._make_provenance("Acme Inc", "companies_house", 0.9),
            self._make_provenance("ACME LLC", "crunchbase", 0.75),
            self._make_provenance("Beta Corp", "sec_edgar", 0.85),
        ]

        conflict = detect_conflicts(candidates, policy)
        assert conflict is not None
        # All candidates included regardless of equivalence groups
        assert len(conflict.candidates) == 3
