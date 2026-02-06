"""Circuit breaker pattern for external service calls.

Prevents cascading failures by temporarily blocking calls to services that
are consistently failing.  States: CLOSED → OPEN → HALF_OPEN → CLOSED.

Usage::

    cb = CircuitBreaker("notion", failure_threshold=5, recovery_timeout=30)

    # Direct call
    result = await cb.call(some_async_func, arg1, arg2)

    # Decorator
    @cb.protect
    async def call_notion():
        ...
"""

import functools
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker '{name}' is open. Retry after {retry_after:.1f}s."
        )


class CircuitBreaker:
    """Async-compatible circuit breaker.

    Parameters
    ----------
    name:
        Identifier for this breaker (e.g. "notion", "github").
    failure_threshold:
        Consecutive failures before the circuit trips to OPEN.
    recovery_timeout:
        Seconds to wait in OPEN before transitioning to HALF_OPEN.
    half_open_max_calls:
        Number of probe calls allowed in HALF_OPEN state.
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        # Internal state
        self._state = "closed"
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None
        self._opened_at: Optional[float] = None

    # -- Properties -----------------------------------------------------------

    @property
    def state(self) -> str:
        """Current state, with automatic OPEN → HALF_OPEN transition."""
        if self._state == "open" and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.recovery_timeout:
                self._state = "half_open"
                self._half_open_calls = 0
                logger.info("Circuit '%s' transitioned to HALF_OPEN", self.name)
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    # -- Core API -------------------------------------------------------------

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute *func* through the circuit breaker.

        Raises ``CircuitOpenError`` if the circuit is open and the recovery
        timeout has not elapsed.
        """
        current = self.state  # triggers auto-transition check

        if current == "open":
            raise CircuitOpenError(
                self.name, self.recovery_timeout - (time.monotonic() - self._opened_at)
            )

        try:
            result = await func(*args, **kwargs)
        except Exception:
            self._record_failure()
            raise

        self._record_success()
        return result

    # -- Decorator ------------------------------------------------------------

    def protect(self, func: Callable) -> Callable:
        """Decorator to wrap an async function with this circuit breaker."""

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await self.call(func, *args, **kwargs)

        return wrapper

    # -- Stats & Reset --------------------------------------------------------

    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }

    def reset(self) -> None:
        """Force the circuit back to CLOSED."""
        self._state = "closed"
        self._failure_count = 0
        self._half_open_calls = 0
        self._opened_at = None
        logger.info("Circuit '%s' manually reset to CLOSED", self.name)

    # -- Internal helpers -----------------------------------------------------

    def _record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == "half_open":
            # Any failure in half-open → re-open
            self._trip()
        elif self._state == "closed" and self._failure_count >= self.failure_threshold:
            self._trip()

    def _record_success(self) -> None:
        self._success_count += 1

        if self._state == "half_open":
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                self._state = "closed"
                self._failure_count = 0
                self._opened_at = None
                logger.info("Circuit '%s' recovered → CLOSED", self.name)
        elif self._state == "closed":
            # Success resets consecutive failure count
            self._failure_count = 0

    def _trip(self) -> None:
        self._state = "open"
        self._opened_at = time.monotonic()
        logger.warning(
            "Circuit '%s' tripped to OPEN after %d failures",
            self.name,
            self._failure_count,
        )
