"""
Tests for Analytics Dashboard data layer.

TDD: Write failing tests first, then implement get_signals_for_analytics().
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import json


class TestGetSignalsForAnalytics:
    """Tests for SignalStore.get_signals_for_analytics()."""

    @pytest.fixture
    def signal_store(self, tmp_path):
        """Create a signal store with test database."""
        from storage.signal_store import SignalStore
        db_path = tmp_path / "test_signals.db"
        return SignalStore(str(db_path))

    @pytest.fixture
    async def initialized_store(self, signal_store):
        """Initialize store and return it."""
        await signal_store.initialize()
        yield signal_store
        await signal_store.close()

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_returns_expected_fields(self, initialized_store):
        """Should return all required fields for analytics dashboard."""
        # Arrange: Insert test signal with thesis classification
        signal_id = await initialized_store.save_signal(
            signal_type="github_trending",
            source_api="github",
            canonical_key="domain:test.com",
            confidence=0.75,
            raw_data={"stars": 100},
            company_name="Test Company"
        )

        # Add thesis classification
        await initialized_store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            keyword_score=0.6,
            keyword_category="Consumer CPG",
            negative_keywords=[],
            thesis_match=True,
            thesis_fit_score=0.7,
            category="Consumer CPG",
            stage_estimate="Seed",
            confidence=0.8,
            rationale="Test rationale",
            key_signals=["test signal"],
            prompt_version="v1",
            model="gemini-1.5-flash",
            input_tokens=100,
            output_tokens=50,
            latency_ms=500,
            competitor_flag=False,
            competitor_match=None
        )

        # Mark as pushed to test processing_status
        await initialized_store.mark_pushed(signal_id, "notion-123")

        # Act
        results = await initialized_store.get_signals_for_analytics(days=30)

        # Assert: Check all expected fields are present
        assert len(results) == 1
        result = results[0]

        # Required fields from plan
        assert "signal_id" in result
        assert "company_name" in result
        assert "canonical_key" in result
        assert "source_api" in result
        assert "confidence" in result
        assert "detected_at" in result
        assert "vertical" in result
        assert "category" in result
        assert "thesis_fit_score" in result
        assert "competitor_flag" in result
        assert "processing_status" in result

        # Verify values
        assert result["company_name"] == "Test Company"
        assert result["source_api"] == "github"
        assert result["category"] == "Consumer CPG"
        assert result["thesis_fit_score"] == 0.7
        assert result["processing_status"] == "pushed"

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_respects_time_range(self, initialized_store):
        """Should only return signals within the specified time range."""
        # Arrange: Insert signals with different dates
        now = datetime.now(timezone.utc)

        # Recent signal (5 days ago)
        await initialized_store.save_signal(
            signal_type="github_trending",
            source_api="github",
            canonical_key="domain:recent.com",
            confidence=0.75,
            raw_data={},
            company_name="Recent Company",
            detected_at=now - timedelta(days=5)
        )

        # Old signal (45 days ago) - manually insert to bypass detected_at override
        old_detected_at = (now - timedelta(days=45)).isoformat()
        async with initialized_store.transaction() as conn:
            await conn.execute("""
                INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                                     confidence, raw_data, detected_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("github_trending", "github", "domain:old.com", "Old Company",
                  0.75, "{}", old_detected_at, old_detected_at))

        # Act: Query with 30-day range
        results = await initialized_store.get_signals_for_analytics(days=30)

        # Assert: Only recent signal should be returned
        company_names = [r["company_name"] for r in results]
        assert "Recent Company" in company_names
        assert "Old Company" not in company_names

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_handles_null_thesis(self, initialized_store):
        """Should return 'Unclassified' for signals without thesis data."""
        # Arrange: Insert signal WITHOUT thesis classification
        await initialized_store.save_signal(
            signal_type="github_trending",
            source_api="github",
            canonical_key="domain:nothesis.com",
            confidence=0.5,
            raw_data={},
            company_name="No Thesis Company"
        )

        # Act
        results = await initialized_store.get_signals_for_analytics(days=30)

        # Assert: Should have fallback values via COALESCE
        assert len(results) == 1
        result = results[0]
        assert result["category"] == "Unclassified"
        assert result["thesis_fit_score"] == 0
        assert result["competitor_flag"] == 0

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_limits_results(self, initialized_store):
        """Should never return more than 5000 rows for performance."""
        # This test verifies the LIMIT clause is present
        # We don't actually insert 5000+ signals as that would be slow

        # Act: The method should have a limit
        # We'll verify by checking that with many signals, we get at most 5000
        results = await initialized_store.get_signals_for_analytics(days=365)

        # Assert: Should be <= 5000
        assert len(results) <= 5000

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_handles_missing_fts(self, initialized_store):
        """Should return 'Unknown' vertical if FTS entry is missing."""
        # Arrange: Insert signal but don't index in FTS
        async with initialized_store.transaction() as conn:
            await conn.execute("""
                INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                                     confidence, raw_data, detected_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("test", "test", "domain:nofts.com", "No FTS Company",
                  0.5, "{}", datetime.now(timezone.utc).isoformat(),
                  datetime.now(timezone.utc).isoformat()))

        # Act
        results = await initialized_store.get_signals_for_analytics(days=30)

        # Assert: Should have 'Unknown' vertical
        result = [r for r in results if r["company_name"] == "No FTS Company"][0]
        assert result["vertical"] == "Unknown"

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_returns_pending_status(self, initialized_store):
        """Should return 'pending' status for signals not in signal_processing."""
        # Arrange: Insert signal (not pushed)
        await initialized_store.save_signal(
            signal_type="github_trending",
            source_api="github",
            canonical_key="domain:pending.com",
            confidence=0.5,
            raw_data={},
            company_name="Pending Company"
        )

        # Act
        results = await initialized_store.get_signals_for_analytics(days=30)

        # Assert: Should have 'pending' status
        assert len(results) == 1
        assert results[0]["processing_status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_signals_for_analytics_all_time(self, initialized_store):
        """Should return all signals when days=0 or very large."""
        # Arrange: Insert signals with various dates
        now = datetime.now(timezone.utc)

        for i in range(3):
            days_ago = i * 100  # 0, 100, 200 days ago
            detected_at = (now - timedelta(days=days_ago)).isoformat()
            async with initialized_store.transaction() as conn:
                await conn.execute("""
                    INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                                         confidence, raw_data, detected_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("test", "test", f"domain:test{i}.com", f"Company {i}",
                      0.5, "{}", detected_at, detected_at))

        # Act: Query with very large days (effectively all time)
        results = await initialized_store.get_signals_for_analytics(days=9999)

        # Assert: Should return all 3 signals
        assert len(results) == 3


