"""
Tests for utils/text_extraction.py

Phase 0C: Data-Driven Tuning
"""

import pytest

from utils.text_extraction import (
    extract_text,
    get_fields,
    get_value,
    normalize_source,
    strip_html,
    DEFAULT_FIELDS,
    MAX_OUTPUT_LENGTH,
    SOURCE_FIELD_MAP,
)


class TestNormalizeSource:
    """Test source normalization."""

    def test_lowercase(self):
        """Source should be lowercased."""
        assert normalize_source("HACKER_NEWS") == "hacker_news"
        assert normalize_source("ProductHunt") == "producthunt"

    def test_hyphen_to_underscore(self):
        """Hyphens should become underscores."""
        assert normalize_source("hacker-news") == "hacker_news"

    def test_product_hunt_alias(self):
        """product_hunt should normalize to producthunt."""
        assert normalize_source("product_hunt") == "producthunt"
        assert normalize_source("product_hunt_launch") == "producthunt"

    def test_hacker_news_aliases(self):
        """Various HN aliases should normalize correctly."""
        assert normalize_source("hackernews") == "hacker_news"
        assert normalize_source("hn") == "hacker_news"
        assert normalize_source("hacker-news") == "hacker_news"

    def test_none_returns_default(self):
        """None source should return _default."""
        assert normalize_source(None) == "_default"

    def test_empty_returns_default(self):
        """Empty source should return _default."""
        assert normalize_source("") == "_default"

    def test_unknown_source_passthrough(self):
        """Unknown sources should pass through normalized."""
        assert normalize_source("new_source") == "new_source"


class TestGetFields:
    """Test field list retrieval."""

    def test_hacker_news_gets_title_and_story_text(self):
        """HN should include title and story_text."""
        fields = get_fields("hacker_news")
        assert "title" in fields
        assert "story_text" in fields

    def test_hacker_news_includes_default_fields(self):
        """HN should also include default fields."""
        fields = get_fields("hacker_news")
        # HN-specific first, then defaults
        assert "description" in fields
        assert "tagline" in fields

    def test_fields_are_deduped(self):
        """Fields should be deduplicated."""
        fields = get_fields("hacker_news")
        # title appears in both HN and default, should only appear once
        assert fields.count("title") == 1

    def test_source_specific_fields_come_first(self):
        """Source-specific fields should come before defaults."""
        fields = get_fields("hacker_news")
        title_idx = fields.index("title")
        story_text_idx = fields.index("story_text")
        description_idx = fields.index("description")
        # story_text is HN-specific, description is default
        assert story_text_idx < description_idx

    def test_unknown_source_gets_default_fields(self):
        """Unknown source should get default fields."""
        fields = get_fields("unknown_source")
        assert fields == DEFAULT_FIELDS

    def test_producthunt_alias_works(self):
        """product_hunt alias should work."""
        fields1 = get_fields("producthunt")
        fields2 = get_fields("product_hunt")
        assert fields1 == fields2


class TestStripHtml:
    """Test HTML stripping."""

    def test_removes_simple_tags(self):
        """Simple tags should be removed."""
        text = "<p>Hello</p> <b>World</b>"
        result = strip_html(text)
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result
        assert "World" in result

    def test_script_content_removed(self):
        """Script tags and their content should be removed."""
        text = "Before <script>alert('xss');</script> After"
        result = strip_html(text)
        assert "alert" not in result
        assert "script" not in result
        assert "Before" in result
        assert "After" in result

    def test_style_content_removed(self):
        """Style tags and their content should be removed."""
        text = "Text <style>.foo { color: red; }</style> More"
        result = strip_html(text)
        assert "color" not in result
        assert "style" not in result
        assert "Text" in result
        assert "More" in result

    def test_multiline_script_removed(self):
        """Multiline scripts should be removed."""
        text = """Hello
        <script>
        function evil() {
            return 'bad';
        }
        </script>
        World"""
        result = strip_html(text)
        assert "evil" not in result
        assert "function" not in result

    def test_preserves_word_boundaries(self):
        """Tags replaced with space to prevent word concatenation."""
        text = "<span>Hello</span><span>World</span>"
        result = strip_html(text)
        # Should have space between Hello and World
        assert "Hello" in result
        assert "World" in result


