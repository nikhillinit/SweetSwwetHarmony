"""Tests for competitor detection."""
import pytest
import json
from pathlib import Path
from utils.competitor_detector import CompetitorDetector, CompetitorMatch


class TestCompetitorMatch:
    """Test CompetitorMatch dataclass."""

    def test_match_has_required_fields(self):
        match = CompetitorMatch(
            portfolio_company="PortCo Alpha",
            category="consumer_cpg",
            matched_keywords=["meal kit"],
            confidence=0.8,
        )
        assert match.portfolio_company == "PortCo Alpha"
        assert match.category == "consumer_cpg"

    def test_to_dict(self):
        match = CompetitorMatch(
            portfolio_company="FitTrack",
            category="consumer_health_tech",
            matched_keywords=["fitness app"],
            confidence=0.9,
        )
        d = match.to_dict()
        assert d["portfolio_company"] == "FitTrack"
        assert d["matched_keywords"] == ["fitness app"]


class TestCompetitorDetector:
    """Test competitor detection logic."""

    @pytest.fixture
    def detector(self, tmp_path):
        # Create test portfolio file
        portfolio = {
            "companies": [
                {
                    "name": "MealBox Co",
                    "category": "consumer_cpg",
                    "keywords": ["meal kit", "food delivery", "subscription meals"]
                },
                {
                    "name": "FitTrack",
                    "category": "consumer_health_tech",
                    "keywords": ["fitness app", "workout tracking"]
                }
            ]
        }
        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text(json.dumps(portfolio))

        return CompetitorDetector(str(portfolio_path))

    def test_detects_same_category_competitor(self, detector):
        """Should detect competitor in same category with keyword match."""
        result = detector.check(
            category="consumer_cpg",
            description="We deliver meal kits to your door",
        )
        assert result is not None
        assert result.portfolio_company == "MealBox Co"

    def test_no_match_different_category(self, detector):
        """Should not flag if category doesn't match."""
        result = detector.check(
            category="travel_hospitality",
            description="We deliver meal kits to your door",
        )
        assert result is None

    def test_no_match_no_keyword_overlap(self, detector):
        """Should not flag if no keyword overlap."""
        result = detector.check(
            category="consumer_cpg",
            description="We sell organic supplements",
        )
        assert result is None

    def test_returns_none_for_empty_portfolio(self, tmp_path):
        """Should handle empty portfolio gracefully."""
        empty_path = tmp_path / "empty.json"
        empty_path.write_text('{"companies": []}')

        detector = CompetitorDetector(str(empty_path))
        result = detector.check("consumer_cpg", "meal kit delivery")
        assert result is None

    def test_handles_missing_file(self, tmp_path):
        """Should handle missing portfolio file gracefully."""
        detector = CompetitorDetector(str(tmp_path / "missing.json"))
        result = detector.check("consumer_cpg", "meal kit")
        assert result is None

    def test_reload_portfolio(self, tmp_path):
        """Should be able to reload portfolio from file."""
        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text('{"companies": []}')

        detector = CompetitorDetector(str(portfolio_path))
        assert detector.check("consumer_cpg", "meal kit") is None

        # Update file
        portfolio_path.write_text(json.dumps({
            "companies": [{
                "name": "Test",
                "category": "consumer_cpg",
                "keywords": ["meal kit"]
            }]
        }))
        detector.reload()

        result = detector.check("consumer_cpg", "meal kit delivery")
        assert result is not None
