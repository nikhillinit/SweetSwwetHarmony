"""Dashboard helpers resolve default DB paths through the guarded resolver."""

from __future__ import annotations

import sys
import types


class _FakeEmbeddingStore:
    captured_db_paths: list[str] = []

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.captured_db_paths.append(db_path)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeEmbeddingGenerator:
    pass


class _FakeSimilarityEngine:
    def __init__(self, *, embedding_store, embedding_generator):
        self.embedding_store = embedding_store
        self.embedding_generator = embedding_generator

    async def find_similar(self, canonical_key: str, n: int):
        return [{"canonical_key": canonical_key, "limit": n}]


def _install_similarity_fakes(monkeypatch) -> None:
    _FakeEmbeddingStore.captured_db_paths.clear()

    embedding_store_module = types.ModuleType("storage.embedding_store")
    embedding_store_module.EmbeddingStore = _FakeEmbeddingStore
    monkeypatch.setitem(sys.modules, "storage.embedding_store", embedding_store_module)

    embedding_generator_module = types.ModuleType("utils.embedding_generator")
    embedding_generator_module.EmbeddingGenerator = _FakeEmbeddingGenerator
    monkeypatch.setitem(sys.modules, "utils.embedding_generator", embedding_generator_module)

    similarity_engine_module = types.ModuleType("utils.similarity_engine")
    similarity_engine_module.SimilarityEngine = _FakeSimilarityEngine
    monkeypatch.setitem(sys.modules, "utils.similarity_engine", similarity_engine_module)


def test_mini_scout_similar_companies_resolves_default_db_path(monkeypatch):
    from dashboard import mini_scout

    _install_similarity_fakes(monkeypatch)
    monkeypatch.setattr(
        mini_scout,
        "resolve_db_path_env",
        lambda db_path=None: "resolved-dashboard.db",
    )

    result = mini_scout.find_similar_companies_for_signal("domain:acme.com")

    assert result == [{"canonical_key": "domain:acme.com", "limit": 10}]
    assert _FakeEmbeddingStore.captured_db_paths == ["resolved-dashboard.db"]


def test_url_profiler_similar_companies_resolves_default_db_path(monkeypatch):
    from dashboard import url_profiler_page

    _install_similarity_fakes(monkeypatch)
    monkeypatch.setattr(
        url_profiler_page,
        "resolve_db_path_env",
        lambda db_path=None: "resolved-dashboard.db",
    )

    result = url_profiler_page.find_similar_companies("domain:acme.com")

    assert result == [{"canonical_key": "domain:acme.com", "limit": 20}]
    assert _FakeEmbeddingStore.captured_db_paths == ["resolved-dashboard.db"]