class TestGetValue:
    """Test value extraction from dicts."""

    def test_simple_key(self):
        """Simple key extraction."""
        data = {"description": "Hello World"}
        assert get_value(data, "description") == "Hello World"

    def test_missing_key(self):
        """Missing key returns empty string."""
        data = {"description": "Hello"}
        assert get_value(data, "missing") == ""

    def test_nested_path(self):
        """Dotted path for nested extraction."""
        data = {"profile": {"bio": "Test bio"}}
        assert get_value(data, "profile.bio") == "Test bio"

    def test_deeply_nested_path(self):
        """Deep nesting works."""
        data = {"a": {"b": {"c": "deep"}}}
        assert get_value(data, "a.b.c") == "deep"

    def test_broken_nested_path(self):
        """Broken path returns empty string."""
        data = {"profile": {"name": "Test"}}
        assert get_value(data, "profile.bio") == ""

    def test_list_of_strings(self):
        """List of strings joined."""
        data = {"tags": ["health", "wellness", "fitness"]}
        assert get_value(data, "tags") == "health wellness fitness"

    def test_ignores_non_string_in_list(self):
        """Non-strings in list are ignored."""
        data = {"tags": ["health", 123, "wellness", True]}
        result = get_value(data, "tags")
        assert "health" in result
        assert "wellness" in result
        assert "123" not in result
        assert "True" not in result

    def test_ignores_integer_value(self):
        """Integer values return empty string."""
        data = {"count": 100}
        assert get_value(data, "count") == ""

    def test_ignores_boolean_value(self):
        """Boolean values return empty string."""
        data = {"active": True}
        assert get_value(data, "active") == ""

    def test_none_value(self):
        """None value returns empty string."""
        data = {"description": None}
        assert get_value(data, "description") == ""

    def test_empty_data(self):
        """Empty data returns empty string."""
        assert get_value({}, "description") == ""
        assert get_value(None, "description") == ""


