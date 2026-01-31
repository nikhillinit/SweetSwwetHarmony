"""Tests for Team Shape Metrics - Phase D of founder_intel integration.

Analyzes contributor patterns to identify real startup teams:
- 2-5 core contributors indicates a real startup team
- Sustained activity window detection
- Concentration threshold (avoid single-person or huge OSS projects)

TDD: Write failing tests first, then implement.
"""
import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, List


# =============================================================================
# Test Imports (will fail until implementation exists)
# =============================================================================

class TestTeamShapeImports:
    """Test that all required components can be imported."""

    def test_import_team_shape_metrics(self):
        from utils.team_shape import TeamShapeMetrics
        assert TeamShapeMetrics is not None

    def test_import_team_shape_analyzer(self):
        from utils.team_shape import TeamShapeAnalyzer
        assert TeamShapeAnalyzer is not None

    def test_import_calculate_herfindahl(self):
        from utils.team_shape import calculate_herfindahl_index
        assert calculate_herfindahl_index is not None


# =============================================================================
# TeamShapeMetrics Dataclass Tests
# =============================================================================

class TestTeamShapeMetricsDataclass:
    """Test TeamShapeMetrics structure."""

    def test_metrics_has_required_fields(self):
        from utils.team_shape import TeamShapeMetrics

        metrics = TeamShapeMetrics(
            contributor_count=5,
            core_contributor_count=3,
            concentration_score=0.45,
            sustained_activity=True,
            activity_span_days=180,
            top_contributors=[
                {"login": "founder1", "commits": 50, "percentage": 0.50},
                {"login": "founder2", "commits": 30, "percentage": 0.30},
            ],
            monthly_activity={"2026-01": 20, "2025-12": 15},
        )

        assert metrics.contributor_count == 5
        assert metrics.core_contributor_count == 3
        assert metrics.concentration_score == 0.45
        assert metrics.sustained_activity is True
        assert metrics.activity_span_days == 180
        assert len(metrics.top_contributors) == 2
        assert len(metrics.monthly_activity) == 2

    def test_metrics_to_dict(self):
        from utils.team_shape import TeamShapeMetrics

        metrics = TeamShapeMetrics(
            contributor_count=3,
            core_contributor_count=2,
            concentration_score=0.55,
            sustained_activity=True,
            activity_span_days=90,
            top_contributors=[{"login": "dev", "commits": 10, "percentage": 1.0}],
            monthly_activity={"2026-01": 10},
        )

        d = metrics.to_dict()
        assert d["contributor_count"] == 3
        assert d["core_contributor_count"] == 2
        assert d["concentration_score"] == 0.55
        assert d["sustained_activity"] is True
        assert d["activity_span_days"] == 90

    def test_metrics_is_startup_team_property(self):
        """2-5 core contributors = startup team."""
        from utils.team_shape import TeamShapeMetrics

        # 2-5 core contributors = startup team
        startup_team = TeamShapeMetrics(
            contributor_count=8,
            core_contributor_count=3,
            concentration_score=0.40,
            sustained_activity=True,
            activity_span_days=120,
            top_contributors=[],
            monthly_activity={},
        )
        assert startup_team.is_startup_team is True

        # 1 core contributor = solo project
        solo_project = TeamShapeMetrics(
            contributor_count=1,
            core_contributor_count=1,
            concentration_score=1.0,
            sustained_activity=True,
            activity_span_days=120,
            top_contributors=[],
            monthly_activity={},
        )
        assert solo_project.is_startup_team is False

        # 10 core contributors = large OSS project
        large_oss = TeamShapeMetrics(
            contributor_count=50,
            core_contributor_count=10,
            concentration_score=0.10,
            sustained_activity=True,
            activity_span_days=365,
            top_contributors=[],
            monthly_activity={},
        )
        assert large_oss.is_startup_team is False


# =============================================================================
# Herfindahl Index Tests
# =============================================================================

