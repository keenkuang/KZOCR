"""D1: 异常分类 + retry_with_policy 测试。

覆盖：
- 每个异常类型可独立构造和捕获（参数化 + 继承关系验证）
- retry_with_policy 成功/重试后成功/耗尽三种路径
- ExponentialBackoff 集成测试
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from kzocr.engines.errors import (
    ApiError,
    BACKOFF_CONFIGS,
    OcrError,
    OverSizeError,
    RateLimitedError,
    RetryExhaustedError,
    retry_with_policy,
)
from kzocr.engines.ratelimit import ExponentialBackoff


# =============================================================================
# 异常层级验证
# =============================================================================


class TestExceptionHierarchy:
    """验证异常继承关系。"""

    @pytest.mark.parametrize("exc_cls,expected_base", [
        (ApiError, OcrError),
        (RateLimitedError, ApiError),
        (OverSizeError, OcrError),
        (RetryExhaustedError, OcrError),
    ])
    def test_inheritance(self, exc_cls, expected_base):
        assert issubclass(exc_cls, expected_base)

    def test_rate_limited_has_retry_after(self):
        exc = RateLimitedError("限流", retry_after=30.0)
        assert exc.retry_after == 30.0
        assert str(exc) == "限流"

    def test_rate_limited_default_retry_after_none(self):
        exc = RateLimitedError()
        assert exc.retry_after is None

    def test_over_size_error(self):
        exc = OverSizeError("字数超限")
        assert isinstance(exc, OcrError)

    def test_retry_exhausted_error(self):
        exc = RetryExhaustedError("重试耗尽")
        assert isinstance(exc, OcrError)

    @pytest.mark.parametrize("exc_cls,name", [
        (OcrError, "OcrError"),
        (ApiError, "ApiError"),
        (RateLimitedError, "RateLimitedError"),
        (OverSizeError, "OverSizeError"),
        (RetryExhaustedError, "RetryExhaustedError"),
    ])
    def test_class_name(self, exc_cls, name):
        assert exc_cls.__name__ == name


# =============================================================================
# retry_with_policy 测试
# =============================================================================


class TestRetryWithPolicy:
    def test_success_first_attempt(self):
        """首次调用成功 → 立即返回结果。"""
        result = retry_with_policy(
            lambda: "OK",
            backoff=ExponentialBackoff(base_delay=0.001, max_retries=3),
        )
        assert result == "OK"

    def test_success_after_retry(self):
        """前 2 次失败，第 3 次成功。"""
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ApiError("暂不可用")
            return "OK"

        result = retry_with_policy(
            flaky,
            backoff=ExponentialBackoff(base_delay=0.001, max_retries=3),
            error_types=(ApiError,),
        )
        assert result == "OK"
        assert call_count == 3

    def test_exhausted_raises(self):
        """所有重试耗尽 → 抛出 RetryExhaustedError。"""
        with pytest.raises(RetryExhaustedError):
            retry_with_policy(
                lambda: (_ for _ in ()).throw(ApiError("一直失败")),
                backoff=ExponentialBackoff(base_delay=0.001, max_retries=2),
                error_types=(ApiError,),
            )

    def test_exhausted_chains_cause(self):
        """RetryExhaustedError 的 __cause__ 应链到原始异常。"""
        try:
            retry_with_policy(
                lambda: (_ for _ in ()).throw(ApiError("原始错误")),
                backoff=ExponentialBackoff(base_delay=0.001, max_retries=1),
                error_types=(ApiError,),
            )
        except RetryExhaustedError as exc:
            assert exc.__cause__ is not None
            assert "原始错误" in str(exc.__cause__)

    def test_on_exhausted_callback(self):
        """耗尽时调用 on_exhausted。"""
        callback_data = []

        try:
            retry_with_policy(
                lambda: (_ for _ in ()).throw(ApiError("fail")),
                backoff=ExponentialBackoff(base_delay=0.001, max_retries=1),
                error_types=(ApiError,),
                on_exhausted=lambda attempt, exc: callback_data.append((attempt, str(exc))),
            )
        except RetryExhaustedError:
            pass

        assert len(callback_data) == 1
        assert callback_data[0][1] == "fail"

    def test_unhandled_exception_propagates(self):
        """非 error_types 的异常直接抛出，不重试。"""
        with pytest.raises(ValueError, match="不该重试"):
            retry_with_policy(
                lambda: (_ for _ in ()).throw(ValueError("不该重试")),
                backoff=ExponentialBackoff(base_delay=0.001, max_retries=3),
                error_types=(ApiError,),
            )

    def test_rate_limited_retry_after(self):
        """RateLimitedError 带 retry_after，不应重试超过 max_retries。"""
        call_count = 0

        def always_limited():
            nonlocal call_count
            call_count += 1
            raise RateLimitedError("限流", retry_after=0.001)

        with pytest.raises(RetryExhaustedError):
            retry_with_policy(
                always_limited,
                backoff=ExponentialBackoff(base_delay=0.001, max_retries=2),
                error_types=(RateLimitedError, ApiError),
            )
        # 首次 + 2 次重试 = 3 次调用
        assert call_count == 3

    def test_retry_kwargs(self):
        """retry_kwargs 在不同尝试传递不同参数。"""
        call_count = 0

        def target(max_tokens=100):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ApiError("首次失败")
            return max_tokens

        result = retry_with_policy(
            target,
            backoff=ExponentialBackoff(base_delay=0.001, max_retries=1),
            error_types=(ApiError,),
            retry_kwargs={2: {"max_tokens": 800}},
        )
        # 首次失败, 第2次使用 retry_kwargs[2] 传入 max_tokens=800
        assert result == 800

    def test_backoff_configs_exist(self):
        """BACKOFF_CONFIGS 包含所有场景。"""
        assert "api" in BACKOFF_CONFIGS
        assert "ratelimit" in BACKOFF_CONFIGS
        assert "oversize" in BACKOFF_CONFIGS

    def test_backoff_configs_types(self):
        """BACKOFF_CONFIGS 的值都是 ExponentialBackoff 实例。"""
        for key, config in BACKOFF_CONFIGS.items():
            assert isinstance(config, ExponentialBackoff), f"{key} 不是 ExponentialBackoff"

    def test_over_size_error_retry(self):
        """OverSizeError 触发重试。"""
        call_count = 0

        def oversize_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OverSizeError("字数超限")
            return "OK"

        result = retry_with_policy(
            oversize_once,
            backoff=ExponentialBackoff(base_delay=0.001, max_retries=1),
            error_types=(OverSizeError,),
        )
        assert result == "OK"
        assert call_count == 2
