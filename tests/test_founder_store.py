"""
Tests for FounderStore - founder profile storage and scoring.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile

from storage.founder_store import (
    FounderStore,
    FounderProfile,
    FounderExperience,
    ExperienceType,
    FounderRelationship,
    FAANG_COMPANIES,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    # Cleanup happens automatically


@pytest.fixture
async def founder_store(temp_db):
    """Create an initialized FounderStore."""
    store = FounderStore(db_path=temp_db)
    await store.initialize()
    yield store
    await store.close()


class TestFounderExperience:
    """Tests for FounderExperience data class."""

    def test_faang_detection(self):
        """Test that FAANG companies are detected."""
        exp = FounderExperience(
            experience_type=ExperienceType.WORK,
            organization="Google",
            title="Software Engineer",
        )
        assert exp.is_faang is True

    def test_founder_role_detection(self):
        """Test that founder roles are detected."""
        exp = FounderExperience(
            experience_type=ExperienceType.WORK,
            organization="Acme Inc",
            title="Co-Founder & CEO",
        )
        assert exp.is_founder_role is True
        assert exp.is_leadership_role is True

    def test_technical_role_detection(self):
        """Test that technical roles are detected."""
        exp = FounderExperience(
            experience_type=ExperienceType.WORK,
            organization="Startup Co",
            title="CTO",
        )
        assert exp.is_technical_role is True
        assert exp.is_leadership_role is True

    def test_consumer_domain_detection(self):
        """Test that consumer domains are detected."""
        exp = FounderExperience(
            experience_type=ExperienceType.WORK,
            organization="Food Delivery Co",
            title="Product Manager",
            description="Worked on food ordering platform",
        )
        assert exp.is_consumer_domain is True

    def test_duration_calculation(self):
        """Test experience duration calculation."""
        exp = FounderExperience(
            experience_type=ExperienceType.WORK,
            organization="Acme",
            start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2022, 1, 1, tzinfo=timezone.utc),
        )
        assert abs(exp.duration_years - 2.0) < 0.1


class TestFounderProfile:
    """Tests for FounderProfile data class."""

    def test_calculate_score_serial_founder(self):
        """Test that serial founders get high scores."""
        profile = FounderProfile(
            name="John Doe",
            founder_key="linkedin:johndoe",
            canonical_key="domain:acme.ai",
            source_api="linkedin",
            is_serial_founder=True,
            previous_exits=2,
        )
        score = profile.calculate_score()
        assert score >= 0.5  # Serial founder bonus

    def test_calculate_score_faang_experience(self):
        """Test that FAANG experience boosts score."""
        profile = FounderProfile(
            name="Jane Doe",
            founder_key="linkedin:janedoe",
            canonical_key="domain:startup.io",
            source_api="linkedin",
            has_faang_experience=True,
        )
        score = profile.calculate_score()
        assert score >= 0.15

    def test_calculate_score_from_experiences(self):
        """Test score calculation from experience analysis."""
        profile = FounderProfile(
            name="Tech Founder",
            founder_key="linkedin:techfounder",
            canonical_key="domain:tech.co",
            source_api="linkedin",
            experiences=[
                FounderExperience(
                    experience_type=ExperienceType.WORK,
                    organization="Google",
                    title="Senior Engineer",
                    start_date=datetime(2015, 1, 1, tzinfo=timezone.utc),
                    end_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                ),
                FounderExperience(
                    experience_type=ExperienceType.WORK,
                    organization="MyStartup",
                    title="Co-Founder & CTO",
                    start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                ),
            ],
        )
        score = profile.calculate_score()
        # Should have FAANG + technical + founder experience
        assert score >= 0.25
        assert profile.has_faang_experience
        assert profile.is_technical

    def test_max_score_capped(self):
        """Test that score is capped at 1.0."""
        profile = FounderProfile(
            name="Super Founder",
            founder_key="linkedin:superfounder",
            canonical_key="domain:unicorn.io",
            source_api="linkedin",
            is_serial_founder=True,
            previous_exits=10,  # Extreme case
            has_faang_experience=True,
            is_technical=True,
            has_domain_expertise=True,
            years_experience=30,
            current_title="CEO",  # Leadership role bonus
        )
        score = profile.calculate_score()
        # Score should be capped at 1.0 even with extreme values
        assert score <= 1.0
        assert score >= 0.9  # Should be very high


class TestFounderStore:
    """Tests for FounderStore database operations."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, temp_db):
        """Test that initialization creates required tables."""
        store = FounderStore(db_path=temp_db)
        await store.initialize()

        # Check tables exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in await cursor.fetchall()]

        assert "founders" in tables
        assert "founder_experiences" in tables
        assert "founder_signals" in tables
        assert "founder_schema_migrations" in tables

        await store.close()

    @pytest.mark.asyncio
    async def test_save_and_get_founder(self, founder_store):
        """Test saving and retrieving a founder."""
        profile = FounderProfile(
            name="Test Founder",
            founder_key="linkedin:testfounder",
            canonical_key="domain:test.com",
            source_api="linkedin",
            email="test@example.com",
            is_serial_founder=True,
        )

        founder_id = await founder_store.save_founder(profile)
        assert founder_id > 0

        # Retrieve
        retrieved = await founder_store.get_founder("linkedin:testfounder")
        assert retrieved is not None
        assert retrieved.name == "Test Founder"
        assert retrieved.is_serial_founder is True
        assert retrieved.founder_score > 0

    @pytest.mark.asyncio
    async def test_save_founder_with_experiences(self, founder_store):
        """Test saving a founder with experiences."""
        profile = FounderProfile(
            name="Experienced Founder",
            founder_key="linkedin:expfounder",
            canonical_key="domain:exp.io",
            source_api="linkedin",
            experiences=[
                FounderExperience(
                    experience_type=ExperienceType.WORK,
                    organization="Meta",
                    title="Product Manager",
                ),
                FounderExperience(
                    experience_type=ExperienceType.EDUCATION,
                    organization="Stanford",
                    title="MBA",
                ),
            ],
        )

        founder_id = await founder_store.save_founder(profile)

        # Retrieve with experiences
        retrieved = await founder_store.get_founder("linkedin:expfounder")
        assert len(retrieved.experiences) == 2
        assert retrieved.has_faang_experience is True

    @pytest.mark.asyncio
    async def test_update_existing_founder(self, founder_store):
        """Test updating an existing founder."""
        profile = FounderProfile(
            name="Original Name",
            founder_key="linkedin:updatetest",
            canonical_key="domain:update.io",
            source_api="linkedin",
        )

        await founder_store.save_founder(profile)

        # Update
        profile.name = "Updated Name"
        profile.is_serial_founder = True
        await founder_store.save_founder(profile)

        # Verify update
        retrieved = await founder_store.get_founder("linkedin:updatetest")
        assert retrieved.name == "Updated Name"
        assert retrieved.is_serial_founder is True

    @pytest.mark.asyncio
    async def test_get_founders_for_company(self, founder_store):
        """Test getting all founders for a company."""
        # Save multiple founders for same company with varying attributes
        # Use different scoring attributes so scores differ
        profiles_data = [
            {"name": "Founder 0", "is_technical": False, "has_faang_experience": False},
            {"name": "Founder 1", "is_technical": True, "has_faang_experience": False},
            {"name": "Founder 2", "is_technical": True, "has_faang_experience": True, "is_serial_founder": True},
        ]

        for i, data in enumerate(profiles_data):
            profile = FounderProfile(
                name=data["name"],
                founder_key=f"linkedin:founder{i}",
                canonical_key="domain:team.io",
                source_api="linkedin",
                is_technical=data.get("is_technical", False),
                has_faang_experience=data.get("has_faang_experience", False),
                is_serial_founder=data.get("is_serial_founder", False),
            )
            await founder_store.save_founder(profile)

        founders = await founder_store.get_founders_for_company("domain:team.io")
        assert len(founders) == 3
        # Should be sorted by score descending - Founder 2 has highest score
        assert founders[0].name == "Founder 2"
        assert founders[0].founder_score > founders[1].founder_score

    @pytest.mark.asyncio
    async def test_aggregate_founder_score(self, founder_store):
        """Test aggregate score calculation for a company."""
        # Save a high-scoring founder
        profile1 = FounderProfile(
            name="Star Founder",
            founder_key="linkedin:star",
            canonical_key="domain:allstar.io",
            source_api="linkedin",
            is_serial_founder=True,
            has_faang_experience=True,
        )
        await founder_store.save_founder(profile1)

        # Save a supporting founder
        profile2 = FounderProfile(
            name="Support Founder",
            founder_key="linkedin:support",
            canonical_key="domain:allstar.io",
            source_api="linkedin",
            is_technical=True,
        )
        await founder_store.save_founder(profile2)

        score = await founder_store.get_aggregate_founder_score("domain:allstar.io")
        # Should get best founder score + team bonus
        assert score > profile1.calculate_score()

    @pytest.mark.asyncio
    async def test_aggregate_score_no_founders(self, founder_store):
        """Test aggregate score returns 0 for unknown company."""
        score = await founder_store.get_aggregate_founder_score("domain:unknown.io")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_get_stats(self, founder_store):
        """Test getting store statistics."""
        # Add some founders
        for i in range(5):
            profile = FounderProfile(
                name=f"Stat Founder {i}",
                founder_key=f"linkedin:stat{i}",
                canonical_key=f"domain:stat{i}.io",
                source_api="linkedin" if i % 2 == 0 else "github",
                is_serial_founder=i > 2,
            )
            await founder_store.save_founder(profile)

        stats = await founder_store.get_stats()
        assert stats["total_founders"] == 5
        assert stats["serial_founders"] == 2
        assert "linkedin" in stats["by_source"]
        assert "github" in stats["by_source"]


