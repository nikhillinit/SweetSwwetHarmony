"""
Action Buttons Component

Reusable action buttons for company cards: Track, Pass, Pipeline.
Calls the FastAPI backend to execute actions.
"""

import streamlit as st
import httpx
from typing import Optional

from dashboard.api_client import API_BASE_URL


def get_api_client() -> httpx.Client:
    """Get HTTP client for API calls."""
    return httpx.Client(base_url=API_BASE_URL, timeout=10.0)


def render_action_buttons(
    canonical_key: str,
    current_status: str = "inbox",
    actor: str = "dashboard_user",
    compact: bool = False,
) -> Optional[str]:
    """
    Render action buttons for a company.

    Args:
        canonical_key: The company's canonical key
        current_status: Current status (inbox, tracking, passed)
        actor: Who is performing the action
        compact: Use compact layout

    Returns:
        Action taken (if any) or None
    """
    action_taken = None

    # Generate unique keys for this company's buttons
    # Use hash to ensure uniqueness even with similar canonical keys
    import hashlib
    key_hash = hashlib.md5(canonical_key.encode()).hexdigest()[:12]
    key_prefix = f"action_{key_hash}"

    if compact:
        cols = st.columns([1, 1, 1])
    else:
        cols = st.columns([1, 1, 1, 2])

    # Track Button (only show if not already tracking)
    if current_status in ("inbox", "passed"):
        with cols[0]:
            if st.button("Track", key=f"{key_prefix}_track", type="secondary", use_container_width=True):
                action_taken = _execute_track(canonical_key, actor)

    # Pass Button (only show if not already passed)
    if current_status in ("inbox", "tracking"):
        with cols[1]:
            if st.button("Pass", key=f"{key_prefix}_pass", type="secondary", use_container_width=True):
                # Store state to show pass modal
                st.session_state[f"show_pass_modal_{canonical_key}"] = True
                st.rerun()

    # Pipeline Button (only show if tracking or inbox)
    if current_status in ("inbox", "tracking"):
        with cols[2]:
            if st.button("Pipeline", key=f"{key_prefix}_pipeline", type="primary", use_container_width=True):
                action_taken = _execute_pipeline(canonical_key, actor)

    # Handle Pass Modal
    if st.session_state.get(f"show_pass_modal_{canonical_key}", False):
        action_taken = _render_pass_modal(canonical_key, actor, key_prefix)

    return action_taken


def _execute_track(canonical_key: str, actor: str) -> str:
    """Execute track action via API."""
    try:
        with get_api_client() as client:
            response = client.post(
                "/actions/track",
                json={"canonical_key": canonical_key, "actor": actor}
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    st.success(f"Moved to tracking")
                    return "track"
                else:
                    st.error(result.get("message", "Failed to track"))
            else:
                st.error(f"API error: {response.status_code}")
    except httpx.ConnectError:
        st.error("Cannot connect to API. Is the server running?")
    except Exception as e:
        st.error(f"Error: {e}")
    return None


def _execute_pass(canonical_key: str, reason: str, actor: str) -> str:
    """Execute pass action via API."""
    try:
        with get_api_client() as client:
            response = client.post(
                "/actions/pass",
                json={"canonical_key": canonical_key, "reason": reason, "actor": actor}
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    st.success(f"Company passed")
                    return "pass"
                else:
                    st.error(result.get("message", "Failed to pass"))
            else:
                st.error(f"API error: {response.status_code}")
    except httpx.ConnectError:
        st.error("Cannot connect to API. Is the server running?")
    except Exception as e:
        st.error(f"Error: {e}")
    return None


def _execute_pipeline(canonical_key: str, actor: str) -> str:
    """Execute pipeline action via API."""
    try:
        with get_api_client() as client:
            response = client.post(
                "/actions/pipeline",
                json={"canonical_key": canonical_key, "actor": actor}
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    st.success(f"Added to pipeline")
                    return "pipeline"
                else:
                    st.error(result.get("message", "Failed to add to pipeline"))
            else:
                st.error(f"API error: {response.status_code}")
    except httpx.ConnectError:
        st.error("Cannot connect to API. Is the server running?")
    except Exception as e:
        st.error(f"Error: {e}")
    return None


def _render_pass_modal(canonical_key: str, actor: str, key_prefix: str) -> Optional[str]:
    """Render modal for pass reason input."""
    action_taken = None

    with st.container():
        st.markdown("---")
        st.markdown("**Why are you passing on this company?**")

        # Common pass reasons
        reason_options = [
            "Select a reason...",
            "Not consumer-focused",
            "Too early stage",
            "Too late stage (Series B+)",
            "Outside target geography",
            "Competitive overlap",
            "Weak founding team",
            "Market too small",
            "Other"
        ]

        selected_reason = st.selectbox(
            "Reason",
            reason_options,
            key=f"{key_prefix}_pass_reason_select",
            label_visibility="collapsed"
        )

        # Custom reason input if "Other" selected
        if selected_reason == "Other":
            custom_reason = st.text_input(
                "Custom reason",
                key=f"{key_prefix}_pass_reason_custom",
                placeholder="Enter your reason..."
            )
            final_reason = custom_reason
        else:
            final_reason = selected_reason if selected_reason != "Select a reason..." else ""

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Confirm Pass", key=f"{key_prefix}_pass_confirm", type="primary"):
                if final_reason:
                    action_taken = _execute_pass(canonical_key, final_reason, actor)
                    st.session_state[f"show_pass_modal_{canonical_key}"] = False
                    if action_taken:
                        st.rerun()
                else:
                    st.warning("Please select a reason")

        with col2:
            if st.button("Cancel", key=f"{key_prefix}_pass_cancel"):
                st.session_state[f"show_pass_modal_{canonical_key}"] = False
                st.rerun()

    return action_taken
