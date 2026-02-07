"""Test disagreement_detected flag in thesis_classifications."""
import pytest
from storage.signal_store import SignalStore


@pytest.mark.asyncio
class TestDisagreementDetection:
    """Test disagreement detection between keyword and LLM classifiers."""

    async def test_disagreement_high_keyword_low_llm(self, tmp_path):
        """Test disagreement when keyword score high, LLM score low."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        # Create a signal
        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:test1.com",
            company_name="Test Co",
            confidence=0.8,
            raw_data={"test": "data"},
        )

        # Save thesis classification with disagreement
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test1.com",
            keyword_score=0.85,  # High keyword score
            keyword_category="consumer_cpg",
            thesis_fit_score=0.25,  # Low LLM score
            category="excluded",
            confidence="high",
        )

        # Verify disagreement detected
        result = await store.get_thesis_classification("domain:test1.com")
        assert result is not None
        assert result["disagreement_detected"] is True
        assert result["keyword_score"] == 0.85
        assert result["thesis_fit_score"] == 0.25

        await store.close()

    async def test_disagreement_low_keyword_high_llm(self, tmp_path):
        """Test disagreement when keyword score low, LLM score high."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:test2.com",
            company_name="Test Co 2",
            confidence=0.8,
            raw_data={"test": "data"},
        )

        # Save thesis classification with disagreement (opposite direction)
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test2.com",
            keyword_score=0.25,  # Low keyword score
            keyword_category="other",
            thesis_fit_score=0.85,  # High LLM score
            category="consumer_cpg",
            confidence="high",
        )

        # Verify disagreement detected
        result = await store.get_thesis_classification("domain:test2.com")
        assert result is not None
        assert result["disagreement_detected"] is True
        assert result["keyword_score"] == 0.25
        assert result["thesis_fit_score"] == 0.85

        await store.close()

    async def test_agreement_both_high(self, tmp_path):
        """Test no disagreement when both scores high (agreement)."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:test3.com",
            company_name="Test Co 3",
            confidence=0.8,
            raw_data={"test": "data"},
        )

        # Save thesis classification with agreement
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test3.com",
            keyword_score=0.85,  # High keyword score
            keyword_category="consumer_cpg",
            thesis_fit_score=0.80,  # High LLM score
            category="consumer_cpg",
            confidence="high",
        )

        # Verify no disagreement
        result = await store.get_thesis_classification("domain:test3.com")
        assert result is not None
        assert result["disagreement_detected"] is False
        assert result["keyword_score"] == 0.85
        assert result["thesis_fit_score"] == 0.80

        await store.close()

    async def test_agreement_both_low(self, tmp_path):
        """Test no disagreement when both scores low (agreement)."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:test4.com",
            company_name="Test Co 4",
            confidence=0.8,
            raw_data={"test": "data"},
        )

        # Save thesis classification with agreement (both low)
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test4.com",
            keyword_score=0.25,  # Low keyword score
            keyword_category="other",
            thesis_fit_score=0.30,  # Low LLM score
            category="excluded",
            confidence="high",
        )

        # Verify no disagreement
        result = await store.get_thesis_classification("domain:test4.com")
        assert result is not None
        assert result["disagreement_detected"] is False
        assert result["keyword_score"] == 0.25
        assert result["thesis_fit_score"] == 0.30

        await store.close()

    async def test_edge_case_keyword_none(self, tmp_path):
        """Test no disagreement when keyword_score is None."""
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        signal_id = await store.save_signal(
            signal_type="test",
            source_api="test",
            canonical_key="domain:test5.com",
            company_name="Test Co 5",
            confidence=0.8,
            raw_data={"test": "data"},
        )

        # Save thesis classification with only LLM score
        await store.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:test5.com",
            keyword_score=None,  # No keyword score
            thesis_fit_score=0.85,
            category="consumer_cpg",
            confidence="high",
        )

        # Verify no disagreement (need both scores to detect disagreement)
        result = await store.get_thesis_classification("domain:test5.com")
        assert result is not None
        assert result["disagreement_detected"] is False
        assert result["keyword_score"] is None
        assert result["thesis_fit_score"] == 0.85

        await store.close()
