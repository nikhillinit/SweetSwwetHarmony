"""Vectorizer metadata, versioning, and retrain trigger for TF-IDF corpus.

Tracks vectorizer lifecycle so that precedents/exemplars tables stay in sync
with the trained vocabulary. Used by build scripts and retrieval modules.

Phase 3 — case-law + exemplars.
"""

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Optional

VECTORIZER_DIR = os.environ.get("VECTORIZER_DIR", "models/vectorizers")


@dataclass
class VectorizerMetadata:
    """Tracks vectorizer version for invalidation and audit."""

    version: str  # e.g. "v1.0.0"
    trained_at: str  # ISO 8601
    corpus_size: int  # Number of labeled signals used
    corpus_labels: dict  # {"TP": 7, "FP": 23, "UNSURE": 1}
    vocab_size: int  # TF-IDF vocabulary size
    vectorizer_hash: str  # SHA-256 of serialized vectorizer
    min_df: int = 1
    max_features: int = 3000
    ngram_range: tuple = (1, 2)

    def should_retrain(self, current_corpus_size: int) -> bool:
        """Retrain if corpus has more than doubled."""
        return current_corpus_size > self.corpus_size * 2

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "trained_at": self.trained_at,
            "corpus_size": self.corpus_size,
            "corpus_labels": self.corpus_labels,
            "vocab_size": self.vocab_size,
            "vectorizer_hash": self.vectorizer_hash,
            "min_df": self.min_df,
            "max_features": self.max_features,
            "ngram_range": list(self.ngram_range),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VectorizerMetadata":
        ngram = d.get("ngram_range", [1, 2])
        return cls(
            version=d["version"],
            trained_at=d["trained_at"],
            corpus_size=d["corpus_size"],
            corpus_labels=d["corpus_labels"],
            vocab_size=d["vocab_size"],
            vectorizer_hash=d["vectorizer_hash"],
            min_df=d.get("min_df", 1),
            max_features=d.get("max_features", 3000),
            ngram_range=tuple(ngram),
        )


def save_metadata(meta: VectorizerMetadata, path: str) -> None:
    """Save metadata to JSON file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(meta.to_dict(), f, indent=2)


def load_metadata(path: str) -> Optional[VectorizerMetadata]:
    """Load metadata from JSON file. Returns None if file doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return VectorizerMetadata.from_dict(json.load(f))


def load_latest_metadata(directory: str) -> Optional[VectorizerMetadata]:
    """Find and load the most recent metadata file in a directory.

    Looks for files matching *_meta.json, sorted by modification time.
    """
    pattern = os.path.join(directory, "*_meta.json")
    files = glob.glob(pattern)
    if not files:
        return None
    latest = max(files, key=os.path.getmtime)
    return load_metadata(latest)


def count_labeled_signals(db_path: str) -> int:
    """Count labeled signals (TP + FP) in the database.

    Uses synchronous sqlite3 for standalone CLI use.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM signal_quality_metrics WHERE human_label IN ('TP', 'FP')"
        )
        return cursor.fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


def check_retrain_needed(db_path: str, vectorizer_dir: Optional[str] = None) -> tuple[bool, str]:
    """Check if vectorizer needs retraining.

    Returns (needs_retrain: bool, reason: str).

    Triggers retrain if:
    1. No vectorizer exists (first build)
    2. Current corpus size > 2x the trained corpus size
    """
    vdir = vectorizer_dir or VECTORIZER_DIR
    metadata = load_latest_metadata(vdir)

    if metadata is None:
        return True, "No vectorizer found (first build needed)"

    current_size = count_labeled_signals(db_path)
    if metadata.should_retrain(current_size):
        return True, (
            f"Corpus grew from {metadata.corpus_size} to {current_size} "
            f"(>{metadata.corpus_size * 2} threshold)"
        )

    return False, (
        f"No retrain needed (corpus: {metadata.corpus_size} -> {current_size}, "
        f"threshold: {metadata.corpus_size * 2})"
    )
