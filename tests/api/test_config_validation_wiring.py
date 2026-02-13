"""
M1.4 -- Tests for config validation wiring in API lifespan.

Verifies:
1. validate_config() is invoked during lifespan startup
2. STRICT_CONFIG_VALIDATION=true + error -> RuntimeError raised
3. STRICT_CONFIG_VALIDATION=false + error -> startup continues
4. Existing startup_check() still called (not replaced)
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from utils.config_validator import ConfigIssue


class TestConfigValidationInLifespan:
    """Tests for config validation wiring in api/main.py lifespan()."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Ensure STRICT_CONFIG_VALIDATION is unset by default."""
        monkeypatch.delenv("STRICT_CONFIG_VALIDATION", raising=False)

    @pytest.mark.asyncio
    async def test_validate_config_called_during_lifespan(self, monkeypatch):
        """validate_config() must be invoked during lifespan startup."""
        monkeypatch.delenv("STRICT_CONFIG_VALIDATION", raising=False)

        with patch("api.main.validate_config", return_value=[]) as mock_vc, \
             patch("api.main.startup_check", return_value=[]), \
             patch("api.main.SignalStore") as MockStore, \
             patch("api.main.seed_default_users"):
            mock_store = AsyncMock()
            MockStore.return_value = mock_store

            from api.main import lifespan
            app = MagicMock()
            app.state = MagicMock()

            async with lifespan(app):
                pass

            mock_vc.assert_called_once()

    @pytest.mark.asyncio
    async def test_strict_validation_true_with_errors_raises(self, monkeypatch):
        """STRICT_CONFIG_VALIDATION=true + config errors -> RuntimeError."""
        monkeypatch.setenv("STRICT_CONFIG_VALIDATION", "true")

        error_issues = [
            ConfigIssue(level="error", key="NOTION_API_KEY",
                        message="Not configured (required for batch_publish)"),
        ]

        with patch("api.main.validate_config", return_value=error_issues), \
             patch("api.main.startup_check", return_value=[]), \
             patch("api.main.SignalStore") as MockStore, \
             patch("api.main.seed_default_users"):
            mock_store = AsyncMock()
            MockStore.return_value = mock_store

            from api.main import lifespan
            app = MagicMock()
            app.state = MagicMock()

            with pytest.raises(RuntimeError, match="Config validation failed"):
                async with lifespan(app):
                    pass

    @pytest.mark.asyncio
    async def test_strict_validation_false_with_errors_continues(self, monkeypatch):
        """STRICT_CONFIG_VALIDATION=false + config errors -> startup continues."""
        monkeypatch.setenv("STRICT_CONFIG_VALIDATION", "false")

        error_issues = [
            ConfigIssue(level="error", key="DELIVERY_MODE",
                        message="'foobar' is not valid"),
        ]

        with patch("api.main.validate_config", return_value=error_issues), \
             patch("api.main.startup_check", return_value=[]), \
             patch("api.main.SignalStore") as MockStore, \
             patch("api.main.seed_default_users"):
            mock_store = AsyncMock()
            MockStore.return_value = mock_store

            from api.main import lifespan
            app = MagicMock()
            app.state = MagicMock()

            # Should NOT raise -- startup continues despite errors
            async with lifespan(app):
                pass

    @pytest.mark.asyncio
    async def test_startup_check_still_called(self, monkeypatch):
        """Existing startup_check() must still be called (not replaced)."""
        monkeypatch.delenv("STRICT_CONFIG_VALIDATION", raising=False)

        with patch("api.main.validate_config", return_value=[]), \
             patch("api.main.startup_check", return_value=[]) as mock_sc, \
             patch("api.main.SignalStore") as MockStore, \
             patch("api.main.seed_default_users"):
            mock_store = AsyncMock()
            MockStore.return_value = mock_store

            from api.main import lifespan
            app = MagicMock()
            app.state = MagicMock()

            async with lifespan(app):
                pass

            mock_sc.assert_called_once()
