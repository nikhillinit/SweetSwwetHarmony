"""
Shared fixtures for storage tests.

Provides:
- store: Fresh SignalStore with temp file DB
- store_with_signals: Store pre-populated with test signals
- sample_signal_data: Dict of sample signal data for testing
"""

import os
import tempfile
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any

import pytest
import pytest_asyncio

# Add project root to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


@pytest.fixture
def sample_signal_data() -> Dict[str, Any]:
    """Sample signal data for testing."""
    return {
        "signal_type": "funding",
        "source_api": "sec_edgar",
        "canonical_key": "ein:123456789",
        "company_name": "Acme Corp",
        "confidence": 0.75,
        "raw_data": {"amount": 500000, "investors": ["VC Fund A"]},
    }


@pytest.fixture
def sample_signal_data_2() -> Dict[str, Any]:
    """Second sample signal for testing."""
    return {
        "signal_type": "launch",
        "source_api": "product_hunt",
        "canonical_key": "domain:startup.io",
        "company_name": "Startup Inc",
        "confidence": 0.6,
        "raw_data": {"votes": 150, "category": "developer_tools"},
    }


@pytest.fixture
def sample_signal_data_github() -> Dict[str, Any]:
    """GitHub signal for testing."""
    return {
        "signal_type": "github_spike",
        "source_api": "github",
        "canonical_key": "github_org:awesome-startup",
        "company_name": "Awesome Startup",
        "confidence": 0.65,
        "raw_data": {"stars": 1250, "forks": 89, "language": "Python"},
    }


@pytest_asyncio.fixture
async def store() -> AsyncGenerator[SignalStore, None]:
    """Fresh SignalStore with temp file DB for each test."""
    # Create temp file for database
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store

    await store.close()

    # Clean up temp file
    try:
        os.unlink(path)
    except OSError:
        pass  # File may already be deleted


@pytest_asyncio.fixture
async def store_with_signals(store: SignalStore, sample_signal_data: Dict[str, Any], sample_signal_data_2: Dict[str, Any]) -> SignalStore:
    """Store pre-populated with test signals."""
    await store.save_signal(
        signal_type=sample_signal_data["signal_type"],
        source_api=sample_signal_data["source_api"],
        canonical_key=sample_signal_data["canonical_key"],
        company_name=sample_signal_data["company_name"],
        confidence=sample_signal_data["confidence"],
        raw_data=sample_signal_data["raw_data"],
    )
    await store.save_signal(
        signal_type=sample_signal_data_2["signal_type"],
        source_api=sample_signal_data_2["source_api"],
        canonical_key=sample_signal_data_2["canonical_key"],
        company_name=sample_signal_data_2["company_name"],
        confidence=sample_signal_data_2["confidence"],
        raw_data=sample_signal_data_2["raw_data"],
    )
    return store


@pytest.fixture
def temp_db_path() -> str:
    """Create a temp file path for database (caller handles cleanup)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path
