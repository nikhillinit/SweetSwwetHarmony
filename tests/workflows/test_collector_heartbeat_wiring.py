from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from workflows.pipeline import DiscoveryPipeline, PipelineConfig


@pytest.mark.asyncio
async def test_run_single_collector_writes_heartbeat_state(tmp_path, monkeypatch):
    state_path = tmp_path / "collectors.json"
    monkeypatch.setenv("COLLECTOR_STATE_PATH", str(state_path))
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    config = PipelineConfig(db_path=":memory:", github_token="fake-token")
    pipeline = DiscoveryPipeline(config)
    pipeline._store = AsyncMock()
    pipeline._collector_metrics = []

    mock_result = CollectorResult(
        collector="github",
        status=CollectorStatus.SUCCESS,
        signals_found=9,
        signals_new=6,
        signals_suppressed=3,
        dry_run=True,
    )

    with patch("collectors.github.GitHubCollector") as MockCollector:
        mock_collector = AsyncMock()
        mock_collector.run = AsyncMock(return_value=mock_result)
        mock_collector._retry_count = 1
        mock_collector._errors = []
        MockCollector.return_value = mock_collector

        result = await pipeline._run_single_collector("github", dry_run=True)

    assert result.status == CollectorStatus.SUCCESS
    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = state["collectors"]["github"]
    assert state["schema_version"] == 2
    assert entry["runner"] == "pipeline"
    assert entry["configured_status"] == "enabled"
    assert entry["last_run_status"] == "success"
    assert entry["effective_status"] == "healthy"
    assert entry["health"] == "ok"
    assert entry["signals_found"] == 9
    assert entry["signals_new"] == 6
    assert entry["signals_suppressed"] == 3
    assert entry["retries"] == 1
    assert entry["last_duration_seconds"] is not None


@pytest.mark.asyncio
async def test_run_single_collector_writes_skipped_heartbeat_state(tmp_path, monkeypatch):
    state_path = tmp_path / "collectors.json"
    monkeypatch.setenv("COLLECTOR_STATE_PATH", str(state_path))
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)

    config = PipelineConfig(db_path=":memory:")
    pipeline = DiscoveryPipeline(config)
    pipeline._store = AsyncMock()
    pipeline._collector_metrics = []

    result = await pipeline._run_single_collector("news_api", dry_run=True)

    assert result.status == CollectorStatus.SKIPPED
    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = state["collectors"]["news_api"]
    assert entry["configured_status"] == "disabled_missing_key"
    assert entry["last_run_status"] == "skipped"
    assert entry["effective_status"] == "disabled_missing_key"
    assert entry["health"] == "disabled"
    assert entry["consecutive_skips"] == 1
    assert "GNEWS_API_KEY" in entry["error_message"]