class TestFounderSignalLinks:
    """Tests for founder-signal linking."""

    @pytest.mark.asyncio
    async def test_link_founder_to_signal(self, founder_store):
        """Test linking a founder to a signal."""
        profile = FounderProfile(
            name="Linked Founder",
            founder_key="linkedin:linked",
            canonical_key="domain:linked.io",
            source_api="linkedin",
        )
        founder_id = await founder_store.save_founder(profile)

        # Link to a signal (using fake signal_id)
        await founder_store.link_founder_to_signal(
            founder_id=founder_id,
            signal_id=123,
            relationship=FounderRelationship.FOUNDER,
        )

        # Get founders for signal
        founders = await founder_store.get_founders_for_signal(123)
        assert len(founders) == 1
        assert founders[0].name == "Linked Founder"

    @pytest.mark.asyncio
    async def test_multiple_links(self, founder_store):
        """Test multiple founders linked to same signal."""
        signal_id = 456

        for i in range(2):
            profile = FounderProfile(
                name=f"Team Member {i}",
                founder_key=f"linkedin:team{i}",
                canonical_key="domain:team.io",
                source_api="linkedin",
            )
            founder_id = await founder_store.save_founder(profile)

            relationship = FounderRelationship.FOUNDER if i == 0 else FounderRelationship.COFOUNDER
            await founder_store.link_founder_to_signal(founder_id, signal_id, relationship)

        founders = await founder_store.get_founders_for_signal(signal_id)
        assert len(founders) == 2
