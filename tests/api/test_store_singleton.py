"""PR8 — Tests for shared store singleton via api.db.get_store.

Validates that all API routers use the shared get_store dependency
from api.db instead of creating per-request SignalStore instances.
"""

import ast
import os
import pytest


# List of router modules that MUST use api.db.get_store
ROUTER_MODULES = [
    "api/routers/actions.py",
    "api/routers/companies.py",
    "api/routers/entities.py",
    "api/routers/health.py",
    "api/routers/jobs.py",
    "api/routers/public.py",
]

# Root directory
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestNoPerRequestStoreCreation:
    """Ensure routers don't create SignalStore() per request."""

    @pytest.mark.parametrize("module_path", ROUTER_MODULES)
    def test_no_local_get_store_function(self, module_path):
        """Router modules must not define their own get_store function."""
        full_path = os.path.join(ROOT, module_path)
        with open(full_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename=module_path)

        local_get_store_defs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "get_store":
                    local_get_store_defs.append(node.lineno)

        assert local_get_store_defs == [], (
            f"{module_path} defines local get_store() at line(s) {local_get_store_defs}. "
            f"All routers must import get_store from api.db."
        )

    @pytest.mark.parametrize("module_path", ROUTER_MODULES)
    def test_no_signalstore_constructor_call(self, module_path):
        """Router modules must not call SignalStore() directly."""
        full_path = os.path.join(ROOT, module_path)
        with open(full_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename=module_path)

        constructor_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for SignalStore() calls
                func = node.func
                if isinstance(func, ast.Name) and func.id == "SignalStore":
                    constructor_calls.append(node.lineno)
                elif isinstance(func, ast.Attribute) and func.attr == "SignalStore":
                    constructor_calls.append(node.lineno)

        assert constructor_calls == [], (
            f"{module_path} calls SignalStore() at line(s) {constructor_calls}. "
            f"Routers must use the shared store from api.db.get_store."
        )


class TestSharedGetStoreImported:
    """Ensure routers that need a store import it from api.db."""

    @pytest.mark.parametrize("module_path", ROUTER_MODULES)
    def test_imports_get_store_from_api_db(self, module_path):
        """Router modules must import get_store from api.db."""
        full_path = os.path.join(ROOT, module_path)
        with open(full_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename=module_path)

        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "api.db":
                    for alias in node.names:
                        if alias.name == "get_store":
                            found = True
                            break

        assert found, (
            f"{module_path} does not import get_store from api.db. "
            f"All routers must use the shared dependency."
        )


class TestGetStoreFallback:
    """Test that the shared get_store handles missing app.state.store."""

    @pytest.mark.asyncio
    async def test_get_store_returns_app_state_store(self):
        """get_store returns the lifespan-managed store from app.state."""
        from unittest.mock import MagicMock, AsyncMock
        from api.db import get_store

        mock_store = MagicMock()
        mock_request = MagicMock()
        mock_request.app.state.store = mock_store

        result = await get_store(mock_request)
        assert result is mock_store

    @pytest.mark.asyncio
    async def test_get_store_fallback_creates_new_store(self):
        """get_store creates and initializes a new store when app.state.store is missing."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from api.db import get_store

        mock_request = MagicMock()
        mock_request.app.state = MagicMock(spec=[])  # No 'store' attribute

        mock_store_instance = MagicMock()
        mock_store_instance.initialize = AsyncMock()

        # SignalStore is imported lazily inside get_store, patch at source module
        with patch("storage.signal_store.SignalStore", return_value=mock_store_instance) as mock_cls:
            result = await get_store(mock_request)

        mock_cls.assert_called_once()
        mock_store_instance.initialize.assert_awaited_once()
        assert result is mock_store_instance
