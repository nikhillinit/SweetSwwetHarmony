"""Tests for shadow_report.py CLI script."""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.shadow_report import (
    ShadowRecord,
    GateResult,
    ReportSummary,
    load_records,
    compute_report,
    check_parity_gate,
    check_tuning_gate,
    generate_markdown_report,
)


# =============================================================================
# SHADOW RECORD TESTS
# =============================================================================

class TestShadowRecord:
    """Tests for ShadowRecord parsing."""

    def test_from_shadow_log_parses_valid_data(self):
        """Should parse valid shadow log with v2_shadow."""
        log = {
            "canonical_key": "domain:test.com",
            "signal_id": 123,
            "logged_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "computed_value": {
                "keyword_score": 0.5,
                "keyword_category": "consumer_cpg",
                "v2_shadow": {
                    "v1": {
                        "score": 0.5,
                        "routing": "QUALIFIED",
                        "penalty_raw": 0.3,
                        "negative_keywords": ["enterprise"],
                    },
                    "v2": {
                        "score": 0.4,
                        "routing": "HELD",
                        "penalty_raw": 0.5,
                        "negative_keywords": ["enterprise"],
                    },
                    "delta_score": -0.1,
                    "would_change_routing": True,
                    "would_change_is_fit": True,
                    "policy_hash": "abc123",
                },
            },
        }

        record = ShadowRecord.from_shadow_log(log)

        assert record is not None
        assert record.canonical_key == "domain:test.com"
        assert record.signal_id == 123
        assert record.v1_score == 0.5
        assert record.v2_score == 0.4
        assert record.delta_score == -0.1
        assert record.would_change_routing is True
        assert record.policy_hash == "abc123"

    def test_from_shadow_log_returns_none_without_v2_shadow(self):
        """Should return None if v2_shadow is missing."""
        log = {
            "canonical_key": "domain:test.com",
            "computed_value": {
                "keyword_score": 0.5,
            },
        }

        record = ShadowRecord.from_shadow_log(log)
        assert record is None

    def test_to_dict_serializable(self):
        """ShadowRecord.to_dict() should be JSON-serializable."""
        record = ShadowRecord(
            canonical_key="test",
            signal_id=1,
            logged_at="2024-01-01T00:00:00+00:00",
            policy_hash="abc",
            v1_score=0.5,
            v1_routing="QUALIFIED",
            v1_penalty_raw=0.0,
            v1_negative_keywords=[],
            v2_score=0.5,
            v2_routing="QUALIFIED",
            v2_penalty_raw=0.0,
            v2_negative_keywords=[],
            delta_score=0.0,
            would_change_routing=False,
            would_change_is_fit=False,
        )

        data = record.to_dict()
        json_str = json.dumps(data)
        assert isinstance(json_str, str)


# =============================================================================
# LOAD RECORDS TESTS
# =============================================================================

