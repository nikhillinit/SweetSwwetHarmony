"""Team Shape Metrics for Startup Team Detection.

Phase D of founder_intel integration: Analyze contributor patterns to identify
real startup teams vs solo projects or large OSS projects.

Key Metrics:
- contributor_count: Total unique contributors
- core_contributor_count: Contributors with >10% of commits (ideal: 2-5)
- concentration_score: Herfindahl index of commit distribution (0-1)
- sustained_activity: Has commits in 3+ of last 6 months
- activity_span_days: Days between first and last commit

Usage:
    from utils.team_shape import TeamShapeAnalyzer

    analyzer = TeamShapeAnalyzer()
    metrics = analyzer.analyze_from_contributor_stats(contributor_data)

    if metrics.is_startup_team:
        # 2-5 core contributors = likely a real startup
        process_as_startup(metrics)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default thresholds from spec
DEFAULT_CORE_THRESHOLD = 0.10  # >10% of commits = core contributor
DEFAULT_SUSTAINED_MONTHS = 3  # Need activity in 3+ of last 6 months
DEFAULT_LOOKBACK_MONTHS = 6  # Look back 6 months for sustained activity


@dataclass
class TeamShapeMetrics:
    """Metrics describing the shape/composition of a development team.

    Attributes:
        contributor_count: Total unique contributors
        core_contributor_count: Contributors with >10% of commits
        concentration_score: Herfindahl index (0=dispersed, 1=concentrated)
        sustained_activity: True if commits in 3+ of last 6 months
        activity_span_days: Days between first and last commit
        top_contributors: Top 5 contributors with commit counts and percentages
        monthly_activity: Dict of YYYY-MM -> commit count
    """

    contributor_count: int
    core_contributor_count: int
    concentration_score: float
    sustained_activity: bool
    activity_span_days: int
    top_contributors: List[Dict[str, Any]]
    monthly_activity: Dict[str, int]

    @property
    def is_startup_team(self) -> bool:
        """Check if this looks like a real startup team.

        Criteria: 2-5 core contributors indicates a real startup team.
        - 1 core contributor = solo project
        - 2-5 core contributors = startup team
        - 6+ core contributors = large OSS project
        """
        return 2 <= self.core_contributor_count <= 5

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization and shadow logging."""
        return {
            "contributor_count": self.contributor_count,
            "core_contributor_count": self.core_contributor_count,
            "concentration_score": self.concentration_score,
            "sustained_activity": self.sustained_activity,
            "activity_span_days": self.activity_span_days,
            "top_contributors": self.top_contributors,
            "monthly_activity": self.monthly_activity,
            "is_startup_team": self.is_startup_team,
        }


def calculate_herfindahl_index(commit_counts: List[int]) -> float:
    """Calculate Herfindahl-Hirschman Index (HHI) for commit concentration.

    The HHI is the sum of squared market shares (here, commit shares).
    - HHI = 1.0 means one person has all commits (monopoly)
    - HHI = 1/n means perfectly equal distribution among n contributors

    Args:
        commit_counts: List of commit counts per contributor

    Returns:
        HHI value between 0 and 1 (0 if no commits)
    """
    if not commit_counts:
        return 0.0

    total = sum(commit_counts)
    if total == 0:
        return 0.0

    # Sum of squared shares
    hhi = sum((count / total) ** 2 for count in commit_counts)
    return hhi


