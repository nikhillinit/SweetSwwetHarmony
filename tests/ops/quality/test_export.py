"""Tests for ops.quality.export -- labeled dataset export (CSV / JSONL)."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from ops.quality.export import export_dataset_csv, export_dataset_jsonl, iter_labeled_signals
from ops.quality.labels import upsert_resolved_label
from tests.ops.quality.conftest import _insert_signal, _utc_iso


def _label_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    canonical_key: str,
    label: str = "FP",
    label_source: str = "manual",
) -> None:
    """Convenience: upsert a resolved label for a signal."""
    upsert_resolved_label(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        human_label=label,
        label_source=label_source,
    )


class TestExportCsv:
    """Tests for export_dataset_csv."""

    def test_export_csv_empty_db(self, quality_db, tmp_path):
        """Exporting from a DB with no labeled signals returns 0 and writes empty file."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        out_path = tmp_path / "empty.csv"
        count = export_dataset_csv(conn, out_path=out_path, days=90)

        assert count == 0
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        # Empty file (no header, no rows) since there are no labeled signals
        assert content == ""

        conn.close()

    def test_export_csv_with_labels(self, quality_db, tmp_path):
        """Exporting labeled signals produces correct CSV row count."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 3 signals and label them
        signal_ids = []
        for i in range(3):
            sid = _insert_signal(
                conn,
                source_api=f"src_{i}",
                canonical_key=f"domain:csvtest{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_signal(conn, sid, f"domain:csvtest{i}.com", label="TP" if i == 0 else "FP")
            signal_ids.append(sid)

        out_path = tmp_path / "labeled.csv"
        count = export_dataset_csv(conn, out_path=out_path, days=90)

        assert count == 3

        # Verify CSV content
        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3

        conn.close()

    def test_export_csv_has_header(self, quality_db, tmp_path):
        """CSV file has expected column names in the header row."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="header_test",
            canonical_key="domain:header.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, sid, "domain:header.com")

        out_path = tmp_path / "header.csv"
        export_dataset_csv(conn, out_path=out_path, days=90)

        with open(out_path, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()

        # Verify key column names are in the header
        assert "signal_id" in first_line
        assert "canonical_key" in first_line
        assert "source_api" in first_line
        assert "human_label" in first_line
        assert "label_source" in first_line
        assert "detected_at" in first_line

        conn.close()


class TestExportJsonl:
    """Tests for export_dataset_jsonl."""

    def test_export_jsonl_empty_db(self, quality_db, tmp_path):
        """Exporting JSONL from empty DB returns 0."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        out_path = tmp_path / "empty.jsonl"
        count = export_dataset_jsonl(conn, out_path=out_path, days=90)

        assert count == 0
        conn.close()

    def test_export_jsonl_with_labels(self, quality_db, tmp_path):
        """Exporting JSONL produces one JSON line per labeled signal."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        for i in range(4):
            sid = _insert_signal(
                conn,
                source_api=f"jsonl_src_{i}",
                canonical_key=f"domain:jsonl{i}.com",
                detected_at=_utc_iso(1),
            )
            _label_signal(conn, sid, f"domain:jsonl{i}.com", label="FP")

        out_path = tmp_path / "labeled.jsonl"
        count = export_dataset_jsonl(conn, out_path=out_path, days=90)

        assert count == 4

        # Each line must be valid JSON
        lines = out_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 4
        for line in lines:
            obj = json.loads(line)
            assert "signal_id" in obj
            assert "human_label" in obj

        conn.close()


class TestExportWindowFiltering:
    """Tests for time-window filtering in exports."""

    def test_export_window_filtering(self, quality_db, tmp_path):
        """Signals older than the days window are excluded from export."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert a recent signal (1 day ago) -- should be included
        recent_sid = _insert_signal(
            conn,
            source_api="recent",
            canonical_key="domain:recent.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, recent_sid, "domain:recent.com")

        # Insert an old signal (100 days ago) -- should be excluded with days=30
        old_sid = _insert_signal(
            conn,
            source_api="old",
            canonical_key="domain:old.com",
            detected_at=_utc_iso(100),
        )
        _label_signal(conn, old_sid, "domain:old.com")

        out_path = tmp_path / "window.csv"
        count = export_dataset_csv(conn, out_path=out_path, days=30)

        assert count == 1  # only recent signal

        conn.close()


class TestIterLabeledSignals:
    """Tests for iter_labeled_signals."""

    def test_iter_labeled_signals_fields(self, quality_db):
        """Each yielded dict has the expected set of keys."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(
            conn,
            source_api="field_test",
            canonical_key="domain:fields.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, sid, "domain:fields.com", label="TP")

        rows = list(iter_labeled_signals(conn, days=90))

        assert len(rows) == 1
        row = rows[0]

        expected_keys = {
            "signal_id", "canonical_key", "signal_type", "source_api",
            "company_name", "confidence", "detected_at",
            "human_label", "label_source", "labeled_at", "notion_status",
            "title", "description", "url", "domain",
            "keyword_score", "keyword_category", "negative_keywords",
            "thesis_match", "thesis_fit_score", "thesis_category",
            "stage_estimate", "llm_confidence", "rationale",
            "key_signals", "model", "latency_ms", "classified_at",
        }
        assert expected_keys.issubset(set(row.keys()))

        conn.close()


class TestExportLabelSourceFilter:
    """Tests for label_sources filtering in export."""

    def test_export_label_source_filter(self, quality_db, tmp_path):
        """Filtering by label_sources limits which rows are exported."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert signal labeled by "manual"
        sid_manual = _insert_signal(
            conn,
            source_api="filter_src",
            canonical_key="domain:manual_label.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, sid_manual, "domain:manual_label.com", label="FP", label_source="manual")

        # Insert signal labeled by "notion_status_event"
        sid_auto = _insert_signal(
            conn,
            source_api="filter_src",
            canonical_key="domain:auto_label.com",
            detected_at=_utc_iso(1),
        )
        _label_signal(conn, sid_auto, "domain:auto_label.com", label="TP", label_source="notion_status_event")

        # Export with label_sources filter -- manual only
        out_path = tmp_path / "manual_only.csv"
        count = export_dataset_csv(
            conn,
            out_path=out_path,
            days=90,
            label_sources=("manual",),
        )

        assert count == 1

        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["label_source"] == "manual"

        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
