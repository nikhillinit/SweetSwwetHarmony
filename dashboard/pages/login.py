"""
Login Page

Simple login form that authenticates against the FastAPI backend.
Stores JWT token in session state for subsequent API calls.
"""

import streamlit as st
from dashboard.api_client import APIClient, check_api_connection


def render_login_page():
    """Render the login page."""

    # Custom CSS for login page
    st.markdown("""
    <style>
    .login-container {
        max-width: 400px;
        margin: 4rem auto;
        padding: 2rem;
        background: white;
        border: 1px solid #E0D8D1;
        border-radius: 16px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
    }
    .login-header {
        text-align: center;
        margin-bottom: 2rem;
    }
    .login-header h1 {
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        font-size: 1.75rem;
        color: #292929;
        margin-bottom: 0.5rem;
    }
    .login-header p {
        font-family: 'Poppins', sans-serif;
        color: #6B7280;
        font-size: 0.9rem;
    }
    .login-footer {
        text-align: center;
        margin-top: 1.5rem;
        font-size: 0.75rem;
        color: #9CA3AF;
    }
    </style>
    """, unsafe_allow_html=True)

    # Center the login form
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        # Header
        st.markdown("""
        <div class="login-header">
            <h1>Discovery Engine</h1>
            <p>Press On Ventures Command Center</p>
        </div>
        """, unsafe_allow_html=True)

        # Check API connection
        api_connected = check_api_connection()
        if not api_connected:
            st.error("""
            **Cannot connect to API server**

            Make sure the API server is running:
            ```
            uvicorn api.main:app --reload --port 8000
            ```
            """)
            return

        # Login form
        with st.form("login_form"):
            email = st.text_input(
                "Email",
                placeholder="your@email.com",
                key="login_email",
            )

            password = st.text_input(
                "Password",
                type="password",
                placeholder="Enter password",
                key="login_password",
            )

            submit = st.form_submit_button(
                "Sign In",
                use_container_width=True,
            )

        # Handle form submission
        if submit:
            if not email or not password:
                st.error("Please enter email and password")
            else:
                with st.spinner("Signing in..."):
                    client = APIClient()
                    result = client.login(email, password)

                    if result.get("success"):
                        user = result["user"]
                        st.success(f"Welcome, {user.get('name', user.get('email'))}!")
                        st.rerun()
                    else:
                        st.error(result.get("error", "Login failed"))

        # Help text
        st.markdown("""
        <div class="login-footer">
            <p>Development credentials:</p>
            <p>gp@example.com / analyst@example.com</p>
        </div>
        """, unsafe_allow_html=True)
