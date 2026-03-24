"""Shared mock fixtures for HTTP, LLM, and sandbox calls.

Usage:
    from tests.fixtures.mocks import mock_httpx_client, mock_llm_response
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient that returns configurable responses.

    Usage:
        def test_something(mock_httpx_client):
            mock_httpx_client.post.return_value = httpx.Response(200, json={"ok": True})
            # ... test code that uses httpx ...

    Yields (client_mock, context_manager_patch) so the mock is active.
    """
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True}
    mock_response.text = '{"ok": true}'
    mock_response.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_response
    mock_client.get.return_value = mock_response

    # Make the client work as an async context manager
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    return mock_client


@pytest.fixture
def mock_llm_response():
    """Factory fixture for creating mock LLM API responses.

    Usage:
        def test_something(mock_llm_response):
            response = mock_llm_response(content="The answer is 42")
    """
    def _make_response(
        content: str = "Mock LLM response",
        model: str = "mock-model",
        finish_reason: str = "stop",
        usage: dict[str, int] | None = None,
    ) -> MagicMock:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = content
        response.choices[0].finish_reason = finish_reason
        response.model = model
        if usage is None:
            usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        response.usage = MagicMock(**usage)
        return response

    return _make_response


@pytest.fixture
def mock_sandbox():
    """Mock for Codex sandbox execution.

    Usage:
        def test_something(mock_sandbox):
            mock_sandbox.return_value = ("output text", 0)
    """
    mock = AsyncMock()
    mock.return_value = ("sandbox output", 0)  # (stdout, exit_code)
    return mock


def make_consolidated_signal(
    company_name: str = "Test Co",
    canonical_key: str = "domain:test.com",
    source_apis: list[str] | None = None,
    raw_data: dict[str, Any] | None = None,
    signal_ids: list[int] | None = None,
    founding_date: str | None = None,
    detected_at: str = "2026-01-15T00:00:00+00:00",
) -> MagicMock:
    """Create a mock ConsolidatedSignal for claim_extractor tests."""
    sig = MagicMock()
    sig.company_name = company_name
    sig.canonical_key = canonical_key
    sig.source_apis = source_apis or ["github"]
    sig.merged_raw_data = raw_data or {"title": "Test Signal"}
    sig.contributing_signal_ids = signal_ids or [1]
    sig.founding_date = founding_date
    sig.latest_detected_at = detected_at
    return sig
