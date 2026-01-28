"""
Tests for RelationshipStore - Private relationship graph storage.

TDD: Write failing tests first, then implement.

Key requirements:
- Separate private_graph.db (NOT signals.db)
- Store domain relationships (intro_count, reply_rate, recency)
- Deterministic strength scoring
- No email addresses stored (privacy-first)
"""

import pytest
import tempfile
import os
from datetime import datetime, timezone, timedelta
from typing import Optional


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.unlink(path)
        except PermissionError:
            pass  # Windows file locking


class TestRelationshipStoreInit:
    """Tests for RelationshipStore initialization."""

    def test_init_with_default_path(self):
        """Should initialize with default private_graph.db path."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore()
        assert store.db_path == "private_graph.db"

    def test_init_with_custom_path(self, temp_db):
        """Should initialize with custom path."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        assert store.db_path == temp_db


class TestRelationshipStoreSchema:
    """Tests for database schema creation."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, temp_db):
        """initialize() should create domain_relationships table."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        # Verify table exists by querying it
        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_relationships'"
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "domain_relationships"

    @pytest.mark.asyncio
    async def test_schema_has_required_columns(self, temp_db):
        """domain_relationships should have required columns."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        async with store.transaction() as conn:
            cursor = await conn.execute("PRAGMA table_info(domain_relationships)")
            columns = await cursor.fetchall()

        column_names = {col[1] for col in columns}

        required_columns = {
            "id",
            "me_email_hash",  # Hashed for privacy
            "target_domain",
            "intro_count",
            "reply_count",
            "total_messages",
            "last_contact_at",
            "first_contact_at",
            "created_at",
            "updated_at",
        }

        assert required_columns.issubset(column_names)


class TestDomainEdgeOperations:
    """Tests for upserting domain edges."""

    @pytest.mark.asyncio
    async def test_upsert_domain_edge_creates_new(self, temp_db):
        """upsert_domain_edge() should create new relationship."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="investor.com",
            intro_count=2,
            reply_count=1,
            total_messages=5,
            last_contact_at=datetime.now(timezone.utc),
        )

        # Verify edge exists
        strength = await store.get_domain_strength("user@example.com", "investor.com")
        assert strength is not None
        assert strength.intro_count == 2
        assert strength.reply_count == 1

    @pytest.mark.asyncio
    async def test_upsert_domain_edge_updates_existing(self, temp_db):
        """upsert_domain_edge() should update existing relationship."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        now = datetime.now(timezone.utc)

        # Create initial edge
        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="investor.com",
            intro_count=1,
            reply_count=0,
            total_messages=2,
            last_contact_at=now,
        )

        # Update with more data
        later = now + timedelta(days=7)
        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="investor.com",
            intro_count=3,  # Updated
            reply_count=2,  # Updated
            total_messages=10,  # Updated
            last_contact_at=later,  # Updated
        )

        # Verify updated values
        strength = await store.get_domain_strength("user@example.com", "investor.com")
        assert strength.intro_count == 3
        assert strength.reply_count == 2
        assert strength.total_messages == 10

    @pytest.mark.asyncio
    async def test_email_hash_privacy(self, temp_db):
        """me_email should be hashed, not stored plaintext."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        email = "sensitive@example.com"
        await store.upsert_domain_edge(
            me_email=email,
            target_domain="investor.com",
            intro_count=1,
            reply_count=0,
            total_messages=1,
            last_contact_at=datetime.now(timezone.utc),
        )

        # Check database directly - should NOT find plaintext email
        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT me_email_hash FROM domain_relationships LIMIT 1"
            )
            row = await cursor.fetchone()

        email_hash = row[0]
        assert email not in email_hash  # Should be hashed
        assert len(email_hash) >= 32  # SHA256 or similar


