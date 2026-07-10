"""F3 断点续跑测试（resume / retry-failed）+ 滚动 benchmark 测试。"""

from __future__ import annotations


from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.scheduler import (
    EngineOverrides,
    EngineScheduler,
    Budget,
    PageInfo,
)
from kzocr.engine.types import AdapterMeta, EngineConfig


def _meta(name: str, tier: int = 1) -> AdapterMeta:
    return AdapterMeta(name=name, label=name, tier=tier)


# ── 滚动 benchmark ──

def test_rolling_window_starts_empty():
    """新建 EngineStats 的滚动窗口为空。"""
    from kzocr.scheduler.registry import EngineStats
    s = EngineStats()
    assert s.rolling_latencies == []
    assert s.rolling_failures == []
    assert s.recent_avg_latency_ms == 0.0
    assert s.recent_fail_rate == 0.0


def test_rolling_window_updates_after_record():
    """record 后滚动窗口更新。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("e1"), EngineConfig())
    for i in range(20):
        reg.record("e1", success=True, glyph="PASS", latency_ms=500, pages=1)
    s = reg.get("e1").stats
    assert len(s.rolling_latencies) == 20
    assert len(s.rolling_failures) == 20
    assert s.recent_avg_latency_ms == 500.0
    assert s.recent_fail_rate == 0.0


def test_rolling_window_capped_at_100():
    """超过 100 次时只保留最近 100 条。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("e2"), EngineConfig())
    for i in range(150):
        reg.record("e2", success=True, glyph="PASS", latency_ms=i, pages=1)
    s = reg.get("e2").stats
    assert len(s.rolling_latencies) == 100
    assert len(s.rolling_failures) == 100
    # 最近 100 个 latency 应等于 i=50..149
    assert s.rolling_latencies[0] == 50.0
    assert s.rolling_latencies[-1] == 149.0


def test_rolling_rate_mixed_scores():
    """近期失败率影响得分排序（高失败率降权）。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("good"), EngineConfig())
    reg.register_adapter(_meta("bad"), EngineConfig())
    for _ in range(10):
        reg.record("good", success=True, glyph="PASS", latency_ms=100, pages=1)
    for _ in range(10):
        success = (_ % 2 == 0)
        reg.record("bad", success=success, glyph="PASS" if success else "FAIL", latency_ms=100, pages=1)
    sched = EngineScheduler()
    cands = sched.select_candidates(reg, 1, PageInfo(page_num=1), Budget(max_pages=50, max_wall_clock_ms=7200000))
    names = [c.meta.name for c in cands]
    assert names[0] == "good"


# ── 自适应调速 backoff ──

def test_backoff_threshold_excludes_slow_engine():
    """超过延迟阈值的引擎被排除。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("fast", 2), EngineConfig())
    reg.register_adapter(_meta("slow", 2), EngineConfig())
    for _ in range(10):
        reg.record("fast", success=True, glyph="PASS", latency_ms=500, pages=1)
        reg.record("slow", success=True, glyph="PASS", latency_ms=60000, pages=1)
    sched = EngineScheduler()
    cands = sched.select_candidates(
        reg, 2, PageInfo(page_num=1), Budget(max_pages=50, max_wall_clock_ms=7200000, allow_cloud_vision=True),
        overrides=EngineOverrides(backoff_threshold_ms=10000),
    )
    assert all(c.meta.name == "fast" for c in cands)


# ── F3 EngineOverrides 字段 ──

def test_engine_overrides_resume_fields():
    ov = EngineOverrides(resume=True)
    assert ov.resume is True
    assert ov.retry_failed is False
    ov2 = EngineOverrides(retry_failed=True)
    assert ov2.resume is False
    assert ov2.retry_failed is True
