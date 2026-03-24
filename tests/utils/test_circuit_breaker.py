"""Tests for utils.circuit_breaker — CircuitBreaker and CircuitOpenError."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.fixtures.time_control import MockMonotonicClock
from utils.circuit_breaker import CircuitBreaker, CircuitOpenError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cb(
    name: str = "test",
    failure_threshold: int = 3,
    recovery_timeout: float = 30.0,
    half_open_max_calls: int = 1,
) -> CircuitBreaker:
    """Build a CircuitBreaker with small defaults for tests."""
    return CircuitBreaker(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        half_open_max_calls=half_open_max_calls,
    )


async def _ok(*_args, **_kwargs) -> str:
    """Always-succeeding async function."""
    return "ok"


async def _fail(*_args, **_kwargs):
    """Always-failing async function."""
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Fixture: controllable monotonic clock
# ---------------------------------------------------------------------------

@pytest.fixture
def clock():
    """Patch ``time.monotonic`` with a controllable clock.

    The circuit breaker module does ``import time`` then calls
    ``time.monotonic()``, so patching the attribute on the *module* object
    is the most reliable approach.
    """
    mock_clock = MockMonotonicClock(start=1000.0)
    with patch("utils.circuit_breaker.time.monotonic", mock_clock):
        yield mock_clock


# ---------------------------------------------------------------------------
# 1. CLOSED -> OPEN on threshold
# ---------------------------------------------------------------------------

class TestClosedToOpen:
    """After failure_threshold consecutive failures, state changes to open."""

    @pytest.mark.asyncio
    async def test_trips_open_on_threshold(self, clock):
        """Circuit trips to OPEN after exactly failure_threshold failures."""
        cb = _make_cb(failure_threshold=3)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_does_not_trip_below_threshold(self, clock):
        """Circuit stays CLOSED when failures < threshold."""
        cb = _make_cb(failure_threshold=3)

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        assert cb.state == "closed"
        assert cb.failure_count == 2


# ---------------------------------------------------------------------------
# 2. Failure counting — successes reset the count
# ---------------------------------------------------------------------------

class TestFailureCounting:
    """Failures increment count; successes reset it."""

    @pytest.mark.asyncio
    async def test_failure_increments_count(self, clock):
        """Each failure increments the failure counter."""
        cb = _make_cb(failure_threshold=5)

        with pytest.raises(RuntimeError):
            await cb.call(_fail)
        assert cb.failure_count == 1

        with pytest.raises(RuntimeError):
            await cb.call(_fail)
        assert cb.failure_count == 2

    @pytest.mark.asyncio
    async def test_success_resets_count_in_closed(self, clock):
        """A success in CLOSED state resets failure_count to 0."""
        cb = _make_cb(failure_threshold=5)

        # Accumulate some failures
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)
        assert cb.failure_count == 3

        # One success resets
        await cb.call(_ok)
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_interleaved_success_prevents_trip(self, clock):
        """Interleaving a success prevents reaching the threshold."""
        cb = _make_cb(failure_threshold=3)

        # 2 failures, 1 success, 2 failures — never 3 consecutive
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)
        await cb.call(_ok)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        assert cb.state == "closed"
        assert cb.failure_count == 2


# ---------------------------------------------------------------------------
# 3. OPEN -> HALF_OPEN after timeout
# ---------------------------------------------------------------------------

class TestOpenToHalfOpen:
    """After recovery_timeout seconds elapse, state transitions to half_open."""

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self, clock):
        """State becomes half_open once recovery_timeout has elapsed."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        # Trip the circuit
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)
        assert cb.state == "open"

        # Advance past timeout
        clock.advance(31.0)
        assert cb.state == "half_open"

    @pytest.mark.asyncio
    async def test_stays_open_before_timeout(self, clock):
        """State remains open when less than recovery_timeout has passed."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        clock.advance(29.0)
        assert cb.state == "open"


# ---------------------------------------------------------------------------
# 4. HALF_OPEN -> CLOSED on success
# ---------------------------------------------------------------------------

class TestHalfOpenToClosed:
    """A successful call in half_open resets to closed."""

    @pytest.mark.asyncio
    async def test_success_in_half_open_closes_circuit(self, clock):
        """One success (half_open_max_calls=1) transitions to CLOSED."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0, half_open_max_calls=1)

        # Trip open
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        # Wait for half_open
        clock.advance(30.0)
        assert cb.state == "half_open"

        # Succeed
        result = await cb.call(_ok)
        assert result == "ok"
        assert cb.state == "closed"
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_multiple_successes_needed(self, clock):
        """When half_open_max_calls > 1, need that many successes to close."""
        cb = _make_cb(
            failure_threshold=3,
            recovery_timeout=30.0,
            half_open_max_calls=2,
        )

        # Trip open
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        clock.advance(30.0)
        assert cb.state == "half_open"

        # First success — still half_open
        await cb.call(_ok)
        assert cb.state == "half_open"

        # Second success — now closed
        await cb.call(_ok)
        assert cb.state == "closed"


