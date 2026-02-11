"""
Tests for Pipeline integration with Community Collectors (Telegram, Discord).

Tests that the pipeline correctly:
- Instantiates community collectors with proper credentials
- Handles missing credentials gracefully (skip with message)
- Passes configuration to community collectors
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import os

from workflows.pipeline import (
    DiscoveryPipeline,
    PipelineConfig,
    CollectorResult,
    CollectorStatus,
)


@pytest.fixture
def pipeline_config():
    """Create a basic pipeline config."""
    return PipelineConfig(
        notion_api_key="test_key",
        notion_database_id="test_db",
    )


@pytest.fixture
def mock_store():
    """Create a mock signal store."""
    store = MagicMock()
    store.get_suppression_cache = MagicMock(return_value=set())
    store.get_signal_metrics = AsyncMock(return_value={})
    store.count_signals = MagicMock(return_value=0)
    return store


# =============================================================================
# TELEGRAM COLLECTOR INTEGRATION TESTS
# =============================================================================

class TestTelegramCollectorIntegration:
    """Tests for Telegram collector in pipeline."""

    @pytest.mark.asyncio
    async def test_telegram_skipped_without_credentials(self, pipeline_config, mock_store):
        """Telegram collector is skipped if credentials missing."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove any existing telegram credentials
            os.environ.pop("TELEGRAM_API_ID", None)
            os.environ.pop("TELEGRAM_API_HASH", None)

            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store

            # Should skip gracefully
            result = await pipeline._run_single_collector("telegram", dry_run=True)

            assert result.status == CollectorStatus.SKIPPED
            assert "TELEGRAM_API_ID" in result.error_message or "TELEGRAM_API_HASH" in result.error_message

    @pytest.mark.asyncio
    async def test_telegram_instantiated_with_credentials(self, pipeline_config, mock_store):
        """Telegram collector is instantiated when credentials present."""
        with patch.dict(os.environ, {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abcdef123456",
            "TELEGRAM_CHANNELS": "@testchannel,@anotherchannel",
        }):
            with patch("collectors.telegram.TelegramCollector") as MockCollector:
                mock_instance = AsyncMock()
                mock_instance.run = AsyncMock(return_value=MagicMock(
                    signals_found=5,
                    status=CollectorStatus.SUCCESS,
                ))
                MockCollector.return_value = mock_instance

                pipeline = DiscoveryPipeline(config=pipeline_config)
                pipeline._store = mock_store

                result = await pipeline._run_single_collector("telegram", dry_run=True)

                # Verify collector was instantiated with credentials
                MockCollector.assert_called_once()
                call_kwargs = MockCollector.call_args.kwargs
                assert call_kwargs.get("api_id") == "12345"
                assert call_kwargs.get("api_hash") == "abcdef123456"
                assert "@testchannel" in call_kwargs.get("channels", [])

    @pytest.mark.asyncio
    async def test_telegram_channels_parsed_correctly(self, pipeline_config, mock_store):
        """Telegram channels are parsed from comma-separated env var."""
        with patch.dict(os.environ, {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abcdef123456",
            "TELEGRAM_CHANNELS": "@channel1, @channel2, @channel3",  # spaces around commas
        }):
            with patch("collectors.telegram.TelegramCollector") as MockCollector:
                mock_instance = AsyncMock()
                mock_instance.run = AsyncMock(return_value=MagicMock(
                    signals_found=0,
                    status=CollectorStatus.SUCCESS,
                ))
                MockCollector.return_value = mock_instance

                pipeline = DiscoveryPipeline(config=pipeline_config)
                pipeline._store = mock_store

                await pipeline._run_single_collector("telegram", dry_run=True)

                # Verify channels were parsed (trimmed of spaces)
                call_kwargs = MockCollector.call_args.kwargs
                channels = call_kwargs.get("channels", [])
                assert len(channels) == 3
                assert "@channel1" in channels
                assert "@channel2" in channels
                assert "@channel3" in channels


