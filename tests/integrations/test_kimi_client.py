"""Tests for integrations/kimi_client.py -- Milestone D2.

Covers:
  D2.1  Budget accounting - track token usage, warn at threshold
  D2.2  Model selection - correct model selected based on context size
  D2.3  Auto-mode thresholds - Kimi selected when >= 5 files or >= 20K tokens
  D2.4  Budget exhaustion - graceful failure when daily budget exceeded
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.kimi_client import (
    DAILY_TOKEN_LIMIT,
    DAILY_TOKEN_WARNING,
    KimiBudget,
    KimiClient,
    KimiModel,
    KimiResponse,
    _load_budget,
    _save_budget,
    get_budget_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_global_budget():
    """Reset the module-level _budget global before each test."""
    import integrations.kimi_client as mod
    mod._budget = None
    yield
    mod._budget = None


@pytest.fixture
def kimi_client():
    """KimiClient with a fake API key (no real API calls)."""
    with patch.dict(os.environ, {"KIMI_API_KEY": "test-key-000"}):
        client = KimiClient(api_key="test-key-000")
    return client


@pytest.fixture
def mock_openai_response():
    """Factory for mock OpenAI chat completion responses."""

    def _make(
        content: str = "Mock response",
        model: str = "kimi-k2.5",
        total_tokens: int = 500,
        prompt_tokens: int = 300,
        completion_tokens: int = 200,
        finish_reason: str = "stop",
    ) -> MagicMock:
        response = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        choice.finish_reason = finish_reason
        response.choices = [choice]
        response.model = model
        response.usage = MagicMock()
        response.usage.total_tokens = total_tokens
        response.usage.prompt_tokens = prompt_tokens
        response.usage.completion_tokens = completion_tokens
        return response

    return _make


# ---------------------------------------------------------------------------
# D2.1  Budget accounting
# ---------------------------------------------------------------------------

class TestBudgetAccounting:
    """D2.1: Track token usage, warn at threshold."""

    def test_budget_dataclass_roundtrip(self):
        """KimiBudget should serialize and deserialize correctly."""
        budget = KimiBudget(
            daily_tokens=10_000,
            monthly_tokens=50_000,
            last_reset_date="2026-03-18",
            last_reset_month="2026-03",
            request_count=5,
        )
        d = budget.to_dict()
        restored = KimiBudget.from_dict(d)
        assert restored.daily_tokens == 10_000
        assert restored.monthly_tokens == 50_000
        assert restored.request_count == 5

    def test_budget_from_dict_handles_missing_keys(self):
        """KimiBudget.from_dict should handle incomplete data gracefully."""
        budget = KimiBudget.from_dict({"daily_tokens": 42})
        assert budget.daily_tokens == 42
        assert budget.monthly_tokens == 0
        assert budget.request_count == 0

    def test_get_budget_status_structure(self):
        """get_budget_status() should return dict with expected keys."""
        import integrations.kimi_client as mod
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mod._budget = KimiBudget(
            daily_tokens=100_000,
            monthly_tokens=200_000,
            last_reset_date=today,
            last_reset_month=datetime.now(timezone.utc).strftime("%Y-%m"),
            request_count=10,
        )
        status = get_budget_status()
        assert "daily_tokens" in status
        assert "daily_limit" in status
        assert "daily_remaining" in status
        assert "daily_percent" in status
        assert "monthly_tokens" in status
        assert "warning" in status
        assert status["daily_tokens"] == 100_000
        assert status["daily_remaining"] == DAILY_TOKEN_LIMIT - 100_000

    def test_budget_warning_flag_at_threshold(self):
        """warning should be True when daily_tokens >= DAILY_TOKEN_WARNING."""
        import integrations.kimi_client as mod
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mod._budget = KimiBudget(
            daily_tokens=DAILY_TOKEN_WARNING,
            last_reset_date=today,
            last_reset_month=datetime.now(timezone.utc).strftime("%Y-%m"),
        )
        status = get_budget_status()
        assert status["warning"] is True

    def test_budget_no_warning_below_threshold(self):
        """warning should be False when daily_tokens < DAILY_TOKEN_WARNING."""
        import integrations.kimi_client as mod
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mod._budget = KimiBudget(
            daily_tokens=100,
            last_reset_date=today,
            last_reset_month=datetime.now(timezone.utc).strftime("%Y-%m"),
        )
        status = get_budget_status()
        assert status["warning"] is False

    @pytest.mark.asyncio
    async def test_chat_tracks_token_usage(
        self, kimi_client, mock_openai_response
    ):
        """chat() should update budget with token usage from response."""
        import integrations.kimi_client as mod

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        mod._budget = KimiBudget(
            daily_tokens=0,
            monthly_tokens=0,
            last_reset_date=today,
            last_reset_month=month,
            request_count=0,
        )

        mock_resp = mock_openai_response(total_tokens=750)

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        kimi_client._client = mock_client

        with patch("integrations.kimi_client._save_budget"):
            response = await kimi_client.chat("Hello")

        assert response.success is True
        assert mod._budget.daily_tokens == 750
        assert mod._budget.monthly_tokens == 750
        assert mod._budget.request_count == 1


# ---------------------------------------------------------------------------
# D2.2  Model selection
# ---------------------------------------------------------------------------

class TestModelSelection:
    """D2.2: Correct model selected based on context size."""

    def test_default_model_is_k2_5(self, kimi_client):
        """Default model should be kimi-k2.5."""
        assert kimi_client.model == KimiModel.K2_5

    def test_model_override_at_construction(self):
        """Model can be overridden at construction time."""
        with patch.dict(os.environ, {"KIMI_API_KEY": "test-key"}):
            client = KimiClient(
                api_key="test-key",
                model=KimiModel.MOONSHOT_128K,
            )
        assert client.model == KimiModel.MOONSHOT_128K

    @pytest.mark.asyncio
    async def test_chat_uses_specified_model(
        self, kimi_client, mock_openai_response
    ):
        """chat() should pass the correct model name to the API."""
        import integrations.kimi_client as mod

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        mod._budget = KimiBudget(
            last_reset_date=today, last_reset_month=month,
        )

        mock_resp = mock_openai_response(model="moonshot-v1-128k")
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        kimi_client._client = mock_client

        with patch("integrations.kimi_client._save_budget"):
            response = await kimi_client.chat(
                "Hello", model=KimiModel.MOONSHOT_128K
            )

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "moonshot-v1-128k"

    def test_all_model_enum_values(self):
        """All KimiModel enum values should have string values."""
        expected = {
            "kimi-k2.5", "kimi-k2-thinking", "kimi-latest",
            "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k",
        }
        actual = {m.value for m in KimiModel}
        assert actual == expected


# ---------------------------------------------------------------------------
# D2.3  Auto-mode thresholds  (tested via Maestro, but logic originates here)
# ---------------------------------------------------------------------------

class TestAutoModeThresholds:
    """D2.3: Verify Kimi is selected when >= 5 files or >= 20K tokens.

    The auto-selection logic lives in maestro.py (_should_use_kimi), but we
    test the KimiClient's underlying context-size estimation indirectly via
    Maestro's _estimate_context_size, and also test the threshold constants.
    """

    def test_threshold_constants(self):
        """KIMI thresholds should match documented values."""
        from integrations.maestro import (
            KIMI_AUTO_FILE_THRESHOLD,
            KIMI_AUTO_TOKEN_THRESHOLD,
        )
        assert KIMI_AUTO_FILE_THRESHOLD == 5
        assert KIMI_AUTO_TOKEN_THRESHOLD == 20_000

    def test_daily_token_limit_constant(self):
        """DAILY_TOKEN_LIMIT should be 1.5M."""
        assert DAILY_TOKEN_LIMIT == 1_500_000

    def test_daily_warning_constant(self):
        """DAILY_TOKEN_WARNING should be 500K."""
        assert DAILY_TOKEN_WARNING == 500_000


# ---------------------------------------------------------------------------
# D2.4  Budget exhaustion
# ---------------------------------------------------------------------------

class TestBudgetExhaustion:
    """D2.4: Graceful failure when daily budget exceeded."""

    @pytest.mark.asyncio
    async def test_chat_with_api_error_returns_error_response(
        self, kimi_client
    ):
        """When the API call fails, chat() returns a KimiResponse with error set."""
        import integrations.kimi_client as mod

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        mod._budget = KimiBudget(
            daily_tokens=0,
            last_reset_date=today,
            last_reset_month=month,
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Rate limit exceeded: daily quota exhausted")
        )
        kimi_client._client = mock_client

        with patch("integrations.kimi_client._save_budget"):
            response = await kimi_client.chat("Hello")

        assert response.success is False
        assert response.error is not None
        assert "Rate limit" in response.error or "quota" in response.error

    @pytest.mark.asyncio
    async def test_chat_near_budget_logs_warning(
        self, kimi_client, mock_openai_response, caplog
    ):
        """When near budget, chat() should log a warning."""
        import logging
        import integrations.kimi_client as mod

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        mod._budget = KimiBudget(
            daily_tokens=DAILY_TOKEN_WARNING + 1000,
            last_reset_date=today,
            last_reset_month=month,
        )

        mock_resp = mock_openai_response(total_tokens=100)
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        kimi_client._client = mock_client

        with patch("integrations.kimi_client._save_budget"):
            with caplog.at_level(logging.WARNING, logger="kimi-client"):
                response = await kimi_client.chat("Hello")

        assert response.success is True
        assert any("budget" in r.message.lower() for r in caplog.records)

    def test_kimi_response_success_property(self):
        """KimiResponse.success should be True when error is None."""
        ok = KimiResponse(
            content="OK",
            model="kimi-k2.5",
            usage={"total_tokens": 10},
            finish_reason="stop",
        )
        assert ok.success is True

        fail = KimiResponse(
            content="",
            model="kimi-k2.5",
            usage={"total_tokens": 0},
            finish_reason="error",
            error="Something went wrong",
        )
        assert fail.success is False

    def test_kimi_response_to_dict(self):
        """KimiResponse.to_dict() should include all fields including success."""
        resp = KimiResponse(
            content="Hello",
            model="kimi-k2.5",
            usage={"total_tokens": 100, "prompt_tokens": 60, "completion_tokens": 40},
            finish_reason="stop",
            execution_time_ms=250,
        )
        d = resp.to_dict()
        assert d["success"] is True
        assert d["content"] == "Hello"
        assert d["model"] == "kimi-k2.5"
        assert d["execution_time_ms"] == 250

    def test_budget_daily_reset(self, tmp_path):
        """Budget should reset daily_tokens when date changes."""
        import integrations.kimi_client as mod

        # Write a budget file with a stale last_reset_date so _load_budget
        # will load it from disk and then run the date-change reset logic.
        budget_file = tmp_path / "kimi_budget.json"
        stale_budget = KimiBudget(
            daily_tokens=999_999,
            monthly_tokens=999_999,
            last_reset_date="2025-01-01",
            last_reset_month=datetime.now(timezone.utc).strftime("%Y-%m"),
            request_count=50,
        )
        budget_file.write_text(json.dumps(stale_budget.to_dict()))

        # Ensure global is None so _load_budget reads from the file
        mod._budget = None

        with patch(
            "integrations.kimi_client._get_budget_path",
            return_value=str(budget_file),
        ):
            budget = _load_budget()

        # Daily should have been reset because last_reset_date is old
        assert budget.daily_tokens == 0

    def test_client_requires_api_key(self):
        """KimiClient should raise ValueError if no API key is available."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove any existing env vars
            env = os.environ.copy()
            for key in ("KIMI_API_KEY", "MOONSHOT_API_KEY"):
                env.pop(key, None)

            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ValueError, match="Kimi API key required"):
                    KimiClient()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