# ---------------------------------------------------------------------------
# 5. HALF_OPEN -> OPEN on failure
# ---------------------------------------------------------------------------

class TestHalfOpenToOpen:
    """A failed call in half_open returns to open."""

    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens(self, clock):
        """A single failure in HALF_OPEN re-trips to OPEN."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        # Trip open
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        # Enter half_open
        clock.advance(30.0)
        assert cb.state == "half_open"

        # Fail again
        with pytest.raises(RuntimeError):
            await cb.call(_fail)

        assert cb.state == "open"


# ---------------------------------------------------------------------------
# 6. Timeout boundary edge cases
# ---------------------------------------------------------------------------

class TestTimeoutBoundary:
    """At exactly recovery_timeout seconds, behavior is correct."""

    @pytest.mark.asyncio
    async def test_exactly_at_timeout_transitions(self, clock):
        """At exactly recovery_timeout seconds, state should be half_open.

        The implementation uses ``elapsed >= recovery_timeout``.
        """
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        # Advance *exactly* to the boundary
        clock.advance(30.0)
        assert cb.state == "half_open"

    @pytest.mark.asyncio
    async def test_one_tick_before_timeout_stays_open(self, clock):
        """At recovery_timeout - epsilon, still open."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        clock.advance(29.999)
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_one_tick_after_timeout_is_half_open(self, clock):
        """At recovery_timeout + epsilon, half_open."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        clock.advance(30.001)
        assert cb.state == "half_open"


# ---------------------------------------------------------------------------
# 7. CircuitOpenError raised with correct retry_after
# ---------------------------------------------------------------------------

class TestCircuitOpenError:
    """Calling when open raises CircuitOpenError with correct retry_after."""

    @pytest.mark.asyncio
    async def test_raises_circuit_open_error(self, clock):
        """Calling when OPEN raises CircuitOpenError."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(_ok)

        assert exc_info.value.name == "test"

    @pytest.mark.asyncio
    async def test_retry_after_decreases_over_time(self, clock):
        """retry_after should reflect remaining time until recovery."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        # 10 seconds later, retry_after should be ~20
        clock.advance(10.0)

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(_ok)

        assert exc_info.value.retry_after == pytest.approx(20.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_error_message_contains_name(self, clock):
        """Error message should include the circuit breaker name."""
        cb = _make_cb(name="notion", failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        with pytest.raises(CircuitOpenError, match="notion"):
            await cb.call(_ok)

    def test_circuit_open_error_attributes(self):
        """CircuitOpenError stores name and retry_after."""
        err = CircuitOpenError("github", 15.5)
        assert err.name == "github"
        assert err.retry_after == 15.5
        assert "github" in str(err)
        assert "15.5" in str(err)


# ---------------------------------------------------------------------------
# 8. stats() returns correct dict
# ---------------------------------------------------------------------------

class TestStats:
    """stats() returns correct dict with state, failure_count, etc."""

    def test_initial_stats(self):
        """Stats for a freshly-created breaker."""
        cb = _make_cb(name="svc", failure_threshold=5, recovery_timeout=60.0)
        s = cb.stats()

        assert s["name"] == "svc"
        assert s["state"] == "closed"
        assert s["failure_count"] == 0
        assert s["success_count"] == 0
        assert s["last_failure_time"] is None
        assert s["failure_threshold"] == 5
        assert s["recovery_timeout"] == 60.0

    @pytest.mark.asyncio
    async def test_stats_after_failures(self, clock):
        """Stats reflect failure_count and last_failure_time after failures."""
        cb = _make_cb(failure_threshold=5)

        with pytest.raises(RuntimeError):
            await cb.call(_fail)

        s = cb.stats()
        assert s["failure_count"] == 1
        assert s["last_failure_time"] is not None

    @pytest.mark.asyncio
    async def test_stats_after_successes(self, clock):
        """Stats reflect success_count."""
        cb = _make_cb()

        await cb.call(_ok)
        await cb.call(_ok)

        s = cb.stats()
        assert s["success_count"] == 2

    @pytest.mark.asyncio
    async def test_stats_reflect_open_state(self, clock):
        """Stats show 'open' state after tripping."""
        cb = _make_cb(failure_threshold=3)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        s = cb.stats()
        assert s["state"] == "open"
        assert s["failure_count"] == 3


# ---------------------------------------------------------------------------
# 9. reset() resets state to closed
# ---------------------------------------------------------------------------

class TestReset:
    """reset() resets state to closed."""

    @pytest.mark.asyncio
    async def test_reset_from_open(self, clock):
        """reset() returns OPEN circuit to CLOSED."""
        cb = _make_cb(failure_threshold=3)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)
        assert cb.state == "open"

        cb.reset()

        assert cb.state == "closed"
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_reset_allows_calls_again(self, clock):
        """After reset, calls succeed normally."""
        cb = _make_cb(failure_threshold=3)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        cb.reset()
        result = await cb.call(_ok)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_reset_from_half_open(self, clock):
        """reset() returns HALF_OPEN circuit to CLOSED."""
        cb = _make_cb(failure_threshold=3, recovery_timeout=30.0)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        clock.advance(30.0)
        assert cb.state == "half_open"

        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0


# ---------------------------------------------------------------------------
# 10. Decorator (protect) integration
# ---------------------------------------------------------------------------

class TestProtectDecorator:
    """The @cb.protect decorator wraps async functions with the breaker."""

    @pytest.mark.asyncio
    async def test_protect_passes_through_on_closed(self, clock):
        """Decorated function works normally when closed."""
        cb = _make_cb()

        @cb.protect
        async def my_func(x: int) -> int:
            return x * 2

        assert await my_func(5) == 10

    @pytest.mark.asyncio
    async def test_protect_raises_circuit_open(self, clock):
        """Decorated function raises CircuitOpenError when open."""
        cb = _make_cb(failure_threshold=3)

        @cb.protect
        async def always_fail():
            raise RuntimeError("nope")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await always_fail()

        with pytest.raises(CircuitOpenError):
            await always_fail()


# ---------------------------------------------------------------------------
# 11. Full lifecycle round-trip
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end: closed -> open -> half_open -> closed."""

    @pytest.mark.asyncio
    async def test_full_state_cycle(self, clock):
        """Walk through the complete state machine lifecycle."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=10.0)

        # Start closed
        assert cb.state == "closed"

        # Trip open with 2 failures
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)
        assert cb.state == "open"

        # Rejected while open
        with pytest.raises(CircuitOpenError):
            await cb.call(_ok)

        # Wait for recovery
        clock.advance(10.0)
        assert cb.state == "half_open"

        # Succeed in half_open -> back to closed
        result = await cb.call(_ok)
        assert result == "ok"
        assert cb.state == "closed"
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_double_trip_cycle(self, clock):
        """Trip, recover to half_open, fail again, re-trip, then recover."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=10.0)

        # First trip
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)
        assert cb.state == "open"

        # Recover to half_open
        clock.advance(10.0)
        assert cb.state == "half_open"

        # Fail in half_open -> re-open
        with pytest.raises(RuntimeError):
            await cb.call(_fail)
        assert cb.state == "open"

        # Recover again
        clock.advance(10.0)
        assert cb.state == "half_open"

        # Succeed this time -> closed
        await cb.call(_ok)
        assert cb.state == "closed"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
