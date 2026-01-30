"""
Tests for Synthesis Types Module (Phase G)

Tests for:
- FieldProvenance dataclass
- FieldChoice dataclass
- ConflictRecord dataclass
"""

import pytest
from datetime import datetime, timezone


class TestFieldProvenance:
    """Tests for FieldProvenance dataclass."""

    def test_create_basic_provenance(self):
        from utils.synthesis_types import FieldProvenance

        now = datetime.now(timezone.utc)
        prov = FieldProvenance(
            value="Acme Inc",
            normalized_value="acme",
            source_key="companies_house",
            signal_id=123,
            confidence=0.85,
            detected_at=now,
            evidence_ref="signal:123",
        )

        assert prov.value == "Acme Inc"
        assert prov.normalized_value == "acme"
        assert prov.source_key == "companies_house"
        assert prov.signal_id == 123
        assert prov.confidence == 0.85
        assert prov.detected_at == now
        assert prov.evidence_ref == "signal:123"

    def test_run_id_optional(self):
        from utils.synthesis_types import FieldProvenance

        now = datetime.now(timezone.utc)
        prov = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        assert prov.run_id is None

    def test_run_id_can_be_set(self):
        from utils.synthesis_types import FieldProvenance

        now = datetime.now(timezone.utc)
        prov = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
            run_id="run-abc123",
        )

        assert prov.run_id == "run-abc123"

    def test_provenance_is_frozen(self):
        from utils.synthesis_types import FieldProvenance

        now = datetime.now(timezone.utc)
        prov = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            prov.value = "changed"

    def test_provenance_to_dict(self):
        from utils.synthesis_types import FieldProvenance

        now = datetime.now(timezone.utc)
        prov = FieldProvenance(
            value="Acme",
            normalized_value="acme",
            source_key="sec_edgar",
            signal_id=42,
            confidence=0.9,
            detected_at=now,
            evidence_ref="signal:42",
            run_id="run-xyz",
        )

        d = prov.to_dict()
        assert d["value"] == "Acme"
        assert d["normalized_value"] == "acme"
        assert d["source_key"] == "sec_edgar"
        assert d["signal_id"] == 42
        assert d["confidence"] == 0.9
        assert d["run_id"] == "run-xyz"


