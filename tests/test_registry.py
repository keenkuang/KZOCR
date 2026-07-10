"""E1 EngineRegistry 与 select_candidates 测试（v0.7 §3 / §4.3）。"""

from __future__ import annotations

from kzocr.engine.types import AdapterMeta, EngineConfig
from kzocr.engines.errors import SchedulerError
from kzocr.scheduler.registry import (
    AVG_LATENCY_DEFAULT_MS,
    GLYPH_PASS_RATE_DEFAULT,
    EngineRegistry,
    select_candidates,
)


def _meta(name: str, tier: int = 1, requires_network: bool = False) -> AdapterMeta:
    return AdapterMeta(
        name=name,
        label=name,
        tier=tier,
        requires_network=requires_network,
    )


def test_register_and_get():
    reg = EngineRegistry()
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig(api_key_env="X"))
    assert reg.get("paddleocr") is not None
    assert reg.get("missing") is None


def test_list_by_tier():
    reg = EngineRegistry()
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg.register_adapter(_meta("sensenova", 2, requires_network=True), EngineConfig())
    assert {r.meta.name for r in reg.list_by_tier(1)} == {"paddleocr"}
    assert {r.meta.name for r in reg.list_by_tier(2)} == {"sensenova"}


def test_cold_start_defaults():
    reg = EngineRegistry()
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    r = reg.get("paddleocr")
    assert r.glyph_pass_rate == GLYPH_PASS_RATE_DEFAULT
    assert r.avg_latency_per_page_ms == AVG_LATENCY_DEFAULT_MS


def test_record_updates_stats_and_derived():
    reg = EngineRegistry()
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg.record("paddleocr", success=True, glyph="PASS", latency_ms=2000, pages=1)
    reg.record("paddleocr", success=True, glyph="PASS", latency_ms=2000, pages=1)
    r = reg.get("paddleocr")
    assert r.stats.total_pages == 2
    assert r.glyph_pass_rate == 1.0
    assert r.avg_latency_per_page_ms == 2000.0


def test_record_unknown_and_fail_counts():
    reg = EngineRegistry()
    reg.register_adapter(_meta("x", 1), EngineConfig())
    reg.record("x", success=False, glyph="FAIL", latency_ms=100, error="boom")
    reg.record("x", success=True, glyph="UNKNOWN", latency_ms=100)
    r = reg.get("x")
    assert r.stats.glyph_fail_count == 1
    assert r.stats.glyph_unknown_count == 1
    assert r.stats.last_error == "boom"
    # FAIL+UNKNOWN 计入分母，PASS 为 0 → 通过率 0
    assert r.glyph_pass_rate == 0.0


def test_record_unknown_engine_raises():
    reg = EngineRegistry()
    try:
        reg.record("nope", success=True)
    except SchedulerError:
        pass
    else:
        raise AssertionError("未注册引擎应抛 SchedulerError")


def test_select_candidates_tier_filter_and_rank():
    reg = EngineRegistry()
    reg.register_adapter(_meta("rapidocr", 1), EngineConfig())
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg.record("paddleocr", success=True, glyph="PASS", latency_ms=1000, pages=10)
    reg.record("rapidocr", success=True, glyph="FAIL", latency_ms=5000, pages=10)
    cands = select_candidates(reg, tier=1)
    names = [c.meta.name for c in cands]
    assert names[0] == "paddleocr"  # 评分更高者优先
    assert set(names) == {"paddleocr", "rapidocr"}


def test_select_candidates_prefer_accuracy():
    reg = EngineRegistry()
    reg.register_adapter(_meta("a", 1), EngineConfig())
    reg.register_adapter(_meta("b", 1), EngineConfig())
    reg.record("a", success=True, glyph="FAIL", latency_ms=1000, pages=5)
    reg.record("b", success=True, glyph="PASS", latency_ms=1000, pages=5)
    cands = select_candidates(reg, tier=1, prefer="accuracy")
    assert cands[0].meta.name == "b"


def test_select_candidates_prefer_speed():
    reg = EngineRegistry()
    reg.register_adapter(_meta("a", 1), EngineConfig())
    reg.register_adapter(_meta("b", 1), EngineConfig())
    reg.record("a", success=True, glyph="PASS", latency_ms=8000, pages=5)
    reg.record("b", success=True, glyph="PASS", latency_ms=500, pages=5)
    cands = select_candidates(reg, tier=1, prefer="speed")
    assert cands[0].meta.name == "b"


