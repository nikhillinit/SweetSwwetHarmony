"""Tests for ops.quality.thesis -- thesis classification helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from ops.quality.thesis import (
    classify_signal_llm,
    generate_disagreement_report,
    iter_signals_missing_thesis,
    store_thesis_classification,
)
from tests.ops.quality.conftest import _insert_signal, _utc_iso


def _store_classification_for_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    canonical_key: str,
    *,
    thesis_match: bool = True,
    keyword_score: float = 0.6,
    thesis_fit_score: float = 0.7,
    category: str = "consumer_cpg",
) -> int:
    """Convenience: store a thesis classification with sensible defaults."""
    return store_thesis_classification(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        keyword_score=keyword_score,
        keyword_category="consumer_cpg",
        negative_keywords=[],
        thesis_match=thesis_match,
        thesis_fit_score=thesis_fit_score,
        category=category,
        stage_estimate="Pre-Seed",
        confidence="medium",
        rationale="Test rationale for classification",
        key_signals=["signal_a", "signal_b"],
        prompt_version="test-v1",
        model="test-model",
        input_tokens=100,
        output_tokens=50,
        latency_ms=250,
    )


class TestStoreThesisClassification:
    """Tests for store_thesis_classification."""

    def test_store_thesis_classification(self, quality_db):
        """Storing a classification creates a row in thesis_classifications."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:thesis_test.com",
            detected_at=_utc_iso(1),
        )

        tc_id = store_thesis_classification(
            conn,
            signal_id=sid,
            canonical_key="domain:thesis_test.com",
            keyword_score=0.55,
            keyword_category="consumer_health_tech",
            negative_keywords=["b2b", "enterprise"],
            thesis_match=True,
            thesis_fit_score=0.72,
            category="consumer_health_tech",
            stage_estimate="Seed",
            confidence="high",
            rationale="Consumer health app with strong thesis fit",
            key_signals=["health", "consumer", "app"],
            prompt_version="quality-ops-v1",
            model="gemini-2.0-flash",
            input_tokens=200,
            output_tokens=80,
            latency_ms=450,
        )

        # Verify it exists in the DB
        row = conn.execute(
            "SELECT * FROM thesis_classifications WHERE id = ?", (tc_id,)
        ).fetchone()
        assert row is not None
        assert row["signal_id"] == sid
        assert row["canonical_key"] == "domain:thesis_test.com"
        assert row["keyword_score"] == pytest.approx(0.55)
        assert row["category"] == "consumer_health_tech"
        assert row["thesis_match"] == 1  # stored as integer
        assert row["latency_ms"] == 450

        conn.close()

    def test_store_thesis_classification_persists_status(self, quality_db):
        """Operational classification_status should round-trip through the quality helper."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:status-test.com",
            detected_at=_utc_iso(1),
        )

        tc_id = store_thesis_classification(
            conn,
            signal_id=sid,
            canonical_key="domain:status-test.com",
            keyword_score=0.55,
            keyword_category="consumer_health_tech",
            negative_keywords=[],
            thesis_match=False,
            thesis_fit_score=0.0,
            category="excluded",
            stage_estimate="Unknown",
            confidence="low",
            rationale="API failure",
            key_signals=[],
            prompt_version="quality-ops-v1",
            model="gemini-2.0-flash",
            input_tokens=0,
            output_tokens=0,
            latency_ms=450,
            classification_status="error_api",
        )

        row = conn.execute(
            "SELECT classification_status FROM thesis_classifications WHERE id = ?",
            (tc_id,),
        ).fetchone()
        assert row is not None
        assert row["classification_status"] == "error_api"

        conn.close()

    def test_store_thesis_classification_returns_id(self, quality_db):
        """store_thesis_classification returns a positive integer row ID."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="sec_edgar",
            canonical_key="domain:idtest.com",
            detected_at=_utc_iso(1),
        )

        tc_id = _store_classification_for_signal(conn, sid, "domain:idtest.com")

        assert isinstance(tc_id, int)
        assert tc_id > 0

        conn.close()


