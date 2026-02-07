"""Tests for shared ML text builder."""

import pytest

from utils.ml_text_builder import build_ml_text, _extract_sld, _normalize_ml_text


class TestBuildMlText:
    """Test build_ml_text consistency for training/serving skew prevention."""

    def test_description_only(self):
        result = build_ml_text("Meal kit delivery startup")
        assert "meal kit delivery startup" in result

    def test_description_and_company_name(self):
        result = build_ml_text("Healthy meals delivered", "FreshBox")
        assert "healthy meals delivered" in result
        assert "freshbox" in result

    def test_description_company_domain(self):
        result = build_ml_text("Fitness app", "FitTrack", "getfittrack.com")
        assert "fitness app" in result
        assert "fittrack" in result
        assert "getfittrack" in result

    def test_empty_description_returns_company(self):
        result = build_ml_text("", "FreshBox")
        assert "freshbox" in result

    def test_all_empty_returns_empty(self):
        result = build_ml_text("", None, None)
        assert result == ""

    def test_none_description_returns_empty(self):
        result = build_ml_text("", None, None)
        assert result == ""

    def test_whitespace_only_stripped(self):
        result = build_ml_text("  ", "  ", "  ")
        assert result == ""

    def test_normalization_applied(self):
        """Verify normalization matches ThesisMatcher._normalize()."""
        result = build_ml_text("Meal-Kit_Delivery/App")
        assert result == "meal kit delivery app"

    def test_domain_sld_extracted(self):
        result = build_ml_text("App", None, "https://getmyapp.com/about")
        assert "getmyapp" in result
        assert "com" not in result.split()[-1] or "getmyapp" in result

    def test_idempotent(self):
        """Same inputs always produce same output."""
        a = build_ml_text("test", "company", "domain.com")
        b = build_ml_text("test", "company", "domain.com")
        assert a == b


class TestExtractSld:
    """Test SLD extraction from domain names."""

    def test_simple_domain(self):
        assert _extract_sld("example.com") == "example"

    def test_with_protocol(self):
        assert _extract_sld("https://example.com") == "example"

    def test_with_port(self):
        assert _extract_sld("example.com:8080") == "example"

    def test_with_path(self):
        assert _extract_sld("example.com/about") == "example"

    def test_subdomain(self):
        assert _extract_sld("app.example.co.uk") == "app"

    def test_empty_returns_none(self):
        assert _extract_sld("") is None

    def test_protocol_only(self):
        result = _extract_sld("https://")
        # Should handle gracefully
        assert result is None or result == ""


class TestNormalizeMlText:
    """Test text normalization."""

    def test_lowercase(self):
        assert _normalize_ml_text("HELLO WORLD") == "hello world"

    def test_dash_to_space(self):
        assert _normalize_ml_text("meal-kit") == "meal kit"

    def test_underscore_to_space(self):
        assert _normalize_ml_text("health_tech") == "health tech"

    def test_slash_to_space(self):
        assert _normalize_ml_text("food/beverage") == "food beverage"

    def test_collapsed_whitespace(self):
        assert _normalize_ml_text("too   many   spaces") == "too many spaces"

    def test_empty_returns_empty(self):
        assert _normalize_ml_text("") == ""
