"""Dashboard pages module."""

from dashboard.pages.login import render_login_page
from dashboard.pages.health import render_health_page

__all__ = ["render_login_page", "render_health_page"]
