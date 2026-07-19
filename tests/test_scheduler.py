"""E2 EngineScheduler 测试（v0.7 §4）。"""

from __future__ import annotations

import time
from unittest import mock

import pytest

from kzocr.engine.types import AdapterMeta, EngineConfig, PageLayout
from kzocr.engines.errors import PinnedEngineUnavailableError
from kzocr.scheduler.registry import EngineRegistration, EngineRegistry, EngineStats
from kzocr.scheduler.scheduler import (
    Budget,
    EngineOverrides,
    EngineScheduler,
    PageInfo,
    _compute_bayesian_score,
    _select_poll_candidate,
    domain_adjust,
)


def _am(name: str = "e", tier: int = 1, requires_network: bool = False) -> AdapterMeta:
    return AdapterMeta(
        name=name, label=name, tier=tier, kind="page",
        requires_network=requires_network,
    )


def _reg(**kw) -> EngineRegistration:
    kw.setdefault("config", EngineConfig())
    if "meta" not in kw:
        kw["meta"] = _am()
    if "stats" not in kw:
        kw["stats"] = EngineStats()
    return EngineRegistration(**kw)


# ──── Budget ────

class TestBudget:
    def test_defaults(self):
        b = Budget(max_pages=50, max_wall_clock_ms=7200000)
        assert not b.exhausted
        assert b.max_time_per_page_ms == 120000
        assert not b.allow_cloud_vision

    def test_exhaust(self):
        b = Budget(max_pages=50, max_wall_clock_ms=7200000)
        b.exhaust()
        assert b.exhausted

    def test_check_time_budget(self):
        b = Budget(max_pages=50, max_wall_clock_ms=5000)
        assert b.check_time_budget(1.0)
        assert not b.check_time_budget(10.0)


# ──── _compute_bayesian_score ────

class TestComputeBayesianScore:
    def test_cold_finite(self):
        assert _compute_bayesian_score(_reg()) > 0

    def test_long_only_below_5(self):
        reg = _reg(stats=EngineStats(
            total_pages=5, total_latency_ms=5000,
            glyph_pass_count=4, glyph_fail_count=1,
        ))
        reg.stats.rolling_latencies = [100, 200, 300]  # <5
        assert _compute_bayesian_score(reg) > 0

    def test_mixed_ge_5(self):
        reg = _reg(stats=EngineStats(
            total_pages=5, total_latency_ms=5000,
            glyph_pass_count=4, glyph_fail_count=1,
        ))
        reg.stats.rolling_latencies = [100] * 5
        reg.stats.rolling_failures = [True] * 5
        assert _compute_bayesian_score(reg) > 0

    def test_decay_parameter(self):
        reg = _reg(stats=EngineStats(total_pages=1, total_latency_ms=1000))
        reg.stats.last_seen = time.time() - 86400 * 14  # 14 days ago
        score_7 = _compute_bayesian_score(reg, half_life_days=7.0)
        score_14 = _compute_bayesian_score(reg, half_life_days=14.0)
        assert score_14 > score_7


# ──── domain_adjust ────

class TestDomainAdjust:
    def test_vertical_tier2_boost(self):
        reg = _reg(meta=_am(tier=2),
                    stats=EngineStats(total_pages=1, total_latency_ms=1000))
        pl = PageLayout(page_num=0, is_vertical=True)
        assert domain_adjust(1.0, reg, PageInfo(page_num=0), page_layout=pl) == pytest.approx(1.0 * 1.5 + 0.2)

    def test_vertical_tier1_no_boost(self):
        reg = _reg(meta=_am(tier=1),
                    stats=EngineStats(total_pages=1, total_latency_ms=1000))
        pl = PageLayout(page_num=0, is_vertical=True)
        assert domain_adjust(1.0, reg, PageInfo(page_num=0, is_vertical=True), page_layout=pl) == 1.0

    def test_laser_fast_boost(self):
        reg = _reg(stats=EngineStats(total_pages=1, total_latency_ms=3000))
        result = domain_adjust(1.0, reg, PageInfo(page_num=0, pub_era="laser"))
        assert result == pytest.approx(1.1)

    def test_formula_high_pass_boost(self):
        reg = _reg(stats=EngineStats(
            total_pages=10, total_latency_ms=10000,
            glyph_pass_count=9, glyph_fail_count=1,
        ))
        pi = PageInfo(page_num=0, book_type="formula")
        # pass_rate=0.9 → exprs gate: > 0.9? No, 0.9 is NOT > 0.9
        # So no boost should be applied
        reg.glyph_pass_rate  # just access to verify (0.9)
        result = domain_adjust(1.0, reg, pi)
        assert result == pytest.approx(1.0)  # expr is >0.9, 0.9 fails

    def test_no_adjustment(self):
        reg = _reg(stats=EngineStats(total_pages=1, total_latency_ms=10000))
        assert domain_adjust(1.0, reg, PageInfo(page_num=0)) == pytest.approx(1.0)


