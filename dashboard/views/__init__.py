"""Dashboard views module."""

from dashboard.views.login import render_login_page
from dashboard.views.health import render_health_page

__all__ = ["render_login_page", "render_health_page"]
