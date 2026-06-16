"""Tests for the learning-loop-only workflow surface and providers."""

from __future__ import annotations

import argparse
import json
import sqlite3
from unittest.mock import patch
from pathlib import Path

import pytest

from ops.quality.labels import label_signal_manual, list_adj_review_candidates
from ops.quality.stats import build_router_diagnostic_summary
from ops.quality.thesis import list_disagreement_candidates, store_thesis_classification
from ops.quality_cli import (
    _build_review_set_payload,
    _cmd_learning_loop_apply_labels,
    _cmd_learning_loop_rerun_diagnostic,
    _cmd_learning_loop_review_set,
    _validate_apply_labels_payload,
    _validate_review_set_payload,
    register_quality_commands,
)
from tests.ops.quality.conftest import _insert_signal, _utc_iso


def _parser():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    register_quality_commands(subs)
    return parser


def _store_classification(
    conn: sqlite3.Connection,
    signal_id: int,
    canonical_key: str,
    *,
    keyword_score: float,
    thesis_fit_score: float,
    thesis_match: bool,
    classified_at: str | None = None,
) -> int:
    return store_thesis_classification(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        keyword_score=keyword_score,
        keyword_category="consumer_cpg",
        negative_keywords=[],
        thesis_match=thesis_match,
        thesis_fit_score=thesis_fit_score,
        category="consumer_cpg",
        stage_estimate="Seed",
        confidence="high",
        rationale="test",
        key_signals=["consumer"],
        prompt_version="test-v1",
        model="test-model",
        input_tokens=10,
        output_tokens=10,
        latency_ms=10,
        classified_at=classified_at,
    )


class TestLearningLoopCliParsing:
    def test_review_set_args(self):
        args = _parser().parse_args(
            ["quality", "--db", "test.db", "learning-loop", "review-set", "--out-json", "out.json"]
        )
        assert args.command == "quality"
        assert args.quality_cmd == "learning-loop"
        assert args.learning_loop_cmd == "review-set"
        assert args.out_json == "out.json"

    def test_apply_labels_args(self):
        args = _parser().parse_args(
            ["quality", "--db", "test.db", "learning-loop", "apply-labels", "--in-json", "in.json"]
        )
        assert args.learning_loop_cmd == "apply-labels"
        assert args.in_json == "in.json"

    def test_rerun_diagnostic_args(self):
        args = _parser().parse_args(
            ["quality", "--db", "test.db", "learning-loop", "rerun-diagnostic", "--out-dir", "diag"]
        )
        assert args.learning_loop_cmd == "rerun-diagnostic"
        assert args.out_dir == "diag"


