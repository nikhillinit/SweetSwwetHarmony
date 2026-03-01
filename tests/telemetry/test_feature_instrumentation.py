"""Tests for feature instrumentation wrapper."""

import os
import tempfile
from unittest.mock import patch

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore
from telemetry.feature_instrumentation import instrument_feature
from utils.feature_states import FeatureRegistry, FeatureState
from utils.instrumentation import metrics


@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


class TestInstrumentFeatureActive:
    @pytest.mark.asyncio
    async def test_active_executes_and_records_metrics(self):
        registry = FeatureRegistry()

        async def my_feature(*, canonical_key="test"):
            return {"score": 0.9}

        with patch.object(
            registry, "get_state", return_value=FeatureState.ACTIVE
        ):
            wrapped = instrument_feature(
                my_feature,
                feature_name="test_feat",
                registry=registry,
            )
            result = await wrapped(canonical_key="key1")

        assert result == {"score": 0.9}
        assert metrics.get_count("feature.test_feat.invocations") == 1
        assert metrics.get_count("feature.test_feat.success") == 1
        assert metrics.get_count("feature.test_feat.failure") == 0
        timer = metrics.get_timer("feature.test_feat.latency")
        assert timer["count"] == 1


class TestInstrumentFeatureShadow:
    @pytest.mark.asyncio
    async def test_shadow_logs_to_shadow_log(self, store):
        registry = FeatureRegistry()

        async def my_feature(*, canonical_key="test"):
            return {"score": 0.5}

        with patch.object(
            registry, "get_state", return_value=FeatureState.SHADOW
        ):
            wrapped = instrument_feature(
                my_feature,
                feature_name="shadow_feat",
                registry=registry,
                store=store,
            )
            result = await wrapped(canonical_key="company:acme")

        assert result == {"score": 0.5}
        assert metrics.get_count("feature.shadow_feat.success") == 1

        # Verify shadow_log was written
        logs = await store.get_shadow_logs(feature_name="shadow_feat")
        assert len(logs) >= 1


class TestInstrumentFeatureOff:
    @pytest.mark.asyncio
    async def test_off_skips_execution(self):
        registry = FeatureRegistry()
        call_count = 0

        async def my_feature(*, canonical_key="test"):
            nonlocal call_count
            call_count += 1
            return {"score": 0.9}

        with patch.object(
            registry, "get_state", return_value=FeatureState.OFF
        ):
            wrapped = instrument_feature(
                my_feature,
                feature_name="off_feat",
                registry=registry,
            )
            result = await wrapped(canonical_key="key1")

        assert result is None
        assert call_count == 0
        assert metrics.get_count("feature.off_feat.skipped") == 1
        assert metrics.get_count("feature.off_feat.invocations") == 0


class TestInstrumentFeatureError:
    @pytest.mark.asyncio
    async def test_error_increments_failure_counter(self):
        registry = FeatureRegistry()

        async def my_feature(*, canonical_key="test"):
            raise ValueError("computation failed")

        with patch.object(
            registry, "get_state", return_value=FeatureState.ACTIVE
        ):
            wrapped = instrument_feature(
                my_feature,
                feature_name="err_feat",
                registry=registry,
            )
            with pytest.raises(ValueError, match="computation failed"):
                await wrapped(canonical_key="key1")

        assert metrics.get_count("feature.err_feat.failure") == 1
        assert metrics.get_count("feature.err_feat.success") == 0


class TestInstrumentFeatureDecorator:
    @pytest.mark.asyncio
    async def test_decorator_syntax(self):
        registry = FeatureRegistry()

        with patch.object(
            registry, "get_state", return_value=FeatureState.ACTIVE
        ):
            @instrument_feature(
                feature_name="decorated_feat", registry=registry,
            )
            async def compute(*, canonical_key="test"):
                return 42

            result = await compute(canonical_key="key1")

        assert result == 42
        assert metrics.get_count("feature.decorated_feat.success") == 1


class TestInstrumentSyncFunction:
    def test_sync_function_works(self):
        """Sync functions are wrapped and callable without await."""
        registry = FeatureRegistry()

        def sync_compute(*, canonical_key="test"):
            return {"sync_score": 0.7}

        with patch.object(
            registry, "get_state", return_value=FeatureState.ACTIVE
        ):
            wrapped = instrument_feature(
                sync_compute,
                feature_name="sync_feat",
                registry=registry,
            )
            result = wrapped(canonical_key="key1")

        assert result == {"sync_score": 0.7}
        assert metrics.get_count("feature.sync_feat.success") == 1

    def test_sync_off_skips(self):
        """Sync wrapper returns None when feature is OFF."""
        registry = FeatureRegistry()

        def sync_compute(*, canonical_key="test"):
            return {"sync_score": 0.7}

        with patch.object(
            registry, "get_state", return_value=FeatureState.OFF
        ):
            wrapped = instrument_feature(
                sync_compute,
                feature_name="sync_off_feat",
                registry=registry,
            )
            result = wrapped(canonical_key="key1")

        assert result is None
        assert metrics.get_count("feature.sync_off_feat.skipped") == 1


class TestCanonicalKeyGetter:
    @pytest.mark.asyncio
    async def test_custom_key_getter(self, store):
        registry = FeatureRegistry()

        async def my_feature(company_key: str):
            return {"score": 0.5}

        with patch.object(
            registry, "get_state", return_value=FeatureState.SHADOW
        ):
            wrapped = instrument_feature(
                my_feature,
                feature_name="getter_feat",
                registry=registry,
                store=store,
                canonical_key_getter=lambda args, kwargs: args[0] if args else "unknown",
            )
            result = await wrapped("domain:acme.ai")

        assert result == {"score": 0.5}
        logs = await store.get_shadow_logs(feature_name="getter_feat")
        assert len(logs) >= 1
