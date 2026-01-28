"""
API Client for Dashboard

Provides authenticated access to the FastAPI backend.
Stores auth token in Streamlit session state.

Usage:
    from dashboard.api_client import APIClient, require_auth

    # Check if authenticated
    if not require_auth():
        return  # Will show login page

    # Use client
    client = APIClient()
    health = client.get_health_detailed()
"""

import streamlit as st
import httpx
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

# =============================================================================
# CONFIGURATION
# =============================================================================

API_BASE_URL = "http://127.0.0.1:8000/api/v1"
API_TIMEOUT = 10.0


# =============================================================================
# AUTH STATE HELPERS
# =============================================================================

def get_auth_token() -> Optional[str]:
    """Get auth token from session state."""
    return st.session_state.get("auth_token")


def get_current_user() -> Optional[Dict[str, Any]]:
    """Get current user from session state."""
    return st.session_state.get("auth_user")


def set_auth(token: str, user: Dict[str, Any], expires_at: str):
    """Store auth credentials in session state."""
    st.session_state.auth_token = token
    st.session_state.auth_user = user
    st.session_state.auth_expires_at = expires_at


def clear_auth():
    """Clear auth credentials from session state."""
    for key in ["auth_token", "auth_user", "auth_expires_at"]:
        if key in st.session_state:
            del st.session_state[key]


def is_authenticated() -> bool:
    """Check if user is authenticated with valid token."""
    token = get_auth_token()
    if not token:
        return False

    # Check expiration
    expires_at = st.session_state.get("auth_expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                clear_auth()
                return False
        except (ValueError, TypeError):
            pass

    return True


# =============================================================================
# API CLIENT
# =============================================================================

@dataclass
class APIError:
    """API error response."""
    status_code: int
    message: str
    detail: Optional[str] = None


class APIClient:
    """
    Authenticated HTTP client for the FastAPI backend.

    Automatically injects auth token into requests.
    Handles 401 responses by clearing auth state.
    """

    def __init__(self, base_url: str = API_BASE_URL, timeout: float = API_TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout

    def _get_headers(self) -> Dict[str, str]:
        """Get headers with auth token."""
        headers = {"Content-Type": "application/json"}
        token = get_auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _handle_response(self, response: httpx.Response) -> Optional[Dict[str, Any]]:
        """Handle response and check for auth errors."""
        if response.status_code == 401:
            clear_auth()
            st.rerun()
            return None

        if response.status_code >= 400:
            return {"error": True, "status_code": response.status_code, "detail": response.text}

        return response.json()

    # -------------------------------------------------------------------------
    # Auth Endpoints
    # -------------------------------------------------------------------------

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user and store token.

        Returns:
            {"success": True, "user": {...}} on success
            {"success": False, "error": "..."} on failure
        """
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.post(
                    "/auth/login",
                    json={"email": email, "password": password},
                )

                if response.status_code == 200:
                    data = response.json()
                    set_auth(
                        token=data["access_token"],
                        user=data["user"],
                        expires_at=data["expires_at"],
                    )
                    return {"success": True, "user": data["user"]}
                else:
                    return {"success": False, "error": response.json().get("detail", "Login failed")}
        except httpx.ConnectError:
            return {"success": False, "error": "Cannot connect to API server"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_auth(self) -> bool:
        """Verify current token is valid."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=3.0) as client:
                response = client.get(
                    "/auth/check",
                    headers=self._get_headers(),
                )
                return response.status_code == 200
        except:
            return False

    def get_me(self) -> Optional[Dict[str, Any]]:
        """Get current user info."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/auth/me",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except:
            return None

    # -------------------------------------------------------------------------
    # Health Endpoints
    # -------------------------------------------------------------------------

    def get_health_detailed(self) -> Optional[Dict[str, Any]]:
        """Get detailed system health."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/health/detailed",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except httpx.ConnectError:
            return {"error": True, "message": "Cannot connect to API server"}
        except Exception as e:
            return {"error": True, "message": str(e)}

    def get_collectors(self) -> Optional[List[Dict[str, Any]]]:
        """Get collector health status."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/health/collectors",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    return response.json()
                return None
        except:
            return None

    def get_database_health(self) -> Optional[Dict[str, Any]]:
        """Get database health and stats."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/health/database",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except:
            return None

    def get_relationship_health(self) -> Optional[Dict[str, Any]]:
        """Get relationship data health."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/health/relationships",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except:
            return None

    # -------------------------------------------------------------------------
    # Jobs Endpoints
    # -------------------------------------------------------------------------

    def list_jobs(
        self,
        job_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Optional[Dict[str, Any]]:
        """List recent jobs."""
        try:
            params = {"limit": limit}
            if job_type:
                params["job_type"] = job_type
            if status:
                params["job_status"] = status

            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/jobs",
                    params=params,
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except httpx.ConnectError:
            return {"error": True, "message": "Cannot connect to API server"}
        except Exception as e:
            return {"error": True, "message": str(e)}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job details."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    f"/jobs/{job_id}",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except:
            return None

    def get_job_logs(self, job_id: str, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        """Get job logs."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    f"/jobs/{job_id}/logs",
                    params={"limit": limit},
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    return response.json()
                return None
        except:
            return None

    def start_collect_job(self, collector: str, dry_run: bool = False) -> Optional[Dict[str, Any]]:
        """Start a collection job."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.post(
                    "/jobs/collect",
                    json={"collector": collector, "dry_run": dry_run},
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except httpx.ConnectError:
            return {"error": True, "message": "Cannot connect to API server"}
        except Exception as e:
            return {"error": True, "message": str(e)}

    def start_process_job(self, dry_run: bool = False) -> Optional[Dict[str, Any]]:
        """Start a processing job."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.post(
                    "/jobs/process",
                    json={"dry_run": dry_run},
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except httpx.ConnectError:
            return {"error": True, "message": "Cannot connect to API server"}
        except Exception as e:
            return {"error": True, "message": str(e)}

    def start_sync_job(self) -> Optional[Dict[str, Any]]:
        """Start a Notion sync job."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.post(
                    "/jobs/sync",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except httpx.ConnectError:
            return {"error": True, "message": "Cannot connect to API server"}
        except Exception as e:
            return {"error": True, "message": str(e)}

    def cancel_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Cancel a running job."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.post(
                    f"/jobs/{job_id}/cancel",
                    headers=self._get_headers(),
                )
                return self._handle_response(response)
        except:
            return None

    def get_job_types(self) -> Optional[Dict[str, Any]]:
        """Get available job types and collectors."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(
                    "/jobs/types",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    return response.json()
                return None
        except:
            return None


# =============================================================================
# HEALTH CHECK HELPERS
# =============================================================================

def check_api_connection() -> bool:
    """Check if API server is reachable."""
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"{API_BASE_URL.replace('/api/v1', '')}/health")
            return response.status_code == 200
    except:
        return False


# =============================================================================
# AUTH DECORATOR FOR PAGES
# =============================================================================

def require_auth() -> bool:
    """
    Check if user is authenticated.

    Returns True if authenticated, False if not (caller should show login).

    Usage:
        if not require_auth():
            render_login_page()
            return

        # Rest of page...
    """
    return is_authenticated()
