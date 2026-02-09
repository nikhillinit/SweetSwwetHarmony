"""Tests for lightweight in-process instrumentation."""

import time

import pytest

from utils.instrumentation import Metrics


class TestCounters:
    def test_increment(self):
        m = Metrics()
        m.increment("test.counter")
        assert m.get_count("test.counter") == 1

    def test_increment_by_amount(self):
        m = Metrics()
        m.increment("test.counter", 5)
        assert m.get_count("test.counter") == 5

    def test_unknown_counter_is_zero(self):
        m = Metrics()
        assert m.get_count("nonexistent") == 0

    def test_multiple_increments(self):
        m = Metrics()
        m.increment("a")
        m.increment("a")
        m.increment("a")
        assert m.get_count("a") == 3


class TestTimers:
    def test_timer_context_manager(self):
        m = Metrics()
        with m.timer("test.op"):
            time.sleep(0.01)
        stats = m.get_timer("test.op")
        assert stats["count"] == 1
        assert stats["avg_ms"] > 0
        assert stats["min_ms"] > 0
        assert stats["max_ms"] > 0

    def test_manual_timing(self):
        m = Metrics()
        m.record_timing("db.query", 42.5)
        stats = m.get_timer("db.query")
        assert stats["count"] == 1
        assert stats["avg_ms"] == 42.5

    def test_unknown_timer(self):
        m = Metrics()
        stats = m.get_timer("nonexistent")
        assert stats["count"] == 0

    def test_multiple_timings(self):
        m = Metrics()
        m.record_timing("op", 10.0)
        m.record_timing("op", 20.0)
        m.record_timing("op", 30.0)
        stats = m.get_timer("op")
        assert stats["count"] == 3
        assert stats["min_ms"] == 10.0
        assert stats["max_ms"] == 30.0
        assert abs(stats["avg_ms"] - 20.0) < 0.01


class TestSnapshot:
    def test_snapshot_empty(self):
        m = Metrics()
        snap = m.snapshot()
        assert snap["counters"] == {}
        assert snap["timers"] == {}

    def test_snapshot_captures_all(self):
        m = Metrics()
        m.increment("requests")
        m.record_timing("latency", 5.0)
        snap = m.snapshot()
        assert snap["counters"]["requests"] == 1
        assert snap["timers"]["latency"]["count"] == 1

    def test_reset(self):
        m = Metrics()
        m.increment("a")
        m.record_timing("b", 1.0)
        m.reset()
        assert m.get_count("a") == 0
        snap = m.snapshot()
        assert snap["counters"] == {}
        assert snap["timers"] == {}
