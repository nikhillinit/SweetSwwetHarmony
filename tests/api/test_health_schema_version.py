"""PR8 — Tests for health endpoint schema version correctness.

Validates that the /health/detailed endpoint returns the actual
CURRENT_SCHEMA_VERSION instead of a hardcoded value.
"""

import ast
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestSchemaVersionNotHardcoded:
    """Verify health.py uses CURRENT_SCHEMA_VERSION, not a literal."""

    def test_imports_current_schema_version(self):
        """health.py must import CURRENT_SCHEMA_VERSION from signal_store."""
        path = os.path.join(ROOT, "api", "routers", "health.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename="health.py")

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "signal_store" in node.module:
                    for alias in node.names:
                        if alias.name == "CURRENT_SCHEMA_VERSION":
                            found = True
                            break

        assert found, (
            "health.py must import CURRENT_SCHEMA_VERSION from storage.signal_store."
        )

    def test_no_hardcoded_schema_version_16(self):
        """health.py must not have a hardcoded schema_version: 16."""
        path = os.path.join(ROOT, "api", "routers", "health.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        # Look for the old hardcoded pattern
        assert '"schema_version": 16' not in source, (
            "health.py still has hardcoded schema_version: 16. "
            "Must use CURRENT_SCHEMA_VERSION."
        )
        assert "'schema_version': 16" not in source, (
            "health.py still has hardcoded schema_version: 16. "
            "Must use CURRENT_SCHEMA_VERSION."
        )


class TestSchemaVersionValue:
    """Verify the CURRENT_SCHEMA_VERSION value is reasonable."""

    def test_schema_version_is_positive(self):
        """CURRENT_SCHEMA_VERSION should be a positive integer version."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 1

    def test_schema_version_is_integer(self):
        """CURRENT_SCHEMA_VERSION must be an integer."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION
        assert isinstance(CURRENT_SCHEMA_VERSION, int)


class TestDetailedHealthSchemaVersion:
    """Integration test: /health/detailed returns correct schema_version."""

    @pytest.fixture
    def app(self):
        """Minimal FastAPI app with health router."""
        from api.routers.health import router
        test_app = FastAPI()
        test_app.include_router(router)
        return test_app

    @pytest.fixture
    def mock_store(self):
        """Mock store that returns basic stats."""
        store = MagicMock()
        store.get_stats = AsyncMock(return_value={"total_signals": 10})
        store._get_db = AsyncMock()
        return store

    def test_detailed_health_returns_correct_schema_version(self, app, mock_store):
        """The /health/detailed endpoint should report CURRENT_SCHEMA_VERSION."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION

        app.state.store = mock_store

        # Patch external dependencies that might fail in test
        # check_activation_readiness is imported lazily inside the endpoint,
        # so patch at the source module
        with patch("api.routers.health.SIGNAL_HEALTH_AVAILABLE", False), \
             patch("api.routers.health.RELATIONSHIP_HEALTH_AVAILABLE", False), \
             patch("monitoring.activation_gate.check_activation_readiness",
                   side_effect=Exception("skip"), create=True):
            with patch.dict(os.environ, {}, clear=False):
                client = TestClient(app)
                resp = client.get("/health/detailed")

        assert resp.status_code == 200
        data = resp.json()

        # Find the database component
        db_component = None
        for comp in data["components"]:
            if comp["name"] == "database":
                db_component = comp
                break

        assert db_component is not None, "No 'database' component in health response"
        assert db_component["details"]["schema_version"] == CURRENT_SCHEMA_VERSION, (
            f"Expected schema_version={CURRENT_SCHEMA_VERSION}, "
            f"got {db_component['details']['schema_version']}"
        )
