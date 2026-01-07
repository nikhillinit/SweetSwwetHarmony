"""
Tests for hard disqualifiers - Stage 1 thesis filter.

Tests keyword-based rejection of non-consumer signals.
"""

import pytest


class TestDisqualifyResult:
    """Test DisqualifyResult dataclass"""

    def test_disqualify_result_exists(self):
        """DisqualifyResult should be importable"""
        from consumer.thesis_filter.hard_disqualifiers import DisqualifyResult

        result = DisqualifyResult(passed=True)
        assert result.passed is True
        assert result.reason is None

    def test_disqualify_result_with_reason(self):
        """DisqualifyResult should store rejection reason"""
        from consumer.thesis_filter.hard_disqualifiers import DisqualifyResult

        result = DisqualifyResult(
            passed=False,
            reason="Contains B2B keywords",
            category="b2b"
        )
        assert result.passed is False
        assert result.reason == "Contains B2B keywords"
        assert result.category == "b2b"


class TestKeywordSets:
    """Test keyword sets are properly defined"""

    def test_b2b_keywords_exist(self):
        """B2B_KEYWORDS set should exist and contain expected terms"""
        from consumer.thesis_filter.hard_disqualifiers import B2B_KEYWORDS

        assert isinstance(B2B_KEYWORDS, set)
        assert "enterprise" in B2B_KEYWORDS
        assert "b2b" in B2B_KEYWORDS
        assert "saas" in B2B_KEYWORDS

    def test_crypto_keywords_exist(self):
        """CRYPTO_KEYWORDS set should exist and contain expected terms"""
        from consumer.thesis_filter.hard_disqualifiers import CRYPTO_KEYWORDS

        assert isinstance(CRYPTO_KEYWORDS, set)
        assert "blockchain" in CRYPTO_KEYWORDS
        assert "crypto" in CRYPTO_KEYWORDS
        assert "nft" in CRYPTO_KEYWORDS

    def test_services_keywords_exist(self):
        """SERVICES_KEYWORDS set should exist and contain expected terms"""
        from consumer.thesis_filter.hard_disqualifiers import SERVICES_KEYWORDS

        assert isinstance(SERVICES_KEYWORDS, set)
        assert "agency" in SERVICES_KEYWORDS
        assert "consulting" in SERVICES_KEYWORDS

    def test_job_keywords_exist(self):
        """JOB_KEYWORDS set should exist and contain expected terms"""
        from consumer.thesis_filter.hard_disqualifiers import JOB_KEYWORDS

        assert isinstance(JOB_KEYWORDS, set)
        assert "hiring" in JOB_KEYWORDS
        assert "join our team" in JOB_KEYWORDS

    def test_consumer_positive_keywords_exist(self):
        """CONSUMER_POSITIVE_KEYWORDS should exist for reducing false positives"""
        from consumer.thesis_filter.hard_disqualifiers import CONSUMER_POSITIVE_KEYWORDS

        assert isinstance(CONSUMER_POSITIVE_KEYWORDS, set)
        assert "food" in CONSUMER_POSITIVE_KEYWORDS
        assert "fitness" in CONSUMER_POSITIVE_KEYWORDS


class TestCheckTextFunction:
    """Test the check_text function if it exists"""

    def test_module_importable(self):
        """hard_disqualifiers module should be importable"""
        from consumer.thesis_filter import hard_disqualifiers

        assert hard_disqualifiers is not None


class TestDisqualifierLogic:
    """Test disqualification logic patterns"""

    def test_b2b_terms_in_keywords(self):
        """B2B keywords should include common enterprise terms"""
        from consumer.thesis_filter.hard_disqualifiers import B2B_KEYWORDS

        enterprise_terms = ["enterprise", "b2b", "saas", "api", "devops"]
        for term in enterprise_terms:
            assert term in B2B_KEYWORDS, f"Missing B2B term: {term}"

    def test_crypto_terms_comprehensive(self):
        """Crypto keywords should cover major cryptocurrency terms"""
        from consumer.thesis_filter.hard_disqualifiers import CRYPTO_KEYWORDS

        crypto_terms = ["bitcoin", "ethereum", "nft", "defi", "web3"]
        for term in crypto_terms:
            assert term in CRYPTO_KEYWORDS, f"Missing crypto term: {term}"

    def test_consumer_verticals_covered(self):
        """Consumer positive keywords should cover CPG/Health/Travel"""
        from consumer.thesis_filter.hard_disqualifiers import CONSUMER_POSITIVE_KEYWORDS

        # CPG terms
        assert "food" in CONSUMER_POSITIVE_KEYWORDS
        assert "beverage" in CONSUMER_POSITIVE_KEYWORDS

        # Health terms
        assert "fitness" in CONSUMER_POSITIVE_KEYWORDS
        assert "wellness" in CONSUMER_POSITIVE_KEYWORDS
