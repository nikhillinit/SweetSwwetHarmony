"""Phase 7.3 — Tests for circuit breaker pattern.

TDD RED: These tests should fail until utils/circuit_breaker.py is implemented.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Import the circuit breaker (will fail until implemented)
# ---------------------------------------------------------------------------


@pytest.fixture
def make_breaker():
    """Factory to create a CircuitBreaker with configurable thresholds."""
    from utils.circuit_breaker import CircuitBreaker

    def _make(
        name="test",
        failure_threshold=3,
        recovery_timeout=1.0,
        half_open_max_calls=1,
    ):
        return CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            half_open_max_calls=half_open_max_calls,
        )

    return _make


# ===========================================================================
# State Transition Tests
# ===========================================================================


class TestCircuitBreakerStates:
    """Verify CLOSED → OPEN → HALF_OPEN → CLOSED transitions."""

    @pytest.mark.asyncio
    async def test_starts_closed(self, make_breaker):
        cb = make_breaker()
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_stays_closed_on_success(self, make_breaker):
        cb = make_breaker(failure_threshold=3)
        func = AsyncMock(return_value="ok")

        result = await cb.call(func)
        assert result == "ok"
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self, make_breaker):
        """After N consecutive failures, circuit trips to OPEN."""
        cb = make_breaker(failure_threshold=3)
        func = AsyncMock(side_effect=ConnectionError("down"))

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(func)

        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_open_rejects_immediately(self, make_breaker):
        """While OPEN, calls are rejected without invoking the function."""
        from utils.circuit_breaker import CircuitOpenError

        cb = make_breaker(failure_threshold=1)
        func = AsyncMock(side_effect=ConnectionError("down"))

        # Trip the breaker
        with pytest.raises(ConnectionError):
            await cb.call(func)
        assert cb.state == "open"

        # Next call should be rejected immediately
        func.reset_mock()
        with pytest.raises(CircuitOpenError):
            await cb.call(func)
        func.assert_not_called()

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self, make_breaker):
        """After recovery_timeout, circuit moves to HALF_OPEN."""
        cb = make_breaker(failure_threshold=1, recovery_timeout=0.1)
        func = AsyncMock(side_effect=ConnectionError("down"))

        # Trip
        with pytest.raises(ConnectionError):
            await cb.call(func)
        assert cb.state == "open"

        # Wait for recovery timeout
        await asyncio.sleep(0.15)
        assert cb.state == "half_open"

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self, make_breaker):
        """Successful call in HALF_OPEN returns to CLOSED."""
        cb = make_breaker(failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=1)
        fail_func = AsyncMock(side_effect=ConnectionError("down"))

        # Trip
        with pytest.raises(ConnectionError):
            await cb.call(fail_func)

        # Wait for half-open
        await asyncio.sleep(0.15)
        assert cb.state == "half_open"

        # Successful probe
        ok_func = AsyncMock(return_value="recovered")
        result = await cb.call(ok_func)
        assert result == "recovered"
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self, make_breaker):
        """Failed call in HALF_OPEN returns to OPEN."""
        cb = make_breaker(failure_threshold=1, recovery_timeout=0.1)
        fail_func = AsyncMock(side_effect=ConnectionError("down"))

        # Trip
        with pytest.raises(ConnectionError):
            await cb.call(fail_func)

        # Wait for half-open
        await asyncio.sleep(0.15)
        assert cb.state == "half_open"

        # Failed probe → re-open
        with pytest.raises(ConnectionError):
            await cb.call(fail_func)
        assert cb.state == "open"


# ===========================================================================
# Failure Counting Tests
# ===========================================================================


class TestFailureCounting:
    """Verify failure counter behavior."""

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, make_breaker):
        """A success between failures resets the counter."""
        cb = make_breaker(failure_threshold=3)
        fail_func = AsyncMock(side_effect=ConnectionError("down"))
        ok_func = AsyncMock(return_value="ok")

        # 2 failures
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.call(fail_func)

        # 1 success resets counter
        await cb.call(ok_func)

        # 2 more failures — should still be closed (counter reset)
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.call(fail_func)
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_failure_count_exposed(self, make_breaker):
        """Can inspect the current failure count."""
        cb = make_breaker(failure_threshold=5)
        fail_func = AsyncMock(side_effect=ConnectionError("down"))

        with pytest.raises(ConnectionError):
            await cb.call(fail_func)
        assert cb.failure_count == 1

        with pytest.raises(ConnectionError):
            await cb.call(fail_func)
        assert cb.failure_count == 2


# ===========================================================================
# Decorator / Context Manager Tests
# ===========================================================================


class TestCircuitBreakerDecorator:
    """Test the decorator pattern for wrapping async functions."""

    @pytest.mark.asyncio
    async def test_decorator_wraps_function(self, make_breaker):
        """@circuit_breaker.protect wraps an async function."""
        cb = make_breaker(failure_threshold=2)

        @cb.protect
        async def flaky_service():
            return "data"

        result = await flaky_service()
        assert result == "data"

    @pytest.mark.asyncio
    async def test_decorator_trips_on_failures(self, make_breaker):
        """Decorated function trips the circuit after threshold failures."""
        from utils.circuit_breaker import CircuitOpenError

        cb = make_breaker(failure_threshold=2)
        call_count = 0

        @cb.protect
        async def broken_service():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("nope")

        # 2 failures → trip
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await broken_service()

        # 3rd call → rejected without calling
        with pytest.raises(CircuitOpenError):
            await broken_service()
        assert call_count == 2  # Only called twice, not three times


# ===========================================================================
# Stats / Observability Tests
# ===========================================================================


class TestCircuitBreakerStats:
    """Circuit breaker exposes useful stats for monitoring."""

    @pytest.mark.asyncio
    async def test_stats_dict(self, make_breaker):
        """stats() returns a dict with key metrics."""
        cb = make_breaker(failure_threshold=3)
        ok_func = AsyncMock(return_value="ok")

        await cb.call(ok_func)

        stats = cb.stats()
        assert stats["name"] == "test"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["success_count"] >= 1
        assert "last_failure_time" in stats

    @pytest.mark.asyncio
    async def test_manual_reset(self, make_breaker):
        """reset() forces circuit back to CLOSED."""
        cb = make_breaker(failure_threshold=1)
        fail_func = AsyncMock(side_effect=ConnectionError("down"))

        with pytest.raises(ConnectionError):
            await cb.call(fail_func)
        assert cb.state == "open"

        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0
