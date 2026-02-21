"""
Tests for utils/hn_title.py — shared HN title prefix parsing.

Covers:
- strip_hn_prefix() for Show/Launch/Demo/Ask HN variants
- extract_name_from_hn_body() separator-based extraction
"""

import pytest

from utils.hn_title import strip_hn_prefix, extract_name_from_hn_body, HN_SEP_RE


class TestStripHnPrefix:
    """Test HN prefix stripping for recognized prefixes."""

    def test_show_hn_with_colon(self):
        cleaned, prefix = strip_hn_prefix("Show HN: Acme - fast tool")
        assert cleaned == "Acme - fast tool"
        assert prefix == "show"

    def test_launch_hn_with_colon(self):
        cleaned, prefix = strip_hn_prefix(
            "Launch HN: Queenly (YC W21) \u2014 Marketplace"
        )
        assert cleaned == "Queenly (YC W21) \u2014 Marketplace"
        assert prefix == "launch"

    def test_demo_hn_with_colon(self):
        cleaned, prefix = strip_hn_prefix("Demo HN: MyApp \u2014 beta")
        assert cleaned == "MyApp \u2014 beta"
        assert prefix == "demo"

    def test_ask_hn_with_colon(self):
        cleaned, prefix = strip_hn_prefix("Ask HN: What's your fav tool?")
        assert cleaned == "What's your fav tool?"
        assert prefix == "ask"

    def test_lowercase(self):
        cleaned, prefix = strip_hn_prefix("show hn: acme")
        assert cleaned == "acme"
        assert prefix == "show"

    def test_no_colon_no_body(self):
        cleaned, prefix = strip_hn_prefix("Show HN")
        assert cleaned == ""
        assert prefix == "show"

    def test_no_match_random_title(self):
        cleaned, prefix = strip_hn_prefix("Some random title")
        assert cleaned == "Some random title"
        assert prefix is None

    def test_empty_string(self):
        cleaned, prefix = strip_hn_prefix("")
        assert cleaned == ""
        assert prefix is None

    def test_tell_hn_not_recognized(self):
        cleaned, prefix = strip_hn_prefix("Tell HN: something")
        assert cleaned == "Tell HN: something"
        assert prefix is None

    def test_showcase_hn_not_recognized(self):
        cleaned, prefix = strip_hn_prefix("Showcase HN: Acme")
        assert cleaned == "Showcase HN: Acme"
        assert prefix is None

    def test_launch_hn_no_colon(self):
        cleaned, prefix = strip_hn_prefix("Launch HN Acme - tool")
        assert cleaned == "Acme - tool"
        assert prefix == "launch"

    def test_mixed_case(self):
        cleaned, prefix = strip_hn_prefix("SHOW HN: BigThing")
        assert cleaned == "BigThing"
        assert prefix == "show"


class TestExtractNameFromHnBody:
    """Test company name extraction from HN title body."""

    def test_dash_separator(self):
        assert extract_name_from_hn_body("Acme - the fast tool") == "Acme"

    def test_em_dash_separator(self):
        assert extract_name_from_hn_body("Acme \u2014 fast tool") == "Acme"

    def test_multi_word_name(self):
        assert extract_name_from_hn_body("Fresh Bowls - meal delivery") == "Fresh Bowls"

    def test_paren_separator(self):
        assert extract_name_from_hn_body("Queenly (YC W21) \u2014 Marketplace") == "Queenly"

    def test_comma_separator(self):
        assert extract_name_from_hn_body("Acme, the AI tool") == "Acme"

    def test_no_separator(self):
        assert extract_name_from_hn_body("Raycast") is None

    def test_empty_string(self):
        assert extract_name_from_hn_body("") is None

    def test_whitespace_only(self):
        assert extract_name_from_hn_body("   ") is None

    def test_en_dash(self):
        assert extract_name_from_hn_body("Zed \u2013 a fast editor") == "Zed"

    def test_pipe_separator(self):
        assert extract_name_from_hn_body("Acme| The ultimate tool") == "Acme"

    def test_preserves_casing(self):
        result = extract_name_from_hn_body("MyApp - something")
        assert result == "MyApp"


class TestHnSepReExported:
    """Verify HN_SEP_RE matches expected patterns (same as old _SHOW_HN_SEP_RE)."""

    def test_dash(self):
        assert HN_SEP_RE.search("Zed - editor") is not None

    def test_em_dash(self):
        assert HN_SEP_RE.search("Acme \u2014 tool") is not None

    def test_paren(self):
        assert HN_SEP_RE.search("App (beta)") is not None

    def test_comma(self):
        assert HN_SEP_RE.search("Acme, tool") is not None

    def test_no_match(self):
        assert HN_SEP_RE.search("SingleWord") is None
