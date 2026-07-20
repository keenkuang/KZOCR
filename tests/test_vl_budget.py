"""W4 VL 预算守卫单元测试（零资源、纯逻辑）。

覆盖：不限预算、per_run 上限、per_day 跨书累计（注入内存 DayStore + fake clock）、
超预算判定、summary 输出。
"""

from __future__ import annotations

from kzocr.scheduler.vl_budget import (
    VLBudgetConfig,
    VLBudgetTracker,
    _MemDayStore,
)


def _tracker(per_run=0, per_day=0, clock=lambda: 1700000000.0, day_store=None):
    return VLBudgetTracker(
        VLBudgetConfig(per_run=per_run, per_day=per_day),
        clock=clock,
        day_store=day_store,
    )


def test_unlimited_budget_always_spends():
    """per_run=per_day=0（默认）表示不限，can_spend 恒 True。"""
    t = _tracker()
    assert t.can_spend() is True
    for _ in range(100):
        assert t.can_spend() is True
        t.spend()
    assert t.run_used == 100
    assert t.summary() == "run=100/∞;day=0/∞"


def test_per_run_budget_enforced():
    """per_run=N 时，前 N 次 can_spend 为 True，第 N+1 次起为 False。"""
    t = _tracker(per_run=3)
    assert t.can_spend() is True
    t.spend()
    t.spend()
    assert t.can_spend() is True
    t.spend()
    assert t.run_used == 3
    assert t.can_spend() is False
    assert t.exhausted is True
    # 超限后再 spend 仅用于记录，不回退；can_spend 仍 False
    t.spend()
    assert t.can_spend() is False


def test_per_run_budget_exact_boundary():
    """per_run=1：恰好一次调用后预算耗尽（边界包含语义）。"""
    t = _tracker(per_run=1)
    assert t.can_spend() is True
    t.spend()
    assert t.can_spend() is False
    assert t.summary() == "run=1/1;day=0/∞"


def test_per_day_budget_loads_from_store():
    """per_day 从 DayStore 读取当日已用计数，达到上限即不可花费。"""
    store = _MemDayStore()
    store.add("2023-11-01", 5)
    t = _tracker(per_day=5, clock=lambda: 1698796800.0, day_store=store)  # 2023-11-01
    assert t.day_used == 5
    assert t.can_spend() is False  # 已达上限


def test_per_day_budget_accumulates_across_trackers():
    """同一 DayStore 跨 orchestrate_book 调用（跨书）累计当日调用数。"""
    store = _MemDayStore()
    t1 = _tracker(per_day=10, clock=lambda: 1698796800.0, day_store=store)
    assert t1.can_spend() is True
    t1.spend()
    t1.spend()
    assert store.get("2023-11-01") == 2
    # 新 tracker（新书/新进程，共享 store）看到累计值
    t2 = _tracker(per_day=10, clock=lambda: 1698796800.0, day_store=store)
    assert t2.day_used == 2
    for _ in range(8):
        t2.spend()
    assert t2.can_spend() is False  # 2 + 8 = 10 达上限
    assert store.get("2023-11-01") == 10


def test_per_day_respects_date_boundary():
    """不同日期的计数互相独立。"""
    store = _MemDayStore()
    store.add("2023-11-01", 100)
    t = _tracker(per_day=5, clock=lambda: 1698883200.0, day_store=store)  # 2023-11-02
    assert t.day_used == 0  # 当日无计数
    assert t.can_spend() is True


def test_per_run_and_per_day_both_limit():
    """两个维度任一超限即不可花费。"""
    store = _MemDayStore()
    store.add("2023-11-01", 3)
    t = _tracker(per_run=5, per_day=3, clock=lambda: 1698796800.0, day_store=store)
    # per_day 已达 3/3 → 即便 per_run 有余量也不可花费
    assert t.can_spend() is False
    assert t.exhausted is True
