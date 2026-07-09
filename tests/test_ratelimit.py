"""C3: ratelimit.py 测试。"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from kzocr.engines.ratelimit import (
    AdaptiveRateLimiter,
    ExponentialBackoff,
    MultiTokenRateLimiter,
    RateLimitStore,
)


class TestExponentialBackoff:
    def test_increases_with_attempt(self):
        b = ExponentialBackoff(base_delay=1.0, max_retries=5, jitter=0)
        d1 = b._compute_delay(1)
        d2 = b._compute_delay(2)
        d3 = b._compute_delay(3)
        assert d1 <= d2 <= d3

    def test_max_delay_capped(self):
        b = ExponentialBackoff(base_delay=1.0, max_delay=10.0, jitter=0)
        d = b._compute_delay(10)  # 1 * 2^9 = 512, capped at 10
        assert d <= 10.0

    def test_jitter_range(self):
        b = ExponentialBackoff(base_delay=2.0, jitter=0.5)
        d = b._compute_delay(1)
        assert 2.0 <= d <= 3.0  # 2 * (1 + 0.5) = 3.0

    def test_sleep_blocking(self):
        b = ExponentialBackoff(base_delay=0.01, max_retries=2, jitter=0)
        start = time.monotonic()
        b.sleep(1)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.005  # should have waited


class TestAdaptiveRateLimiter:
    def test_current_interval_default(self):
        lim = AdaptiveRateLimiter(base_interval=0.5, max_interval=60.0)
        assert lim.current_interval == 0.5

    def test_doubles_on_error(self):
        lim = AdaptiveRateLimiter(base_interval=1.0, max_interval=60.0)
        orig = lim.current_interval
        lim.report_error(503)
        assert lim.current_interval == orig * 2

    def test_recovers_on_success(self):
        lim = AdaptiveRateLimiter(base_interval=1.0, max_interval=60.0)
        lim.report_error(503)  # 2.0
        lim.report_error(503)  # 4.0
        for _ in range(5):
            lim.report_success()
        assert lim.current_interval < 4.0

    def test_not_below_base(self):
        lim = AdaptiveRateLimiter(base_interval=0.5, max_interval=60.0)
        for _ in range(10):
            lim.report_success()
        assert lim.current_interval >= 0.5

    def test_not_above_max(self):
        lim = AdaptiveRateLimiter(base_interval=1.0, max_interval=8.0)
        for _ in range(10):
            lim.report_error(503)
        assert lim.current_interval <= 8.0

    def test_429_triggers_double(self):
        lim = AdaptiveRateLimiter(base_interval=1.0, max_interval=60.0)
        orig = lim.current_interval
        lim.report_error(429)
        assert lim.current_interval == orig * 2


class TestMultiTokenRateLimiter:
    def test_acquire_fast_when_full(self):
        lim = MultiTokenRateLimiter(tokens=100, window_seconds=60)
        start = time.monotonic()
        lim.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5  # fast when bucket is full

    def test_initial_remaining(self):
        lim = MultiTokenRateLimiter(tokens=10, window_seconds=60)
        lim.acquire()
        assert lim.remaining >= 9  # one was just consumed

    def test_drains_tokens(self):
        lim = MultiTokenRateLimiter(tokens=3, window_seconds=60)
        for _ in range(3):
            lim.acquire()
        assert lim.remaining == 0

    def test_max_tokens_upper_bound(self):
        with pytest.raises(ValueError, match="tokens must be between 1 and 100000"):
            MultiTokenRateLimiter(tokens=100001, window_seconds=60)


class TestRateLimitStore:
    def test_save_load(self):
        store = RateLimitStore()
        store.save("k1", 3.0, 100.0, 2)
        row = store.load("k1")
        assert row is not None
        _, interval, last_ts, streak = row
        assert interval == 3.0
        assert last_ts == 100.0
        assert streak == 2
        store.close()

    def test_load_missing(self):
        store = RateLimitStore()
        assert store.load("nonexistent") is None
        store.close()

    def test_persistence_across_instances(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store1 = RateLimitStore(db_path)
            store1.save("k2", 5.0, 200.0, 3)
            store1.close()

            store2 = RateLimitStore(db_path)
            row = store2.load("k2")
            assert row is not None
            _, interval, last_ts, streak = row
            assert interval == 5.0
            assert last_ts == 200.0
            assert streak == 3
            store2.close()
        finally:
            os.unlink(db_path)

    def test_close(self):
        store = RateLimitStore()
        store.close()
        # close 后操作不抛异常
        store.close()
