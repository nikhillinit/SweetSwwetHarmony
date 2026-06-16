"""
Root-level pytest configuration for Discovery Engine.

Configures:
- pytest-asyncio for async test support
- Custom markers (integration, etc.)
- Test environment setup
"""

import os

import pytest

# #149 durability guard: storage.db_paths.resolve_canonical_db_path() fails closed
# when the canonical signals DB resolves inside the repo working tree. The test
# suite legitimately uses in-tree scratch/fixture DBs (tmp_path or "signals.db"),
# so default the allow-flag ON for the whole session. setdefault respects an
# explicit override (e.g. a test or CI job that wants to exercise fail-closed),
# and tests asserting the guard fires monkeypatch.delenv() it. Set in os.environ
# so subprocesses spawned with the inherited environment see it too.
os.environ.setdefault("HARMONIC_ALLOW_IN_TREE_DB", "true")


def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    # Register custom markers to avoid warnings
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (may require network access)"
    )
    config.addinivalue_line(
        "markers",
        "asyncio: marks tests as async (automatically handled by pytest-asyncio)"
    )


# pytest-asyncio configuration
# Set asyncio_mode to "auto" so async tests don't need @pytest.mark.asyncio decorators
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(scope="session")
def event_loop_policy():
    """
    Set event loop policy for the test session.

    This ensures consistent async behavior across all tests.
    """
    import asyncio
    return asyncio.get_event_loop_policy()
