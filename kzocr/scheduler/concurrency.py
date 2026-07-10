"""并发引擎调度器：AdaptiveController + ThreadPoolExecutor。

AdaptiveController 根据引擎即时延迟/错误率动态调整并发 Worker 数。
参考 traedocu V3.4 AdaptiveController 设计。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class AdaptiveController:
    """自适应并发控制器。"""

    base_workers: int = 2
    min_workers: int = 1
    max_workers: int = 5
    error_threshold: float = 0.3
    success_threshold: float = 0.05

    current_target: int = 2
    _recent_errors: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.current_target = self.base_workers  # 最近 20 次 success/fail

    def record_result(self, success: bool) -> None:
        """记录一次引擎调用结果，按错误率调整并发目标。"""
        self._recent_errors.append(success)
        if len(self._recent_errors) > 20:
            self._recent_errors.pop(0)
        if len(self._recent_errors) < 5:
            return
        error_rate = sum(1 for s in self._recent_errors if not s) / len(self._recent_errors)
        if error_rate > self.error_threshold:
            old = self.current_target
            self.current_target = max(self.min_workers, self.current_target - 1)
            if old != self.current_target:
                _logger.info("[concurrency] 降并发: %d→%d (错误率=%.0f%%)", old, self.current_target, error_rate * 100)
        elif error_rate < self.success_threshold:
            old = self.current_target
            self.current_target = min(self.max_workers, self.current_target + 1)
            if old != self.current_target:
                _logger.info("[concurrency] 升并发: %d→%d (错误率=%.0f%%)", old, self.current_target, error_rate * 100)

    @property
    def workers(self) -> int:
        return self.current_target


def run_engines_concurrent(
    engines: list[Any],
    page_input: Any,
    timeout_s: float = 120.0,
    max_workers: int = 3,
) -> tuple[Optional[Any], Optional[str]]:
    """并发执行多个引擎的 run_page，返回首个成功的结果。

    Args:
        engines: EngineRegistration 列表。
        page_input: PageInput 实例。
        timeout_s: 单引擎超时（秒）。
        max_workers: 并发上限。

    Returns:
        (result, engine_name) — 首个成功的 AdapterPageResult 与引擎名；
        (None, None) — 全部失败。
    """
    if not engines:
        return None, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for engine in engines:
            future = executor.submit(_run_one, engine, page_input, timeout_s)
            future_map[future] = engine.meta.name

        for future in as_completed(future_map):
            engine_name = future_map[future]
            try:
                result = future.result(timeout=timeout_s + 5)
                if result is not None:
                    # 取消其余未完成的任务
                    for f in future_map:
                        if not f.done():
                            f.cancel()
                    return result, engine_name
            except Exception as exc:
                _logger.warning("[concurrency] engine=%s failed: %s", engine_name, exc)
                continue

    return None, None


def _run_one(engine, page_input, timeout_s: float):
    """执行单个引擎并返回结果或 None。"""
    try:
        result = engine.adapter.run_page(page_input)
        return result
    except Exception:
        return None