class TestLoadRecords:
    """Tests for loading records from JSONL."""

    def test_load_records_from_jsonl(self, tmp_path):
        """Should load records from JSONL file."""
        jsonl_path = tmp_path / "test.jsonl"

        records_data = [
            {
                "canonical_key": "test1",
                "signal_id": 1,
                "logged_at": "2024-01-01T00:00:00+00:00",
                "policy_hash": "abc",
                "v1_score": 0.5,
                "v1_routing": "QUALIFIED",
                "v1_penalty_raw": 0.0,
                "v1_negative_keywords": [],
                "v2_score": 0.5,
                "v2_routing": "QUALIFIED",
                "v2_penalty_raw": 0.0,
                "v2_negative_keywords": [],
                "delta_score": 0.0,
                "would_change_routing": False,
                "would_change_is_fit": False,
            },
            {
                "canonical_key": "test2",
                "signal_id": 2,
                "logged_at": "2024-01-02T00:00:00+00:00",
                "policy_hash": "abc",
                "v1_score": 0.6,
                "v1_routing": "QUALIFIED",
                "v1_penalty_raw": 0.0,
                "v1_negative_keywords": [],
                "v2_score": 0.3,
                "v2_routing": "HELD",
                "v2_penalty_raw": 0.3,
                "v2_negative_keywords": ["enterprise"],
                "delta_score": -0.3,
                "would_change_routing": True,
                "would_change_is_fit": True,
            },
        ]

        with open(jsonl_path, "w") as f:
            for r in records_data:
                f.write(json.dumps(r) + "\n")

        records = load_records(jsonl_path)

        assert len(records) == 2
        assert records[0].canonical_key == "test1"
        assert records[1].would_change_routing is True

    def test_load_records_skips_empty_lines(self, tmp_path):
        """Should skip empty lines in JSONL."""
        jsonl_path = tmp_path / "test.jsonl"

        with open(jsonl_path, "w") as f:
            f.write(json.dumps({
                "canonical_key": "test",
                "signal_id": 1,
                "logged_at": "",
                "policy_hash": None,
                "v1_score": 0.5,
                "v1_routing": "QUALIFIED",
                "v1_penalty_raw": 0.0,
                "v1_negative_keywords": [],
                "v2_score": 0.5,
                "v2_routing": "QUALIFIED",
                "v2_penalty_raw": 0.0,
                "v2_negative_keywords": [],
                "delta_score": 0.0,
                "would_change_routing": False,
                "would_change_is_fit": False,
            }) + "\n")
            f.write("\n")  # Empty line
            f.write("   \n")  # Whitespace line

        records = load_records(jsonl_path)
        assert len(records) == 1


# =============================================================================
# COMPUTE REPORT TESTS
# =============================================================================

class TestComputeReport:
    """Tests for computing report summary."""

    def test_compute_report_empty_records(self):
        """Should handle empty records list."""
        summary = compute_report([])

        assert summary.total_records == 0
        assert summary.routing_change_rate == 0.0

    def test_compute_report_calculates_metrics(self):
        """Should calculate correct metrics."""
        records = [
            ShadowRecord(
                canonical_key="test1", signal_id=1, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            ),
            ShadowRecord(
                canonical_key="test2", signal_id=2, logged_at="", policy_hash="abc",
                v1_score=0.6, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.3, v2_routing="HELD", v2_penalty_raw=0.3, v2_negative_keywords=[],
                delta_score=-0.3, would_change_routing=True, would_change_is_fit=True,
            ),
        ]

        summary = compute_report(records)

        assert summary.total_records == 2
        assert summary.records_with_routing_change == 1
        assert summary.routing_change_rate == 0.5
        assert summary.max_abs_delta == 0.3
        assert summary.policy_hash == "abc"

    def test_compute_report_transition_matrix(self):
        """Should build correct transition matrix."""
        records = [
            ShadowRecord(
                canonical_key="test1", signal_id=1, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            ),
            ShadowRecord(
                canonical_key="test2", signal_id=2, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.2, v2_routing="HELD", v2_penalty_raw=0.3, v2_negative_keywords=[],
                delta_score=-0.3, would_change_routing=True, would_change_is_fit=True,
            ),
            ShadowRecord(
                canonical_key="test3", signal_id=3, logged_at="", policy_hash="abc",
                v1_score=0.2, v1_routing="HELD", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.2, v2_routing="HELD", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            ),
        ]

        summary = compute_report(records)

        assert summary.transition_matrix["QUALIFIED"]["QUALIFIED"] == 1
        assert summary.transition_matrix["QUALIFIED"]["HELD"] == 1
        assert summary.transition_matrix["HELD"]["HELD"] == 1


# =============================================================================
# PARITY GATE TESTS
# =============================================================================

