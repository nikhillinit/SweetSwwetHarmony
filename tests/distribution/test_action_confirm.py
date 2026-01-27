"""
Tests for safe action execution (scanner protection).

CRITICAL: GET /execute must NOT mutate state.
This protects against email security scanners triggering actions.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from api.auth.magic_tokens import (
    peek_token,
    consume_token,
    create_action_token,
    TokenError,
    SECRET_KEY,
    ALGORITHM,
)
import jwt


class TestPeekToken:
    """Tests for peek_token (validation without consumption)."""

    def test_peek_valid_token(self):
        """peek_token should validate and return payload without consuming."""
        # Create a valid token manually
        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "test-nonce-123",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,  # +1 day
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        # Peek should work
        result = peek_token(token)

        assert result.canonical_key == "domain:test.com"
        assert result.action == "track"
        assert result.nonce == "test-nonce-123"

    def test_peek_expired_token_raises(self):
        """peek_token should raise for expired tokens."""
        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "test-nonce-123",
            "iat": datetime.now(timezone.utc).timestamp() - 86400,
            "exp": datetime.now(timezone.utc).timestamp() - 3600,  # expired 1hr ago
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        with pytest.raises(TokenError, match="expired"):
            peek_token(token)

    def test_peek_invalid_token_raises(self):
        """peek_token should raise for invalid tokens."""
        with pytest.raises(TokenError, match="Invalid token"):
            peek_token("not-a-valid-token")

    def test_peek_missing_fields_raises(self):
        """peek_token should raise if required fields missing."""
        payload = {
            "ck": "domain:test.com",
            # missing "act" and "jti"
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        with pytest.raises(TokenError, match="missing required fields"):
            peek_token(token)


class TestConsumeToken:
    """Tests for consume_token (one-time use enforcement)."""

    @pytest.mark.asyncio
    async def test_consume_valid_token(self):
        """consume_token should validate and consume nonce."""
        # Create a valid token
        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "unique-nonce-456",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        # Mock store
        mock_store = MagicMock()
        mock_store.consume_token_nonce = AsyncMock(return_value={"nonce": "unique-nonce-456"})

        result = await consume_token(mock_store, token)

        assert result.canonical_key == "domain:test.com"
        assert result.action == "track"
        mock_store.consume_token_nonce.assert_called_once_with("unique-nonce-456")

    @pytest.mark.asyncio
    async def test_consume_already_used_raises(self):
        """consume_token should raise if nonce already used."""
        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "used-nonce",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        # Mock store returns None (nonce already consumed)
        mock_store = MagicMock()
        mock_store.consume_token_nonce = AsyncMock(return_value=None)

        with pytest.raises(TokenError, match="already been used"):
            await consume_token(mock_store, token)


class TestGetExecuteSafety:
    """
    CRITICAL: Tests that GET /execute does NOT mutate state.

    These tests verify the scanner protection mechanism.
    """

    @pytest.mark.asyncio
    async def test_get_execute_does_not_consume_token(self):
        """GET /execute should peek at token, not consume it."""
        from api.routers.actions import show_action_confirmation

        # Create valid token
        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "test-nonce",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        # Mock store
        mock_store = MagicMock()
        mock_store.get_company_by_key = AsyncMock(return_value={"company_name": "Test Co"})
        # consume_token_nonce should NOT be called
        mock_store.consume_token_nonce = AsyncMock()

        # Call GET endpoint
        result = await show_action_confirmation(token=token, store=mock_store)

        # Verify it returned HTML (not JSON error)
        assert isinstance(result, str)
        assert "Confirm" in result or "confirm" in result.lower()

        # CRITICAL: consume_token_nonce should NOT have been called
        mock_store.consume_token_nonce.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_execute_returns_html_confirmation(self):
        """GET /execute should return HTML confirmation page."""
        from api.routers.actions import show_action_confirmation

        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "test-nonce",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        mock_store = MagicMock()
        mock_store.get_company_by_key = AsyncMock(return_value={"company_name": "Test Co"})

        result = await show_action_confirmation(token=token, store=mock_store)

        # Should be HTML
        assert "<!DOCTYPE html>" in result or "<html" in result
        # Should contain form for POST
        assert 'method="POST"' in result
        # Should contain the token
        assert token in result or "token" in result.lower()

    @pytest.mark.asyncio
    async def test_post_execute_consumes_token(self):
        """POST /execute should consume the token."""
        from api.routers.actions import execute_magic_link

        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "post-nonce",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        mock_store = MagicMock()
        mock_store._db = MagicMock()  # Needed for action handler
        mock_store.consume_token_nonce = AsyncMock(return_value={"nonce": "post-nonce"})
        mock_store.get_company_by_key = AsyncMock(return_value={"company_name": "Test Co"})
        mock_store.upsert_company_state = AsyncMock()
        mock_store.log_company_action = AsyncMock()

        result = await execute_magic_link(token=token, store=mock_store)

        # CRITICAL: consume_token_nonce SHOULD have been called
        mock_store.consume_token_nonce.assert_called_once_with("post-nonce")


class TestTokenSingleUse:
    """Tests for token single-use enforcement."""

    @pytest.mark.asyncio
    async def test_token_cannot_be_used_twice(self):
        """Same token should fail on second use."""
        payload = {
            "ck": "domain:test.com",
            "act": "track",
            "jti": "single-use-nonce",
            "iat": datetime.now(timezone.utc).timestamp(),
            "exp": (datetime.now(timezone.utc).timestamp()) + 86400,
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        mock_store = MagicMock()

        # First call succeeds
        mock_store.consume_token_nonce = AsyncMock(return_value={"nonce": "single-use-nonce"})
        result1 = await consume_token(mock_store, token)
        assert result1.canonical_key == "domain:test.com"

        # Second call fails (nonce already consumed)
        mock_store.consume_token_nonce = AsyncMock(return_value=None)
        with pytest.raises(TokenError, match="already been used"):
            await consume_token(mock_store, token)