class TestDomainStrength:
    """Tests for get_domain_strength() method."""

    @pytest.mark.asyncio
    async def test_get_domain_strength_returns_none_if_not_found(self, temp_db):
        """Should return None for non-existent relationship."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        strength = await store.get_domain_strength("user@example.com", "nonexistent.com")
        assert strength is None

    @pytest.mark.asyncio
    async def test_get_domain_strength_returns_data(self, temp_db):
        """Should return DomainStrength with all fields."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        now = datetime.now(timezone.utc)
        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="investor.com",
            intro_count=3,
            reply_count=2,
            total_messages=10,
            last_contact_at=now,
        )

        strength = await store.get_domain_strength("user@example.com", "investor.com")

        assert strength is not None
        assert strength.target_domain == "investor.com"
        assert strength.intro_count == 3
        assert strength.reply_count == 2
        assert strength.total_messages == 10
        assert strength.reply_rate == pytest.approx(0.2)  # 2/10
        assert strength.strength_score >= 0.0
        assert strength.strength_score <= 1.0

    @pytest.mark.asyncio
    async def test_deterministic_strength_score(self, temp_db):
        """Strength score should be deterministic given same inputs."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        now = datetime.now(timezone.utc)
        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="investor.com",
            intro_count=5,
            reply_count=3,
            total_messages=10,
            last_contact_at=now,
        )

        # Get strength multiple times - should be identical
        strength1 = await store.get_domain_strength("user@example.com", "investor.com")
        strength2 = await store.get_domain_strength("user@example.com", "investor.com")

        assert strength1.strength_score == strength2.strength_score


class TestDatabaseIsolation:
    """Tests for database isolation (private_graph.db != signals.db)."""

    @pytest.mark.asyncio
    async def test_uses_separate_database_file(self, temp_db):
        """RelationshipStore should use private_graph.db, not signals.db."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        # Verify the database file is NOT signals.db
        assert "signals.db" not in temp_db
        assert os.path.exists(temp_db)

    @pytest.mark.asyncio
    async def test_no_relationship_data_in_signals_db(self, temp_db):
        """signals.db should never contain relationship data."""
        from storage.relationship_store import RelationshipStore
        from storage.signal_store import SignalStore

        # Create both stores
        relationship_store = RelationshipStore(db_path=temp_db)
        await relationship_store.initialize()

        signals_db = temp_db.replace(".db", "_signals.db")
        signal_store = SignalStore(db_path=signals_db)
        await signal_store.initialize()

        # Add relationship data
        await relationship_store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="investor.com",
            intro_count=1,
            reply_count=0,
            total_messages=1,
            last_contact_at=datetime.now(timezone.utc),
        )

        # Verify signals.db does NOT have domain_relationships table
        async with signal_store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_relationships'"
            )
            row = await cursor.fetchone()

        assert row is None  # Table should NOT exist in signals.db

        # Cleanup: close connections before deleting
        await signal_store.close()
        await relationship_store.close()
        if os.path.exists(signals_db):
            try:
                os.unlink(signals_db)
            except PermissionError:
                pass  # Windows file locking


class TestStrengthScoreFormula:
    """Tests for deterministic strength scoring formula."""

    @pytest.mark.asyncio
    async def test_zero_intros_low_score(self, temp_db):
        """0 intros should result in low strength score."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="cold.com",
            intro_count=0,  # No intros
            reply_count=0,
            total_messages=5,
            last_contact_at=datetime.now(timezone.utc),
        )

        strength = await store.get_domain_strength("user@example.com", "cold.com")
        assert strength.strength_score < 0.5  # Should be low without intros

    @pytest.mark.asyncio
    async def test_many_intros_high_score(self, temp_db):
        """Many intros should result in high strength score."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="warm.com",
            intro_count=10,  # Many intros
            reply_count=8,
            total_messages=20,
            last_contact_at=datetime.now(timezone.utc),
        )

        strength = await store.get_domain_strength("user@example.com", "warm.com")
        assert strength.strength_score > 0.7  # Should be high with many intros

    @pytest.mark.asyncio
    async def test_stale_relationship_lower_score(self, temp_db):
        """Stale relationships should have lower recency component."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        # Old contact (1 year ago)
        old_contact = datetime.now(timezone.utc) - timedelta(days=365)

        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="stale.com",
            intro_count=5,
            reply_count=3,
            total_messages=10,
            last_contact_at=old_contact,
        )

        strength = await store.get_domain_strength("user@example.com", "stale.com")

        # Should be lower than recent contact with same intro_count
        assert strength.recency_score < 0.1  # Very stale


# =============================================================================
# PHASE 4: LP RELATIONSHIP SUPPORT
# =============================================================================

class TestLPRelationshipSchema:
    """Tests for LP relationship schema additions (Phase 4)."""

    @pytest.mark.asyncio
    async def test_schema_version_2_adds_lp_fields(self, temp_db):
        """Schema v2 should add source, lp_status, lp_name columns."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        async with store.transaction() as conn:
            cursor = await conn.execute("PRAGMA table_info(domain_relationships)")
            columns = await cursor.fetchall()

        column_names = {col[1] for col in columns}

        # Phase 4 additions
        lp_columns = {"source", "lp_status", "lp_name"}
        assert lp_columns.issubset(column_names), f"Missing LP columns: {lp_columns - column_names}"