class TestHerfindahlIndex:
    """Test Herfindahl-Hirschman Index calculation for commit concentration."""

    def test_single_contributor_returns_1(self):
        """100% concentration = HHI of 1.0"""
        from utils.team_shape import calculate_herfindahl_index

        commit_counts = [100]
        hhi = calculate_herfindahl_index(commit_counts)
        assert hhi == 1.0

    def test_equal_distribution_two_contributors(self):
        """50-50 split = HHI of 0.5"""
        from utils.team_shape import calculate_herfindahl_index

        commit_counts = [50, 50]
        hhi = calculate_herfindahl_index(commit_counts)
        assert hhi == 0.5

    def test_equal_distribution_four_contributors(self):
        """25-25-25-25 split = HHI of 0.25"""
        from utils.team_shape import calculate_herfindahl_index

        commit_counts = [25, 25, 25, 25]
        hhi = calculate_herfindahl_index(commit_counts)
        assert hhi == 0.25

    def test_unequal_distribution(self):
        """Unequal distribution: 70-20-10 = 0.49 + 0.04 + 0.01 = 0.54"""
        from utils.team_shape import calculate_herfindahl_index

        commit_counts = [70, 20, 10]
        hhi = calculate_herfindahl_index(commit_counts)
        assert abs(hhi - 0.54) < 0.001

    def test_empty_list_returns_0(self):
        from utils.team_shape import calculate_herfindahl_index

        hhi = calculate_herfindahl_index([])
        assert hhi == 0.0

    def test_all_zeros_returns_0(self):
        from utils.team_shape import calculate_herfindahl_index

        hhi = calculate_herfindahl_index([0, 0, 0])
        assert hhi == 0.0

    def test_highly_concentrated(self):
        """95-3-2 = very high concentration"""
        from utils.team_shape import calculate_herfindahl_index

        commit_counts = [95, 3, 2]
        hhi = calculate_herfindahl_index(commit_counts)
        # 0.9025 + 0.0009 + 0.0004 = 0.9038
        assert hhi > 0.9

    def test_widely_distributed(self):
        """10 equal contributors = low concentration"""
        from utils.team_shape import calculate_herfindahl_index

        commit_counts = [10] * 10
        hhi = calculate_herfindahl_index(commit_counts)
        # 10 * (0.1)^2 = 0.1
        assert abs(hhi - 0.1) < 0.001


# =============================================================================
# Sustained Activity Detection Tests
# =============================================================================

