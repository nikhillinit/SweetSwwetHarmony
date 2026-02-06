"""In-memory TTL cache for health endpoints.

Prevents DB hammering when monitoring tools poll every few seconds.
"""

import functools
import time
from typing import Any, Callable


def ttl_cache(ttl_seconds: float = 30.0) -> Callable:
    """Decorator that caches function results for *ttl_seconds*.

    - Caches based on positional + keyword arguments (hashable only).
    - Provides a ``cache_clear()`` method on the decorated function.
    """

    def decorator(func: Callable) -> Callable:
        cache: dict[tuple, tuple[float, Any]] = {}

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = args + tuple(sorted(kwargs.items()))
            now = time.monotonic()
            if key in cache:
                ts, value = cache[key]
                if now - ts < ttl_seconds:
                    return value
            result = func(*args, **kwargs)
            cache[key] = (now, result)
            return result

        def cache_clear() -> None:
            cache.clear()

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        return wrapper

    return decorator