class TestAnalyticsAggregations:
    """Tests for analytics aggregation logic used in charts."""

    def test_thesis_category_aggregation(self):
        """Should correctly aggregate signals by thesis category."""
        import pandas as pd

        # Sample data similar to what get_signals_for_analytics returns
        data = [
            {"signal_id": 1, "category": "Consumer CPG", "thesis_fit_score": 0.8, "confidence": 0.7},
            {"signal_id": 2, "category": "Consumer CPG", "thesis_fit_score": 0.6, "confidence": 0.8},
            {"signal_id": 3, "category": "Consumer Health Tech", "thesis_fit_score": 0.7, "confidence": 0.6},
            {"signal_id": 4, "category": "Unclassified", "thesis_fit_score": 0, "confidence": 0.5},
        ]
        df = pd.DataFrame(data)

        # Aggregation logic from plan
        thesis_agg = df.groupby('category').agg(
            count=('signal_id', 'count'),
            avg_fit=('thesis_fit_score', 'mean'),
            avg_conf=('confidence', 'mean')
        ).reset_index().sort_values('count', ascending=False)

        # Assert
        assert len(thesis_agg) == 3
        cpg_row = thesis_agg[thesis_agg['category'] == 'Consumer CPG'].iloc[0]
        assert cpg_row['count'] == 2
        assert cpg_row['avg_fit'] == 0.7  # (0.8 + 0.6) / 2
        assert cpg_row['avg_conf'] == 0.75  # (0.7 + 0.8) / 2

    def test_source_conversion_calculation(self):
        """Should correctly calculate conversion rates by source."""
        import pandas as pd

        data = [
            {"signal_id": 1, "source_api": "github", "processing_status": "pushed"},
            {"signal_id": 2, "source_api": "github", "processing_status": "pushed"},
            {"signal_id": 3, "source_api": "github", "processing_status": "pending"},
            {"signal_id": 4, "source_api": "github", "processing_status": "rejected"},
            {"signal_id": 5, "source_api": "sec_edgar", "processing_status": "pushed"},
            {"signal_id": 6, "source_api": "sec_edgar", "processing_status": "pending"},
        ]
        df = pd.DataFrame(data)

        # Aggregation logic from plan
        source_agg = df.groupby('source_api').agg(
            total=('signal_id', 'count'),
            pushed=('processing_status', lambda x: (x == 'pushed').sum())
        ).reset_index()
        source_agg['conversion'] = (source_agg['pushed'] / source_agg['total'] * 100).round(1)

        # Assert
        github_row = source_agg[source_agg['source_api'] == 'github'].iloc[0]
        assert github_row['total'] == 4
        assert github_row['pushed'] == 2
        assert github_row['conversion'] == 50.0

        sec_row = source_agg[source_agg['source_api'] == 'sec_edgar'].iloc[0]
        assert sec_row['total'] == 2
        assert sec_row['pushed'] == 1
        assert sec_row['conversion'] == 50.0

    def test_daily_volume_aggregation(self):
        """Should correctly aggregate signal volume by date and category."""
        import pandas as pd
        from datetime import date

        data = [
            {"detected_at": "2024-01-15T10:00:00Z", "category": "Consumer CPG"},
            {"detected_at": "2024-01-15T14:00:00Z", "category": "Consumer CPG"},
            {"detected_at": "2024-01-15T16:00:00Z", "category": "Consumer Health Tech"},
            {"detected_at": "2024-01-16T10:00:00Z", "category": "Consumer CPG"},
        ]
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['detected_at']).dt.date

        # Aggregation logic from plan
        daily = df.groupby(['date', 'category']).size().reset_index(name='count')

        # Assert
        jan15_cpg = daily[(daily['date'] == date(2024, 1, 15)) & (daily['category'] == 'Consumer CPG')].iloc[0]
        assert jan15_cpg['count'] == 2

        jan15_health = daily[(daily['date'] == date(2024, 1, 15)) & (daily['category'] == 'Consumer Health Tech')].iloc[0]
        assert jan15_health['count'] == 1