class TestProviders:
    def test_router_diagnostic_summary_uses_frozen_quality_stats_shape(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)

        summary = build_router_diagnostic_summary(conn, db_path=db_path, days=90)
        assert set(summary["quality_stats"].keys()) == {"labeled", "decided", "tp", "fp", "unsure", "adj", "fp_rate"}
        assert isinstance(summary["quality_stats"]["labeled"], int)
        assert isinstance(summary["quality_stats"]["decided"], int)
        assert isinstance(summary["quality_stats"]["fp_rate"], float)
        conn.close()

    def test_disagreement_provider_returns_structured_rows(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:kwfp.com", detected_at=_utc_iso(1))
        _store_classification(
            conn,
            sid_fp,
            "domain:kwfp.com",
            keyword_score=0.85,
            thesis_fit_score=0.10,
            thesis_match=False,
        )
        sid_fn = _insert_signal(conn, source_api="github", canonical_key="domain:kwfn.com", detected_at=_utc_iso(2))
        _store_classification(
            conn,
            sid_fn,
            "domain:kwfn.com",
            keyword_score=0.10,
            thesis_fit_score=0.85,
            thesis_match=True,
        )

        rows = list_disagreement_candidates(conn, days=30, limit=10)
        assert [r.reason_code for r in rows] == ["kw_high_llm_low", "kw_low_llm_high"]
        assert all(r.queue_type == "disagreement" for r in rows)
        conn.close()

    def test_adj_provider_returns_structured_rows(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid = _insert_signal(conn, source_api="github", canonical_key="domain:adj.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid, label="ADJ", created_by="analyst", reason="adjacent")

        rows = list_adj_review_candidates(conn, days=90, limit=10)
        assert len(rows) == 1
        assert rows[0].queue_type == "adj"
        assert rows[0].reason_code == "adj_followup"
        conn.close()

    def test_router_diagnostic_summary_computable(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)

        summary = build_router_diagnostic_summary(conn, db_path=db_path, days=90)
        assert summary["branch_recommendation"]["name"] == "no_routing_problem_detected"
        assert summary["join_coverage"]["latest_row_mismatches"] == 0
        assert summary["discrimination"]["score_max"] == pytest.approx(0.9)
        conn.close()

    def test_router_diagnostic_summary_fails_closed_on_latest_mismatch(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(
            conn,
            sid_tp,
            "domain:tp.com",
            keyword_score=0.8,
            thesis_fit_score=0.9,
            thesis_match=True,
            classified_at="2099-01-01T00:00:00+00:00",
        )
        _store_classification(
            conn,
            sid_tp,
            "domain:tp.com",
            keyword_score=0.7,
            thesis_fit_score=0.8,
            thesis_match=True,
            classified_at="2000-01-01T00:00:00+00:00",
        )
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)

        summary = build_router_diagnostic_summary(conn, db_path=db_path, days=90)
        assert summary["branch_recommendation"]["name"] == "diagnostic_cannot_be_computed"
        assert summary["join_coverage"]["latest_row_mismatches"] > 0
        conn.close()

    def test_router_diagnostic_summary_handles_equal_timestamp_tie(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")

        tie_ts = "2026-04-06T00:00:00+00:00"
        _store_classification(
            conn,
            sid_tp,
            "domain:tp.com",
            keyword_score=0.75,
            thesis_fit_score=0.85,
            thesis_match=True,
            classified_at=tie_ts,
        )
        _store_classification(
            conn,
            sid_tp,
            "domain:tp.com",
            keyword_score=0.80,
            thesis_fit_score=0.90,
            thesis_match=True,
            classified_at=tie_ts,
        )
        _store_classification(
            conn,
            sid_fp,
            "domain:fp.com",
            keyword_score=0.2,
            thesis_fit_score=0.1,
            thesis_match=False,
            classified_at=tie_ts,
        )

        summary = build_router_diagnostic_summary(conn, db_path=db_path, days=90)
        assert summary["join_coverage"]["latest_row_mismatches"] == 0
        assert summary["branch_recommendation"]["name"] == "no_routing_problem_detected"
        conn.close()

    def test_router_diagnostic_summary_ignores_out_of_window_mismatch(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)

        sid_old = _insert_signal(conn, source_api="news_api", canonical_key="domain:old.com", detected_at=_utc_iso(365))
        _store_classification(
            conn,
            sid_old,
            "domain:old.com",
            keyword_score=0.7,
            thesis_fit_score=0.8,
            thesis_match=True,
            classified_at="2099-01-01T00:00:00+00:00",
        )
        _store_classification(
            conn,
            sid_old,
            "domain:old.com",
            keyword_score=0.6,
            thesis_fit_score=0.7,
            thesis_match=True,
            classified_at="2000-01-01T00:00:00+00:00",
        )

        summary = build_router_diagnostic_summary(conn, db_path=db_path, days=90)
        assert summary["join_coverage"]["latest_row_mismatches"] == 0
        assert summary["branch_recommendation"]["name"] == "no_routing_problem_detected"
        conn.close()

    def test_router_diagnostic_summary_fails_closed_on_missing_latest_thesis_row(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)

        summary = build_router_diagnostic_summary(conn, db_path=db_path, days=90)
        assert summary["join_coverage"]["decisive_joined_rows"] == 1
        assert summary["quality_stats"]["decided"] == 2
        assert summary["branch_recommendation"]["name"] == "diagnostic_cannot_be_computed"
        conn.close()


class TestLearningLoopWorkflow:
    def test_review_set_payload_and_validation(self, quality_db):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:kwfp.com", detected_at=_utc_iso(1))
        _store_classification(conn, sid_fp, "domain:kwfp.com", keyword_score=0.85, thesis_fit_score=0.1, thesis_match=False)
        sid_adj = _insert_signal(conn, source_api="github", canonical_key="domain:adj.com", detected_at=_utc_iso(2))
        label_signal_manual(conn, signal_id=sid_adj, label="ADJ", created_by="analyst", reason="adj")

        payload = _build_review_set_payload(
            db_path=db_path,
            disagreement_candidates=list_disagreement_candidates(conn, days=30, limit=10),
            adj_candidates=list_adj_review_candidates(conn, days=90, limit=10),
            window_days=90,
        )
        _validate_review_set_payload(payload)
        assert payload["schema_version"] == "learning_loop_review_set.v1"
        assert payload["items"][0]["queue_type"] == "adj"
        conn.close()

    def test_review_set_validation_handles_same_bucket_desc_order(self):
        payload = {
            "schema_version": "learning_loop_review_set.v1",
            "generated_at": _utc_iso(0),
            "db_path": "signals.db",
            "window_days": 90,
            "sort_key": ["queue_type", "priority_rank", "detected_at", "signal_id"],
            "items": [
                {
                    "signal_id": 20,
                    "queue_type": "disagreement",
                    "canonical_key": "domain:b.com",
                    "company_name": "B",
                    "source_api": "github",
                    "detected_at": "2026-04-06T10:00:00+00:00",
                    "priority_rank": 10,
                    "reason_code": "kw_high_llm_low",
                    "reason_summary": "b",
                },
                {
                    "signal_id": 10,
                    "queue_type": "disagreement",
                    "canonical_key": "domain:a.com",
                    "company_name": "A",
                    "source_api": "github",
                    "detected_at": "2026-04-05T10:00:00+00:00",
                    "priority_rank": 10,
                    "reason_code": "kw_high_llm_low",
                    "reason_summary": "a",
                },
            ],
        }

        _validate_review_set_payload(payload)

    def test_apply_labels_payload_validation_rejects_duplicates(self):
        payload = {
            "schema_version": "learning_loop_apply_labels.v1",
            "requested_by": "tester",
            "requested_at": _utc_iso(0),
            "sort_key": ["signal_id"],
            "items": [
                {"signal_id": 1, "label": "TP", "created_by": "tester", "reason": "a"},
                {"signal_id": 1, "label": "FP", "created_by": "tester", "reason": "b"},
            ],
        }
        with pytest.raises(ValueError, match="duplicate"):
            _validate_apply_labels_payload(payload)

    def test_review_set_validation_rejects_invalid_schema_version(self):
        payload = {
            "schema_version": "wrong.v1",
            "generated_at": _utc_iso(0),
            "db_path": "signals.db",
            "window_days": 90,
            "sort_key": ["queue_type", "priority_rank", "detected_at", "signal_id"],
            "items": [],
        }
        with pytest.raises(ValueError, match="schema_version"):
            _validate_review_set_payload(payload)

    def test_apply_labels_validation_rejects_invalid_schema_version(self):
        payload = {
            "schema_version": "wrong.v1",
            "requested_by": "tester",
            "requested_at": _utc_iso(0),
            "sort_key": ["signal_id"],
            "items": [],
        }
        with pytest.raises(ValueError, match="schema_version"):
            _validate_apply_labels_payload(payload)

    def test_apply_labels_command_uses_manual_path(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        sid = _insert_signal(conn, source_api="github", canonical_key="domain:apply.com", detected_at=_utc_iso(1))
        conn.close()

        in_path = tmp_path / "apply.json"
        in_path.write_text(
            json.dumps(
                {
                    "schema_version": "learning_loop_apply_labels.v1",
                    "requested_by": "tester",
                    "requested_at": _utc_iso(0),
                    "sort_key": ["signal_id"],
                    "items": [
                        {
                            "signal_id": sid,
                            "label": "TP",
                            "created_by": "tester",
                            "reason": "fits thesis",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        args = argparse.Namespace(db_path=db_path, in_json=str(in_path))
        _cmd_learning_loop_apply_labels(args)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        feedback = conn.execute("SELECT COUNT(*) AS c FROM quality_feedback WHERE signal_id = ?", (sid,)).fetchone()["c"]
        resolved = conn.execute("SELECT human_label FROM signal_quality_metrics WHERE signal_id = ?", (sid,)).fetchone()
        assert feedback == 1
        assert resolved["human_label"] == "TP"
        conn.close()

    def test_review_set_command_writes_json_and_markdown(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:kwfp.com", detected_at=_utc_iso(1))
        _store_classification(conn, sid_fp, "domain:kwfp.com", keyword_score=0.85, thesis_fit_score=0.1, thesis_match=False)
        sid_adj = _insert_signal(conn, source_api="github", canonical_key="domain:adj.com", detected_at=_utc_iso(2))
        label_signal_manual(conn, signal_id=sid_adj, label="ADJ", created_by="analyst", reason="adj")
        conn.close()

        out_json = tmp_path / "review-set.json"
        out_md = tmp_path / "review-set.md"
        args = argparse.Namespace(db_path=db_path, days=30, adj_days=90, limit=200, out_json=str(out_json), out_md=str(out_md))
        _cmd_learning_loop_review_set(args)

        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "learning_loop_review_set.v1"
        assert out_md.exists()

    def test_rerun_diagnostic_command_writes_artifacts(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)
        conn.close()

        out_dir = tmp_path / "diag"
        args = argparse.Namespace(db_path=db_path, days=90, out_dir=str(out_dir))
        _cmd_learning_loop_rerun_diagnostic(args)

        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["branch_recommendation"]["name"] == "no_routing_problem_detected"
        assert (out_dir / "summary.md").exists()

    def test_rerun_diagnostic_requires_90_day_window(self, quality_db, tmp_path):
        db_path, _store = quality_db
        out_dir = tmp_path / "diag"
        args = argparse.Namespace(
            db_path=db_path,
            days=30,
            out_dir=str(out_dir),
            model="gemini-3.5-flash",
            prompt_version="quality-ops-v1",
        )
        with pytest.raises(ValueError, match="90-day parity window"):
            _cmd_learning_loop_rerun_diagnostic(args)

    def test_rerun_diagnostic_fails_closed_when_stale_refresh_fails(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1), created_at=_utc_iso(120))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1), created_at=_utc_iso(120))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        stale_id = _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)
        conn.execute(
            "UPDATE thesis_classifications SET model = NULL, prompt_version = NULL WHERE id = ?",
            (stale_id,),
        )
        conn.commit()
        conn.close()

        out_dir = tmp_path / "diag-stale"
        args = argparse.Namespace(
            db_path=db_path,
            days=90,
            out_dir=str(out_dir),
            model="gemini-3.5-flash",
            prompt_version="quality-ops-v1",
        )
        with patch("ops.quality_cli.refresh_signal_ids_missing_provenance", return_value={"attempted": 1, "succeeded": 0, "failed": 1, "results": [], "errors": [{"signal_id": sid_tp, "error": "boom"}]}):
            _cmd_learning_loop_rerun_diagnostic(args)

        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["branch_recommendation"]["name"] == "diagnostic_cannot_be_computed"
        assert set(summary["quality_stats"].keys()) == {"labeled", "decided", "tp", "fp", "unsure", "adj", "fp_rate"}

    def test_rerun_diagnostic_fails_closed_when_more_than_200_stale(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        for i in range(201):
            sid = _insert_signal(
                conn,
                source_api="github",
                canonical_key=f"domain:stale-{i}.com",
                detected_at=_utc_iso(1),
                created_at=_utc_iso(120),
            )
            label_signal_manual(conn, signal_id=sid, label="FP", created_by="analyst", reason="fp")
            tc_id = _store_classification(
                conn,
                sid,
                f"domain:stale-{i}.com",
                keyword_score=0.2,
                thesis_fit_score=0.1,
                thesis_match=False,
            )
            conn.execute(
                "UPDATE thesis_classifications SET model = NULL, prompt_version = NULL WHERE id = ?",
                (tc_id,),
            )
        conn.commit()
        conn.close()

        out_dir = tmp_path / "diag-too-many-stale"
        args = argparse.Namespace(
            db_path=db_path,
            days=90,
            out_dir=str(out_dir),
            model="gemini-3.5-flash",
            prompt_version="quality-ops-v1",
        )
        _cmd_learning_loop_rerun_diagnostic(args)

        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["branch_recommendation"]["name"] == "diagnostic_cannot_be_computed"

    def test_rerun_diagnostic_output_preserves_frozen_quality_stats_shape(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        _store_classification(conn, sid_fp, "domain:fp.com", keyword_score=0.2, thesis_fit_score=0.1, thesis_match=False)
        conn.close()

        out_dir = tmp_path / "diag-frozen"
        args = argparse.Namespace(
            db_path=db_path,
            days=90,
            out_dir=str(out_dir),
            model="gemini-3.5-flash",
            prompt_version="quality-ops-v1",
        )
        _cmd_learning_loop_rerun_diagnostic(args)

        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert set(summary["quality_stats"].keys()) == {"labeled", "decided", "tp", "fp", "unsure", "adj", "fp_rate"}
        assert isinstance(summary["quality_stats"]["labeled"], int)
        assert isinstance(summary["quality_stats"]["decided"], int)
        assert isinstance(summary["quality_stats"]["fp_rate"], float)

    def test_rerun_diagnostic_fails_closed_when_missing_latest_thesis_refresh_fails(self, quality_db, tmp_path):
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        sid_tp = _insert_signal(conn, source_api="github", canonical_key="domain:tp.com", detected_at=_utc_iso(1))
        sid_fp = _insert_signal(conn, source_api="rss_feeds", canonical_key="domain:fp.com", detected_at=_utc_iso(1))
        label_signal_manual(conn, signal_id=sid_tp, label="TP", created_by="analyst", reason="tp")
        label_signal_manual(conn, signal_id=sid_fp, label="FP", created_by="analyst", reason="fp")
        _store_classification(conn, sid_tp, "domain:tp.com", keyword_score=0.8, thesis_fit_score=0.9, thesis_match=True)
        conn.close()

        out_dir = tmp_path / "diag-missing-row"
        args = argparse.Namespace(
            db_path=db_path,
            days=90,
            out_dir=str(out_dir),
            model="gemini-3.5-flash",
            prompt_version="quality-ops-v1",
        )
        with patch("ops.quality_cli.refresh_signal_ids_missing_provenance", return_value={"attempted": 1, "succeeded": 0, "failed": 1, "results": [], "errors": [{"signal_id": sid_fp, "error": "boom"}]}):
            _cmd_learning_loop_rerun_diagnostic(args)

        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["branch_recommendation"]["name"] == "diagnostic_cannot_be_computed"
