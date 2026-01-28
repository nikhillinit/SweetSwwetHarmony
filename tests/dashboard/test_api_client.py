"""
Tests for dashboard API client.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


class MockSessionState(dict):
    """Mock Streamlit session state that supports both dict and attribute access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{key}'")

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{key}'")


# Mock streamlit before importing
import sys
mock_st = MagicMock()
mock_st.session_state = MockSessionState()
mock_st.rerun = MagicMock()
sys.modules['streamlit'] = mock_st

from dashboard.api_client import (
    get_auth_token,
    get_current_user,
    set_auth,
    clear_auth,
    is_authenticated,
    APIClient,
    check_api_connection,
)


class TestAuthState:
    """Test authentication state management."""

    def setup_method(self):
        """Reset session state before each test."""
        mock_st.session_state = MockSessionState()

    def test_get_auth_token_empty(self):
        """Test getting token when not set."""
        assert get_auth_token() is None

    def test_set_and_get_auth(self):
        """Test setting and getting auth credentials."""
        set_auth(
            token="test_token",
            user={"email": "test@example.com", "role": "analyst"},
            expires_at="2030-01-01T00:00:00Z",
        )

        assert get_auth_token() == "test_token"
        assert get_current_user()["email"] == "test@example.com"

    def test_clear_auth(self):
        """Test clearing auth credentials."""
        set_auth(
            token="test_token",
            user={"email": "test@example.com"},
            expires_at="2030-01-01T00:00:00Z",
        )
        clear_auth()

        assert get_auth_token() is None
        assert get_current_user() is None

    def test_is_authenticated_false_when_no_token(self):
        """Test is_authenticated returns False when no token."""
        assert is_authenticated() is False

    def test_is_authenticated_true_with_valid_token(self):
        """Test is_authenticated returns True with valid token."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        set_auth(
            token="test_token",
            user={"email": "test@example.com"},
            expires_at=future,
        )

        assert is_authenticated() is True

    def test_is_authenticated_false_with_expired_token(self):
        """Test is_authenticated returns False with expired token."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        set_auth(
            token="test_token",
            user={"email": "test@example.com"},
            expires_at=past,
        )

        # Should clear auth and return False
        assert is_authenticated() is False
        assert get_auth_token() is None


class TestAPIClient:
    """Test API client methods."""

    def setup_method(self):
        """Reset session state before each test."""
        mock_st.session_state = MockSessionState()

    def test_client_initialization(self):
        """Test client initializes with defaults."""
        client = APIClient()
        assert client.base_url == "http://127.0.0.1:8000/api/v1"
        assert client.timeout == 10.0

    def test_client_custom_config(self):
        """Test client with custom configuration."""
        client = APIClient(base_url="http://custom:9000/api", timeout=30.0)
        assert client.base_url == "http://custom:9000/api"
        assert client.timeout == 30.0

    def test_get_headers_without_auth(self):
        """Test headers without authentication."""
        client = APIClient()
        headers = client._get_headers()

        assert "Content-Type" in headers
        assert "Authorization" not in headers

    def test_get_headers_with_auth(self):
        """Test headers with authentication."""
        set_auth(
            token="test_token",
            user={"email": "test@example.com"},
            expires_at="2030-01-01T00:00:00Z",
        )

        client = APIClient()
        headers = client._get_headers()

        assert headers["Authorization"] == "Bearer test_token"

    @patch('httpx.Client')
    def test_login_success(self, mock_client_class):
        """Test successful login."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_token",
            "expires_at": "2030-01-01T00:00:00Z",
            "user": {"email": "test@example.com", "role": "analyst", "name": "Test"},
        }

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = APIClient()
        result = client.login("test@example.com", "password")

        assert result["success"] is True
        assert result["user"]["email"] == "test@example.com"
        assert get_auth_token() == "new_token"

    @patch('httpx.Client')
    def test_login_failure(self, mock_client_class):
        """Test failed login."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"detail": "Invalid credentials"}

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = APIClient()
        result = client.login("test@example.com", "wrong_password")

        assert result["success"] is False
        assert "Invalid credentials" in result["error"]

    @patch('httpx.Client')
    def test_get_health_detailed(self, mock_client_class):
        """Test getting detailed health."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "components": [
                {"name": "database", "status": "healthy"},
            ],
        }

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = APIClient()
        result = client.get_health_detailed()

        assert result["status"] == "healthy"
        assert len(result["components"]) == 1

    @patch('httpx.Client')
    def test_list_jobs(self, mock_client_class):
        """Test listing jobs."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jobs": [
                {"id": "job-1", "job_type": "collect", "status": "completed"},
                {"id": "job-2", "job_type": "process", "status": "running"},
            ],
            "total": 2,
        }

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = APIClient()
        result = client.list_jobs(limit=10)

        assert result["total"] == 2
        assert len(result["jobs"]) == 2

    @patch('httpx.Client')
    def test_start_collect_job(self, mock_client_class):
        """Test starting a collection job."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "job-123",
            "job_type": "collect",
            "status": "pending",
        }

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        # Set auth for POST request
        set_auth(
            token="test_token",
            user={"email": "test@example.com"},
            expires_at="2030-01-01T00:00:00Z",
        )

        client = APIClient()
        result = client.start_collect_job(collector="github")

        assert result["id"] == "job-123"
        assert result["status"] == "pending"


class TestCheckAPIConnection:
    """Test API connection checking."""

    @patch('httpx.Client')
    def test_api_connected(self, mock_client_class):
        """Test when API is connected."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        assert check_api_connection() is True

    @patch('httpx.Client')
    def test_api_disconnected(self, mock_client_class):
        """Test when API is not connected."""
        import httpx
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client

        assert check_api_connection() is False
