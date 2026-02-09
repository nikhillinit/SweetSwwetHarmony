"""Tests for Task 3.13: Retrain trigger (corpus > 2x → auto-rebuild).

Verifies:
- No vectorizer → retrain needed
- Corpus doubled → retrain needed
- Corpus grew by 50% → retrain NOT needed
- check_retrain_needed returns correct reasons
"""

import json
import os
import sqlite3

import pytest

from intelligence.vectorizer_config import (
    VectorizerMetadata,
    check_retrain_needed,
    count_labeled_signals,
    save_metadata,
)


@pytest.fixture
def metadata_dir(tmp_path):
    """Create a temp directory for vectorizer metadata."""
    d = tmp_path / "vectorizers"
    d.mkdir()
    return str(d)


@pytest.fixture
def db_path(tmp_path):
    """Create a minimal test DB with signal_quality_metrics."""
    path = str(tmp_path / "test_retrain.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""
        CREATE TABLE signal_quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            human_label TEXT NOT NULL,
            canonical_key TEXT,
            label_source TEXT DEFAULT 'manual',
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()
    return path


def _insert_labels(db_path: str, tp: int = 0, fp: int = 0):
    """Insert labeled signals into the test DB."""
    conn = sqlite3.connect(db_path)
    for i in range(tp):
        conn.execute(
            "INSERT INTO signal_quality_metrics (signal_id, human_label) VALUES (?, 'TP')",
            (i + 1,),
        )
    for i in range(fp):
        conn.execute(
            "INSERT INTO signal_quality_metrics (signal_id, human_label) VALUES (?, 'FP')",
            (tp + i + 1,),
        )
    conn.commit()
    conn.close()


def _save_test_metadata(metadata_dir: str, corpus_size: int = 31, version: str = "v1.0.0"):
    """Save test metadata file."""
    meta = VectorizerMetadata(
        version=version,
        trained_at="2026-02-09T00:00:00Z",
        corpus_size=corpus_size,
        corpus_labels={"TP": 7, "FP": 24},
        vocab_size=1500,
        vectorizer_hash="test_hash_abc123",
    )
    path = os.path.join(metadata_dir, f"case_law_{version}_meta.json")
    save_metadata(meta, path)
    return meta


class TestRetrainTrigger:

    def test_no_vectorizer_needs_retrain(self, db_path, metadata_dir):
        """No vectorizer metadata → retrain needed."""
        needs, reason = check_retrain_needed(db_path, metadata_dir)
        assert needs is True
        assert "No vectorizer" in reason or "first build" in reason

    def test_corpus_doubled_needs_retrain(self, db_path, metadata_dir):
        """Corpus grew from 31 to 63+ (>2x) → retrain needed."""
        _save_test_metadata(metadata_dir, corpus_size=31)
        _insert_labels(db_path, tp=20, fp=43)  # 63 total > 31*2=62

        needs, reason = check_retrain_needed(db_path, metadata_dir)
        assert needs is True
        assert "grew from 31 to 63" in reason

    def test_corpus_50_percent_growth_no_retrain(self, db_path, metadata_dir):
        """Corpus grew by ~50% (31 → 47) → retrain NOT needed."""
        _save_test_metadata(metadata_dir, corpus_size=31)
        _insert_labels(db_path, tp=10, fp=37)  # 47 total < 31*2=62

        needs, reason = check_retrain_needed(db_path, metadata_dir)
        assert needs is False
        assert "No retrain needed" in reason

    def test_corpus_exactly_double_no_retrain(self, db_path, metadata_dir):
        """Corpus exactly doubled (31 → 62) → NOT triggered (> not >=)."""
        _save_test_metadata(metadata_dir, corpus_size=31)
        _insert_labels(db_path, tp=10, fp=52)  # 62 total = 31*2, NOT > 62

        needs, reason = check_retrain_needed(db_path, metadata_dir)
        assert needs is False

    def test_count_labeled_signals(self, db_path):
        """count_labeled_signals counts only TP + FP."""
        _insert_labels(db_path, tp=5, fp=10)
        # Also add an UNSURE which should NOT be counted
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO signal_quality_metrics (signal_id, human_label) VALUES (100, 'UNSURE')"
        )
        conn.commit()
        conn.close()

        count = count_labeled_signals(db_path)
        assert count == 15  # 5 TP + 10 FP, not 16

    def test_count_labeled_signals_empty_db(self, db_path):
        """count_labeled_signals returns 0 for empty DB."""
        count = count_labeled_signals(db_path)
        assert count == 0

    def test_should_retrain_method(self):
        """VectorizerMetadata.should_retrain() triggers at > 2x."""
        meta = VectorizerMetadata(
            version="v1.0.0",
            trained_at="2026-01-01",
            corpus_size=30,
            corpus_labels={"TP": 7, "FP": 23},
            vocab_size=1000,
            vectorizer_hash="abc",
        )
        assert meta.should_retrain(60) is False   # exactly 2x → no
        assert meta.should_retrain(61) is True    # > 2x → yes
        assert meta.should_retrain(30) is False   # same → no
        assert meta.should_retrain(45) is False   # 1.5x → no
