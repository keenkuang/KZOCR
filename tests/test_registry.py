"""E1 EngineRegistry 测试（v0.7 §3）。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from kzocr.engine.types import AdapterMeta, EngineConfig, ProbeResult
from kzocr.engines.errors import SchedulerError
from kzocr.scheduler.registry import (
    AVG_LATENCY_DEFAULT_MS,
    BAYESIAN_C,
    BAYESIAN_PRIOR,
    GLYPH_PASS_RATE_DEFAULT,
    EngineRegistration,
    EngineRegistry,
    EngineStats,
    _apply_event,
    _bayesian_score,
    _probe_one,
    probe_engines,
)


def _am(name: str = "test", tier: int = 1, **kw) -> AdapterMeta:
    return AdapterMeta(name=name, label=name, tier=tier, kind="page", **kw)


def _reg(**kw) -> EngineRegistration:
    """快速构造注册项（config 自动填入默认值）。"""
    kw.setdefault("config", EngineConfig())
    if "meta" not in kw:
        kw["meta"] = _am()
    if "stats" not in kw:
        kw["stats"] = EngineStats()
    return EngineRegistration(**kw)


# ──────── EngineStats ────────

class TestEngineStats:
    def test_default_values(self):
        s = EngineStats()
        assert s.total_calls == 0
        assert s.total_latency_ms == 0
        assert s.glyph_pass_count == 0
        assert s.last_error is None

    def test_recent_avg_latency_empty(self):
        assert EngineStats().recent_avg_latency_ms == 0.0

    def test_recent_avg_latency(self):
        s = EngineStats(rolling_latencies=[100.0, 200.0])
        assert s.recent_avg_latency_ms == 150.0

    def test_recent_fail_rate_empty(self):
        assert EngineStats().recent_fail_rate == 0.0

    def test_recent_fail_rate_calc(self):
        s = EngineStats(rolling_failures=[True, False, True])
        assert s.recent_fail_rate == pytest.approx(1.0 / 3.0)

    def test_decay_fresh(self):
        assert EngineStats().decay() == 1.0

    def test_decay_past(self):
        s = EngineStats(last_seen=time.time() - 86400 * 7)
        assert s.decay(half_life_days=7.0) == pytest.approx(0.5, abs=0.001)

    def test_repr_masks_last_error(self):
        s = EngineStats(last_error="secret/path/key")
        assert "<redacted>" in repr(s)
        assert "secret" not in repr(s)

    def test_repr_no_error(self):
        s = EngineStats()
        assert "None" in repr(s)


# ──────── EngineRegistration ────────

class TestEngineRegistration:
    def test_glyph_pass_rate_cold(self):
        assert _reg().glyph_pass_rate == GLYPH_PASS_RATE_DEFAULT

    def test_glyph_pass_rate_with_data(self):
        r = _reg(stats=EngineStats(glyph_pass_count=8, glyph_fail_count=2))
        assert r.glyph_pass_rate == 0.8

    def test_glyph_pass_rate_rare_counts_as_pass(self):
        r = _reg(stats=EngineStats(glyph_pass_count=5, glyph_rare_count=3, glyph_fail_count=2))
        assert r.glyph_pass_rate == 0.8

    def test_glyph_pass_rate_unknown_uncertain_no_pass(self):
        r = _reg(stats=EngineStats(glyph_unknown_count=1, glyph_uncertain_count=1))
        # total=2, pass+rare=0 → 0.0
        assert r.glyph_pass_rate == 0.0

    def test_avg_latency_cold(self):
        r = _reg(stats=EngineStats(total_pages=0, total_latency_ms=0))
        # total_latency_ms==0 → fallback
        assert r.avg_latency_per_page_ms == AVG_LATENCY_DEFAULT_MS

    def test_avg_latency_with_data(self):
        r = _reg(stats=EngineStats(total_pages=10, total_latency_ms=20000))
        assert r.avg_latency_per_page_ms == 2000.0

    def test_repr_masks_config_and_adapter(self):
        r = _reg(adapter=object())
        rep = repr(r)
        assert "<EngineConfig>" in rep
        assert "<set>" in rep


# ──────── EngineRegistry ────────

class TestEngineRegistry:
    def _r(self) -> EngineRegistry:
        return EngineRegistry()

    def test_register_and_get(self):
        reg = _reg(meta=_am("a1"))
        r = self._r()
        r.register(reg)
        assert r.get("a1") is reg

    def test_get_nonexistent(self):
        assert self._r().get("nonexistent") is None

    def test_register_adapter(self):
        r = self._r()
        result = r.register_adapter(_am("a1"), EngineConfig())
        assert r.get("a1") is result

    def test_list(self):
        r = self._r()
        r.register_adapter(_am("a1"), EngineConfig())
        r.register_adapter(_am("a2"), EngineConfig())
        assert len(r.list()) == 2

    def test_list_by_tier(self):
        r = self._r()
        t1 = r.register_adapter(_am("a1", tier=1), EngineConfig())
        t2 = r.register_adapter(_am("a2", tier=2), EngineConfig())
        assert r.list_by_tier(1) == [t1]
        assert r.list_by_tier(2) == [t2]

    def test_list_by_tier_excludes_unavailable(self):
        r = self._r()
        r.register_adapter(_am("a1", tier=1), EngineConfig(), status="UNAVAILABLE")
        r.register_adapter(_am("a2", tier=1), EngineConfig(), status="HEALTHY")
        assert len(r.list_by_tier(1)) == 1
        assert len(r.list_by_tier(1, include_unavailable=True)) == 2

    def test_mark_unavailable(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig())
        r.mark_unavailable("a1")
        assert reg.status == "UNAVAILABLE"

    def test_mark_degraded(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig())
        r.mark_degraded("a1")
        assert reg.status == "DEGRADED"

    def test_mark_healthy(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig(), status="UNAVAILABLE")
        r.mark_healthy("a1")
        assert reg.status == "HEALTHY"

    def test_set_status_unregistered_raises(self):
        with pytest.raises(SchedulerError, match="未注册"):
            self._r().mark_unavailable("nonexistent")

    def test_record_basic(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig())
        r.record("a1", success=True, glyph="PASS", latency_ms=1000, pages=3)
        assert reg.stats.total_calls == 1
        assert reg.stats.total_pages == 3
        assert reg.stats.total_latency_ms == 1000
        assert reg.stats.glyph_pass_count == 1

    def test_record_all_glyph_statuses(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig())
        for g in ("PASS", "FAIL", "UNKNOWN", "RARE", "UNCERTAIN"):
            r.record("a1", success=True, glyph=g)
        assert reg.stats.glyph_pass_count == 1
        assert reg.stats.glyph_fail_count == 1
        assert reg.stats.glyph_unknown_count == 1
        assert reg.stats.glyph_rare_count == 1
        assert reg.stats.glyph_uncertain_count == 1

    def test_record_failure_sets_error(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig())
        r.record("a1", success=False, error="timeout")
        assert reg.stats.last_error == "timeout"

    def test_record_unregistered_raises(self):
        with pytest.raises(SchedulerError, match="未注册"):
            self._r().record("nonexistent", success=True)

    def test_record_rolling_window(self):
        r = self._r()
        reg = r.register_adapter(_am("a1"), EngineConfig())
        for i in range(105):
            r.record("a1", success=True, latency_ms=float(i))
        assert len(reg.stats.rolling_latencies) == 100

    def test_persist_empty_dir(self):
        r = self._r()
        r.register_adapter(_am("a1"), EngineConfig())
        r.persist_benchmarks()  # should not crash

    def test_persist_and_load_roundtrip(self, tmp_path):
        bdir = str(tmp_path / "bench")
        r = EngineRegistry(benchmark_dir=bdir)
        r.register_adapter(_am("paddleocr"), EngineConfig())
        r.record("paddleocr", success=True, glyph="PASS", latency_ms=4500)
        r.persist_benchmarks()
        assert (Path(bdir) / "paddleocr.ndjson").exists()
        # 加载回新 registry
        r2 = EngineRegistry(benchmark_dir=bdir)
        r2.register(_reg(meta=_am("paddleocr")))
        r2.load_benchmarks()
        s = r2.get("paddleocr").stats
        assert s.total_calls == 1
        assert s.total_latency_ms == 4500
        assert s.glyph_pass_count == 1

    def test_load_benchmarks_dir_not_exists(self):
        EngineRegistry(benchmark_dir="/nonexistent/12345").load_benchmarks()

    def test_load_benchmarks_skips_unregistered(self, tmp_path):
        bdir = str(tmp_path / "bench")
        Path(bdir).mkdir()
        (Path(bdir) / "ghost.ndjson").write_text(
            json.dumps({"engine": "ghost"}) + "\n", encoding="utf-8")
        r = EngineRegistry(benchmark_dir=bdir)
        r.load_benchmarks()
        assert r.list() == []

    def test_apply_event_none_reg(self):
        _apply_event(None, {})  # should not crash


# ──────── _bayesian_score ────────

class TestBayesianScore:
    def test_cold_start_finite(self):
        assert _bayesian_score(_reg()) > 0

    def test_with_data(self):
        r = _reg(stats=EngineStats(
            total_pages=10, total_latency_ms=10000,
            glyph_pass_count=9, glyph_fail_count=1,
        ))
        assert _bayesian_score(r) > 0

    def test_higher_pass_rate_wins(self):
        s_hi = EngineStats(total_pages=10, total_latency_ms=10000,
                            glyph_pass_count=9, glyph_fail_count=1)
        s_lo = EngineStats(total_pages=10, total_latency_ms=10000,
                            glyph_pass_count=5, glyph_fail_count=5)
        assert _bayesian_score(_reg(stats=s_hi)) > _bayesian_score(_reg(stats=s_lo))

    def test_formula_consistency(self):
        r = _reg(stats=EngineStats(
            total_pages=5, total_latency_ms=5000,
            glyph_pass_count=4, glyph_fail_count=1,
        ))
        n, pr, lat = 5, 0.8, 1000.0
        expected = (pr * n + BAYESIAN_C * BAYESIAN_PRIOR) / (n + BAYESIAN_C) * (1.0 / lat)
        assert _bayesian_score(r) == pytest.approx(expected, rel=0.01)


# ──────── probe_engines ────────

def _probe_reg(name: str = "test", tier: int = 1, probe: dict | None = None,
               status: str = "HEALTHY", config: EngineConfig | None = None) -> EngineRegistration:
    meta = _am(name, tier=tier)
    if probe is not None:
        meta.probe = probe
    return _reg(meta=meta, config=config or EngineConfig(), status=status)


class TestProbeEngines:
    def test_env_key_present(self):
        reg = _probe_reg(probe={"method": "env", "key": "KZOCR_TEST_BENCH"})
        with patch.dict(os.environ, {"KZOCR_TEST_BENCH": "1"}, clear=False):
            _probe_one(reg, None)
        assert reg.status == "HEALTHY"

    def test_env_key_missing(self):
        reg = _probe_reg(probe={"method": "env", "key": "KZOCR_TEST_BENCH_MISSING"})
        with patch.dict(os.environ, {}, clear=False):
            _probe_one(reg, None)
        assert reg.status == "UNAVAILABLE"

    def test_env_cached(self):
        reg = _probe_reg(probe={"method": "env", "key": "KZOCR_TEST_BENCH_CACHE"})
        _probe_one(reg, ProbeResult(keys={"KZOCR_TEST_BENCH_CACHE": True}))
        assert reg.status == "HEALTHY"

    def test_file_exists(self, tmp_path):
        f = tmp_path / "marker.txt"
        f.write_text("")
        reg = _probe_reg(probe={"method": "file", "path": str(f)})
        _probe_one(reg, None)
        assert reg.status == "HEALTHY"

    def test_file_not_exists(self):
        reg = _probe_reg(probe={"method": "file", "path": "/nonexistent/marker"})
        _probe_one(reg, None)
        assert reg.status == "UNAVAILABLE"

    def test_port_unreachable(self):
        reg = _probe_reg(probe={"method": "port", "host": "127.0.0.1", "port": 1})
        _probe_one(reg, None)
        assert reg.status == "UNAVAILABLE"

    def test_api_no_url(self):
        reg = _probe_reg(probe={"method": "api"})
        _probe_one(reg, None)
        assert reg.status == "HEALTHY"

    def test_api_invalid_url(self):
        reg = _probe_reg(probe={"method": "api", "url": "http://[::1]:99999"})
        _probe_one(reg, None)
        assert reg.status == "UNAVAILABLE"

    def test_no_method_keeps_status(self):
        reg = _probe_reg(status="DEGRADED")
        _probe_one(reg, None)
        assert reg.status == "DEGRADED"

    def test_probe_engines_call_all(self):
        r = EngineRegistry()
        r.register(_probe_reg("has_key", probe={"method": "env", "key": "KZOCR_TEST_BENCH"}))
        r.register(_probe_reg("no_key", probe={"method": "env", "key": "KZOCR_TEST_BENCH_MISSING"}))
        with patch.dict(os.environ, {"KZOCR_TEST_BENCH": "1"}, clear=False):
            probe_engines(r)
        assert r.get("has_key").status == "HEALTHY"
        assert r.get("no_key").status == "UNAVAILABLE"
