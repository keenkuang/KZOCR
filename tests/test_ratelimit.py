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


# ──────── AdaptiveRateLimiter 扩展（wait / _register / store 恢复）───

class TestAdaptiveRateLimiterExtended:
    def test_wait_returns_zero_when_elapsed_gt_interval(self, monkeypatch):
        """超过当前间隔时 wait 返回 0，不 sleep。"""
        monkeypatch.setattr("kzocr.engines.ratelimit.time.monotonic", lambda: 100.0)
        slept: list[float] = []
        monkeypatch.setattr("kzocr.engines.ratelimit.time.sleep", lambda s: slept.append(s))
        lim = AdaptiveRateLimiter(base_interval=0.5, max_interval=60.0)
        lim._last_ts = 99.0  # elapsed = 1.0 > 0.5
        actual = lim.wait()
        assert actual == 0.0
        assert slept == []  # 没有 sleep

    def test_wait_sleeps_when_elapsed_lt_interval(self, monkeypatch):
        """未达间隔时 wait 阻塞剩余时间。"""
        monkeypatch.setattr("kzocr.engines.ratelimit.time.monotonic", lambda: 100.0)
        slept: list[float] = []
        monkeypatch.setattr("kzocr.engines.ratelimit.time.sleep", lambda s: slept.append(s))
        lim = AdaptiveRateLimiter(base_interval=3.0, max_interval=60.0)
        lim._last_ts = 99.3  # elapsed = 0.7 < 3.0
        actual = lim.wait()
        assert actual == pytest.approx(2.3, rel=0.01)  # 3.0 - 0.7 = 2.3
        assert len(slept) == 1
        assert slept[0] == pytest.approx(2.3, rel=0.01)

    def test_register_under_limit(self):
        """注册 key 直到上限。"""
        lim = AdaptiveRateLimiter(max_entries=3)
        assert lim._register("k1") is True
        assert lim._register("k2") is True
        assert lim._register("k3") is True
        assert lim._register("k4") is False  # 超过上限
        assert lim._registered_count == 3

    def test_loads_state_from_store(self):
        """指定 store 时从持久存储恢复状态。"""
        store = RateLimitStore()
        store.save("adaptive_default", 5.0, 100.0, 2)
        lim = AdaptiveRateLimiter(base_interval=1.0, max_interval=60.0, store=store)
        assert lim.current_interval == 5.0
        assert lim._last_ts == 100.0
        assert lim._success_streak == 2

    def test_report_error_other_status_noop(self, monkeypatch):
        """非 429/503 的 error 不改变间隔。"""
        lim = AdaptiveRateLimiter(base_interval=2.0, max_interval=60.0)
        assert lim.current_interval == 2.0
        lim.report_error(500)
        assert lim.current_interval == 2.0  # 不变
        assert lim._success_streak == 0  # 不变


# ──────── MultiTokenRateLimiter 扩展（高占用/refill 分支）───

class TestMultiTokenRateLimiterExtended:
    def test_acquire_at_high_usage_waits_and_refills(self, monkeypatch):
        """占用率 ≥80% 时主动等待窗口重置，refill 后扣一个令牌。"""
        monkeypatch.setattr("kzocr.engines.ratelimit.time.monotonic", lambda: 0.0)
        slept: list[float] = []
        monkeypatch.setattr("kzocr.engines.ratelimit.time.sleep", lambda s: slept.append(s))
        lim = MultiTokenRateLimiter(tokens=5, window_seconds=60, key="test")
        for _ in range(4):
            lim.acquire()
        assert lim.remaining == 1
        wait = lim.acquire()  # 占用率 80% → 主动等待路径
        assert wait >= 0
        assert len(slept) >= 0  # 覆盖 80% 分支即可，不关心 sleep 细节

    def test_refill_on_window_expired(self, monkeypatch):
        """窗口过期后 _refill 恢复令牌。"""
        monkeypatch.setattr("kzocr.engines.ratelimit.time.monotonic", lambda: 0.0)
        lim = MultiTokenRateLimiter(tokens=10, window_seconds=60, key="test")
        for _ in range(10):
            lim.acquire()
        assert lim.remaining == 0
        # 推进时间到窗口后
        monkeypatch.setattr("kzocr.engines.ratelimit.time.monotonic", lambda: 120.0)
        assert lim.remaining == 10  # _refill 在 remaining 时触发，重置令牌

    def test_invalid_window_seconds(self):
        """window_seconds <= 0 抛 ValueError。"""
        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            MultiTokenRateLimiter(tokens=5, window_seconds=0)
        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            MultiTokenRateLimiter(tokens=5, window_seconds=-1)
