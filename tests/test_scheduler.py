"""E2 EngineScheduler 测试（v0.7 §4 / §10.1 / §10.3）。"""

from __future__ import annotations

import time

from kzocr.engine.types import AdapterMeta, EngineConfig, PageLayout
from kzocr.scheduler.registry import EngineRegistry, EngineStats
from kzocr.scheduler.scheduler import (
    Budget,
    EngineOverrides,
    EngineScheduler,
    PageInfo,
    _compute_bayesian_score,
    domain_adjust,
)
from kzocr.engines.errors import PinnedEngineUnavailableError


def _meta(name: str, tier: int = 1, requires_network: bool = False, probe: dict | None = None):
    return AdapterMeta(
        name=name, label=name, tier=tier, requires_network=requires_network, probe=probe or {}
    )


def _reg(*names: str, tier: int = 1, requires_network: bool = False) -> EngineRegistry:
    r = EngineRegistry()
    for n in names:
        r.register_adapter(_meta(n, tier, requires_network), EngineConfig())
    return r


def _budget(allow_cloud_vision: bool = False) -> Budget:
    return Budget(max_pages=50, max_wall_clock_ms=7_200_000, allow_cloud_vision=allow_cloud_vision)


def _page(book_type: str = "", pub_era: str = "", is_vertical: bool = False) -> PageInfo:
    return PageInfo(page_num=1, book_type=book_type, pub_era=pub_era, is_vertical=is_vertical)


def _layout(is_vertical: bool = False) -> PageLayout:
    return PageLayout(page_num=1, is_vertical=is_vertical)


def test_tier_filter():
    reg = _reg("a", "b", tier=1)
    reg.register_adapter(_meta("c", 2), EngineConfig())
    sched = EngineScheduler()
    cands = sched.select_candidates(reg, tier=1, page_info=_page(), budget=_budget())
    assert {c.meta.name for c in cands} == {"a", "b"}


def test_vertical_skips_tier1():
    reg = _reg("a", tier=1)
    sched = EngineScheduler()
    # 竖排 → T1 返回空
    assert sched.select_candidates(
        reg, tier=1, page_info=_page(), budget=_budget(), page_layout=_layout(is_vertical=True)
    ) == []
    # 非竖排 → 正常返回
    assert sched.select_candidates(reg, tier=1, page_info=_page(), budget=_budget()) != []


def test_allow_cloud_vision_filter():
    # 用 tier=1（上限=2）而非 tier=2（上限=1），避免 Top-N 截断干扰云端过滤断言
    reg = EngineRegistry()
    reg.register_adapter(_meta("local", 1), EngineConfig())
    reg.register_adapter(_meta("cloud", 1, requires_network=True), EngineConfig())
    sched = EngineScheduler()
    # 不允许云端 → cloud 被过滤，剩 local
    cands = sched.select_candidates(reg, tier=1, page_info=_page(), budget=_budget(False))
    assert {c.meta.name for c in cands} == {"local"}
    # 允许云端 → 两者都在
    cands = sched.select_candidates(reg, tier=1, page_info=_page(), budget=_budget(True))
    assert {c.meta.name for c in cands} == {"local", "cloud"}


def test_resource_filter_excludes_unavailable():
    reg = _reg("a", "b", tier=1)
    reg.mark_unavailable("a")
    sched = EngineScheduler()
    cands = sched.select_candidates(reg, tier=1, page_info=_page(), budget=_budget())
    assert [c.meta.name for c in cands] == ["b"]


def test_pinned_engine():
    reg = _reg("a", "b", tier=1)
    sched = EngineScheduler()
    cands = sched.select_candidates(
        reg, tier=1, page_info=_page(), budget=_budget(),
        overrides=EngineOverrides(pinned_engine="a"),
    )
    assert [c.meta.name for c in cands] == ["a"]


def test_pinned_engine_not_found():
    reg = _reg("a", tier=1)
    sched = EngineScheduler()
    try:
        sched.select_candidates(
            reg, tier=1, page_info=_page(), budget=_budget(),
            overrides=EngineOverrides(pinned_engine="missing"),
        )
    except PinnedEngineUnavailableError:
        pass
    else:
        raise AssertionError("pinned 不存在应抛 PinnedEngineUnavailableError")


def test_pinned_engine_unavailable():
    reg = _reg("a", tier=1)
    reg.mark_unavailable("a")
    sched = EngineScheduler()
    try:
        sched.select_candidates(
            reg, tier=1, page_info=_page(), budget=_budget(),
            overrides=EngineOverrides(pinned_engine="a"),
        )
    except PinnedEngineUnavailableError:
        pass
    else:
        raise AssertionError("pinned 为 UNAVAILABLE 应抛 PinnedEngineUnavailableError")


def test_top_n_limit(monkeypatch):
    reg = _reg("a", "b", "c", tier=1)
    monkeypatch.setattr("kzocr.scheduler.scheduler._should_poll", lambda: False)
    sched = EngineScheduler()
    cands = sched.select_candidates(reg, tier=1, page_info=_page(), budget=_budget())
    assert len(cands) == 2  # Tier 1 默认上限 2


def test_budget_exhausted():
    reg = _reg("a", tier=1)
    budget = _budget()
    budget.exhaust()
    sched = EngineScheduler()
    assert sched.select_candidates(reg, tier=1, page_info=_page(), budget=budget) == []


def test_decay_cold_and_aged():
    cold = EngineStats(last_seen=0.0)
    assert cold.decay() == 1.0
    aged = EngineStats(last_seen=time.time() - 86400 * 30)  # 30 天前
    assert aged.decay(7.0) < 1.0


def test_bayesian_score_positive():
    reg = _reg("a", tier=1)
    reg.record("a", success=True, glyph="PASS", latency_ms=1000, pages=10)
    assert _compute_bayesian_score(reg.get("a")) > 0


def test_domain_adjust_vertical_tier2():
    reg = _reg("e", tier=2)
    eng = reg.get("e")
    layout = _layout(is_vertical=True)
    assert domain_adjust(1.0, eng, _page(), layout) == 1.7  # base*1.5 + 0.2


def test_domain_adjust_laser_fast():
    reg = _reg("e", tier=1)
    reg.record("e", success=True, glyph="PASS", latency_ms=1000, pages=1)
    eng = reg.get("e")
    assert domain_adjust(1.0, eng, _page(pub_era="laser")) == 1.1


def test_domain_adjust_formula_high_recall():
    reg = _reg("e", tier=1)
    reg.record("e", success=True, glyph="PASS", latency_ms=8000, pages=5)
    eng = reg.get("e")
    assert domain_adjust(1.0, eng, _page(book_type="formula")) == 1.1


def test_prefer_speed_sorts_by_latency():
    reg = EngineRegistry()
    reg.register_adapter(_meta("slow", 1), EngineConfig())
    reg.register_adapter(_meta("fast", 1), EngineConfig())
    reg.record("slow", success=True, glyph="PASS", latency_ms=8000, pages=5)
    reg.record("fast", success=True, glyph="PASS", latency_ms=500, pages=5)
    sched = EngineScheduler()
    cands = sched.select_candidates(
        reg, tier=1, page_info=_page(), budget=_budget(),
        overrides=EngineOverrides(prefer="speed"),
    )
    assert cands[0].meta.name == "fast"