class TestLPRelationshipUpsert:
    """Tests for upsert_lp_relationship() method."""

    @pytest.mark.asyncio
    async def test_upsert_lp_relationship_creates_new(self, temp_db):
        """upsert_lp_relationship() should create new LP relationship."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="lp-firm.com",
            lp_status="Docs Signed",
            lp_name="John Smith",
            notion_score=0.95,
        )

        # Verify LP relationship exists
        lp_rel = await store.get_lp_relationship("user@example.com", "lp-firm.com")
        assert lp_rel is not None
        assert lp_rel.lp_status == "Docs Signed"
        assert lp_rel.lp_name == "John Smith"
        assert lp_rel.source == "notion_lp"

    @pytest.mark.asyncio
    async def test_upsert_lp_relationship_updates_existing(self, temp_db):
        """upsert_lp_relationship() should update existing LP."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        # Initial LP
        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="lp-firm.com",
            lp_status="In Database",
            lp_name="Jane Doe",
            notion_score=0.25,
        )

        # Update LP status
        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="lp-firm.com",
            lp_status="Docs Signed",  # Status upgrade
            lp_name="Jane Doe",
            notion_score=0.95,  # Score upgrade
        )

        lp_rel = await store.get_lp_relationship("user@example.com", "lp-firm.com")
        assert lp_rel.lp_status == "Docs Signed"
        assert lp_rel.notion_score == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_lp_relationship_preserves_gmail_data(self, temp_db):
        """LP relationship should preserve existing Gmail data."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        # First: Gmail relationship
        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="combined.com",
            intro_count=5,
            reply_count=3,
            total_messages=10,
            last_contact_at=datetime.now(timezone.utc),
        )

        # Then: LP relationship on same domain
        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="combined.com",
            lp_status="Verbal Confirm",
            lp_name="Combined Contact",
            notion_score=0.70,
        )

        # Gmail data should still be there
        strength = await store.get_domain_strength("user@example.com", "combined.com")
        assert strength.intro_count == 5
        assert strength.reply_count == 3

        # LP data should also be there
        lp_rel = await store.get_lp_relationship("user@example.com", "combined.com")
        assert lp_rel.lp_status == "Verbal Confirm"


class TestLPRelationshipQuery:
    """Tests for get_lp_relationship() method."""

    @pytest.mark.asyncio
    async def test_get_lp_relationship_returns_none_if_not_found(self, temp_db):
        """Should return None for non-existent LP relationship."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        lp_rel = await store.get_lp_relationship("user@example.com", "nonexistent.com")
        assert lp_rel is None

    @pytest.mark.asyncio
    async def test_get_lp_relationship_returns_data(self, temp_db):
        """Should return LPRelationship with all fields."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="lp-fund.com",
            lp_status="Engagement Sent",
            lp_name="Fund Manager",
            notion_score=0.40,
        )

        lp_rel = await store.get_lp_relationship("user@example.com", "lp-fund.com")

        assert lp_rel is not None
        assert lp_rel.target_domain == "lp-fund.com"
        assert lp_rel.lp_status == "Engagement Sent"
        assert lp_rel.lp_name == "Fund Manager"
        assert lp_rel.source == "notion_lp"
        assert lp_rel.notion_score == pytest.approx(0.40)


class TestCombinedRelationshipQuery:
    """Tests for get_combined_relationship() with Gmail + LP data."""

    @pytest.mark.asyncio
    async def test_get_combined_relationship_gmail_only(self, temp_db):
        """Should return Gmail data when no LP exists."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="gmail-only.com",
            intro_count=3,
            reply_count=2,
            total_messages=8,
            last_contact_at=datetime.now(timezone.utc),
        )

        combined = await store.get_combined_relationship("user@example.com", "gmail-only.com")

        assert combined is not None
        assert combined.gmail_score is not None
        assert combined.gmail_score > 0
        assert combined.notion_score is None
        assert combined.lp_status is None

    @pytest.mark.asyncio
    async def test_get_combined_relationship_lp_only(self, temp_db):
        """Should return LP data when no Gmail exists."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="lp-only.com",
            lp_status="Docs Signed",
            lp_name="LP Contact",
            notion_score=0.95,
        )

        combined = await store.get_combined_relationship("user@example.com", "lp-only.com")

        assert combined is not None
        assert combined.gmail_score is None
        assert combined.notion_score == pytest.approx(0.95)
        assert combined.lp_status == "Docs Signed"

    @pytest.mark.asyncio
    async def test_get_combined_relationship_both_sources(self, temp_db):
        """Should return merged data from both Gmail and LP."""
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        # Gmail data
        await store.upsert_domain_edge(
            me_email="user@example.com",
            target_domain="both.com",
            intro_count=5,
            reply_count=4,
            total_messages=10,
            last_contact_at=datetime.now(timezone.utc),
        )

        # LP data
        await store.upsert_lp_relationship(
            me_email="user@example.com",
            target_domain="both.com",
            lp_status="Verbal Confirm",
            lp_name="Combined Person",
            notion_score=0.70,
        )

        combined = await store.get_combined_relationship("user@example.com", "both.com")

        assert combined is not None
        assert combined.gmail_score is not None
        assert combined.gmail_score > 0
        assert combined.notion_score == pytest.approx(0.70)
        assert combined.lp_status == "Verbal Confirm"
        assert combined.lp_name == "Combined Person"