class TestSustainedActivityDetection:
    """Test sustained activity window detection."""

    def test_sustained_activity_3_of_6_months(self):
        """Activity in 3+ of last 6 months = sustained."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        monthly_activity = {
            "2026-01": 10,
            "2025-12": 5,
            "2025-11": 8,
            # No activity in Oct, Sep, Aug
        }

        assert analyzer._check_sustained_activity(monthly_activity) is True

    def test_not_sustained_only_2_months(self):
        """Activity in only 2 months = not sustained."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        monthly_activity = {
            "2026-01": 10,
            "2025-12": 5,
            # Only 2 months
        }

        assert analyzer._check_sustained_activity(monthly_activity) is False

    def test_sustained_activity_considers_recent_6_months(self):
        """Only consider last 6 months for sustained activity check."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        # Activity long ago doesn't count
        monthly_activity = {
            "2025-01": 100,
            "2024-12": 100,
            "2024-11": 100,
            "2024-10": 100,
            # But only 1 recent month
            "2026-01": 5,
        }

        # Should not be sustained (only 1 recent month)
        assert analyzer._check_sustained_activity(monthly_activity) is False

    def test_empty_activity_not_sustained(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        assert analyzer._check_sustained_activity({}) is False


# =============================================================================
# Core Contributor Detection Tests
# =============================================================================

class TestCoreContributorDetection:
    """Test detection of core contributors (>10% of commits)."""

    def test_identify_core_contributors(self):
        """Contributors with >10% of commits are core."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        # Total commits: 100
        contributors = [
            {"login": "founder", "commits": 50, "percentage": 0.50},  # 50% - core
            {"login": "cofounder", "commits": 30, "percentage": 0.30},  # 30% - core
            {"login": "helper1", "commits": 10, "percentage": 0.10},  # 10% - NOT core (>10% required)
            {"login": "helper2", "commits": 5, "percentage": 0.05},  # 5% - not core
            {"login": "helper3", "commits": 5, "percentage": 0.05},  # 5% - not core
        ]

        core = analyzer._identify_core_contributors(contributors)
        # Only founder and cofounder have >10% (helper1 has exactly 10%, not >10%)
        assert len(core) == 2
        assert "founder" in [c["login"] for c in core]
        assert "cofounder" in [c["login"] for c in core]

    def test_all_contributors_core_when_small_team(self):
        """In a 2-person team, both are likely core."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        contributors = [
            {"login": "founder", "commits": 60, "percentage": 0.60},  # 60% - core
            {"login": "cofounder", "commits": 40, "percentage": 0.40},  # 40% - core
        ]

        core = analyzer._identify_core_contributors(contributors)
        assert len(core) == 2

    def test_no_core_when_no_contributors(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        core = analyzer._identify_core_contributors([])
        assert len(core) == 0


# =============================================================================
# Activity Span Calculation Tests
# =============================================================================

class TestActivitySpanCalculation:
    """Test activity span (days between first and last commit)."""

    def test_activity_span_from_dates(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        # First commit 90 days ago, last commit today
        first_commit = datetime.now(timezone.utc) - timedelta(days=90)
        last_commit = datetime.now(timezone.utc)

        span = analyzer._calculate_activity_span(first_commit, last_commit)
        assert span == 90

    def test_activity_span_same_day(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        today = datetime.now(timezone.utc)

        span = analyzer._calculate_activity_span(today, today)
        assert span == 0

    def test_activity_span_none_dates_returns_0(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        span = analyzer._calculate_activity_span(None, None)
        assert span == 0


# =============================================================================
# TeamShapeAnalyzer Integration Tests
# =============================================================================

class TestTeamShapeAnalyzerIntegration:
    """Test TeamShapeAnalyzer with sample data."""

    def test_analyze_from_contributor_data(self):
        """Analyze team shape from contributor stats."""
        from utils.team_shape import TeamShapeAnalyzer, TeamShapeMetrics

        analyzer = TeamShapeAnalyzer()

        contributor_data = [
            {
                "login": "founder",
                "total": 100,
                "weeks": [
                    {"w": 1735689600, "a": 500, "d": 200, "c": 20},  # 2025-01-01
                    {"w": 1738368000, "a": 300, "d": 100, "c": 15},  # 2025-02-01
                ]
            },
            {
                "login": "cofounder",
                "total": 50,
                "weeks": [
                    {"w": 1735689600, "a": 200, "d": 50, "c": 10},
                    {"w": 1738368000, "a": 150, "d": 30, "c": 8},
                ]
            },
            {
                "login": "helper",
                "total": 10,
                "weeks": [
                    {"w": 1735689600, "a": 50, "d": 10, "c": 3},
                ]
            },
        ]

        metrics = analyzer.analyze_from_contributor_stats(contributor_data)

        assert isinstance(metrics, TeamShapeMetrics)
        assert metrics.contributor_count == 3
        # founder (100), cofounder (50), helper (10) - total 160
        # founder: 62.5%, cofounder: 31.25%, helper: 6.25%
        # Core (>10%): founder, cofounder
        assert metrics.core_contributor_count == 2
        assert len(metrics.top_contributors) <= 5

    def test_analyze_empty_data(self):
        """Empty contributor data returns zeroed metrics."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        metrics = analyzer.analyze_from_contributor_stats([])

        assert metrics.contributor_count == 0
        assert metrics.core_contributor_count == 0
        assert metrics.concentration_score == 0.0
        assert metrics.sustained_activity is False

    def test_analyze_single_contributor(self):
        """Single contributor = solo project, not startup team."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        contributor_data = [
            {
                "login": "solo_dev",
                "total": 100,
                "weeks": [
                    {"w": 1735689600, "a": 1000, "d": 500, "c": 100},
                ]
            }
        ]

        metrics = analyzer.analyze_from_contributor_stats(contributor_data)

        assert metrics.contributor_count == 1
        assert metrics.core_contributor_count == 1
        assert metrics.concentration_score == 1.0
        assert metrics.is_startup_team is False

    def test_analyze_ideal_startup_team(self):
        """3-person startup team with balanced contributions."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        contributor_data = [
            {
                "login": "founder1",
                "total": 150,
                "weeks": [
                    {"w": 1735689600, "a": 500, "d": 200, "c": 50},
                    {"w": 1738368000, "a": 300, "d": 100, "c": 50},
                    {"w": 1740960000, "a": 200, "d": 80, "c": 50},
                ]
            },
            {
                "login": "founder2",
                "total": 120,
                "weeks": [
                    {"w": 1735689600, "a": 400, "d": 150, "c": 40},
                    {"w": 1738368000, "a": 250, "d": 90, "c": 40},
                    {"w": 1740960000, "a": 150, "d": 60, "c": 40},
                ]
            },
            {
                "login": "founder3",
                "total": 80,
                "weeks": [
                    {"w": 1735689600, "a": 300, "d": 100, "c": 25},
                    {"w": 1738368000, "a": 180, "d": 70, "c": 25},
                    {"w": 1740960000, "a": 120, "d": 50, "c": 30},
                ]
            },
        ]

        metrics = analyzer.analyze_from_contributor_stats(contributor_data)

        assert metrics.contributor_count == 3
        assert metrics.core_contributor_count == 3  # All >10%
        assert metrics.is_startup_team is True
        # Concentration should be moderate (not 1.0, not too low)
        assert 0.2 < metrics.concentration_score < 0.6


# =============================================================================
# Shadow Logging Format Tests
# =============================================================================

