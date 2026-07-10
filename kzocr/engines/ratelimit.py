"""
C3 自适应限流器模块 — 生产级限流/重试原语。
# B3 兼容：通信目标（sensenova.cn, api.deepseek.com, modelscope.cn）已在 B3 egress allowlist 中。

继承 TOC 项目经过 7000+ 次调用验证（<1% 失败率）的设计。

迁移注记
--------
`modelscope_pool.py` 中当前使用原始 `time.sleep(_RETRY_DELAY)` 做 provider 切换
等待，且 `CloudLLMPool.chat()/chat_vision()` 中无主动限流/回退机制。
建议后续将：

    time.sleep(_RETRY_DELAY)

替换为：

    from kzocr.engines.ratelimit import ExponentialBackoff
    backoff = ExponentialBackoff(base_delay=1.0, max_retries=len(provider.models))
    for attempt in range(backoff.max_retries):
        ...
        backoff.sleep(attempt + 1)

同时在 `CloudLLMPool.__init__` 中为每个 provider 创建 `MultiTokenRateLimiter`
和 `AdaptiveRateLimiter`，取代当前的硬编码 1s sleep + 无状态重试策略。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# ExponentialBackoff
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class ExponentialBackoff:
    """指数退避等待 — 每次重试间隔呈指数增长，附加随机抖动。

    delay = min(base_delay * 2^(attempt-1), max_delay) * (1 + random() * jitter)

    Example:
        backoff = ExponentialBackoff(base_delay=2.0, max_retries=5)
        for attempt in range(1, backoff.max_retries + 1):
            try:
                result = api_call()
                return result
            except RateLimitError:
                backoff.sleep(attempt)
    """

    base_delay: float = 2.0
    max_retries: int = 5
    max_delay: float = 300.0
    jitter: float = 0.5  # 0–50% 随机抖动

    def _compute_delay(self, attempt: int) -> float:
        """计算第 attempt 次重试的等待时长（不含 sleep）。"""
        raw = self.base_delay * (2 ** (attempt - 1))
        clamped = min(raw, self.max_delay)
        factor = 1.0 + random.random() * self.jitter
        return clamped * factor

    def sleep(self, attempt: int) -> None:
        """阻塞等待，时长按指数退避 + 随机抖动计算。attempt 从 1 开始。"""
        delay = self._compute_delay(attempt)
        logger.debug("[backoff] attempt=%d delay=%.2fs", attempt, delay)
        time.sleep(delay)


# ───────────────────────────────────────────────────────────────────────────
# AdaptiveRateLimiter
# ───────────────────────────────────────────────────────────────────────────

class AdaptiveRateLimiter:
    """自适应速率限制器 — 固定间隔 + 错误时指数退避。

    收到 429/503 时间隔翻倍（上限 max_interval），
    连续 5 次成功则间隔 ×0.9（不降穿 base_interval），
    线程安全。

    v0.4 AMEND H1 修复：base_interval 从 6.0s 降至 3.0s。
    """

    def __init__(
        self,
        base_interval: float = 3.0,
        max_interval: float = 60.0,
        max_entries: int = 10000,
        store: Optional[RateLimitStore] = None,
    ):
        self._base = base_interval
        self._max = max_interval
        self._current = base_interval
        self._success_streak = 0
        self._last_ts = 0.0  # 上次放行时间戳（monotonic）
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._registered_count = 0
        self._store = store
        if store is not None:
            state = store.load("adaptive_default")
            if state is not None:
                _, interval, last_ts, success_streak = state
                self._current = interval
                self._last_ts = last_ts
                self._success_streak = success_streak
                logger.info(
                    "[ratelimit] 从持久存储恢复状态: interval=%.2f streak=%d",
                    self._current, self._success_streak,
                )

    def _register(self, key: str) -> bool:
        """注册一个 key；返回 False 表示达到上限、拒绝注册。"""
        if self._registered_count >= self._max_entries:
            logger.warning(
                "[ratelimit] 注册数 %d 已达上限 %d，拒绝注册 key=%s",
                self._registered_count, self._max_entries, key,
            )
            return False
        self._registered_count += 1
        return True

    def _persist(self) -> None:
        """将当前状态写入持久存储（如有）。"""
        if self._store is not None:
            try:
                self._store.save(
                    "adaptive_default", self._current, self._last_ts, self._success_streak,
                )
            except Exception:
                logger.exception("[ratelimit] 持久化状态失败")

    def wait(self) -> float:
        """阻塞直到允许下一次请求。返回实际等待秒数。"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_ts
            if elapsed < self._current:
                wait = self._current - elapsed
            else:
                wait = 0.0
            if wait > 0:
                time.sleep(wait)
                actual = wait
            else:
                actual = 0.0
            self._last_ts = time.monotonic()
        self._persist()
        return actual

    def report_success(self) -> None:
        """报告一次成功。连续 5 次成功则适度缩短间隔。"""
        with self._lock:
            self._success_streak += 1
            if self._success_streak >= 5:
                self._current = max(self._base, self._current * 0.9)
                self._success_streak = 0
                logger.debug("[ratelimit] 连续5次成功，间隔降至 %.2fs", self._current)
        self._persist()

    def report_error(self, status_code: int = 503) -> None:
        """报告一次限流/错误。429/503 使间隔翻倍。"""
        with self._lock:
            if status_code in (429, 503):
                self._current = min(self._max, self._current * 2)
                self._success_streak = 0
                logger.debug(
                    "[ratelimit] 收到 %d，间隔升至 %.2fs", status_code, self._current
                )
        self._persist()

    @property
    def current_interval(self) -> float:
        """当前间隔（只读快照）。"""
        with self._lock:
            return self._current