class TestExtractText:
    """Test the main extract_text function."""

    def test_hacker_news_extracts_title(self):
        """HN signals should extract title."""
        raw_data = {
            "title": "Show HN: My Startup",
            "story_text": "We built something cool",
        }
        text = extract_text(raw_data, source="hacker_news")
        assert "Show HN: My Startup" in text
        assert "built something cool" in text

    def test_producthunt_extracts_description(self):
        """PH signals should extract description and tagline."""
        raw_data = {
            "description": "AI-powered meal planning",
            "tagline": "Healthy eating made easy",
            "category": "Health & Wellness",
        }
        text = extract_text(raw_data, source="producthunt")
        assert "AI-powered meal planning" in text
        assert "Healthy eating made easy" in text
        assert "Health & Wellness" in text

    def test_crunchbase_extracts_description(self):
        """Crunchbase signals should extract description."""
        raw_data = {
            "description": "Women freeze eggs for free",
            "tagline": "Split fertility program",
            "funding_round": "Series A",  # Should be ignored (not a text field)
        }
        text = extract_text(raw_data, source="crunchbase")
        assert "Women freeze eggs" in text
        assert "Split fertility" in text
        assert "Series A" not in text

    def test_fallback_to_default_fields(self):
        """Unknown source should use default fields."""
        raw_data = {
            "description": "Some description",
            "tagline": "Some tagline",
        }
        text = extract_text(raw_data, source="unknown_source")
        assert "Some description" in text
        assert "Some tagline" in text

    def test_html_stripped_by_default(self):
        """HTML should be stripped by default."""
        raw_data = {"description": "<p>Hello <b>World</b></p>"}
        text = extract_text(raw_data, source="producthunt")
        assert "<p>" not in text
        assert "<b>" not in text
        assert "Hello" in text
        assert "World" in text

    def test_html_stripping_can_be_disabled(self):
        """HTML stripping can be disabled."""
        raw_data = {"description": "<p>Hello</p>"}
        text = extract_text(raw_data, source="producthunt", strip_markup=False)
        assert "<p>" in text

    def test_entities_unescaped(self):
        """HTML entities should be unescaped."""
        raw_data = {"description": "Food &amp; Beverage"}
        text = extract_text(raw_data, source="producthunt")
        assert "Food & Beverage" in text
        assert "&amp;" not in text

    def test_whitespace_collapsed(self):
        """Whitespace should be collapsed."""
        raw_data = {"description": "Hello    World\n\nTest"}
        text = extract_text(raw_data, source="producthunt")
        assert "  " not in text
        assert "\n" not in text

    def test_empty_data_returns_empty_string(self):
        """Empty data returns empty string."""
        assert extract_text({}, source="producthunt") == ""
        assert extract_text(None, source="producthunt") == ""

    def test_no_matching_fields_returns_empty(self):
        """No matching fields returns empty string."""
        raw_data = {"unknown_field": "value"}
        # HN-specific fields + defaults, none match
        text = extract_text(raw_data, source="hacker_news")
        # Should be empty or just whitespace
        assert text.strip() == ""

    def test_length_capping(self):
        """Very long text should be capped."""
        long_text = "A" * 20000
        raw_data = {"description": long_text}
        text = extract_text(raw_data, source="producthunt")
        assert len(text) <= MAX_OUTPUT_LENGTH

    def test_no_lowercasing(self):
        """Text should NOT be lowercased."""
        raw_data = {"description": "Hello WORLD"}
        text = extract_text(raw_data, source="producthunt")
        assert "Hello WORLD" in text

    def test_source_normalization_in_extract(self):
        """Source normalization should work in extract_text."""
        raw_data = {"title": "Test", "story_text": "Content"}
        # Various HN representations should all work
        t1 = extract_text(raw_data, source="hacker_news")
        t2 = extract_text(raw_data, source="hacker-news")
        t3 = extract_text(raw_data, source="HACKER_NEWS")
        assert t1 == t2 == t3


class TestIntegration:
    """Integration tests with realistic data."""

    def test_real_hacker_news_signal(self):
        """Test with realistic HN signal data."""
        raw_data = {
            "canonical_key": "domain:example.com",
            "company_name": "Example Startup",
            "company_domain": "example.com",
            "hacker_news_id": "12345",
            "title": "Show HN: We built a meal planning app",
            "points": 150,  # Should be ignored
            "num_comments": 45,  # Should be ignored
            "author": "founder",
            "is_show_hn": True,  # Should be ignored
            "story_text": "We've been working on this for 6 months. It's a consumer health app.",
            "url": "https://example.com",
        }
        text = extract_text(raw_data, source="hacker_news")

        # Should include title and story_text
        assert "Show HN" in text
        assert "meal planning" in text
        assert "consumer health app" in text

        # Should NOT include numeric/boolean fields
        assert "150" not in text
        assert "True" not in text

    def test_real_producthunt_signal(self):
        """Test with realistic PH signal data."""
        raw_data = {
            "description": "The world's only full-service robotic manicure",
            "tagline": "AI-powered robotic manicure in under 10 minutes",
            "category": "Health & Wellness",
        }
        text = extract_text(raw_data, source="producthunt")

        assert "robotic manicure" in text
        assert "AI-powered" in text
        assert "Health & Wellness" in text

    def test_hacker_news_with_missing_story_text(self):
        """HN signal without story_text should still work via defaults."""
        raw_data = {
            "title": "Show HN: My startup",
            # No story_text
            "description": "Fallback description",  # From defaults
        }
        text = extract_text(raw_data, source="hacker_news")

        # Should get title (HN-specific) and description (default fallback)
        assert "Show HN" in text
        assert "Fallback description" in text
