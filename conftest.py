"""
Root-level pytest configuration for Discovery Engine.

Configures:
- pytest-asyncio for async test support
- Custom markers (integration, etc.)
- Test environment setup
"""

import pytest


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