# ───────────────────────────────────────────────────────────────────────────
# MultiTokenRateLimiter
# ───────────────────────────────────────────────────────────────────────────

class MultiTokenRateLimiter:
    """令牌桶速率限制器 — 按窗口控制并发/总量，可配置每个 service。

    tokens 个令牌在 window_seconds 秒窗口内放行，
    用完即阻塞直到下一窗口；
    占用率 ≥80% 时主动进入等待（避免突发耗尽）。

    Example:
        limiter = MultiTokenRateLimiter(tokens=10, window_seconds=60, key="deepseek")
        wait = limiter.acquire()
        # 发起请求…
    """

    def __init__(self, tokens: int, window_seconds: float, key: str = "default"):
        if tokens < 1 or tokens > 100000:
            raise ValueError("tokens must be between 1 and 100000")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max_tokens = tokens
        self._window = window_seconds
        self._key = key
        self._lock = threading.Lock()
        self._available = tokens
        self._window_start = time.monotonic()

    @property
    def remaining(self) -> int:
        """当前窗口剩余令牌数。"""
        with self._lock:
            self._refill()
            return int(self._available)

    @property
    def key(self) -> str:
        return self._key

    def acquire(self) -> float:
        """获取一个令牌；若不足则阻塞直到窗口重置。返回等待秒数。"""
        with self._lock:
            self._refill()
            now = time.monotonic()
            elapsed = now - self._window_start
            usage = 1.0 - (self._available / self._max_tokens)

            # 占用率 ≥80% → 主动等待窗口重置再释放令牌（平滑请求率）
            if usage >= 0.8:
                if elapsed < self._window:
                    wait = self._window - elapsed
                    logger.debug(
                        "[tokenbucket/%s] 占用率 %.0f%% ≥80%%，主动等待 %.2fs",
                        self._key, usage * 100, wait,
                    )
                    time.sleep(wait)
                self._refill()
                self._available -= 1
                return max(self._window - elapsed, 0.0) if elapsed < self._window else 0.0

            # 令牌充足 → 立即获取
            if self._available >= 1:
                self._available -= 1
                return 0.0

            # 令牌耗尽 → 等待下一窗口
            if elapsed >= self._window:
                # 窗口已过，兜底 refill
                self._available = self._max_tokens - 1
                self._window_start = now
                return 0.0

            wait = self._window - elapsed
            time.sleep(wait)
            self._refill()
            self._available -= 1
            return wait

    def _refill(self) -> None:
        """内部刷新令牌：若已超当前窗口则重置。"""
        now = time.monotonic()
        elapsed = now - self._window_start
        if elapsed >= self._window:
            self._available = self._max_tokens
            self._window_start = now


# ───────────────────────────────────────────────────────────────────────────
# RateLimitStore — SQLite 持久化后端
# ───────────────────────────────────────────────────────────────────────────

class RateLimitStore:
    """SQLite 持久化后端，存储限流器状态。

    可选依赖：Python sqlite3（标准库）。
    重启后配额不丢失（除非使用 :memory:）。
    """

    def __init__(self, db_path: str = ":memory:"):
        import sqlite3

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS rate_limit_state ("
            "  key TEXT PRIMARY KEY,"
            "  interval REAL,"
            "  last_ts REAL,"
            "  success_streak INTEGER"
            ")"
        )
        self._conn.commit()

    def save(self, key: str, interval: float, last_ts: float, success_streak: int) -> None:
        """持久化限流器状态。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO rate_limit_state (key, interval, last_ts, success_streak)"
            "  VALUES (?, ?, ?, ?)",
            (key, interval, last_ts, success_streak),
        )
        self._conn.commit()

    def load(self, key: str) -> Optional[tuple]:
        """加载限流器状态；key 不存在时返回 None。"""
        cur = self._conn.execute(
            "SELECT key, interval, last_ts, success_streak FROM rate_limit_state WHERE key = ?",
            (key,),
        )
        row = cur.fetchone()
        return row if row is not None else None

    def close(self) -> None:
        """关闭数据库连接。"""
        try:
            self._conn.close()
        except Exception:
            pass