class TeamShapeAnalyzer:
    """Analyzes team shape from GitHub contributor statistics.

    Designed to identify real startup teams (2-5 core contributors)
    and filter out solo projects or large OSS projects.
    """

    def __init__(
        self,
        core_threshold: float = DEFAULT_CORE_THRESHOLD,
        sustained_months: int = DEFAULT_SUSTAINED_MONTHS,
        lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    ):
        """Initialize analyzer with configurable thresholds.

        Args:
            core_threshold: Minimum share to be a core contributor (default 0.10)
            sustained_months: Months needed for sustained activity (default 3)
            lookback_months: How far back to look for activity (default 6)
        """
        self.core_threshold = core_threshold
        self.sustained_months = sustained_months
        self.lookback_months = lookback_months

    def analyze_from_contributor_stats(
        self,
        contributor_data: Optional[List[Dict[str, Any]]],
    ) -> TeamShapeMetrics:
        """Analyze team shape from GitHub contributor stats API response.

        Args:
            contributor_data: List from /repos/{owner}/{repo}/stats/contributors
                Each item has: login, total, weeks (list of {w, a, d, c})

        Returns:
            TeamShapeMetrics with analysis results
        """
        if not contributor_data:
            return TeamShapeMetrics(
                contributor_count=0,
                core_contributor_count=0,
                concentration_score=0.0,
                sustained_activity=False,
                activity_span_days=0,
                top_contributors=[],
                monthly_activity={},
            )

        # Extract commit counts per contributor
        contributors = []
        for contrib in contributor_data:
            login = contrib.get("login")
            total = contrib.get("total", 0)
            if login and total > 0:
                contributors.append({
                    "login": login,
                    "commits": total,
                    "weeks": contrib.get("weeks", []),
                })

        if not contributors:
            return TeamShapeMetrics(
                contributor_count=0,
                core_contributor_count=0,
                concentration_score=0.0,
                sustained_activity=False,
                activity_span_days=0,
                top_contributors=[],
                monthly_activity={},
            )

        # Calculate metrics
        total_commits = sum(c["commits"] for c in contributors)

        # Add percentage to each contributor
        for contrib in contributors:
            contrib["percentage"] = contrib["commits"] / total_commits if total_commits > 0 else 0

        # Sort by commits descending
        contributors.sort(key=lambda x: x["commits"], reverse=True)

        # Identify core contributors
        core_contributors = self._identify_core_contributors(contributors)

        # Calculate concentration (Herfindahl index)
        commit_counts = [c["commits"] for c in contributors]
        concentration = calculate_herfindahl_index(commit_counts)

        # Aggregate monthly activity across all contributors
        monthly_activity = self._aggregate_monthly_activity(contributor_data)

        # Check sustained activity
        sustained = self._check_sustained_activity(monthly_activity)

        # Calculate activity span
        first_commit, last_commit = self._get_activity_bounds(contributor_data)
        activity_span = self._calculate_activity_span(first_commit, last_commit)

        # Top 5 contributors
        top_contributors = [
            {
                "login": c["login"],
                "commits": c["commits"],
                "percentage": round(c["percentage"], 4),
            }
            for c in contributors[:5]
        ]

        return TeamShapeMetrics(
            contributor_count=len(contributors),
            core_contributor_count=len(core_contributors),
            concentration_score=round(concentration, 4),
            sustained_activity=sustained,
            activity_span_days=activity_span,
            top_contributors=top_contributors,
            monthly_activity=monthly_activity,
        )

    def _identify_core_contributors(
        self,
        contributors: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Identify core contributors (>10% of commits by default).

        Args:
            contributors: List of contributor dicts with 'commits' and 'percentage'

        Returns:
            List of core contributors
        """
        return [c for c in contributors if c.get("percentage", 0) > self.core_threshold]

    def _check_sustained_activity(
        self,
        monthly_activity: Dict[str, int],
    ) -> bool:
        """Check if there is sustained activity (commits in 3+ of last 6 months).

        Args:
            monthly_activity: Dict of YYYY-MM -> commit count

        Returns:
            True if sustained activity detected
        """
        if not monthly_activity:
            return False

        # Get the last 6 months from now
        now = datetime.now(timezone.utc)
        recent_months = set()

        for i in range(self.lookback_months):
            month_date = now - timedelta(days=i * 30)
            month_key = month_date.strftime("%Y-%m")
            recent_months.add(month_key)

        # Count months with activity in the recent window
        active_months = sum(
            1 for month, count in monthly_activity.items()
            if month in recent_months and count > 0
        )

        return active_months >= self.sustained_months

    def _calculate_activity_span(
        self,
        first_commit: Optional[datetime],
        last_commit: Optional[datetime],
    ) -> int:
        """Calculate days between first and last commit.

        Args:
            first_commit: Datetime of first commit
            last_commit: Datetime of last commit

        Returns:
            Number of days (0 if either is None)
        """
        if not first_commit or not last_commit:
            return 0

        delta = last_commit - first_commit
        return max(0, delta.days)

    def _get_activity_bounds(
        self,
        contributor_data: List[Dict[str, Any]],
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        """Get the first and last commit dates from contributor data.

        Args:
            contributor_data: List from GitHub stats/contributors API

        Returns:
            Tuple of (first_commit_date, last_commit_date)
        """
        all_weeks = []

        for contrib in contributor_data:
            weeks = contrib.get("weeks", [])
            for week in weeks:
                if week.get("c", 0) > 0:  # Has commits
                    timestamp = week.get("w")
                    if timestamp:
                        all_weeks.append(timestamp)

        if not all_weeks:
            return None, None

        first_timestamp = min(all_weeks)
        last_timestamp = max(all_weeks)

        return (
            datetime.fromtimestamp(first_timestamp, tz=timezone.utc),
            datetime.fromtimestamp(last_timestamp, tz=timezone.utc),
        )

    def _extract_monthly_activity(
        self,
        weeks: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Extract monthly activity from week data.

        Args:
            weeks: List of week dicts with 'w' (timestamp) and 'c' (commits)

        Returns:
            Dict of YYYY-MM -> commit count
        """
        monthly: Dict[str, int] = {}

        for week in weeks:
            timestamp = week.get("w")
            commits = week.get("c", 0)

            if timestamp and commits > 0:
                dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                month_key = dt.strftime("%Y-%m")
                monthly[month_key] = monthly.get(month_key, 0) + commits

        return monthly

    def _aggregate_monthly_activity(
        self,
        contributor_data: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Aggregate monthly activity across all contributors.

        Args:
            contributor_data: List from GitHub stats/contributors API

        Returns:
            Dict of YYYY-MM -> total commit count
        """
        monthly: Dict[str, int] = {}

        for contrib in contributor_data:
            weeks = contrib.get("weeks", [])
            contrib_monthly = self._extract_monthly_activity(weeks)

            for month, count in contrib_monthly.items():
                monthly[month] = monthly.get(month, 0) + count

        return monthly
