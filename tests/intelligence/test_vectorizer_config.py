"""Tests for intelligence/vectorizer_config.py.

Verifies:
- VectorizerMetadata serialization round-trip (to_dict / from_dict)
- should_retrain() triggers at 2x corpus size
- should_retrain() does not trigger below threshold
- save_metadata / load_metadata round-trip to JSON file
- load_latest_metadata finds most recent metadata file
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from intelligence.vectorizer_config import (
    VectorizerMetadata,
    save_metadata,
    load_metadata,
    load_latest_metadata,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_metadata(**overrides) -> VectorizerMetadata:
    """Create VectorizerMetadata with sensible defaults."""
    defaults = dict(
        version="v1.0.0",
        trained_at="2026-02-09T12:00:00Z",
        corpus_size=31,
        corpus_labels={"TP": 7, "FP": 23, "UNSURE": 1},
        vocab_size=1200,
        vectorizer_hash="abc123def456",
        min_df=1,
        max_features=3000,
        ngram_range=(1, 2),
    )
    defaults.update(overrides)
    return VectorizerMetadata(**defaults)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    """VectorizerMetadata dict round-trip."""

    def test_to_dict_contains_all_fields(self):
        meta = _make_metadata()
        d = meta.to_dict()
        assert d["version"] == "v1.0.0"
        assert d["corpus_size"] == 31
        assert d["corpus_labels"] == {"TP": 7, "FP": 23, "UNSURE": 1}
        assert d["vocab_size"] == 1200
        assert d["vectorizer_hash"] == "abc123def456"
        assert d["min_df"] == 1
        assert d["max_features"] == 3000
        assert d["ngram_range"] == [1, 2]  # tuple → list in JSON

    def test_from_dict_round_trip(self):
        original = _make_metadata()
        rebuilt = VectorizerMetadata.from_dict(original.to_dict())
        assert rebuilt.version == original.version
        assert rebuilt.corpus_size == original.corpus_size
        assert rebuilt.corpus_labels == original.corpus_labels
        assert rebuilt.ngram_range == original.ngram_range

    def test_from_dict_converts_ngram_list_to_tuple(self):
        d = _make_metadata().to_dict()
        d["ngram_range"] = [1, 3]
        meta = VectorizerMetadata.from_dict(d)
        assert meta.ngram_range == (1, 3)
        assert isinstance(meta.ngram_range, tuple)


# ---------------------------------------------------------------------------
# Retrain trigger
# ---------------------------------------------------------------------------

class TestShouldRetrain:
    """should_retrain() logic: corpus > 2x → retrain."""

    def test_retrain_at_double_corpus(self):
        meta = _make_metadata(corpus_size=31)
        assert meta.should_retrain(63) is True  # 63 > 62

    def test_no_retrain_at_exact_double(self):
        meta = _make_metadata(corpus_size=31)
        assert meta.should_retrain(62) is False  # 62 == 31*2, not >

    def test_no_retrain_below_threshold(self):
        meta = _make_metadata(corpus_size=31)
        assert meta.should_retrain(45) is False

    def test_retrain_with_large_growth(self):
        meta = _make_metadata(corpus_size=10)
        assert meta.should_retrain(100) is True

    def test_no_retrain_same_size(self):
        meta = _make_metadata(corpus_size=31)
        assert meta.should_retrain(31) is False


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

class TestFileIO:
    """save_metadata / load_metadata / load_latest_metadata."""

    def test_save_and_load_round_trip(self, tmp_path):
        meta = _make_metadata()
        path = str(tmp_path / "case_law_v1.0.0_meta.json")
        save_metadata(meta, path)

        loaded = load_metadata(path)
        assert loaded.version == meta.version
        assert loaded.corpus_size == meta.corpus_size
        assert loaded.ngram_range == meta.ngram_range

    def test_save_creates_file(self, tmp_path):
        meta = _make_metadata()
        path = str(tmp_path / "test_meta.json")
        save_metadata(meta, path)
        assert os.path.exists(path)

    def test_load_nonexistent_returns_none(self, tmp_path):
        result = load_metadata(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_load_latest_finds_newest(self, tmp_path):
        # Create two metadata files
        m1 = _make_metadata(version="v1.0.0", corpus_size=20)
        m2 = _make_metadata(version="v2.0.0", corpus_size=50)
        save_metadata(m1, str(tmp_path / "case_law_v1.0.0_meta.json"))
        save_metadata(m2, str(tmp_path / "case_law_v2.0.0_meta.json"))

        latest = load_latest_metadata(str(tmp_path))
        assert latest is not None
        assert latest.version == "v2.0.0"
        assert latest.corpus_size == 50

    def test_load_latest_empty_dir(self, tmp_path):
        result = load_latest_metadata(str(tmp_path))
        assert result is None

    def test_saved_file_is_valid_json(self, tmp_path):
        meta = _make_metadata()
        path = str(tmp_path / "meta.json")
        save_metadata(meta, path)

        with open(path) as f:
            data = json.load(f)
        assert data["version"] == "v1.0.0"
