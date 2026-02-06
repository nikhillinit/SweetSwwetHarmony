"""Quality Ops end-to-end flywheel tests.

Verifies the complete feedback flywheel:
  signal insertion -> manual labeling -> stats computation
  -> pattern detection -> tuning proposal generation -> CSV export

Each test exercises multiple Quality Ops modules in sequence to confirm
they compose correctly against a real (temporary) SQLite database.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from ops.quality.db import quality_conn
from ops.quality.export import export_dataset_csv
from ops.quality.labels import label_signal_manual
from ops.quality.patterns import PatternConfig, detect_patterns
from ops.quality.stats import get_overall_stats
from ops.quality.tuning import generate_tuning_proposal
from tests.ops.quality.conftest import _insert_signal, _utc_iso


class TestFlywheelLabelToStats:
    """Label signals manually, then verify overall stats reflect the labels."""

    def test_flywheel_label_to_stats(self, quality_db, tmp_path):
        """Insert signals, label 2 as FP and 1 as TP, confirm FP rate = 2/3."""
        db_path, store = quality_db

        with quality_conn(db_path) as conn:
            # Insert 3 signals
            sid1 = _insert_signal(conn, canonical_key="domain:alpha.com", company_name="Alpha", detected_at=_utc_iso(1))
            sid2 = _insert_signal(conn, canonical_key="domain:beta.com", company_name="Beta", detected_at=_utc_iso(1))
            sid3 = _insert_signal(conn, canonical_key="domain:gamma.com", company_name="Gamma", detected_at=_utc_iso(1))

            # Label signals manually
            fid1, upsert1 = label_signal_manual(conn, signal_id=sid1, label="FP", created_by="tester", reason="B2B SaaS")
            fid2, upsert2 = label_signal_manual(conn, signal_id=sid2, label="FP", created_by="tester", reason="Enterprise tool")
            fid3, upsert3 = label_signal_manual(conn, signal_id=sid3, label="TP", created_by="tester", reason="Consumer CPG")

            # Verify feedback IDs are positive
            assert fid1 > 0
            assert fid2 > 0
            assert fid3 > 0

            # Verify upsert results
            assert upsert1.human_label == "FP"
            assert upsert2.human_label == "FP"
            assert upsert3.human_label == "TP"

            # Check overall stats
            stats = get_overall_stats(conn, days=30)

            assert stats["labeled"] == 3.0
            assert stats["fp"] == 2.0
            assert stats["tp"] == 1.0
            assert stats["unsure"] == 0.0
            assert abs(stats["fp_rate"] - 2.0 / 3.0) < 1e-9


class TestFlywheelDetectPatterns:
    """Insert many FP signals from the same source, then confirm pattern detection finds them."""

    def test_flywheel_detect_patterns(self, quality_db, tmp_path):
        """Insert 15 signals from same source_api, label all FP, detect source_api_fp_rate pattern."""
        db_path, store = quality_db

        with quality_conn(db_path) as conn:
            signal_ids = []
            for i in range(15):
                sid = _insert_signal(
                    conn,
                    source_api="noisy_collector",
                    canonical_key=f"domain:noisy{i}.com",
                    company_name=f"Noisy Co {i}",
                    detected_at=_utc_iso(1),
                    raw_data=json.dumps({"description": f"Some B2B enterprise product {i}", "url": f"https://noisy{i}.com"}),
                )
                signal_ids.append(sid)

            # Label all 15 as FP
            for sid in signal_ids:
                label_signal_manual(conn, signal_id=sid, label="FP", created_by="tester", reason="Not consumer")

            # Run pattern detection with low thresholds to ensure match
            config = PatternConfig(days=30, min_count=5, fp_rate_threshold=0.50)
            patterns = detect_patterns(conn, config=config)

            # There should be at least one source_api_fp_rate pattern for noisy_collector
            source_patterns = [p for p in patterns if p["type"] == "source_api_fp_rate"]
            assert len(source_patterns) >= 1

            noisy_pattern = [p for p in source_patterns if p["source_api"] == "noisy_collector"]
            assert len(noisy_pattern) == 1
            assert noisy_pattern[0]["fp"] == 15
            assert noisy_pattern[0]["fp_rate"] == 1.0


class TestFlywheelTuningProposal:
    """Generate a tuning proposal from a list of detected patterns."""

    def test_flywheel_tuning_proposal(self, tmp_path):
        """Create patterns list, generate a YAML tuning proposal, verify the file is written."""
        # Create a temporary negative policy file
        tmp_policy_path = tmp_path / "neg_policy.yaml"
        tmp_policy_path.write_text(
            yaml.safe_dump({"negative_keywords": {}}, sort_keys=False),
            encoding="utf-8",
        )

        # Synthetic patterns (simulating what detect_patterns would return)
        patterns = [
            {
                "type": "source_api_fp_rate",
                "source_api": "noisy_collector",
                "fp": 20,
                "tp": 2,
                "unsure": 1,
                "fp_rate": 0.91,
                "window_days": 30,
                "recommendation": "Investigate source parsing.",
            },
            {
                "type": "duplicate_fp_description",
                "count": 12,
                "normalized_description": "an enterprise b2b saas platform for developers",
                "example_signal_ids": [1, 2, 3],
                "recommendation": "Add negative keyword.",
            },
        ]

        proposal_path = tmp_path / "proposal.yaml"

        proposal = generate_tuning_proposal(
            patterns=patterns,
            window_days=30,
            out_path=proposal_path,
            negative_policy_path=str(tmp_policy_path),
        )

        # Verify the file was written
        assert proposal_path.exists()

        # Verify YAML content is valid
        written = yaml.safe_load(proposal_path.read_text(encoding="utf-8"))
        assert written is not None
        assert "version" in written
        assert written["version"] == 1
        assert written["window_days"] == 30
        assert "actions" in written
        assert "notes" in written

        # The duplicate_fp_description pattern has "enterprise" and "b2b" in the
        # normalized description, so the tuning generator should propose those as
        # negative keyword additions.
        assert len(written["actions"]) >= 1
        action_keywords = {a["keyword"] for a in written["actions"]}
        # At least one of the known candidate keywords should be proposed
        assert action_keywords & {"enterprise", "b2b"}

        # There should be notes for both source_api and duplicate_fp patterns
        assert len(written["notes"]) >= 2

        # Verify the returned dict matches the written file
        assert proposal["version"] == written["version"]
        assert proposal["window_days"] == written["window_days"]


class TestFlywheelExportCsv:
    """Insert signals, label them, export to CSV, and verify row count."""

    def test_flywheel_export_csv(self, quality_db, tmp_path):
        """Insert 4 signals, label them, export CSV, verify 4 data rows."""
        db_path, store = quality_db

        with quality_conn(db_path) as conn:
            sids = []
            labels = ["TP", "FP", "TP", "UNSURE"]
            for i, lbl in enumerate(labels):
                sid = _insert_signal(
                    conn,
                    source_api="csv_test_source",
                    canonical_key=f"domain:csvtest{i}.com",
                    company_name=f"CSV Test Co {i}",
                    detected_at=_utc_iso(1),
                    raw_data=json.dumps({"description": f"Export test company {i}", "url": f"https://csvtest{i}.com"}),
                )
                sids.append(sid)
                label_signal_manual(conn, signal_id=sid, label=lbl, created_by="tester", reason=f"Test label {lbl}")

            csv_path = tmp_path / "export.csv"
            row_count = export_dataset_csv(conn, out_path=csv_path, days=30)

            assert row_count == 4
            assert csv_path.exists()

            # Verify actual CSV content
            with csv_path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            assert len(rows) == 4

            # Verify expected fields are present in each row
            for row in rows:
                assert "signal_id" in row
                assert "human_label" in row
                assert "source_api" in row
                assert row["source_api"] == "csv_test_source"

            # Check label distribution
            exported_labels = sorted(r["human_label"] for r in rows)
            assert exported_labels == sorted(["FP", "TP", "TP", "UNSURE"])


class TestFkConstraintEnforcement:
    """Verify FK constraints prevent orphan rows in quality_feedback."""

    def test_fk_constraint_enforcement(self, quality_db):
        """Inserting quality_feedback with a nonexistent signal_id must raise IntegrityError."""
        db_path, store = quality_db

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        try:
            # Confirm FK constraints are actually on
            fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()
            assert fk_status[0] == 1, "FK constraints are not enabled"

            # Use a signal_id that does not exist in the signals table
            bad_signal_id = 999999

            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO quality_feedback (signal_id, label, reason, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (bad_signal_id, "FP", "test reason", "tester", _utc_iso(0)),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
