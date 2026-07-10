"""并发调度测试：AdaptiveController + run_engines_concurrent。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from kzocr.scheduler.concurrency import AdaptiveController, run_engines_concurrent
from kzocr.engine.types import AdapterPageResult, PageInput


# ── AdaptiveController ──

def test_controller_starts_at_base():
    c = AdaptiveController(base_workers=3)
    assert c.workers == 3


def test_controller_error_rate_increases_triggers_reduction():
    c = AdaptiveController(error_threshold=0.3)
    # 10 次失败 out of 20 → 50% > 30%
    for _ in range(7):
        c.record_result(True)
    for _ in range(13):
        c.record_result(False)
    assert c.workers < c.base_workers


def test_controller_low_error_increases():
    c = AdaptiveController(success_threshold=0.05, base_workers=2, max_workers=5)
    # 20 次成功 → 0% 错误率 < 5%
    for _ in range(20):
        c.record_result(True)
    assert c.workers > c.base_workers


def test_controller_respects_bounds():
    c = AdaptiveController(base_workers=2, min_workers=1, max_workers=3)
    for _ in range(100):
        c.record_result(True)
    assert c.workers <= c.max_workers
    for _ in range(100):
        c.record_result(False)
    assert c.workers >= c.min_workers


# ── run_engines_concurrent ──

def test_concurrent_first_succeeds():
    engines = [
        _make_engine("fast", delay=0.01, text="fast result"),
        _make_engine("slow", delay=0.5, text="slow result"),
    ]
    pi = PageInput(page_num=0, img=None)  # type: ignore
    result, engine_name = run_engines_concurrent(engines, pi, timeout_s=5)
    assert result is not None
    assert engine_name == "fast"


def test_concurrent_all_fail():
    class FailAdapter:
        def run_page(self, pi):
            raise RuntimeError("fail")
    engines = [MagicMock()]
    engines[0].meta.name = "fail1"
    engines[0].adapter = FailAdapter()
    pi = PageInput(page_num=0, img=None)  # type: ignore
    result, engine_name = run_engines_concurrent(engines, pi, timeout_s=0.5)
    assert result is None
    assert engine_name is None


def test_concurrent_empty_engines():
    result, engine_name = run_engines_concurrent([], PageInput(page_num=0, img=None), timeout_s=1)  # type: ignore
    assert result is None


def _make_engine(name: str, delay: float = 0, text: str = "result"):
    """创建 mock engine registration。"""
    eng = MagicMock()
    eng.meta.name = name
    class _Adapter:
        def run_page(self, pi):
            if delay:
                time.sleep(delay)
            return AdapterPageResult(text=text)
    eng.adapter = _Adapter()
    return eng