class TestShadowLoggingFormat:
    """Test the format suitable for shadow_log storage."""

    def test_metrics_to_shadow_log_format(self):
        """TeamShapeMetrics.to_dict() should be suitable for shadow logging."""
        from utils.team_shape import TeamShapeMetrics
        import json

        metrics = TeamShapeMetrics(
            contributor_count=4,
            core_contributor_count=2,
            concentration_score=0.45,
            sustained_activity=True,
            activity_span_days=120,
            top_contributors=[
                {"login": "dev1", "commits": 60, "percentage": 0.60},
                {"login": "dev2", "commits": 40, "percentage": 0.40},
            ],
            monthly_activity={"2026-01": 50, "2025-12": 45},
        )

        shadow_data = metrics.to_dict()

        # Should be JSON-serializable
        json_str = json.dumps(shadow_data)
        assert json_str is not None

        # Should contain key fields for analysis
        assert "contributor_count" in shadow_data
        assert "core_contributor_count" in shadow_data
        assert "concentration_score" in shadow_data
        assert "sustained_activity" in shadow_data
        assert "is_startup_team" in shadow_data


# =============================================================================
# Edge Cases Tests
# =============================================================================

class TestTeamShapeEdgeCases:
    """Test edge cases and error handling."""

    def test_handles_none_input(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()
        metrics = analyzer.analyze_from_contributor_stats(None)
        assert metrics.contributor_count == 0

    def test_handles_malformed_contributor_data(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        # Missing required fields
        malformed = [
            {"login": "dev"},  # missing 'total' and 'weeks'
            {"total": 10},  # missing 'login' and 'weeks'
        ]

        # Should not crash
        metrics = analyzer.analyze_from_contributor_stats(malformed)
        assert metrics is not None

    def test_handles_zero_total_commits(self):
        """Contributors with 0 commits should be handled."""
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        contributor_data = [
            {"login": "inactive", "total": 0, "weeks": []},
            {"login": "active", "total": 50, "weeks": []},
        ]

        metrics = analyzer.analyze_from_contributor_stats(contributor_data)
        # Should count the active contributor
        assert metrics.contributor_count >= 1


# =============================================================================
# Threshold Configuration Tests
# =============================================================================

class TestTeamShapeThresholds:
    """Test configurable thresholds."""

    def test_default_core_threshold_is_0_10(self):
        from utils.team_shape import TeamShapeAnalyzer, DEFAULT_CORE_THRESHOLD

        assert DEFAULT_CORE_THRESHOLD == 0.10
        analyzer = TeamShapeAnalyzer()
        assert analyzer.core_threshold == 0.10

    def test_custom_core_threshold(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer(core_threshold=0.15)
        assert analyzer.core_threshold == 0.15

    def test_default_sustained_months_is_3(self):
        from utils.team_shape import TeamShapeAnalyzer, DEFAULT_SUSTAINED_MONTHS

        assert DEFAULT_SUSTAINED_MONTHS == 3
        analyzer = TeamShapeAnalyzer()
        assert analyzer.sustained_months == 3

    def test_custom_sustained_months(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer(sustained_months=4)
        assert analyzer.sustained_months == 4


# =============================================================================
# Monthly Activity Extraction Tests
# =============================================================================

class TestMonthlyActivityExtraction:
    """Test extraction of monthly activity from week data."""

    def test_extract_monthly_activity(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        weeks = [
            {"w": 1735689600, "c": 10},  # 2025-01-01
            {"w": 1736294400, "c": 5},   # 2025-01-08
            {"w": 1738886400, "c": 8},   # 2025-02-07
        ]

        monthly = analyzer._extract_monthly_activity(weeks)

        assert "2025-01" in monthly
        assert monthly["2025-01"] == 15  # 10 + 5
        assert "2025-02" in monthly
        assert monthly["2025-02"] == 8

    def test_extract_monthly_from_multiple_contributors(self):
        from utils.team_shape import TeamShapeAnalyzer

        analyzer = TeamShapeAnalyzer()

        contributor_data = [
            {
                "login": "dev1",
                "total": 20,
                "weeks": [
                    {"w": 1735689600, "c": 10},  # 2025-01
                    {"w": 1738886400, "c": 10},  # 2025-02
                ]
            },
            {
                "login": "dev2",
                "total": 10,
                "weeks": [
                    {"w": 1735689600, "c": 5},   # 2025-01
                    {"w": 1738886400, "c": 5},   # 2025-02
                ]
            },
        ]

        monthly = analyzer._aggregate_monthly_activity(contributor_data)

        assert monthly["2025-01"] == 15  # 10 + 5
        assert monthly["2025-02"] == 15  # 10 + 5
