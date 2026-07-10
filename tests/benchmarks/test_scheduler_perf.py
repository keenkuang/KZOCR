"""调度器性能基准：select_candidates / _compute_bayesian_score 耗时断言。"""

from __future__ import annotations

import time

from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.scheduler import (
    Budget,
    EngineScheduler,
    PageInfo,
    _compute_bayesian_score,
)
from kzocr.engine.types import AdapterMeta, EngineConfig


def _meta(name: str, tier: int = 1) -> AdapterMeta:
    return AdapterMeta(name=name, label=name, tier=tier)


def _register_engines(n: int = 10) -> EngineRegistry:
    reg = EngineRegistry()
    for i in range(n):
        reg.register_adapter(_meta(f"e{i}", tier=1), EngineConfig())
    return reg


def _populate_stats(reg: EngineRegistry, n: int = 50):
    """给每个引擎注入 n 条 benchmark 记录。"""
    for i in range(n):
        for name in [e.meta.name for e in reg.list()]:
            reg.record(name, success=True, glyph="PASS", latency_ms=500 + i * 10, pages=1)


def test_select_candidates_performance():
    """select_candidates 应在 10ms 内完成。"""
    reg = _register_engines(20)
    _populate_stats(reg, 100)
    sched = EngineScheduler()
    budget = Budget(max_pages=50, max_wall_clock_ms=7200000)
    t0 = time.monotonic()
    for _ in range(50):
        sched.select_candidates(reg, 1, PageInfo(page_num=1), budget)
    elapsed = (time.monotonic() - t0) / 50
    assert elapsed < 0.01, f"select_candidates avg {elapsed*1000:.1f}ms > 10ms"


def test_bayesian_score_performance():
    """_compute_bayesian_score 应在 1ms 内完成。"""
    reg = _register_engines(1)
    _populate_stats(reg, 100)
    engine = reg.get("e0")
    t0 = time.monotonic()
    for _ in range(1000):
        _compute_bayesian_score(engine)
    elapsed = (time.monotonic() - t0) / 1000
    assert elapsed < 0.001, f"bayesian_score avg {elapsed*1000:.3f}ms > 1ms"
