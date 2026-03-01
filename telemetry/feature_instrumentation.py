"""Feature instrumentation wrapper — automatic telemetry for ACTIVE/SHADOW features.

Wraps feature computation functions with:
- Timer metrics via utils.instrumentation.metrics
- Counter metrics (call count, success/failure)
- SHADOW mode: automatic logging to shadow_log
- OFF mode: skip execution entirely

Supports both async and sync feature functions (fix E).

Usage:
    from telemetry.feature_instrumentation import instrument_feature
    from utils.feature_states import FeatureRegistry

    registry = FeatureRegistry()

    @instrument_feature(feature_name="boilerplate_defense", registry=registry)
    async def compute_boilerplate_match(signal, *, canonical_key):
        ...

    # Or with custom key extraction:
    @instrument_feature(
        feature_name="thesis_match", registry=registry,
        canonical_key_getter=lambda args, kwargs: kwargs.get("company_key"),
    )
    async def compute_thesis(*, company_key):
        ...
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
from typing import Any, Callable, Optional, TYPE_CHECKING

from utils.feature_states import FeatureRegistry, FeatureState
from utils.instrumentation import metrics

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


def instrument_feature(
    fn: Optional[Callable] = None,
    *,
    feature_name: str,
    registry: FeatureRegistry,
    store: Optional["SignalStore"] = None,
    canonical_key_getter: Optional[Callable] = None,
) -> Callable:
    """Wrap a feature computation function with instrumentation.

    Args:
        fn: The function to wrap (async or sync)
        feature_name: Feature name for metrics and shadow_log
        registry: FeatureRegistry to check state
        store: Optional SignalStore for shadow_log writes
        canonical_key_getter: Optional callable(args, kwargs) -> str
            for extracting canonical_key. Defaults to kwargs["canonical_key"].

    Returns:
        Wrapped function that:
        - OFF: returns None immediately
        - ACTIVE: executes + records metrics
        - SHADOW: executes + records metrics + writes shadow_log
    """

    def _get_canonical_key(args: tuple, kwargs: dict) -> str:
        if canonical_key_getter is not None:
            return canonical_key_getter(args, kwargs)
        return kwargs.get("canonical_key", "unknown")

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                state = registry.get_state(feature_name)

                if state == FeatureState.OFF:
                    metrics.increment(f"feature.{feature_name}.skipped")
                    return None

                metrics.increment(f"feature.{feature_name}.invocations")

                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    metrics.record_timing(
                        f"feature.{feature_name}.latency", elapsed_ms
                    )
                    metrics.increment(f"feature.{feature_name}.success")

                    if state == FeatureState.SHADOW and store is not None:
                        key = _get_canonical_key(args, kwargs)
                        try:
                            await store.log_shadow_computation(
                                feature_name=feature_name,
                                canonical_key=key,
                                computed_value=(
                                    result
                                    if isinstance(result, dict)
                                    else {"value": result}
                                ),
                            )
                        except Exception as log_err:
                            logger.warning(
                                "Shadow log failed for %s: %s",
                                feature_name, log_err,
                            )

                    return result

                except Exception:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    metrics.record_timing(
                        f"feature.{feature_name}.latency", elapsed_ms
                    )
                    metrics.increment(f"feature.{feature_name}.failure")
                    raise

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                state = registry.get_state(feature_name)

                if state == FeatureState.OFF:
                    metrics.increment(f"feature.{feature_name}.skipped")
                    return None

                metrics.increment(f"feature.{feature_name}.invocations")

                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    metrics.record_timing(
                        f"feature.{feature_name}.latency", elapsed_ms
                    )
                    metrics.increment(f"feature.{feature_name}.success")

                    if state == FeatureState.SHADOW and store is not None:
                        key = _get_canonical_key(args, kwargs)
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(
                                    store.log_shadow_computation(
                                        feature_name=feature_name,
                                        canonical_key=key,
                                        computed_value=(
                                            result
                                            if isinstance(result, dict)
                                            else {"value": result}
                                        ),
                                    )
                                )
                            else:
                                logger.debug(
                                    "No running event loop; skipping shadow log for %s",
                                    feature_name,
                                )
                        except Exception as log_err:
                            logger.warning(
                                "Shadow log failed for %s: %s",
                                feature_name, log_err,
                            )

                    return result

                except Exception:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    metrics.record_timing(
                        f"feature.{feature_name}.latency", elapsed_ms
                    )
                    metrics.increment(f"feature.{feature_name}.failure")
                    raise

            return sync_wrapper

    if fn is not None:
        return decorator(fn)
    return decorator
