"""D1: KZOCR 异常分类体系 + retry_with_policy。

异常层级：
    OcrError (基类)
    ├── ApiError          — API 调用失败（HTTP 错误/超时）
    │   └── RateLimitedError — 429/503 限流（带 retry_after 头）
    ├── OverSizeError     — 字数超阈值（L1 触发后重 OCR 仍超）
    └── RetryExhaustedError — 重试耗尽，跳过该页

retry_with_policy 使用 ExponentialBackoff（来自 ratelimit.py）实现指数退避。
"""
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from kzocr.engines.ratelimit import ExponentialBackoff

logger = logging.getLogger(__name__)

T = TypeVar("T")


class OcrError(Exception):
    """KZOCR 所有的 OCR 相关异常基类。"""


class SchedulerError(OcrError):
    """v0.7 调度层异常：未注册引擎、调度策略失败等（设计 §7）。"""


class PinnedEngineUnavailableError(SchedulerError):
    """手动指定引擎不可用（§4.1 覆盖检查）。"""


class AllEnginesFailedError(SchedulerError):
    """所有可用引擎均失败（§7 E5）。编排主循环中所有 tier 引擎尝试完毕后
    所有页均未成功时抛出，供上层调用方（如 CLI / Celery）决定是否降级。"""


class ApiError(OcrError):
    """API 调用失败（HTTP 错误/超时）。"""


class RateLimitedError(ApiError):
    """429/503 限流错误。"""

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class OverSizeError(OcrError):
    """字数超阈值（L1 触发后重 OCR 仍超）。"""


class RetryExhaustedError(OcrError):
    """重试耗尽，跳过该页。"""


# ── 退避配置表 ──

BACKOFF_CONFIGS: dict[str, ExponentialBackoff] = {
    "api":      ExponentialBackoff(base_delay=1.0, max_retries=3, max_delay=300.0, jitter=0.5),
    "ratelimit": ExponentialBackoff(base_delay=1.0, max_retries=3, max_delay=60.0,  jitter=0.3),
    "oversize": ExponentialBackoff(base_delay=0,    max_retries=1, max_delay=60.0,  jitter=0.1),
}


def retry_with_policy(
    fn: Callable[..., T],
    backoff: ExponentialBackoff,
    error_types: tuple[type[Exception], ...] = (ApiError,),
    retry_kwargs: dict[int, dict] | None = None,
    on_exhausted: Callable[[int, Exception], None] | None = None,
) -> T:
    """按指数退避策略执行 fn，耗尽后抛出 RetryExhaustedError。

    Args:
        fn              : 要执行的函数。
        backoff         : ExponentialBackoff 实例（决定退避参数）。
        error_types     : 哪些异常触发重试（默认 ApiError）。
        retry_kwargs    : attempt 序号 → fn 的参数字典（用于 OverSizeError 的 max_tokens 调整）。
        on_exhausted    : 所有重试耗尽后回调 (attempt, last_exception) → None。

    Returns:
        fn 成功执行的返回值。

    Raises:
        RetryExhaustedError: 所有重试耗尽，原异常通过 __cause__ 链传递。
    """
    last_exc: Exception | None = None
    max_attempts = backoff.max_retries + 1  # +1 表示首次尝试

    for attempt in range(1, max_attempts + 1):
        try:
            if retry_kwargs and attempt in retry_kwargs:
                return fn(**retry_kwargs[attempt])
            return fn()
        except error_types as exc:
            last_exc = exc
            logger.warning(
                "[retry] attempt=%d/%d failed: %s: %s",
                attempt, max_attempts, type(exc).__name__, exc,
            )
            if attempt < max_attempts:
                # RateLimitedError: 优先使用 Retry-After header
                if isinstance(exc, RateLimitedError) and exc.retry_after is not None:
                    delay = exc.retry_after
                    logger.info("[retry] RateLimited: 使用 Retry-After=%.2fs", delay)
                else:
                    delay = backoff._compute_delay(attempt)
                backoff.sleep(attempt)
        except Exception as exc:
            # 非 error_types 的异常直接抛出
            raise exc

    # 所有重试耗尽
    if on_exhausted:
        on_exhausted(max_attempts, last_exc)  # type: ignore[arg-type]

    raise RetryExhaustedError(f"重试耗尽 ({backoff.max_retries} 次)") from last_exc
