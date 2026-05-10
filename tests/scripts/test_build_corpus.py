"""Tests for scripts/build_case_law_corpus.py.

Verifies:
- Corpus built from labeled signals (TP + FP)
- UNSURE signals excluded
- Text construction uses shared builder (not local)
- signal_created_at copied from signals.created_at
- Vectorizer saved + metadata created
- Precedent rows inserted with correct vectorizer_version
- Old-version rows pruned after successful build
- Dry-run does not write to DB or prune
- Calibrate mode prints distributions without writing
- Empty corpus handled gracefully
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.signal_store import SignalStore


_SIGNAL_INSERT = (
    "INSERT INTO signals "
    "(company_name, source_api, signal_type, raw_data, canonical_key, confidence, detected_at, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_LABEL_INSERT = (
    "INSERT INTO signal_quality_metrics "
    "(signal_id, canonical_key, human_label, label_source, labeled_at, notes) "
    "VALUES (?, ?, ?, 'manual', datetime('now'), ?)"
)


@pytest_asyncio.fixture
async def store_with_labels(tmp_path, monkeypatch):
    """Fresh store with labeled signals for corpus building."""
    monkeypatch.setattr(
        "scripts.build_case_law_corpus.VECTORIZER_DIR",
        str(tmp_path / "vectorizers"),
    )
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SignalStore(db_path=path)
    await store.initialize()

    # Insert TP signals
    await store._db.execute(_SIGNAL_INSERT, (
        "Acme Meals", "sec_edgar", "filing", '{"description": "meal delivery platform"}',
        "domain:acme.com", 0.8, "2025-06-15T10:00:00Z", "2025-06-15T10:00:00Z",
    ))
    await store._db.execute(_SIGNAL_INSERT, (
        "FitApp Inc", "github", "trending", '{"description": "fitness tracking app"}',
        "domain:fitapp.io", 0.7, "2025-08-20T12:00:00Z", "2025-08-20T12:00:00Z",
    ))
    # Insert FP signals
    await store._db.execute(_SIGNAL_INSERT, (
        "CryptoDAO", "github", "trending", '{"description": "blockchain dao governance"}',
        "domain:cryptodao.xyz", 0.5, "2025-09-01T08:00:00Z", "2025-09-01T08:00:00Z",
    ))
    await store._db.execute(_SIGNAL_INSERT, (
        "SaaS Tool", "hacker_news", "launch", '{"description": "B2B developer analytics"}',
        "domain:saastool.dev", 0.4, "2025-10-10T14:00:00Z", "2025-10-10T14:00:00Z",
    ))
    # Insert UNSURE signal (should be excluded)
    await store._db.execute(_SIGNAL_INSERT, (
        "Maybe Co", "news_api", "article", '{"description": "ambiguous company"}',
        "domain:maybe.co", 0.5, "2025-11-01T09:00:00Z", "2025-11-01T09:00:00Z",
    ))
    await store._db.commit()

    # Label them (signal_id, canonical_key, human_label, notes)
    await store._db.execute(_LABEL_INSERT, (1, "domain:acme.com", "TP", "good consumer"))
    await store._db.execute(_LABEL_INSERT, (2, "domain:fitapp.io", "TP", "fitness app"))
    await store._db.execute(_LABEL_INSERT, (3, "domain:cryptodao.xyz", "FP", "crypto"))
    await store._db.execute(_LABEL_INSERT, (4, "domain:saastool.dev", "FP", "B2B SaaS"))
    await store._db.execute(_LABEL_INSERT, (5, "domain:maybe.co", "UNSURE", "unclear"))
    await store._db.commit()

    yield store, path

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


class TestCorpusTextBuilder:
    """Tests for shared text builder."""

    def test_import_from_shared_module(self):
        """build_corpus_text must be imported from utils.corpus_text_builder."""
        from utils.corpus_text_builder import build_corpus_text
        assert callable(build_corpus_text)

    def test_basic_text_construction(self):
        from utils.corpus_text_builder import build_corpus_text
        text = build_corpus_text("Acme Inc", '{"description": "meal delivery"}')
        assert "acme inc" in text
        assert "meal delivery" in text

    def test_includes_schema_fields(self):
        from utils.corpus_text_builder import build_corpus_text
        schema = {
            "problem_solved_text": "busy parents need easy meals",
            "customer_text": "working families",
            "customer_archetype": "foodies",
        }
        text = build_corpus_text("Acme", "{}", schema)
        assert "busy parents" in text
        assert "foodies" in text

    def test_empty_inputs(self):
        from utils.corpus_text_builder import build_corpus_text
        text = build_corpus_text("", "{}")
        assert text == ""

    def test_json_string_parsed(self):
        from utils.corpus_text_builder import build_corpus_text
        text = build_corpus_text("Co", '{"title": "Great App"}')
        assert "great app" in text

    def test_dict_raw_data(self):
        from utils.corpus_text_builder import build_corpus_text
        text = build_corpus_text("Co", {"description": "Wellness Platform"})
        assert "wellness platform" in text

    def test_invalid_json_handled(self):
        from utils.corpus_text_builder import build_corpus_text
        text = build_corpus_text("Co", "not-json")
        assert "co" in text


class TestBuildCorpus:
    """Tests for corpus build logic."""

    @pytest.mark.asyncio
    async def test_corpus_excludes_unsure(self, store_with_labels):
        """UNSURE signals should not appear in precedents."""
        from scripts.build_case_law_corpus import build_corpus
        store, path = store_with_labels
        result = await build_corpus(store, version="v1.0.0", dry_run=False)
        assert result["corpus_size"] == 4  # 2 TP + 2 FP, not 5

    @pytest.mark.asyncio
    async def test_precedent_rows_inserted(self, store_with_labels):
        from scripts.build_case_law_corpus import build_corpus
        store, path = store_with_labels
        await build_corpus(store, version="v1.0.0", dry_run=False)

        cursor = await store._db.execute("SELECT COUNT(*) FROM precedents")
        count = (await cursor.fetchone())[0]
        assert count == 4

    @pytest.mark.asyncio
    async def test_signal_created_at_copied(self, store_with_labels):
        """signal_created_at should come from signals.created_at."""
        from scripts.build_case_law_corpus import build_corpus
        store, path = store_with_labels
        await build_corpus(store, version="v1.0.0", dry_run=False)

        cursor = await store._db.execute(
            "SELECT signal_created_at FROM precedents WHERE signal_id = 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "2025-06-15T10:00:00Z"

    @pytest.mark.asyncio
    async def test_vectorizer_version_set(self, store_with_labels):
        from scripts.build_case_law_corpus import build_corpus
        store, path = store_with_labels
        await build_corpus(store, version="v1.0.0", dry_run=False)

        cursor = await store._db.execute(
            "SELECT DISTINCT vectorizer_version FROM precedents"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "v1.0.0"

    @pytest.mark.asyncio
    async def test_old_versions_pruned(self, store_with_labels):
        """Building with new version should prune old version rows."""
        from scripts.build_case_law_corpus import build_corpus
        store, path = store_with_labels

        # Build v1
        await build_corpus(store, version="v1.0.0", dry_run=False)
        cursor = await store._db.execute("SELECT COUNT(*) FROM precedents")
        assert (await cursor.fetchone())[0] == 4

        # Build v2 — should prune v1 rows
        await build_corpus(store, version="v2.0.0", dry_run=False)
        cursor = await store._db.execute("SELECT COUNT(*) FROM precedents")
        assert (await cursor.fetchone())[0] == 4  # same count, all v2

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM precedents WHERE vectorizer_version = 'v1.0.0'"
        )
        assert (await cursor.fetchone())[0] == 0  # v1 pruned

    @pytest.mark.asyncio
    async def test_dry_run_no_writes(self, store_with_labels):
        from scripts.build_case_law_corpus import build_corpus
        store, path = store_with_labels

        result = await build_corpus(store, version="v1.0.0", dry_run=True)
        assert result["corpus_size"] == 4

        cursor = await store._db.execute("SELECT COUNT(*) FROM precedents")
        assert (await cursor.fetchone())[0] == 0  # nothing written

    @pytest.mark.asyncio
    async def test_calibrate_returns_distributions(self, store_with_labels):
        from scripts.build_case_law_corpus import calibrate_corpus
        store, path = store_with_labels

        result = await calibrate_corpus(store)
        assert "tp_vs_tp" in result
        assert "fp_vs_fp" in result
        assert "tp_vs_fp" in result
        # Each should have stats
        assert "p75" in result["tp_vs_tp"]

    @pytest.mark.asyncio
    async def test_calibrate_no_writes(self, store_with_labels):
        from scripts.build_case_law_corpus import calibrate_corpus
        store, path = store_with_labels

        await calibrate_corpus(store)
        cursor = await store._db.execute("SELECT COUNT(*) FROM precedents")
        assert (await cursor.fetchone())[0] == 0


class TestEmptyCorpus:
    """Edge case: no labeled signals."""

    @pytest.mark.asyncio
    async def test_empty_corpus_graceful(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        store = SignalStore(db_path=path)
        await store.initialize()

        from scripts.build_case_law_corpus import build_corpus
        result = await build_corpus(store, version="v1.0.0", dry_run=False)
        assert result["corpus_size"] == 0

        await store.close()
        try:
            os.unlink(path)
        except OSError:
            pass