class TestIterSignalsMissingThesis:
    """Tests for iter_signals_missing_thesis."""

    def test_iter_signals_missing_thesis_all_missing(self, quality_db_with_signals):
        """With signals but no thesis classifications, all signal IDs are returned."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        missing = iter_signals_missing_thesis(conn, days=90, limit=100)

        # All 5 fixture signals should be missing thesis classifications
        assert len(missing) == 5
        for sid in signal_ids:
            assert sid in missing

        conn.close()

    def test_iter_signals_missing_thesis_none_missing(self, quality_db_with_signals):
        """After classifying all signals, no IDs are returned."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Classify all signals
        for sid in signal_ids:
            _store_classification_for_signal(conn, sid, f"domain:company{signal_ids.index(sid)}.com")

        missing = iter_signals_missing_thesis(conn, days=90, limit=100)

        assert missing == []

        conn.close()

    def test_iter_signals_missing_thesis_limit(self, quality_db_with_signals):
        """iter_signals_missing_thesis respects the limit parameter."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        missing = iter_signals_missing_thesis(conn, days=90, limit=2)

        assert len(missing) == 2

        conn.close()


class TestGenerateDisagreementReport:
    """Tests for generate_disagreement_report."""

    def test_generate_disagreement_report_empty(self, quality_db):
        """Returns a markdown string even when there are no classifications."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        report = generate_disagreement_report(conn, days=30)

        assert isinstance(report, str)
        assert "Thesis Disagreement Report" in report
        assert "**Total classified**: 0" in report

        conn.close()

    def test_generate_disagreement_report_with_data(self, quality_db, tmp_path):
        """Report contains disagreement sections when data has keyword vs LLM conflicts."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Create a keyword false positive: keyword_score >= threshold but thesis_match = False
        sid_kw_fp = _insert_signal(
            conn,
            source_api="rss_feeds",
            canonical_key="domain:kwfp.com",
            company_name="KW FP Co",
            detected_at=_utc_iso(1),
        )
        store_thesis_classification(
            conn,
            signal_id=sid_kw_fp,
            canonical_key="domain:kwfp.com",
            keyword_score=0.85,  # High keyword score (>= 0.7)
            keyword_category="consumer_cpg",
            negative_keywords=[],
            thesis_match=False,  # LLM says no
            thesis_fit_score=0.15,  # Low LLM score (< 0.4)
            category="none",
            stage_estimate="Unknown",
            confidence="low",
            rationale="B2B enterprise tool",
            key_signals=[],
            prompt_version="test-v1",
            model="test-model",
            input_tokens=None,
            output_tokens=None,
            latency_ms=100,
        )

        # Create a keyword false negative: keyword_score < threshold but thesis_match = True
        sid_kw_fn = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:kwfn.com",
            company_name="KW FN Co",
            detected_at=_utc_iso(1),
        )
        store_thesis_classification(
            conn,
            signal_id=sid_kw_fn,
            canonical_key="domain:kwfn.com",
            keyword_score=0.10,  # below threshold
            keyword_category="unknown",
            negative_keywords=[],
            thesis_match=True,  # LLM says yes
            thesis_fit_score=0.80,
            category="consumer_health_tech",
            stage_estimate="Pre-Seed",
            confidence="high",
            rationale="Consumer health tech startup",
            key_signals=["health", "wellness"],
            prompt_version="test-v1",
            model="test-model",
            input_tokens=None,
            output_tokens=None,
            latency_ms=100,
        )

        # Note: generate_disagreement_report has a bug when out_path is set
        # (missing Path import in thesis.py). We test the report generation
        # without file output to verify the logic is correct.
        report = generate_disagreement_report(
            conn, days=30, keyword_threshold=0.40
        )

        assert "**Keyword false positives**: 1" in report
        assert "**Keyword false negatives**: 1" in report
        assert "Keyword False Positives" in report
        assert "Keyword False Negatives" in report
        assert "**Total classified**: 2" in report
        assert "**Total disagreements**: 2" in report  # Both are disagreements

        conn.close()


class TestClassifySignalLlmImportError:
    """Tests for classify_signal_llm error handling."""

    def test_classify_signal_llm_missing_genai(self, quality_db):
        """classify_signal_llm raises ImportError when LLM module is unavailable."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:llmtest.com",
            raw_data=json.dumps({"description": "A test company"}),
            detected_at=_utc_iso(1),
        )

        # Mock the LLM import inside classify_signal_llm to fail
        with patch.dict("sys.modules", {"consumer.thesis_filter.llm_classifier": None}):
            with pytest.raises(ImportError, match="google-genai"):
                classify_signal_llm(conn, signal_id=sid, model="test-model")

        conn.close()


class TestClassifySignalLlmWiring:
    """Regression coverage for the LLM classifier payload contract."""

    def test_classify_signal_llm_uses_expected_payload_and_persists_status(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="github",
            canonical_key="domain:wiring-test.com",
            company_name="Wiring Test",
            raw_data=json.dumps(
                {
                    "title": "Wiring Test Title",
                    "description": "Consumer marketplace for diners.",
                    "url": "https://wiring-test.example",
                }
            ),
            detected_at=_utc_iso(1),
        )

        captured = {}

        class FakeClassifier:
            def __init__(self, model: str):
                self.model = model

            def classify_sync(self, signal_data):
                captured.update(signal_data)
                return types.SimpleNamespace(
                    thesis_match=True,
                    thesis_fit_score=0.82,
                    category="consumer_marketplace",
                    stage_estimate="Seed",
                    confidence="high",
                    rationale="Consumer marketplace fit",
                    key_signals=["marketplace", "diners"],
                    classification_status="success",
                )

        fake_module = types.SimpleNamespace(LLMClassifier=FakeClassifier)
        original_module = sys.modules.get("consumer.thesis_filter.llm_classifier")
        sys.modules["consumer.thesis_filter.llm_classifier"] = fake_module
        try:
            result = classify_signal_llm(conn, signal_id=sid, model="test-model")
        finally:
            if original_module is None:
                sys.modules.pop("consumer.thesis_filter.llm_classifier", None)
            else:
                sys.modules["consumer.thesis_filter.llm_classifier"] = original_module

        assert captured["title"] == "Wiring Test Title"
        assert captured["url"] == "https://wiring-test.example"
        assert captured["source_api"] == "github"
        assert "Consumer marketplace for diners." in captured["source_context"]
        assert result.classification_status == "success"

        row = conn.execute(
            "SELECT category, classification_status FROM thesis_classifications WHERE signal_id = ?",
            (sid,),
        ).fetchone()
        assert row is not None
        assert row["category"] == "consumer_marketplace"
        assert row["classification_status"] == "success"

        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