# ──── _select_poll_candidate ────

class TestSelectPollCandidate:
    def test_returns_from_rest(self):
        top = [_reg(meta=_am("a"))]
        rest = [_reg(meta=_am("b")), _reg(meta=_am("c"))]
        pick = _select_poll_candidate(top + rest, top)
        assert pick in rest

    def test_returns_none_when_no_rest(self):
        e = _reg(meta=_am("a"))
        assert _select_poll_candidate([e], [e]) is None

    def test_excludes_unavailable(self):
        top = [_reg(meta=_am("a"))]
        unavailable = _reg(meta=_am("b"), status="UNAVAILABLE")
        assert _select_poll_candidate(top + [unavailable], top) is None


# ──────── 辅助：构造完整 scheduler 测试环境 ────────

def _mk(name: str, tier: int = 1, pass_: int = 5, fail: int = 0,
        latency_ms: int = 1000, status: str = "HEALTHY",
        requires_network: bool = False) -> EngineRegistration:
    total = pass_ + fail
    return _reg(
        meta=_am(name, tier=tier, requires_network=requires_network),
        stats=EngineStats(
            total_pages=total, total_latency_ms=latency_ms * total,
            glyph_pass_count=pass_, glyph_fail_count=fail,
        ),
        status=status,
    )


def _full_registry() -> EngineRegistry:
    r = EngineRegistry()
    for e in [
        _mk("paddle", tier=1, pass_=8, fail=2, latency_ms=1000),
        _mk("rapid", tier=1, pass_=7, fail=3, latency_ms=200),
        _mk("cloud", tier=2, pass_=4, fail=1, latency_ms=10000, requires_network=True),
        _mk("sensenova", tier=2, pass_=5, fail=0, latency_ms=12000, requires_network=True),
    ]:
        r.register(e)
    return r


# ──────── EngineScheduler.select_candidates ────────