class TestFieldChoice:
    """Tests for FieldChoice dataclass."""

    def test_create_basic_choice(self):
        from utils.synthesis_types import FieldProvenance, FieldChoice

        now = datetime.now(timezone.utc)
        chosen = FieldProvenance(
            value="Acme Inc",
            normalized_value="acme",
            source_key="companies_house",
            signal_id=123,
            confidence=0.85,
            detected_at=now,
            evidence_ref="signal:123",
        )

        choice = FieldChoice(
            chosen=chosen,
            decision_rule="g_v1.0:pick_highest_score",
            decision_reason="Selected companies_house: score=0.85",
        )

        assert choice.chosen == chosen
        assert choice.decision_rule == "g_v1.0:pick_highest_score"
        assert "companies_house" in choice.decision_reason

    def test_choice_is_frozen(self):
        from utils.synthesis_types import FieldProvenance, FieldChoice

        now = datetime.now(timezone.utc)
        chosen = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        choice = FieldChoice(
            chosen=chosen,
            decision_rule="test",
            decision_reason="test reason",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            choice.decision_rule = "changed"

    def test_choice_to_dict(self):
        from utils.synthesis_types import FieldProvenance, FieldChoice

        now = datetime.now(timezone.utc)
        chosen = FieldProvenance(
            value="Acme",
            normalized_value="acme",
            source_key="sec_edgar",
            signal_id=42,
            confidence=0.9,
            detected_at=now,
            evidence_ref="signal:42",
        )

        choice = FieldChoice(
            chosen=chosen,
            decision_rule="g_v1.0:pick_highest_score",
            decision_reason="Winner by authority",
        )

        d = choice.to_dict()
        assert "chosen" in d
        assert d["decision_rule"] == "g_v1.0:pick_highest_score"
        assert d["decision_reason"] == "Winner by authority"
        assert d["chosen"]["value"] == "Acme"


class TestConflictRecord:
    """Tests for ConflictRecord dataclass."""

    def test_create_basic_conflict(self):
        from utils.synthesis_types import FieldProvenance, ConflictRecord

        now = datetime.now(timezone.utc)
        cand1 = FieldProvenance(
            value="Acme Inc",
            normalized_value="acme",
            source_key="companies_house",
            signal_id=1,
            confidence=0.85,
            detected_at=now,
            evidence_ref="signal:1",
        )
        cand2 = FieldProvenance(
            value="ACME Corporation",
            normalized_value="acme",
            source_key="sec_edgar",
            signal_id=2,
            confidence=0.80,
            detected_at=now,
            evidence_ref="signal:2",
        )

        conflict = ConflictRecord(
            field_name="company_name",
            candidates=[cand1, cand2],
            conflict_type="VALUE_MISMATCH",
            severity="CRITICAL",
        )

        assert conflict.field_name == "company_name"
        assert len(conflict.candidates) == 2
        assert conflict.conflict_type == "VALUE_MISMATCH"
        assert conflict.severity == "CRITICAL"

    def test_conflict_resolution_optional(self):
        from utils.synthesis_types import FieldProvenance, ConflictRecord

        now = datetime.now(timezone.utc)
        cand = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        conflict = ConflictRecord(
            field_name="description",
            candidates=[cand],
            conflict_type="VALUE_MISMATCH",
            severity="WARNING",
        )

        assert conflict.resolution is None

    def test_conflict_with_resolution(self):
        from utils.synthesis_types import FieldProvenance, FieldChoice, ConflictRecord

        now = datetime.now(timezone.utc)
        cand1 = FieldProvenance(
            value="Option A",
            normalized_value="option a",
            source_key="source1",
            signal_id=1,
            confidence=0.8,
            detected_at=now,
            evidence_ref="signal:1",
        )
        cand2 = FieldProvenance(
            value="Option B",
            normalized_value="option b",
            source_key="source2",
            signal_id=2,
            confidence=0.6,
            detected_at=now,
            evidence_ref="signal:2",
        )

        resolution = FieldChoice(
            chosen=cand1,
            decision_rule="g_v1.0:llm_decision",
            decision_reason="LLM selected Option A as most accurate",
        )

        conflict = ConflictRecord(
            field_name="description",
            candidates=[cand1, cand2],
            conflict_type="VALUE_MISMATCH",
            severity="CRITICAL",
            resolution=resolution,
        )

        assert conflict.resolution is not None
        assert conflict.resolution.chosen == cand1

    def test_conflict_is_frozen(self):
        from utils.synthesis_types import FieldProvenance, ConflictRecord

        now = datetime.now(timezone.utc)
        cand = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        conflict = ConflictRecord(
            field_name="test",
            candidates=[cand],
            conflict_type="VALUE_MISMATCH",
            severity="INFO",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            conflict.field_name = "changed"

    def test_conflict_types(self):
        """Test various conflict type values."""
        from utils.synthesis_types import FieldProvenance, ConflictRecord

        now = datetime.now(timezone.utc)
        cand = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        for conflict_type in ["VALUE_MISMATCH", "AUTHORITY_TIE", "TEMPORAL_CONFLICT", "TYPE_MISMATCH"]:
            conflict = ConflictRecord(
                field_name="test",
                candidates=[cand],
                conflict_type=conflict_type,
                severity="INFO",
            )
            assert conflict.conflict_type == conflict_type

    def test_severity_levels(self):
        """Test various severity levels."""
        from utils.synthesis_types import FieldProvenance, ConflictRecord

        now = datetime.now(timezone.utc)
        cand = FieldProvenance(
            value="test",
            normalized_value="test",
            source_key="github",
            signal_id=1,
            confidence=0.5,
            detected_at=now,
            evidence_ref="signal:1",
        )

        for severity in ["CRITICAL", "WARNING", "INFO"]:
            conflict = ConflictRecord(
                field_name="test",
                candidates=[cand],
                conflict_type="VALUE_MISMATCH",
                severity=severity,
            )
            assert conflict.severity == severity

    def test_conflict_to_dict(self):
        from utils.synthesis_types import FieldProvenance, ConflictRecord

        now = datetime.now(timezone.utc)
        cand1 = FieldProvenance(
            value="A",
            normalized_value="a",
            source_key="s1",
            signal_id=1,
            confidence=0.8,
            detected_at=now,
            evidence_ref="signal:1",
        )
        cand2 = FieldProvenance(
            value="B",
            normalized_value="b",
            source_key="s2",
            signal_id=2,
            confidence=0.7,
            detected_at=now,
            evidence_ref="signal:2",
        )

        conflict = ConflictRecord(
            field_name="company_name",
            candidates=[cand1, cand2],
            conflict_type="VALUE_MISMATCH",
            severity="CRITICAL",
        )

        d = conflict.to_dict()
        assert d["field_name"] == "company_name"
        assert d["conflict_type"] == "VALUE_MISMATCH"
        assert d["severity"] == "CRITICAL"
        assert len(d["candidates"]) == 2
        assert d["resolution"] is None


class TestConflictTypeConstants:
    """Tests for conflict type constant values."""

    def test_conflict_types_defined(self):
        from utils.synthesis_types import (
            CONFLICT_VALUE_MISMATCH,
            CONFLICT_AUTHORITY_TIE,
            CONFLICT_TEMPORAL,
            CONFLICT_TYPE_MISMATCH,
        )

        assert CONFLICT_VALUE_MISMATCH == "VALUE_MISMATCH"
        assert CONFLICT_AUTHORITY_TIE == "AUTHORITY_TIE"
        assert CONFLICT_TEMPORAL == "TEMPORAL_CONFLICT"
        assert CONFLICT_TYPE_MISMATCH == "TYPE_MISMATCH"


class TestSeverityConstants:
    """Tests for severity constant values."""

    def test_severity_constants_defined(self):
        from utils.synthesis_types import (
            SEVERITY_CRITICAL,
            SEVERITY_WARNING,
            SEVERITY_INFO,
        )

        assert SEVERITY_CRITICAL == "CRITICAL"
        assert SEVERITY_WARNING == "WARNING"
        assert SEVERITY_INFO == "INFO"
