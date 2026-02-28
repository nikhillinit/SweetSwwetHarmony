import sys
from unittest.mock import MagicMock


def test_dashboard_views_does_not_import_starwatcher_eagerly():
    """Importing dashboard.views should not eagerly import starwatcher.

    Starwatcher pulls in streamlit.components (package-style import) and
    can break test collection in environments where Streamlit is stubbed.
    """

    # Stub streamlit for the import graph (other view modules use `import streamlit as st`).
    sys.modules.setdefault("streamlit", MagicMock())

    # Ensure a clean import state.
    sys.modules.pop("dashboard.views.starwatcher", None)
    sys.modules.pop("dashboard.views", None)

    __import__("dashboard.views")

    assert "dashboard.views.starwatcher" not in sys.modules
