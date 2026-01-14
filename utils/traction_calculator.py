"""
Traction Calculator - Calculates momentum metrics (Harmonic-inspired).

Part of Deal Intelligence Engine (Phase 2).

This module provides:
- GitHub momentum calculation (stars growth, commit velocity)
- Hiring velocity calculation (job posting frequency)
- Social momentum (Product Hunt votes, HN mentions)
- Composite momentum score with percentile ranking

Usage:
    calculator = TractionCalculator(signal_store)

    # Calculate individual momentum metrics
    github = await calculator.calculate_github_momentum("domain:startup.io")
    hiring = await calculator.calculate_hiring_velocity("domain:startup.io")
    social = await calculator.calculate_social_momentum("domain:startup.io")

    # Calculate composite traction score
    score = await calculator.calculate("domain:startup.io")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class TractionScore:
    """Composite traction score for a company/signal."""
    canonical_key: str
    github_stars_growth_30d: float
    github_commit_velocity: float
    job_posting_velocity: float
    job_count_growth_30d: float
    ph_vote_growth_30d: float
    hn_mention_growth_30d: float
    composite_momentum: float
    momentum_percentile: int


# =============================================================================
# WEIGHTS FOR COMPOSITE SCORE
# =============================================================================

# Weights for composite momentum calculation
MOMENTUM_WEIGHTS = {
    'github_stars': 0.25,
    'hiring': 0.30,
    'social': 0.25,
    'commit_velocity': 0.20,
}

# Normalization caps for individual metrics
NORMALIZATION_CAPS = {
    'stars_growth': 2.0,  # Cap at 200% growth
    'job_velocity': 5.0,  # Cap at 5 jobs per week
    'ph_growth': 3.0,  # Cap at 300% growth
    'hn_growth': 3.0,  # Cap at 300% growth
}


# =============================================================================
# TRACTION CALCULATOR
# =============================================================================

class TractionCalculator:
    """
    Calculates momentum metrics for companies.

    Inspired by Harmonic's approach to measuring company traction
    through multiple signals over time.
    """

    def __init__(self, store):
        """
        Initialize with a signal store.

        Args:
            store: Storage layer with signal access methods
        """
        self.store = store

    async def calculate_github_momentum(
        self,
        canonical_key: str,
    ) -> Dict[str, Any]:
        """
        Calculate GitHub momentum (stars growth, commit velocity).

        Args:
            canonical_key: The canonical identifier for the company

        Returns:
            Dict with stars_growth_30d, commit_velocity, has_data
        """
        # Get GitHub signals for this company
        signals = await self.store.get_signals_for_traction(
            canonical_key=canonical_key,
            signal_types=['github_trending', 'github_activity'],
        )

        if not signals:
            return {
                'stars_growth_30d': 0.0,
                'commit_velocity': 0.0,
                'has_data': False,
            }

        # Filter to GitHub trending signals with star data
        star_signals = [
            s for s in signals
            if s.get('signal_type') == 'github_trending'
            and s.get('raw_data', {}).get('stars') is not None
        ]

        if len(star_signals) < 2:
            # Need at least 2 data points for growth calculation
            stars_growth = 0.0
        else:
            # Sort by detected_at
            star_signals.sort(key=lambda s: s.get('detected_at', datetime.min))

            # Get oldest and newest star counts
            oldest = star_signals[0].get('raw_data', {}).get('stars', 0)
            newest = star_signals[-1].get('raw_data', {}).get('stars', 0)

            if oldest > 0:
                stars_growth = (newest - oldest) / oldest
            else:
                stars_growth = 0.0

        # Calculate commit velocity (commits per day)
        commit_signals = [
            s for s in signals
            if s.get('signal_type') == 'github_activity'
            and s.get('raw_data', {}).get('commits') is not None
        ]

        commit_velocity = 0.0
        if commit_signals:
            total_commits = sum(
                s.get('raw_data', {}).get('commits', 0)
                for s in commit_signals
            )
            # Average over 30 days
            commit_velocity = total_commits / 30.0

        return {
            'stars_growth_30d': stars_growth,
            'commit_velocity': commit_velocity,
            'has_data': True,
        }

    async def calculate_hiring_velocity(
        self,
        canonical_key: str,
    ) -> Dict[str, Any]:
        """
        Calculate hiring velocity (job posting frequency).

        Args:
            canonical_key: The canonical identifier for the company

        Returns:
            Dict with job_posting_velocity, job_count_30d, job_count_growth_30d
        """
        # Get hiring signals
        signals = await self.store.get_signals_for_traction(
            canonical_key=canonical_key,
            signal_types=['hiring_signal'],
        )

        if not signals:
            return {
                'job_posting_velocity': 0.0,
                'job_count_30d': 0,
                'job_count_growth_30d': 0.0,
            }

        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)
        sixty_days_ago = now - timedelta(days=60)

        # Count jobs in recent 30 days
        recent_jobs = [
            s for s in signals
            if s.get('detected_at', datetime.min.replace(tzinfo=timezone.utc)) >= thirty_days_ago
        ]

        # Count jobs in previous 30 days (30-60 days ago)
        old_jobs = [
            s for s in signals
            if sixty_days_ago <= s.get('detected_at', datetime.min.replace(tzinfo=timezone.utc)) < thirty_days_ago
        ]

        job_count_30d = len(recent_jobs)

        # Calculate velocity (jobs per week)
        # Find date range of recent jobs
        if recent_jobs:
            dates = [s.get('detected_at') for s in recent_jobs]
            min_date = min(dates)
            max_date = max(dates)
            days_span = max((max_date - min_date).days, 1)
            weeks = days_span / 7.0
            job_posting_velocity = job_count_30d / max(weeks, 1)
        else:
            job_posting_velocity = 0.0

        # Calculate growth
        old_count = len(old_jobs)
        if old_count > 0:
            job_count_growth = (job_count_30d - old_count) / old_count
        else:
            job_count_growth = 0.0 if job_count_30d == 0 else 1.0

        return {
            'job_posting_velocity': job_posting_velocity,
            'job_count_30d': job_count_30d,
            'job_count_growth_30d': job_count_growth,
        }

    async def calculate_social_momentum(
        self,
        canonical_key: str,
    ) -> Dict[str, Any]:
        """
        Calculate social momentum (Product Hunt, Hacker News).

        Args:
            canonical_key: The canonical identifier for the company

        Returns:
            Dict with ph_vote_growth_30d, hn_mention_growth_30d, has_social_data
        """
        # Get social signals
        signals = await self.store.get_signals_for_traction(
            canonical_key=canonical_key,
            signal_types=['product_hunt_launch', 'hacker_news_mention'],
        )

        if not signals:
            return {
                'ph_vote_growth_30d': 0.0,
                'hn_mention_growth_30d': 0.0,
                'hn_mention_count_30d': 0,
                'has_social_data': False,
            }

        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)
        sixty_days_ago = now - timedelta(days=60)

        # Product Hunt vote growth
        ph_signals = [
            s for s in signals
            if s.get('signal_type') == 'product_hunt_launch'
            and s.get('raw_data', {}).get('upvotes') is not None
        ]

        ph_vote_growth = 0.0
        if len(ph_signals) >= 2:
            ph_signals.sort(key=lambda s: s.get('detected_at', datetime.min))
            oldest_upvotes = ph_signals[0].get('raw_data', {}).get('upvotes', 0)
            newest_upvotes = ph_signals[-1].get('raw_data', {}).get('upvotes', 0)
            if oldest_upvotes > 0:
                ph_vote_growth = (newest_upvotes - oldest_upvotes) / oldest_upvotes

        # Hacker News mention growth
        hn_signals = [
            s for s in signals
            if s.get('signal_type') == 'hacker_news_mention'
        ]

        recent_hn = [
            s for s in hn_signals
            if s.get('detected_at', datetime.min.replace(tzinfo=timezone.utc)) >= thirty_days_ago
        ]

        old_hn = [
            s for s in hn_signals
            if sixty_days_ago <= s.get('detected_at', datetime.min.replace(tzinfo=timezone.utc)) < thirty_days_ago
        ]

        hn_mention_count_30d = len(recent_hn)
        old_hn_count = len(old_hn)

        if old_hn_count > 0:
            hn_mention_growth = (hn_mention_count_30d - old_hn_count) / old_hn_count
        else:
            hn_mention_growth = 0.0 if hn_mention_count_30d == 0 else 1.0

        return {
            'ph_vote_growth_30d': ph_vote_growth,
            'hn_mention_growth_30d': hn_mention_growth,
            'hn_mention_count_30d': hn_mention_count_30d,
            'has_social_data': bool(ph_signals or hn_signals),
        }

    async def calculate(
        self,
        canonical_key: str,
    ) -> TractionScore:
        """
        Calculate composite traction score.

        Combines all momentum sources into a weighted score
        with percentile ranking.

        Args:
            canonical_key: The canonical identifier for the company

        Returns:
            TractionScore with all momentum metrics and percentile
        """
        # Calculate individual components
        github = await self.calculate_github_momentum(canonical_key)
        hiring = await self.calculate_hiring_velocity(canonical_key)
        social = await self.calculate_social_momentum(canonical_key)

        # Normalize and cap metrics
        stars_norm = min(
            abs(github['stars_growth_30d']) / NORMALIZATION_CAPS['stars_growth'],
            1.0
        )
        hiring_norm = min(
            hiring['job_posting_velocity'] / NORMALIZATION_CAPS['job_velocity'],
            1.0
        )
        ph_norm = min(
            abs(social['ph_vote_growth_30d']) / NORMALIZATION_CAPS['ph_growth'],
            1.0
        )
        hn_norm = min(
            abs(social['hn_mention_growth_30d']) / NORMALIZATION_CAPS['hn_growth'],
            1.0
        )

        # Calculate weighted composite
        social_combined = (ph_norm + hn_norm) / 2.0

        composite = (
            MOMENTUM_WEIGHTS['github_stars'] * stars_norm +
            MOMENTUM_WEIGHTS['hiring'] * hiring_norm +
            MOMENTUM_WEIGHTS['social'] * social_combined +
            MOMENTUM_WEIGHTS['commit_velocity'] * min(github['commit_velocity'] / 10.0, 1.0)
        )

        # Ensure composite is between 0 and 1
        composite = max(0.0, min(1.0, composite))

        # Calculate percentile ranking
        percentile = await self._calculate_percentile(composite)

        return TractionScore(
            canonical_key=canonical_key,
            github_stars_growth_30d=github['stars_growth_30d'],
            github_commit_velocity=github['commit_velocity'],
            job_posting_velocity=hiring['job_posting_velocity'],
            job_count_growth_30d=hiring['job_count_growth_30d'],
            ph_vote_growth_30d=social['ph_vote_growth_30d'],
            hn_mention_growth_30d=social['hn_mention_growth_30d'],
            composite_momentum=composite,
            momentum_percentile=percentile,
        )

    async def _calculate_percentile(self, score: float) -> int:
        """
        Calculate percentile rank vs historical scores.

        Args:
            score: The composite momentum score

        Returns:
            Percentile (0-100)
        """
        try:
            historical = await self.store.get_historical_traction_scores()
        except (AttributeError, Exception):
            # Method not implemented or error
            historical = []

        # Ensure we have a valid list
        if not historical or not isinstance(historical, (list, tuple)):
            # Default to 50th percentile when no history
            return 50

        if len(historical) == 0:
            return 50

        # Count how many scores are below this score
        below = sum(1 for h in historical if h < score)
        percentile = int((below / len(historical)) * 100)

        return min(100, max(0, percentile))
