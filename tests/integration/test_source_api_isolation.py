# tests/integration/test_source_api_isolation.py
"""Prove process_pending(source_api=X) leaves source Y's signals untouched."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.signal_store import SignalStore
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


@pytest.fixture()
def scratch_db_path(tmp_path, monkeypatch):
    db = tmp_path / "isolation_test.db"
    monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")  # monkeypatch guarantees cleanup
    yield str(db)


@pytest.fixture()
def seeded_db(scratch_db_path):
    """Scratch DB with one github pending + one hacker_news pending signal."""

    async def _seed():
        store = SignalStore(db_path=scratch_db_path)
        await store.initialize()
        await store.save_signal(
            signal_type="github_trending",
            source_api="github",
            canonical_key="domain:isolation-github.test",
            company_name="Github Co",
            confidence=0.75,
            raw_data={"description": "Consumer health app for athletes"},
            detected_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        await store.save_signal(
            signal_type="hn_show",
            source_api="hacker_news",
            canonical_key="domain:isolation-hn.test",
            company_name="HN Co",
            confidence=0.65,
            raw_data={"description": "Wellness marketplace for gen z"},
            detected_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )
        await store.close()

    asyncio.run(_seed())  # asyncio.get_event_loop() is deprecated/brittle in pytest
    return scratch_db_path


def test_process_github_does_not_mutate_hacker_news_pending(seeded_db):
    """process_pending(source_api='github') must leave hacker_news signals in 'pending'."""

    async def _run():
        config = PipelineConfig(
            db_path=seeded_db,
            read_only=False,
            warmup_suppression_cache=False,
            use_entities=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        await pipeline.initialize()

        # Run process for github only
        await pipeline.process_pending(dry_run=True, source_api="github")

        # Verify hacker_news signal is still pending (not touched)
        store = SignalStore(db_path=seeded_db)
        await store.initialize()
        hn_pending = await store.get_pending_signals(source_api="hacker_news")
        await store.close()
        assert len(hn_pending) == 1, (
            f"Expected 1 hacker_news pending signal after github-only process, got {len(hn_pending)}"
        )
        assert hn_pending[0].source_api == "hacker_news"

    asyncio.run(_run())


def test_process_hacker_news_does_not_touch_github(seeded_db):
    """Symmetric: process_pending(source_api='hacker_news') leaves github signals in 'pending'."""

    async def _run():
        config = PipelineConfig(
            db_path=seeded_db,
            read_only=False,
            warmup_suppression_cache=False,
            use_entities=False,
        )
        pipeline = DiscoveryPipeline(config=config)
        await pipeline.initialize()
        await pipeline.process_pending(dry_run=True, source_api="hacker_news")

        store = SignalStore(db_path=seeded_db)
        await store.initialize()
        gh_pending = await store.get_pending_signals(source_api="github")
        await store.close()
        assert len(gh_pending) == 1
        assert gh_pending[0].source_api == "github"

    asyncio.run(_run())