def test_select_candidates_empty_for_missing_tier():
    reg = EngineRegistry()
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    assert select_candidates(reg, tier=9) == []


def test_record_glyph_rare_and_uncertain_counted():
    """glyph 契约：RARE/UNCERTAIN 不再被静默丢弃，且 RARE 计入通过率分子。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("x", 1), EngineConfig())
    reg.record("x", success=True, glyph="RARE", latency_ms=100)
    reg.record("x", success=True, glyph="UNCERTAIN", latency_ms=100)
    reg.record("x", success=True, glyph="PASS", latency_ms=100)
    r = reg.get("x")
    assert r.stats.glyph_rare_count == 1
    assert r.stats.glyph_uncertain_count == 1
    # 通过率 = (PASS + RARE) / 全部样本 = (1 + 1) / 3
    assert r.glyph_pass_rate == (1 + 1) / 3


def test_avg_latency_no_zero_division():
    """有页数但从未记录延迟时，avg_latency 返回保守默认值而非 0（修复除零）。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("x", 1), EngineConfig())
    reg.record("x", success=True)  # 不传 latency_ms
    r = reg.get("x")
    assert r.avg_latency_per_page_ms == AVG_LATENCY_DEFAULT_MS


def test_select_candidates_no_zero_division():
    """有页数、无延迟记录时 select_candidates 不应崩溃（_bayesian_score 不除零）。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("a", 1), EngineConfig())
    reg.register_adapter(_meta("b", 1), EngineConfig())
    reg.record("a", success=True, glyph="PASS")
    reg.record("b", success=True, glyph="PASS")
    cands = select_candidates(reg, tier=1)
    assert len(cands) == 2


def test_benchmark_persist_and_load_roundtrip(tmp_path):
    """NDJSON 追加式持久化：写 → 重建 → 统计一致（§7.1）。"""
    d = str(tmp_path / "benchmarks")
    reg = EngineRegistry(benchmark_dir=d)
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg.record("paddleocr", success=True, glyph="PASS", latency_ms=2000, pages=3)
    reg.record("paddleocr", success=True, glyph="FAIL", latency_ms=1000, pages=2)
    reg.persist_benchmarks()

    reg2 = EngineRegistry(benchmark_dir=d)
    reg2.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg2.load_benchmarks()
    r = reg2.get("paddleocr")
    assert r.stats.total_pages == 5
    assert r.stats.total_latency_ms == 3000
    assert r.stats.glyph_pass_count == 1
    assert r.stats.glyph_fail_count == 1
    assert r.glyph_pass_rate == 0.5  # (PASS + RARE) / 全部 = (1+0)/2


def test_benchmark_persist_is_append_only(tmp_path):
    """多次 persist 应为跨进程追加，而非覆写（O(1)，不丢历史）。"""
    d = str(tmp_path / "benchmarks")
    reg = EngineRegistry(benchmark_dir=d)
    reg.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg.record("paddleocr", success=True, glyph="PASS", latency_ms=100, pages=1)
    reg.persist_benchmarks()
    reg.record("paddleocr", success=True, glyph="PASS", latency_ms=100, pages=1)
    reg.persist_benchmarks()

    reg2 = EngineRegistry(benchmark_dir=d)
    reg2.register_adapter(_meta("paddleocr", 1), EngineConfig())
    reg2.load_benchmarks()
    assert reg2.get("paddleocr").stats.total_pages == 2


def test_benchmark_persist_no_dir_is_noop():
    """无 benchmark_dir 时 persist 不应抛，且 pending 被清空。"""
    reg = EngineRegistry()
    reg.register_adapter(_meta("x", 1), EngineConfig())
    reg.record("x", success=True, glyph="PASS")
    reg.persist_benchmarks()
    assert reg._pending == []


def test_benchmark_load_missing_dir_is_noop():
    """benchmark 目录不存在时 load 不应抛（冷启动）。"""
    reg = EngineRegistry(benchmark_dir="/nonexistent/benchmarks")
    reg.register_adapter(_meta("x", 1), EngineConfig())
    reg.load_benchmarks()
    assert reg.get("x").stats.total_pages == 0
