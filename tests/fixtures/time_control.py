"""Shared time-control fixtures — no real sleeps.

Usage:
    from tests.fixtures.time_control import frozen_time, mock_monotonic
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


class MockMonotonicClock:
    """Controllable monotonic clock for testing time-dependent state machines.

    Usage:
        clock = MockMonotonicClock(start=0.0)
        clock.advance(30.0)  # advance 30 seconds
        assert clock() == 30.0
    """

    def __init__(self, start: float = 0.0):
        self._time = start

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds

    @property
    def current(self) -> float:
        return self._time


@pytest.fixture
def mock_monotonic():
    """Replace time.monotonic with a controllable clock.

    Returns:
        MockMonotonicClock instance. Call .advance(seconds) to simulate time passing.
    """
    clock = MockMonotonicClock()
    with patch("time.monotonic", clock):
        yield clock


@pytest.fixture
def frozen_time():
    """Freeze datetime.now(timezone.utc) to a fixed value.

    Returns a callable that sets the frozen time.

    Usage:
        def test_something(frozen_time):
            frozen_time("2026-03-19T12:00:00+00:00")
            # datetime.now(timezone.utc) returns the frozen value
    """
    from datetime import datetime, timezone

    original_now = datetime.now
    _frozen_dt = [None]

    def _set_time(iso_str: str) -> datetime:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        _frozen_dt[0] = dt
        return dt

    def _patched_now(tz=None):
        if _frozen_dt[0] is not None and tz == timezone.utc:
            return _frozen_dt[0]
        return original_now(tz)

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now = _patched_now
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        yield _set_time
