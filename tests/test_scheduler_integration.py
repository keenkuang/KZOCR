"""E1+E2 集成测试（v0.7 §10.1）：验证 EngineRegistry 与 EngineScheduler 的协同闭环。

覆盖：
- 跨 tier 的层级过滤（tier1/tier2/tier3 互不串）
- 竖排页跳过 Tier 1
- allow_cloud_vision 过滤云端引擎
- E1 统计（record）→ E2 贝叶斯评分排序 闭环（记录后候选排序随通过率变化）
- probe_engines 对无 probe 配置的引擎保持现状、不抛错
"""

from __future__ import annotations

from kzocr.engine.types import AdapterMeta, EngineConfig, PageLayout
from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.scheduler import (
    Budget,
    EngineOverrides,
    EngineScheduler,
    PageInfo,
)


def _meta(name: str, tier: int = 1, requires_network: bool = False) -> AdapterMeta:
    return AdapterMeta(
        name=name, label=name, tier=tier, requires_network=requires_network
    )


def _budget(allow_cloud_vision: bool = False) -> Budget:
    return Budget(
        max_pages=50, max_wall_clock_ms=7_200_000, allow_cloud_vision=allow_cloud_vision
    )


def _page(is_vertical: bool = False) -> PageInfo:
    return PageInfo(page_num=1, is_vertical=is_vertical)


def _reg_multi_tier() -> EngineRegistry:
    reg = EngineRegistry()
    reg.register_adapter(_meta("paddle", 1), EngineConfig())
    reg.register_adapter(_meta("mineru", 1), EngineConfig())
    reg.register_adapter(_meta("sensenova", 2, requires_network=True), EngineConfig())
    reg.register_adapter(_meta("shizhengpt", 3, requires_network=True), EngineConfig())
    return reg


def test_tier_isolation_across_tiers():
    """select_candidates 按 tier 返回，跨 tier 互不串。"""
    reg = _reg_multi_tier()
    sched = EngineScheduler()
    assert {c.meta.name for c in sched.select_candidates(reg, 1, _page(), _budget())} == {
        "paddle",
        "mineru",
    }
    # tier2/tier3 为云端引擎，需 allow_cloud_vision=True 才保留
    assert {c.meta.name for c in sched.select_candidates(reg, 2, _page(), _budget(True))} == {
        "sensenova"
    }
    assert {c.meta.name for c in sched.select_candidates(reg, 3, _page(), _budget(True))} == {
        "shizhengpt"
    }


def test_vertical_skips_tier1_integration():
    reg = _reg_multi_tier()
    sched = EngineScheduler()
    assert sched.select_candidates(reg, 1, _page(), _budget(), page_layout=PageLayout(page_num=1, is_vertical=True)) == []
    # 非竖排正常返回 Tier1
    assert len(sched.select_candidates(reg, 1, _page(), _budget())) == 2


def test_allow_cloud_vision_filter_integration():
    reg = _reg_multi_tier()
    sched = EngineScheduler()
    # 不允许云端 → 仅本地（sensenova/shizhengpt 均被过滤）
    cands = sched.select_candidates(reg, 2, _page(), _budget(False))
    assert all(not c.meta.requires_network for c in cands)
    # 允许云端 → 云端引擎出现
    cands = sched.select_candidates(reg, 2, _page(), _budget(True))
    assert any(c.meta.requires_network for c in cands)


def test_stats_feed_scheduler_ordering():
    """E1 统计闭环：record 后候选排序随通过率变化（§3.5 贝叶斯评分）。"""
    reg = EngineRegistry()
    # 两引擎同 tier、同延迟，初始按注册顺序
    reg.register_adapter(_meta("a", 1), EngineConfig())
    reg.register_adapter(_meta("b", 1), EngineConfig())
    sched = EngineScheduler()

    before = [c.meta.name for c in sched.select_candidates(reg, 1, _page(), _budget())]
    assert before[0] == "a"  # 注册顺序（n=0 时 prior/latency 相同）

    # 给 a 记 FAIL、b 记 PASS（各 10 页），通过率拉开差距
    reg.record("a", success=False, glyph="FAIL", latency_ms=1000, pages=10)
    reg.record("b", success=True, glyph="PASS", latency_ms=1000, pages=10)

    after = [c.meta.name for c in sched.select_candidates(reg, 1, _page(), _budget())]
    assert after[0] == "b", f"通过率高的 b 应排前，实际 {after}"


def test_probe_engines_keeps_unprobed_engines():
    """probe_engines 对无 probe 配置的引擎保持现状、不抛错（§3.4）。"""
    reg = _reg_multi_tier()
    # 全部无 probe 配置 → 状态保持默认 HEALTHY
    from kzocr.scheduler.registry import probe_engines

    probe_engines(reg)
    assert all(r.status == "HEALTHY" for r in reg.list())


def test_pinned_overrides_tier_filter():
    """pinned_engine 覆盖层级过滤，直接返回该引擎（§4.1 第1步）。"""
    reg = _reg_multi_tier()
    sched = EngineScheduler()
    cands = sched.select_candidates(
        reg,
        1,
        _page(),
        _budget(),
        overrides=EngineOverrides(pinned_engine="shizhengpt"),
    )
    assert [c.meta.name for c in cands] == ["shizhengpt"]


def test_unavailable_excluded_from_candidates():
    """资源不可用引擎在候选中被排除（§4.1 状态位缓存）。"""
    reg = _reg_multi_tier()
    reg.mark_unavailable("paddle")
    sched = EngineScheduler()
    names = {c.meta.name for c in sched.select_candidates(reg, 1, _page(), _budget())}
    assert names == {"mineru"}
