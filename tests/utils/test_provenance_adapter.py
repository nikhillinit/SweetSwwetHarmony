"""
Tests for Provenance Adapter Module (Phase G)

Tests for:
- extract_field_provenance function that converts StoredSignal to Dict[str, FieldProvenance]
"""

import pytest
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# Mock StoredSignal for testing (matches storage/signal_store.py structure)
@dataclass
class MockStoredSignal:
    """Mock StoredSignal for testing."""
    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    company_name: Optional[str]
    confidence: float
    raw_data: Dict[str, Any]
    detected_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing_status: Optional[str] = None


class TestExtractFieldProvenance:
    """Tests for extract_field_provenance function."""

    def test_extracts_company_name(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=123,
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="domain:acme.com",
            company_name="Acme Inc",
            confidence=0.85,
            raw_data={},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test-run")

        assert "company_name" in result
        prov = result["company_name"]
        assert prov.value == "Acme Inc"
        assert prov.source_key == "companies_house"
        assert prov.signal_id == 123
        assert prov.confidence == 0.85
        assert prov.run_id == "test-run"

    def test_skips_none_company_name(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="github_trending",
            source_api="github",
            canonical_key="github_org:acme",
            company_name=None,
            confidence=0.5,
            raw_data={"description": "A great product"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "company_name" not in result

    def test_skips_empty_company_name(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="",
            confidence=0.5,
            raw_data={},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "company_name" not in result

    def test_extracts_description_from_raw_data(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=42,
            signal_type="product_hunt_launch",
            source_api="product_hunt",
            canonical_key="domain:example.com",
            company_name="Example Inc",
            confidence=0.7,
            raw_data={"description": "The best product ever made"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "description" in result
        prov = result["description"]
        assert prov.value == "The best product ever made"
        assert prov.source_key == "product_hunt"
        assert prov.signal_id == 42

    def test_extracts_tagline_as_description(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="crunchbase",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.8,
            raw_data={"tagline": "Innovation at its finest"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "description" in result
        assert result["description"].value == "Innovation at its finest"

    def test_extracts_summary_as_description(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="linkedin",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.6,
            raw_data={"summary": "A company summary"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "description" in result
        assert result["description"].value == "A company summary"

    def test_description_priority_order(self):
        """description takes priority over tagline and summary."""
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.5,
            raw_data={
                "description": "Primary description",
                "tagline": "Should not be used",
                "summary": "Also should not be used",
            },
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert result["description"].value == "Primary description"

    def test_skips_empty_description(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.5,
            raw_data={"description": ""},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "description" not in result

    def test_extracts_founding_date(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=100,
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="reg:ch:12345",
            company_name="Acme Ltd",
            confidence=0.95,
            raw_data={"founding_date": "2020-03-15"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "founding_date" in result
        prov = result["founding_date"]
        assert prov.value == "2020-03-15"
        assert prov.source_key == "companies_house"
        assert prov.signal_id == 100

    def test_extracts_registered_date_as_founding_date(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="sec_edgar",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.9,
            raw_data={"registered_date": "2019-01-01"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "founding_date" in result
        assert result["founding_date"].value == "2019-01-01"

    def test_extracts_incorporation_date_as_founding_date(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="opencorporates",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.8,
            raw_data={"incorporation_date": "2018-06-30"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "founding_date" in result
        assert result["founding_date"].value == "2018-06-30"

    def test_founding_date_priority_order(self):
        """founding_date takes priority over registered_date and incorporation_date."""
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.5,
            raw_data={
                "founding_date": "2020-01-01",
                "registered_date": "2019-06-01",
                "incorporation_date": "2019-01-01",
            },
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert result["founding_date"].value == "2020-01-01"

    def test_evidence_ref_format(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=999,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.5,
            raw_data={},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "company_name" in result
        assert result["company_name"].evidence_ref == "signal:999"

    def test_evidence_ref_includes_field_name_for_raw_data(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=42,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.5,
            raw_data={"description": "Test desc"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert result["description"].evidence_ref == "signal:42:description"

    def test_normalized_values_are_computed(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Acme Inc.",
            confidence=0.5,
            raw_data={"description": "  Trimmed description  "},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        # Company name should be normalized
        assert result["company_name"].normalized_value == "acme"
        # Description should be trimmed
        assert result["description"].normalized_value == "Trimmed description"

    def test_run_id_is_attached(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test",
            confidence=0.5,
            raw_data={"description": "Test"},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="pipeline-run-abc123")

        assert result["company_name"].run_id == "pipeline-run-abc123"
        assert result["description"].run_id == "pipeline-run-abc123"

    def test_all_fields_extracted_from_complete_signal(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="incorporation",
            source_api="companies_house",
            canonical_key="reg:ch:12345",
            company_name="Acme Ltd",
            confidence=0.95,
            raw_data={
                "description": "A great company",
                "founding_date": "2020-01-15",
            },
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "company_name" in result
        assert "description" in result
        assert "founding_date" in result
        assert len(result) == 3

    def test_empty_raw_data_only_extracts_company_name(self):
        from utils.provenance_adapter import extract_field_provenance

        now = datetime.now(timezone.utc)
        signal = MockStoredSignal(
            id=1,
            signal_type="test",
            source_api="test",
            canonical_key="test:1",
            company_name="Test Co",
            confidence=0.5,
            raw_data={},
            detected_at=now,
        )

        result = extract_field_provenance(signal, run_id="test")

        assert "company_name" in result
        assert "description" not in result
        assert "founding_date" not in result


class TestExtractFieldProvenanceBatch:
    """Tests for batch extraction."""

    def test_extract_batch(self):
        from utils.provenance_adapter import extract_field_provenance_batch

        now = datetime.now(timezone.utc)
        signals = [
            MockStoredSignal(
                id=1,
                signal_type="test",
                source_api="source1",
                canonical_key="test:1",
                company_name="Company A",
                confidence=0.8,
                raw_data={"description": "Desc A"},
                detected_at=now,
            ),
            MockStoredSignal(
                id=2,
                signal_type="test",
                source_api="source2",
                canonical_key="test:2",
                company_name="Company B",
                confidence=0.7,
                raw_data={"founding_date": "2020-01-01"},
                detected_at=now,
            ),
        ]

        result = extract_field_provenance_batch(signals, run_id="batch-run")

        assert 1 in result
        assert 2 in result
        assert "company_name" in result[1]
        assert "description" in result[1]
        assert "company_name" in result[2]
        assert "founding_date" in result[2]
