"""
Tests for KeywordExtractor - extracts search keywords from profile text.

TDD: Write failing tests first, then implement.
"""

import pytest


class TestKeywordExtractor:
    """Tests for KeywordExtractor class."""

    def test_extract_basic_keywords(self):
        """Extract keywords from simple profile text."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = """Company: Acme Foods
Problem: Reduces food waste in restaurants
Customer: Restaurant chains and hospitality groups
Business model: B2B_SaaS
Category: Consumer CPG, Travel & Hospitality"""

        keywords = extractor.extract(text, max_keywords=10)

        assert len(keywords) <= 10
        assert len(keywords) >= 3  # Should find at least some keywords
        # Should include domain-relevant terms
        assert any("food" in kw.lower() for kw in keywords)
        assert any("restaurant" in kw.lower() for kw in keywords)

    def test_extract_filters_stopwords(self):
        """Stopwords should be filtered out."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = "The quick brown fox jumps over the lazy dog"

        keywords = extractor.extract(text)

        # Common stopwords should not be in results
        stopwords = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "is"}
        for kw in keywords:
            assert kw.lower() not in stopwords

    def test_extract_preserves_domain_terms(self):
        """Domain-specific terms should be preserved."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = """Company: HealthBot
Problem: Helps consumers track fitness and nutrition
Customer: Health-conscious millennials
Business model: B2C_subscription
Category: Consumer Health Tech"""

        keywords = extractor.extract(text)

        # Should preserve domain terms
        keyword_str = " ".join(keywords).lower()
        assert "health" in keyword_str or "fitness" in keyword_str or "nutrition" in keyword_str

    def test_extract_respects_max_keywords(self):
        """Should not return more than max_keywords."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = "Apple banana cherry date elderberry fig grape honeydew " \
               "jackfruit kiwi lemon mango nectarine orange papaya"

        keywords = extractor.extract(text, max_keywords=5)

        assert len(keywords) <= 5

    def test_extract_empty_text(self):
        """Empty text should return empty list."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()

        keywords = extractor.extract("")

        assert keywords == []

    def test_extract_very_short_text(self):
        """Very short text should return what's available."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()

        keywords = extractor.extract("Food delivery")

        assert len(keywords) >= 1
        assert "food" in [kw.lower() for kw in keywords] or "delivery" in [kw.lower() for kw in keywords]


class TestPositionWeighting:
    """Tests for position-based keyword weighting."""

    def test_early_words_weighted_higher(self):
        """Words appearing earlier should be ranked higher."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()

        # "restaurant" appears first, "hotel" appears last
        text = """Company: Restaurant Corp
Problem: Restaurant software for operations
Customer: Hotels and resorts
Business model: B2B_SaaS
Category: Travel & Hospitality"""

        keywords = extractor.extract(text, max_keywords=5)

        # "restaurant" should appear before "hotel" due to position
        keyword_list = [kw.lower() for kw in keywords]
        if "restaurant" in keyword_list and "hotel" in keyword_list:
            assert keyword_list.index("restaurant") < keyword_list.index("hotel")


class TestCategoryKeywords:
    """Tests for category-specific keyword extraction."""

    def test_extract_with_category_hints(self):
        """Category hints should boost related keywords."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = """Company: FoodApp
Problem: Food delivery platform
Customer: Urban consumers
Business model: B2C_marketplace
Category: Consumer CPG, Consumer Marketplace"""

        keywords = extractor.extract(text)

        # Should include category-related terms
        keyword_str = " ".join(keywords).lower()
        assert "food" in keyword_str or "consumer" in keyword_str or "marketplace" in keyword_str


class TestFTSQueryGeneration:
    """Tests for generating FTS5-compatible queries."""

    def test_build_fts_query_or(self):
        """Build an OR query from keywords."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        keywords = ["food", "delivery", "restaurant"]

        query = extractor.build_fts_query(keywords, operator="OR")

        assert "food" in query
        assert "delivery" in query
        assert "restaurant" in query
        assert " OR " in query

    def test_build_fts_query_and(self):
        """Build an AND query from keywords."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        keywords = ["food", "delivery"]

        query = extractor.build_fts_query(keywords, operator="AND")

        assert " AND " in query

    def test_build_fts_query_escapes_special_chars(self):
        """Special FTS5 characters should be escaped."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        keywords = ["food-delivery", "24/7", "company:name"]

        query = extractor.build_fts_query(keywords)

        # Special chars should be handled (escaped or removed)
        assert ":" not in query or '":"' in query  # Either removed or quoted
        assert query.count('"') % 2 == 0  # Balanced quotes

    def test_build_fts_query_empty(self):
        """Empty keywords should return empty query."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()

        query = extractor.build_fts_query([])

        assert query == ""


class TestEdgeCases:
    """Edge case tests."""

    def test_unicode_characters(self):
        """Handle Unicode characters properly."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = "Caf\u00e9 delivery service for \u65e5\u672c\u8a9e restaurants"

        keywords = extractor.extract(text)

        # Should not crash, should extract something
        assert isinstance(keywords, list)

    def test_numbers_in_text(self):
        """Numbers should be handled appropriately."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = "24/7 food delivery with 100% satisfaction"

        keywords = extractor.extract(text)

        # Numbers alone shouldn't be keywords, but combined terms might be
        assert all(not kw.isdigit() for kw in keywords)

    def test_hyphenated_words(self):
        """Hyphenated words should be handled."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = "Health-tech platform for well-being"

        keywords = extractor.extract(text)

        # Should extract meaningful parts
        keyword_str = " ".join(keywords).lower()
        assert "health" in keyword_str or "tech" in keyword_str or "well" in keyword_str or "being" in keyword_str

    def test_duplicate_words(self):
        """Duplicate words should only appear once in output."""
        from utils.keyword_extractor import KeywordExtractor

        extractor = KeywordExtractor()
        text = "Food food food delivery delivery service"

        keywords = extractor.extract(text)

        # No duplicates
        assert len(keywords) == len(set(keywords))


class TestIntegrationWithProfileBuilder:
    """Integration tests with ProfileTextBuilder."""

    def test_extract_from_built_profile_text(self):
        """Extract keywords from ProfileTextBuilder output."""
        from utils.keyword_extractor import KeywordExtractor
        from utils.profile_text_builder import ProfileTextBuilder

        # Build profile text
        builder = ProfileTextBuilder()
        profile_dict = {
            "company_name": "FreshMeals",
            "problem_solved": "Reduces food waste by connecting restaurants with surplus food to consumers",
            "target_customer": "Environmentally conscious consumers and restaurant owners",
            "business_model": "B2C_marketplace",
            "category_hints": ["Consumer CPG", "Consumer Marketplace"],
        }
        text = builder.build_from_dict(profile_dict)

        # Extract keywords
        extractor = KeywordExtractor()
        keywords = extractor.extract(text, max_keywords=10)

        # Should have relevant keywords
        assert len(keywords) >= 3
        keyword_str = " ".join(keywords).lower()
        assert "food" in keyword_str or "restaurant" in keyword_str or "consumer" in keyword_str