class TestParityGate:
    """Tests for parity gate check."""

    def test_parity_gate_passes_with_identical_results(self):
        """Should pass when v1 == v2."""
        records = [
            ShadowRecord(
                canonical_key=f"test{i}", signal_id=i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            )
            for i in range(300)
        ]

        result = check_parity_gate(records)

        assert result.passed is True
        assert result.exit_code == 0

    def test_parity_gate_fails_with_routing_change(self):
        """Should fail when any routing changes."""
        records = [
            ShadowRecord(
                canonical_key="test1", signal_id=1, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.2, v2_routing="HELD", v2_penalty_raw=0.3, v2_negative_keywords=[],
                delta_score=-0.3, would_change_routing=True, would_change_is_fit=True,
            ),
        ] + [
            ShadowRecord(
                canonical_key=f"test{i}", signal_id=i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            )
            for i in range(2, 300)
        ]

        result = check_parity_gate(records)

        assert result.passed is False
        assert "routing_change_count=1" in result.failures[0]

    def test_parity_gate_insufficient_samples(self):
        """Should return exit code 3 for insufficient samples."""
        records = [
            ShadowRecord(
                canonical_key="test1", signal_id=1, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            )
        ]  # Only 1 record, need 200

        result = check_parity_gate(records)

        assert result.exit_code == 3


# =============================================================================
# TUNING GATE TESTS
# =============================================================================

class TestTuningGate:
    """Tests for tuning gate check."""

    def test_tuning_gate_passes_within_limits(self):
        """Should pass when changes are within limits."""
        # Create 1000 records with 0.5% routing change (5 changes)
        records = [
            ShadowRecord(
                canonical_key=f"test{i}", signal_id=i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            )
            for i in range(995)
        ] + [
            ShadowRecord(
                canonical_key=f"change{i}", signal_id=1000+i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.35, v2_routing="QUALIFIED", v2_penalty_raw=0.15, v2_negative_keywords=[],
                delta_score=-0.15, would_change_routing=False, would_change_is_fit=False,
            )
            for i in range(5)
        ]

        result = check_tuning_gate(records)

        assert result.passed is True

    def test_tuning_gate_fails_high_routing_change(self):
        """Should fail when routing change rate exceeds 1%."""
        # Create 1000 records with 2% routing change (20 changes)
        records = [
            ShadowRecord(
                canonical_key=f"test{i}", signal_id=i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            )
            for i in range(980)
        ] + [
            ShadowRecord(
                canonical_key=f"change{i}", signal_id=1000+i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.2, v2_routing="HELD", v2_penalty_raw=0.3, v2_negative_keywords=[],
                delta_score=-0.3, would_change_routing=True, would_change_is_fit=True,
            )
            for i in range(20)
        ]

        result = check_tuning_gate(records)

        assert result.passed is False
        assert any("routing_change_rate" in f for f in result.failures)

    def test_tuning_gate_small_n_rejects_any_qualified_to_rejected(self):
        """Should fail if any QUALIFIED->REJECTED with N<2000."""
        # Create 1500 records (< 2000) with 1 QUALIFIED->REJECTED
        records = [
            ShadowRecord(
                canonical_key=f"test{i}", signal_id=i, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            )
            for i in range(1499)
        ] + [
            ShadowRecord(
                canonical_key="bad", signal_id=9999, logged_at="", policy_hash="abc",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.05, v2_routing="REJECTED", v2_penalty_raw=0.9, v2_negative_keywords=[],
                delta_score=-0.45, would_change_routing=True, would_change_is_fit=True,
            )
        ]

        result = check_tuning_gate(records)

        assert result.passed is False
        assert any("qualified_to_rejected_count=1" in f for f in result.failures)


# =============================================================================
# MARKDOWN REPORT TESTS
# =============================================================================

class TestMarkdownReport:
    """Tests for markdown report generation."""

    def test_generate_markdown_report(self):
        """Should generate valid markdown."""
        records = [
            ShadowRecord(
                canonical_key="test1", signal_id=1, logged_at="", policy_hash="abc123",
                v1_score=0.5, v1_routing="QUALIFIED", v1_penalty_raw=0.0, v1_negative_keywords=[],
                v2_score=0.5, v2_routing="QUALIFIED", v2_penalty_raw=0.0, v2_negative_keywords=[],
                delta_score=0.0, would_change_routing=False, would_change_is_fit=False,
            ),
        ]
        summary = compute_report(records)

        md = generate_markdown_report(summary, records)

        assert "# Shadow Mode Report" in md
        assert "abc123" in md
        assert "Total Records" in md
        assert "Routing Change Rate" in md