class TestSelectCandidates:
    @staticmethod
    def _budget(**kw) -> Budget:
        return Budget(max_pages=50, max_wall_clock_ms=7200000, **kw)

    def test_pinned_engine_returns_single(self):
        r = _full_registry()
        sched = EngineScheduler()
        result = sched.select_candidates(
            r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            overrides=EngineOverrides(pinned_engine="rapid"),
        )
        assert len(result) == 1
        assert result[0].meta.name == "rapid"

    def test_pinned_unavailable_raises(self):
        r = _full_registry()
        r.mark_unavailable("rapid")
        with pytest.raises(PinnedEngineUnavailableError):
            EngineScheduler().select_candidates(
                r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
                overrides=EngineOverrides(pinned_engine="rapid"),
            )

    def test_pinned_nonexistent_raises(self):
        with pytest.raises(PinnedEngineUnavailableError):
            EngineScheduler().select_candidates(
                _full_registry(), tier=1, page_info=PageInfo(page_num=0),
                budget=self._budget(),
                overrides=EngineOverrides(pinned_engine="ghost"),
            )

    def test_empty_tier_returns_empty(self):
        result = EngineScheduler().select_candidates(
            EngineRegistry(), tier=1,
            page_info=PageInfo(page_num=0), budget=self._budget(),
        )
        assert result == []

    def test_vertical_skips_tier1(self):
        pl = PageLayout(page_num=0, is_vertical=True)
        result = EngineScheduler().select_candidates(
            _full_registry(), tier=1,
            page_info=PageInfo(page_num=0, is_vertical=True),
            budget=self._budget(), page_layout=pl,
        )
        assert result == []

    def test_cloud_filtered_when_not_allowed(self):
        result = EngineScheduler().select_candidates(
            _full_registry(), tier=2,
            page_info=PageInfo(page_num=0),
            budget=self._budget(allow_cloud_vision=False),
        )
        assert result == []

    def test_cloud_included_when_allowed(self):
        result = EngineScheduler().select_candidates(
            _full_registry(), tier=2,
            page_info=PageInfo(page_num=0),
            budget=self._budget(allow_cloud_vision=True),
        )
        assert len(result) >= 1

    def test_backoff_filters_by_latency(self):
        r = _full_registry()
        r.get("paddle").stats.rolling_latencies = [50000] * 10
        sched = EngineScheduler(tier_limits={1: 2})
        result = sched.select_candidates(
            r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            overrides=EngineOverrides(backoff_threshold_ms=30000),
        )
        names = [e.meta.name for e in result]
        assert "rapid" in names
        assert "paddle" not in names

    def test_rate_limited_excluded(self):
        r = _full_registry()
        result = EngineScheduler(tier_limits={1: 2}).select_candidates(
            r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            overrides=EngineOverrides(rate_limited_until={"paddle": time.time() + 3600}),
        )
        names = [e.meta.name for e in result]
        assert "paddle" not in names

    def test_budget_exhausted_returns_empty(self):
        b = self._budget()
        b.exhaust()
        result = EngineScheduler().select_candidates(
            _full_registry(), tier=1, page_info=PageInfo(page_num=0), budget=b,
        )
        assert result == []

    def test_prefer_speed_ordering(self):
        r = _full_registry()
        result = EngineScheduler(tier_limits={1: 2}).select_candidates(
            r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            overrides=EngineOverrides(prefer="speed"),
        )
        delays = [e.avg_latency_per_page_ms for e in result]
        assert delays == sorted(delays)

    def test_prefer_accuracy_ordering(self):
        r = _full_registry()
        result = EngineScheduler(tier_limits={1: 2}).select_candidates(
            r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            overrides=EngineOverrides(prefer="accuracy"),
        )
        assert result[0].meta.name == "paddle"  # 0.8 > 0.7

    def test_top_n_limited(self):
        r = _full_registry()
        # 隔离 5% 轮询采样（_should_poll 随机），仅验证 tier_limits 对 top-N 的硬上限
        with mock.patch("kzocr.scheduler.scheduler._should_poll", return_value=False):
            result = EngineScheduler(tier_limits={1: 1}).select_candidates(
                r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            )
        assert len(result) == 1

    def test_polling_sampling(self):
        """轮询采样确定性验证（mock 掉 5% 随机），并验证其可突破 tier_limits 上限。"""
        r = _full_registry()  # 2 个 tier-1 引擎：paddle / rapid
        sched = EngineScheduler(tier_limits={1: 1})

        # 轮询关闭：严格等于 tier_limits 上限
        with mock.patch("kzocr.scheduler.scheduler._should_poll", return_value=False):
            off = sched.select_candidates(
                r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            )
        assert len(off) == 1

        # 轮询开启且存在未入选候选：在 Top-N 之上额外追加一个（突破上限）
        with mock.patch("kzocr.scheduler.scheduler._should_poll", return_value=True):
            on = sched.select_candidates(
                r, tier=1, page_info=PageInfo(page_num=0), budget=self._budget(),
            )
        assert len(on) == 2  # 突破 tier_limits={1:1}
        assert {e.meta.name for e in on} == {"paddle", "rapid"}

    def test_all_steps_integration(self):
        """Tier2 全被 rate_limited → 空"""
        r = _full_registry()
        result = EngineScheduler(tier_limits={2: 2}).select_candidates(
            r, tier=2, page_info=PageInfo(page_num=0),
            budget=self._budget(allow_cloud_vision=True),
            overrides=EngineOverrides(
                rate_limited_until={"cloud": time.time() + 3600,
                                     "sensenova": time.time() + 3600},
            ),
        )
        assert result == []

    def test_scheduler_config_wired(self):
        from kzocr.config import SchedulerConfig
        sc = SchedulerConfig(max_tier1_engines=1, max_tier2_engines=2, max_tier3_engines=3)
        sched = EngineScheduler(scheduler_config=sc)
        assert sched._max_engines(1) == 1
        assert sched._max_engines(2) == 2
        assert sched._max_engines(3) == 3
