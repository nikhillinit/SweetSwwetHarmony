"""Tests for thesis classification storage."""
import pytest
from datetime import datetime
from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


class TestThesisClassificationSchema:
    """Test thesis_classifications table exists after migration."""

    @pytest.fixture
    async def store(self, tmp_path):
        """Create a fresh store for each test."""
        db_path = str(tmp_path / "test_thesis.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_schema_version_at_least_5(self, store):
        """Schema version should be at least 5 (when thesis_classifications was added)."""
        assert CURRENT_SCHEMA_VERSION >= 5

    @pytest.mark.asyncio
    async def test_thesis_classifications_table_exists(self, store):
        """thesis_classifications table should exist."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_classifications'"
        )
        result = await cursor.fetchone()
        assert result is not None
        assert result[0] == "thesis_classifications"

    @pytest.mark.asyncio
    async def test_thesis_classifications_columns(self, store):
        """thesis_classifications should have all required columns."""
        cursor = await store._db.execute("PRAGMA table_info(thesis_classifications)")
        columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}

        required = {
            "id", "signal_id", "canonical_key",
            "thesis_match", "thesis_fit_score", "category",
            "primary_end_user", "paying_customer", "sells_to_or_operates_in",
            "keyword_score", "keyword_category", "negative_keywords",
            "stage_estimate", "confidence", "rationale", "key_signals",
            "prompt_version", "model", "input_tokens", "output_tokens",
            "classification_status",
            "latency_ms", "classified_at", "competitor_flag", "competitor_match"
        }
        assert required.issubset(column_names)


class TestThesisClassificationStorage:
    """Test save/get methods for thesis classifications."""

    @pytest.fixture
    async def store(self, tmp_path):
        db_path = str(tmp_path / "test_thesis.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def signal_id(self, store):
        """Create a test signal and return its ID."""
        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:test.com",
            company_name="Test Co",
            confidence=0.5,
            raw_data={"description": "Test company"},
        )
        return signal_id

    @pytest.mark.asyncio
    async def test_save_thesis_classification(self, store, signal_id):
        """Should save a thesis classification."""
        result = await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            negative_keywords=[],
            thesis_match=True,
            thesis_fit_score=0.75,
            category="consumer_cpg",
            stage_estimate="seed",
            confidence="high",
            rationale="Meal kit delivery startup",
            key_signals=["meal kit", "d2c"],
            prompt_version="v1.2.0",
            model="gemini-2.0-flash",
        )
        assert result > 0  # Returns the inserted row ID

    @pytest.mark.asyncio
    async def test_get_thesis_classification(self, store, signal_id):
        """Should retrieve a saved classification."""
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            keyword_score=0.6,
            keyword_category="consumer_cpg",
            negative_keywords=["enterprise"],
            thesis_match=True,
            thesis_fit_score=0.75,
            category="consumer_cpg",
        )

        result = await store.get_thesis_classification("domain:test.com")
        assert result is not None
        assert result["thesis_fit_score"] == 0.75
        assert result["category"] == "consumer_cpg"
        assert result["classification_status"] == "success"

    @pytest.mark.asyncio
    async def test_get_thesis_classification_not_found(self, store):
        """Should return None for unknown canonical key."""
        result = await store.get_thesis_classification("domain:unknown.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_recent_classification_within_cache(self, store, signal_id):
        """Should return recent classification (within 7 days)."""
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            thesis_fit_score=0.8,
            category="consumer_health_tech",
        )

        result = await store.get_recent_classification("domain:test.com", days=7)
        assert result is not None
        assert result["thesis_fit_score"] == 0.8

    @pytest.mark.asyncio
    async def test_get_thesis_classification_returns_most_recent(self, store, signal_id):
        """Should return the most recent classification when multiple exist."""
        # Save first classification
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            thesis_fit_score=0.5,
            category="consumer_cpg",
        )

        # Save second classification (should be returned)
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            thesis_fit_score=0.9,
            category="consumer_health_tech",
        )

        result = await store.get_thesis_classification("domain:test.com")
        assert result is not None
        assert result["thesis_fit_score"] == 0.9
        assert result["category"] == "consumer_health_tech"

    @pytest.mark.asyncio
    async def test_save_thesis_classification_with_all_fields(self, store, signal_id):
        """Should save and retrieve all fields correctly."""
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            keyword_score=0.7,
            keyword_category="consumer_health_tech",
            negative_keywords=["b2b", "enterprise"],
            thesis_match=True,
            thesis_fit_score=0.85,
            category="consumer_health_tech",
            primary_end_user="individual_consumer",
            paying_customer="individual_consumer",
            sells_to_or_operates_in="operates_in_industry_for_consumers",
            stage_estimate="pre-seed",
            confidence="high",
            rationale="Fitness app for consumers",
            key_signals=["fitness", "wellness", "d2c"],
            prompt_version="v1.3.0",
            model="gemini-2.0-flash",
            input_tokens=150,
            output_tokens=75,
            latency_ms=450,
            classification_status="error_parse",
            competitor_flag=True,
            competitor_match={"name": "Peloton", "similarity": 0.65},
        )

        result = await store.get_thesis_classification("domain:test.com")
        assert result is not None
        assert result["keyword_score"] == 0.7
        assert result["keyword_category"] == "consumer_health_tech"
        assert result["negative_keywords"] == ["b2b", "enterprise"]
        assert result["thesis_match"] is True
        assert result["thesis_fit_score"] == 0.85
        assert result["category"] == "consumer_health_tech"
        assert result["primary_end_user"] == "individual_consumer"
        assert result["paying_customer"] == "individual_consumer"
        assert (
            result["sells_to_or_operates_in"]
            == "operates_in_industry_for_consumers"
        )
        assert result["stage_estimate"] == "pre-seed"
        assert result["confidence"] == "high"
        assert result["rationale"] == "Fitness app for consumers"
        assert result["key_signals"] == ["fitness", "wellness", "d2c"]
        assert result["prompt_version"] == "v1.3.0"
        assert result["model"] == "gemini-2.0-flash"
        assert result["input_tokens"] == 150
        assert result["output_tokens"] == 75
        assert result["latency_ms"] == 450
        assert result["classification_status"] == "error_parse"
        assert result["competitor_flag"] is True
        assert result["competitor_match"] == {"name": "Peloton", "similarity": 0.65}

    @pytest.mark.asyncio
    async def test_save_thesis_classification_with_minimal_fields(self, store, signal_id):
        """Should save with only required fields."""
        result = await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
        )
        assert result > 0

        classification = await store.get_thesis_classification("domain:test.com")
        assert classification is not None
        assert classification["signal_id"] == signal_id
        assert classification["canonical_key"] == "domain:test.com"
        assert classification["thesis_fit_score"] is None
        assert classification["category"] is None
        assert classification["primary_end_user"] is None
        assert classification["paying_customer"] is None
        assert classification["sells_to_or_operates_in"] is None
        assert classification["classification_status"] == "success"
        assert classification["negative_keywords"] == []
        assert classification["key_signals"] == []

    @pytest.mark.asyncio
    async def test_get_recent_classification_expired(self, store, signal_id):
        """Should return None for classifications older than cache window."""
        from datetime import timezone, timedelta
        import aiosqlite

        # Save a classification
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test.com",
            thesis_fit_score=0.8,
            category="consumer_cpg",
        )

        # Manually update the classified_at to be 10 days ago
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        await store._db.execute(
            "UPDATE thesis_classifications SET classified_at = ? WHERE canonical_key = ?",
            (old_date, "domain:test.com")
        )
        await store._db.commit()

        # Should return None for 7-day cache window
        result = await store.get_recent_classification("domain:test.com", days=7)
        assert result is None

        # But should still be available via get_thesis_classification
        result = await store.get_thesis_classification("domain:test.com")
        assert result is not None


class TestSignalStatusUpdates:
    """Test signal status update methods."""

    @pytest.fixture
    async def store(self, tmp_path):
        db_path = str(tmp_path / "test_status.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def signal_id(self, store):
        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:status-test.com",
            company_name="Status Test Co",
            confidence=0.5,
            raw_data={},
        )
        return signal_id

    @pytest.mark.asyncio
    async def test_update_status_to_held(self, store, signal_id):
        """Should update signal status to held."""
        updated = await store.update_signal_status(
            "domain:status-test.com",
            "held",
            error_message="Low thesis fit",
        )
        assert updated is True

        signals = await store.get_signals_by_status("held")
        assert len(signals) >= 1
        assert any(s.canonical_key == "domain:status-test.com" for s in signals)

    @pytest.mark.asyncio
    async def test_update_status_to_qualified(self, store, signal_id):
        """Should update signal status to qualified."""
        updated = await store.update_signal_status(
            "domain:status-test.com",
            "qualified",
        )
        assert updated is True

        signals = await store.get_signals_by_status("qualified")
        assert len(signals) >= 1

    @pytest.mark.asyncio
    async def test_update_status_to_rejected(self, store, signal_id):
        """Should update signal status to rejected."""
        updated = await store.update_signal_status(
            "domain:status-test.com",
            "rejected",
            error_message="Thesis excluded",
        )
        assert updated is True

    @pytest.mark.asyncio
    async def test_get_signals_by_status_empty(self, store):
        """Should return empty list if no signals with status."""
        signals = await store.get_signals_by_status("qualified")
        assert signals == []

    @pytest.mark.asyncio
    async def test_get_signals_by_status_with_limit(self, store):
        """Should respect limit parameter."""
        # Create multiple signals
        for i in range(5):
            sid = await store.save_signal(
                signal_type="test",
                source_api="test",
                canonical_key=f"domain:limit-test-{i}.com",
                company_name=f"Test Co {i}",
                confidence=0.5,
                raw_data={},
            )
            await store.update_signal_status(f"domain:limit-test-{i}.com", "qualified")

        signals = await store.get_signals_by_status("qualified", limit=3)
        assert len(signals) == 3

    @pytest.mark.asyncio
    async def test_get_status_counts(self, store, signal_id):
        """Should return counts by status."""
        await store.update_signal_status("domain:status-test.com", "qualified")

        counts = await store.get_status_counts()
        assert "qualified" in counts
        assert counts["qualified"] >= 1
