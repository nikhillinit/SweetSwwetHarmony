"""Dashboard views module."""

from dashboard.views.login import render_login_page
from dashboard.views.health import render_health_page
from dashboard.views.ops_health import render_ops_health_page
from dashboard.views.scheduler import render_scheduler_page
from dashboard.views.cost_analysis import render_cost_analysis_page
from dashboard.views.triage_fast import render_triage_fast_page
from dashboard.views.triage_detail import render_triage_detail_page
from dashboard.views.batch_publish import render_batch_publish_page
from dashboard.views.starwatcher import render_starwatcher_page

__all__ = [
    "render_login_page",
    "render_health_page",
    "render_ops_health_page",
    "render_scheduler_page",
    "render_cost_analysis_page",
    "render_triage_fast_page",
    "render_triage_detail_page",
    "render_batch_publish_page",
    "render_starwatcher_page",
]