# =============================================================================
# DISCORD COLLECTOR INTEGRATION TESTS
# =============================================================================

class TestDiscordCollectorIntegration:
    """Tests for Discord collector in pipeline."""

    @pytest.mark.asyncio
    async def test_discord_skipped_without_token(self, pipeline_config, mock_store):
        """Discord collector is skipped if bot token missing."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DISCORD_BOT_TOKEN", None)

            pipeline = DiscoveryPipeline(config=pipeline_config)
            pipeline._store = mock_store

            result = await pipeline._run_single_collector("discord", dry_run=True)

            assert result.status == CollectorStatus.SKIPPED
            assert "DISCORD_BOT_TOKEN" in result.error_message

    @pytest.mark.asyncio
    async def test_discord_instantiated_with_token(self, pipeline_config, mock_store):
        """Discord collector is instantiated when token present."""
        with patch.dict(os.environ, {
            "DISCORD_BOT_TOKEN": "test_token_123",
            "DISCORD_SERVER_IDS": "123456789,987654321",
        }):
            with patch("collectors.discord.DiscordCollector") as MockCollector:
                mock_instance = AsyncMock()
                mock_instance.run = AsyncMock(return_value=MagicMock(
                    signals_found=3,
                    status=CollectorStatus.SUCCESS,
                ))
                MockCollector.return_value = mock_instance

                pipeline = DiscoveryPipeline(config=pipeline_config)
                pipeline._store = mock_store

                result = await pipeline._run_single_collector("discord", dry_run=True)

                # Verify collector was instantiated with token
                MockCollector.assert_called_once()
                call_kwargs = MockCollector.call_args.kwargs
                assert call_kwargs.get("bot_token") == "test_token_123"

    @pytest.mark.asyncio
    async def test_discord_server_ids_parsed_as_integers(self, pipeline_config, mock_store):
        """Discord server IDs are parsed as integers."""
        with patch.dict(os.environ, {
            "DISCORD_BOT_TOKEN": "test_token_123",
            "DISCORD_SERVER_IDS": "123456789, 987654321",  # spaces around commas
        }):
            with patch("collectors.discord.DiscordCollector") as MockCollector:
                mock_instance = AsyncMock()
                mock_instance.run = AsyncMock(return_value=MagicMock(
                    signals_found=0,
                    status=CollectorStatus.SUCCESS,
                ))
                MockCollector.return_value = mock_instance

                pipeline = DiscoveryPipeline(config=pipeline_config)
                pipeline._store = mock_store

                await pipeline._run_single_collector("discord", dry_run=True)

                # Verify server IDs were parsed as integers
                call_kwargs = MockCollector.call_args.kwargs
                server_ids = call_kwargs.get("server_ids", [])
                assert len(server_ids) == 2
                assert 123456789 in server_ids
                assert 987654321 in server_ids
                assert all(isinstance(sid, int) for sid in server_ids)


# =============================================================================
# COMMUNITY COLLECTORS IN FULL PIPELINE TESTS
# =============================================================================

class TestCommunityCollectorsInPipeline:
    """Tests for community collectors as part of full pipeline run."""

    @pytest.mark.asyncio
    async def test_community_collectors_in_collector_list(self, pipeline_config, mock_store):
        """Community collectors can be included in collectors list."""
        with patch.dict(os.environ, {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abcdef",
            "DISCORD_BOT_TOKEN": "token123",
        }):
            with patch("collectors.telegram.TelegramCollector") as MockTelegram:
                with patch("collectors.discord.DiscordCollector") as MockDiscord:
                    # Setup mocks
                    for MockCollector in [MockTelegram, MockDiscord]:
                        mock_instance = AsyncMock()
                        mock_instance.run = AsyncMock(return_value=MagicMock(
                            signals_found=2,
                            status=CollectorStatus.SUCCESS,
                        ))
                        MockCollector.return_value = mock_instance

                    pipeline = DiscoveryPipeline(config=pipeline_config)
                    pipeline._store = mock_store
                    pipeline._initialized = True  # Skip real init (opens DB + Notion)

                    # Run both community collectors
                    results = await pipeline.run_collectors(
                        collector_names=["telegram", "discord"],
                        dry_run=True,
                    )

                    # Both should have been instantiated
                    assert MockTelegram.called
                    assert MockDiscord.called
                    assert len(results) == 2

    @pytest.mark.asyncio
    async def test_mixed_traditional_and_community_collectors(self, pipeline_config, mock_store):
        """Mixed traditional and community collectors work together."""
        with patch.dict(os.environ, {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abcdef",
        }):
            with patch("collectors.telegram.TelegramCollector") as MockTelegram:
                with patch("collectors.hacker_news.HackerNewsCollector") as MockHN:
                    # Setup mocks
                    for MockCollector in [MockTelegram, MockHN]:
                        mock_instance = AsyncMock()
                        mock_instance.run = AsyncMock(return_value=MagicMock(
                            signals_found=1,
                            status=CollectorStatus.SUCCESS,
                        ))
                        MockCollector.return_value = mock_instance

                    pipeline = DiscoveryPipeline(config=pipeline_config)
                    pipeline._store = mock_store
                    pipeline._initialized = True  # Skip real init (opens DB + Notion)

                    # Run mixed collectors
                    results = await pipeline.run_collectors(
                        collector_names=["hacker_news", "telegram"],
                        dry_run=True,
                    )

                    # Both should work
                    assert MockTelegram.called
                    assert MockHN.called
                    assert len(results) == 2


# =============================================================================
# ENVIRONMENT VARIABLE HANDLING TESTS
# =============================================================================

class TestCommunityCollectorEnvVars:
    """Tests for environment variable handling."""

    @pytest.mark.asyncio
    async def test_empty_telegram_channels_uses_defaults(self, pipeline_config, mock_store):
        """Empty TELEGRAM_CHANNELS uses collector defaults."""
        with patch.dict(os.environ, {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "abcdef",
            "TELEGRAM_CHANNELS": "",  # Empty
        }):
            with patch("collectors.telegram.TelegramCollector") as MockCollector:
                mock_instance = AsyncMock()
                mock_instance.run = AsyncMock(return_value=MagicMock(
                    signals_found=0,
                    status=CollectorStatus.SUCCESS,
                ))
                MockCollector.return_value = mock_instance

                pipeline = DiscoveryPipeline(config=pipeline_config)
                pipeline._store = mock_store

                await pipeline._run_single_collector("telegram", dry_run=True)

                # Channels should be None (use defaults)
                call_kwargs = MockCollector.call_args.kwargs
                assert call_kwargs.get("channels") is None or call_kwargs.get("channels") == []

    @pytest.mark.asyncio
    async def test_empty_discord_servers_uses_defaults(self, pipeline_config, mock_store):
        """Empty DISCORD_SERVER_IDS uses collector defaults."""
        with patch.dict(os.environ, {
            "DISCORD_BOT_TOKEN": "test_token",
            "DISCORD_SERVER_IDS": "",  # Empty
        }):
            with patch("collectors.discord.DiscordCollector") as MockCollector:
                mock_instance = AsyncMock()
                mock_instance.run = AsyncMock(return_value=MagicMock(
                    signals_found=0,
                    status=CollectorStatus.SUCCESS,
                ))
                MockCollector.return_value = mock_instance

                pipeline = DiscoveryPipeline(config=pipeline_config)
                pipeline._store = mock_store

                await pipeline._run_single_collector("discord", dry_run=True)

                # Server IDs should be None (use defaults)
                call_kwargs = MockCollector.call_args.kwargs
                assert call_kwargs.get("server_ids") is None or call_kwargs.get("server_ids") == []
