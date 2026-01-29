"""
Tests for Community Sentiment Storage in SignalStore

Tests migration 18 tables and helper methods.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile

from storage.signal_store import SignalStore, signal_store


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
async def store():
    """Create a test signal store with in-memory database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_signals.db"
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()


# =============================================================================
# MIGRATION TESTS
# =============================================================================

class TestCommunitySentimentMigration:
    """Tests for migration 18 - community sentiment tables."""

    @pytest.mark.asyncio
    async def test_community_sentiment_table_exists(self, store):
        """community_sentiment table is created by migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='community_sentiment'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "community_sentiment"

    @pytest.mark.asyncio
    async def test_community_mentions_table_exists(self, store):
        """community_mentions table is created by migration."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='community_mentions'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "community_mentions"

    @pytest.mark.asyncio
    async def test_community_sentiment_indexes_exist(self, store):
        """Required indexes are created."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_community_%'"
        )
        rows = await cursor.fetchall()
        index_names = [r[0] for r in rows]

        assert "idx_community_sent_key" in index_names
        assert "idx_community_sent_source" in index_names
        assert "idx_community_sent_boost" in index_names


# =============================================================================
# COMMUNITY MENTION TESTS
# =============================================================================

class TestSaveCommunitymention:
    """Tests for save_community_mention."""

    @pytest.mark.asyncio
    async def test_save_basic_mention(self, store):
        """Can save a basic community mention."""
        mention_id = await store.save_community_mention(
            source="reddit",
            source_id="abc123",
            title="Check out this startup!",
            url="https://reddit.com/r/startups/abc123",
        )

        assert mention_id > 0

    @pytest.mark.asyncio
    async def test_save_mention_with_sentiment(self, store):
        """Can save mention with sentiment data."""
        mention_id = await store.save_community_mention(
            source="telegram",
            source_id="msg_456",
            canonical_key="domain:acme.ai",
            title="Acme is amazing!",
            sentiment_score=0.8,
            sentiment_label="positive",
            sentiment_method="heuristic",
            keywords_found=["amazing", "love"],
        )

        assert mention_id > 0

        # Verify stored data
        mentions = await store.get_community_mentions("domain:acme.ai")
        assert len(mentions) == 1
        assert mentions[0]["sentiment_score"] == 0.8
        assert mentions[0]["sentiment_label"] == "positive"
        assert "amazing" in mentions[0]["keywords_found"]

    @pytest.mark.asyncio
    async def test_save_reddit_mention(self, store):
        """Can save Reddit-specific mention data."""
        mention_id = await store.save_community_mention(
            source="reddit",
            source_id="t3_xyz",
            canonical_key="domain:startup.io",
            title="Just launched my startup!",
            subreddit="startups",
            author="founder123",
            engagement_score=150,
            posted_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        mentions = await store.get_community_mentions("domain:startup.io", source="reddit")
        assert len(mentions) == 1
        assert mentions[0]["subreddit"] == "startups"
        assert mentions[0]["engagement_score"] == 150

    @pytest.mark.asyncio
    async def test_save_discord_mention(self, store):
        """Can save Discord-specific mention data."""
        mention_id = await store.save_community_mention(
            source="discord",
            source_id="discord_msg_789",
            canonical_key="domain:app.co",
            title="Anyone tried App.co?",
            channel_name="product-feedback",
            sentiment_score=0.5,
            sentiment_label="positive",
        )

        mentions = await store.get_community_mentions("domain:app.co", source="discord")
        assert len(mentions) == 1
        assert mentions[0]["channel_name"] == "product-feedback"

    @pytest.mark.asyncio
    async def test_mention_deduplication(self, store):
        """Duplicate mentions are replaced (upsert)."""
        # Save first version
        await store.save_community_mention(
            source="reddit",
            source_id="same_id",
            sentiment_score=0.3,
        )

        # Save updated version
        await store.save_community_mention(
            source="reddit",
            source_id="same_id",
            sentiment_score=0.8,
            canonical_key="domain:updated.com",
        )

        # Should only have one row
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM community_mentions WHERE source_id = 'same_id'"
        )
        row = await cursor.fetchone()
        assert row[0] == 1

        # Should have updated sentiment
        cursor = await store._db.execute(
            "SELECT sentiment_score FROM community_mentions WHERE source_id = 'same_id'"
        )
        row = await cursor.fetchone()
        assert row[0] == 0.8


# =============================================================================
# COMMUNITY SENTIMENT AGGREGATE TESTS
# =============================================================================

class TestSaveCommunitySentiment:
    """Tests for save_community_sentiment."""

    @pytest.mark.asyncio
    async def test_save_basic_sentiment(self, store):
        """Can save basic community sentiment aggregate."""
        sentiment_id = await store.save_community_sentiment(
            canonical_key="domain:acme.ai",
            source="reddit",
            mention_count=10,
            unique_authors=7,
            avg_sentiment_score=0.6,
            sentiment_label="positive",
            positive_ratio=0.7,
            negative_ratio=0.1,
            neutral_ratio=0.2,
            confidence_boost=0.05,
        )

        assert sentiment_id > 0

    @pytest.mark.asyncio
    async def test_save_sentiment_with_keywords(self, store):
        """Can save sentiment with top keywords."""
        await store.save_community_sentiment(
            canonical_key="domain:acme.ai",
            source="telegram",
            mention_count=5,
            unique_authors=3,
            avg_sentiment_score=0.4,
            sentiment_label="positive",
            positive_ratio=0.6,
            negative_ratio=0.2,
            neutral_ratio=0.2,
            confidence_boost=0.03,
            top_keywords=["innovative", "growing", "funded"],
        )

        sentiment = await store.get_community_sentiment("domain:acme.ai", source="telegram")
        assert sentiment is not None
        assert "innovative" in sentiment["top_keywords"]

    @pytest.mark.asyncio
    async def test_sentiment_upsert(self, store):
        """Sentiment is updated on conflict (same key + source)."""
        # Save initial
        await store.save_community_sentiment(
            canonical_key="domain:test.io",
            source="reddit",
            mention_count=5,
            unique_authors=3,
            avg_sentiment_score=0.3,
            sentiment_label="neutral",
            positive_ratio=0.4,
            negative_ratio=0.2,
            neutral_ratio=0.4,
            confidence_boost=0.0,
        )

        # Update with more mentions
        await store.save_community_sentiment(
            canonical_key="domain:test.io",
            source="reddit",
            mention_count=20,
            unique_authors=15,
            avg_sentiment_score=0.7,
            sentiment_label="positive",
            positive_ratio=0.8,
            negative_ratio=0.1,
            neutral_ratio=0.1,
            confidence_boost=0.08,
        )

        sentiment = await store.get_community_sentiment("domain:test.io", source="reddit")
        assert sentiment["mention_count"] == 20
        assert sentiment["avg_sentiment_score"] == 0.7
        assert sentiment["confidence_boost"] == 0.08


# =============================================================================
# GET COMMUNITY SENTIMENT TESTS
# =============================================================================

class TestGetCommunitySentiment:
    """Tests for get_community_sentiment."""

    @pytest.mark.asyncio
    async def test_get_specific_source(self, store):
        """Can get sentiment for specific source."""
        await store.save_community_sentiment(
            canonical_key="domain:multi.io",
            source="reddit",
            mention_count=10,
            unique_authors=5,
            avg_sentiment_score=0.5,
            sentiment_label="positive",
            positive_ratio=0.6,
            negative_ratio=0.2,
            neutral_ratio=0.2,
            confidence_boost=0.03,
        )

        await store.save_community_sentiment(
            canonical_key="domain:multi.io",
            source="telegram",
            mention_count=5,
            unique_authors=3,
            avg_sentiment_score=0.8,
            sentiment_label="positive",
            positive_ratio=0.9,
            negative_ratio=0.0,
            neutral_ratio=0.1,
            confidence_boost=0.07,
        )

        reddit_sentiment = await store.get_community_sentiment("domain:multi.io", source="reddit")
        assert reddit_sentiment["mention_count"] == 10

        telegram_sentiment = await store.get_community_sentiment("domain:multi.io", source="telegram")
        assert telegram_sentiment["mention_count"] == 5

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, store):
        """Returns None for nonexistent sentiment."""
        sentiment = await store.get_community_sentiment("domain:nonexistent.com")
        assert sentiment is None

    @pytest.mark.asyncio
    async def test_get_all_community_sentiment(self, store):
        """Can get all sentiment sources for a company."""
        await store.save_community_sentiment(
            canonical_key="domain:popular.io",
            source="reddit",
            mention_count=20,
            unique_authors=15,
            avg_sentiment_score=0.6,
            sentiment_label="positive",
            positive_ratio=0.7,
            negative_ratio=0.1,
            neutral_ratio=0.2,
            confidence_boost=0.05,
        )

        await store.save_community_sentiment(
            canonical_key="domain:popular.io",
            source="telegram",
            mention_count=8,
            unique_authors=5,
            avg_sentiment_score=0.7,
            sentiment_label="positive",
            positive_ratio=0.75,
            negative_ratio=0.05,
            neutral_ratio=0.2,
            confidence_boost=0.06,
        )

        await store.save_community_sentiment(
            canonical_key="domain:popular.io",
            source="discord",
            mention_count=12,
            unique_authors=8,
            avg_sentiment_score=0.5,
            sentiment_label="positive",
            positive_ratio=0.6,
            negative_ratio=0.15,
            neutral_ratio=0.25,
            confidence_boost=0.04,
        )

        all_sentiment = await store.get_all_community_sentiment("domain:popular.io")
        assert len(all_sentiment) == 3

        sources = [s["source"] for s in all_sentiment]
        assert "reddit" in sources
        assert "telegram" in sources
        assert "discord" in sources


# =============================================================================
# AGGREGATE BOOST TESTS
# =============================================================================

class TestGetAggregateCommunityBoost:
    """Tests for get_aggregate_community_boost."""

    @pytest.mark.asyncio
    async def test_aggregate_positive_boost(self, store):
        """Aggregates positive boosts correctly."""
        await store.save_community_sentiment(
            canonical_key="domain:viral.io",
            source="reddit",
            mention_count=50,
            unique_authors=40,
            avg_sentiment_score=0.8,
            sentiment_label="positive",
            positive_ratio=0.9,
            negative_ratio=0.05,
            neutral_ratio=0.05,
            confidence_boost=0.05,
        )

        await store.save_community_sentiment(
            canonical_key="domain:viral.io",
            source="telegram",
            mention_count=30,
            unique_authors=20,
            avg_sentiment_score=0.7,
            sentiment_label="positive",
            positive_ratio=0.85,
            negative_ratio=0.05,
            neutral_ratio=0.1,
            confidence_boost=0.04,
        )

        total_boost = await store.get_aggregate_community_boost("domain:viral.io")
        # Sum is 0.09, which is under the cap of 0.10
        assert total_boost == 0.09

    @pytest.mark.asyncio
    async def test_aggregate_boost_caps_at_max(self, store):
        """Total boost is capped at +0.10."""
        # Add multiple high boosts
        for i, source in enumerate(["reddit", "telegram", "discord"]):
            await store.save_community_sentiment(
                canonical_key="domain:hype.io",
                source=source,
                mention_count=100,
                unique_authors=80,
                avg_sentiment_score=0.9,
                sentiment_label="positive",
                positive_ratio=0.95,
                negative_ratio=0.02,
                neutral_ratio=0.03,
                confidence_boost=0.08,  # Each source gives 0.08
            )

        total_boost = await store.get_aggregate_community_boost("domain:hype.io")
        # Sum would be 0.24, but capped at 0.10
        assert total_boost == 0.10

    @pytest.mark.asyncio
    async def test_aggregate_negative_boost(self, store):
        """Negative boosts aggregate correctly."""
        await store.save_community_sentiment(
            canonical_key="domain:scandal.io",
            source="reddit",
            mention_count=100,
            unique_authors=50,
            avg_sentiment_score=-0.8,
            sentiment_label="negative",
            positive_ratio=0.05,
            negative_ratio=0.9,
            neutral_ratio=0.05,
            confidence_boost=-0.12,
        )

        total_boost = await store.get_aggregate_community_boost("domain:scandal.io")
        assert total_boost == -0.12

    @pytest.mark.asyncio
    async def test_aggregate_negative_boost_caps_at_min(self, store):
        """Total boost is capped at -0.15."""
        for source in ["reddit", "telegram", "discord"]:
            await store.save_community_sentiment(
                canonical_key="domain:fraud.io",
                source=source,
                mention_count=200,
                unique_authors=150,
                avg_sentiment_score=-0.9,
                sentiment_label="negative",
                positive_ratio=0.02,
                negative_ratio=0.95,
                neutral_ratio=0.03,
                confidence_boost=-0.10,  # Each source gives -0.10
            )

        total_boost = await store.get_aggregate_community_boost("domain:fraud.io")
        # Sum would be -0.30, but capped at -0.15
        assert total_boost == -0.15

    @pytest.mark.asyncio
    async def test_aggregate_mixed_boost(self, store):
        """Mixed positive/negative boosts balance out."""
        await store.save_community_sentiment(
            canonical_key="domain:mixed.io",
            source="reddit",
            mention_count=50,
            unique_authors=40,
            avg_sentiment_score=0.6,
            sentiment_label="positive",
            positive_ratio=0.7,
            negative_ratio=0.1,
            neutral_ratio=0.2,
            confidence_boost=0.05,
        )

        await store.save_community_sentiment(
            canonical_key="domain:mixed.io",
            source="telegram",
            mention_count=30,
            unique_authors=20,
            avg_sentiment_score=-0.4,
            sentiment_label="negative",
            positive_ratio=0.2,
            negative_ratio=0.6,
            neutral_ratio=0.2,
            confidence_boost=-0.04,
        )

        total_boost = await store.get_aggregate_community_boost("domain:mixed.io")
        # 0.05 + (-0.04) = 0.01
        assert abs(total_boost - 0.01) < 0.001

    @pytest.mark.asyncio
    async def test_aggregate_boost_nonexistent(self, store):
        """Returns 0.0 for nonexistent company."""
        total_boost = await store.get_aggregate_community_boost("domain:unknown.com")
        assert total_boost == 0.0


# =============================================================================
# GET COMMUNITY MENTIONS TESTS
# =============================================================================

class TestGetCommunityMentions:
    """Tests for get_community_mentions."""

    @pytest.mark.asyncio
    async def test_get_mentions_by_canonical_key(self, store):
        """Can retrieve mentions by canonical key."""
        # Add multiple mentions
        for i in range(5):
            await store.save_community_mention(
                source="reddit",
                source_id=f"post_{i}",
                canonical_key="domain:mentioned.io",
                title=f"Post {i}",
                sentiment_score=0.5 + (i * 0.1),
            )

        mentions = await store.get_community_mentions("domain:mentioned.io")
        assert len(mentions) == 5

    @pytest.mark.asyncio
    async def test_get_mentions_by_source(self, store):
        """Can filter mentions by source."""
        await store.save_community_mention(
            source="reddit",
            source_id="reddit_1",
            canonical_key="domain:multi.io",
        )
        await store.save_community_mention(
            source="telegram",
            source_id="tg_1",
            canonical_key="domain:multi.io",
        )

        reddit_only = await store.get_community_mentions("domain:multi.io", source="reddit")
        assert len(reddit_only) == 1
        assert reddit_only[0]["source"] == "reddit"

    @pytest.mark.asyncio
    async def test_get_mentions_limit(self, store):
        """Limit parameter works correctly."""
        for i in range(20):
            await store.save_community_mention(
                source="reddit",
                source_id=f"post_{i}",
                canonical_key="domain:popular.io",
            )

        mentions = await store.get_community_mentions("domain:popular.io", limit=5)
        assert len(mentions) == 5

    @pytest.mark.asyncio
    async def test_get_mentions_ordered_by_detected(self, store):
        """Mentions are ordered by detected_at descending."""
        # Add mentions with different detection times
        await store.save_community_mention(
            source="reddit",
            source_id="old_post",
            canonical_key="domain:ordered.io",
            title="Old post",
        )

        await store.save_community_mention(
            source="reddit",
            source_id="new_post",
            canonical_key="domain:ordered.io",
            title="New post",
        )

        mentions = await store.get_community_mentions("domain:ordered.io")
        # Most recent should be first
        assert mentions[0]["title"] == "New post"
