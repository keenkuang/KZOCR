"""SchedulerConfig 单元测试。"""

from __future__ import annotations

import os

import pytest

from kzocr.config import SchedulerConfig, load_config

# ── fixture：清理环境变量 ──
@pytest.fixture(autouse=True)
def clean_env():
    scheduler_keys = [k for k in os.environ if k.startswith("KZOCR_MAX_") or k.startswith("KZOCR_TOTAL_") or k.startswith("KZOCR_BENCHMARK") or k.startswith("KZOCR_TRACE") or k.startswith("KZOCR_ENGINE_PARALLEL") or k.startswith("KZOCR_ALLOW_CLOUD_VISION") or k.startswith("KZOCR_TIER_LIMIT") or k.startswith("KZOCR_ENABLE_CROSS") or k.startswith("KZOCR_CONSENSUS") or k.startswith("KZOCR_PERSIST_DB") or k.startswith("KZOCR_DB_DIR") or k.startswith("KZOCR_DECAY_HALF")]
    backup = {k: os.environ.pop(k, None) for k in scheduler_keys}
    yield
    os.environ.update({k: v for k, v in backup.items() if v is not None})


class TestSchedulerConfigDefaults:
    def test_default_values(self):
        sc = SchedulerConfig()
        assert sc.max_tier1_engines == 2
        assert sc.max_tier2_engines == 1
        assert sc.max_tier3_engines == 1
        assert sc.max_pages == 50
        assert sc.total_timeout_s == 7200
        assert sc.max_time_per_page_ms == 120000
        assert sc.benchmark_dir == ""
        assert sc.trace_dir == ""
        assert sc.engine_parallel is False
        assert sc.allow_cloud_vision is False
        assert sc.tier_limit == 3
        assert sc.cross_check is False
        assert sc.consensus_sample_rate == 0.0
        assert sc.persist_db is False
        assert sc.db_dir == ""
        assert sc.half_life_days == 7.0

    def test_from_env_empty(self):
        sc = SchedulerConfig.from_env()
        assert sc.max_pages == 50
        assert sc.benchmark_dir == ""

    def test_from_env_explicit(self):
        os.environ["KZOCR_MAX_PAGES"] = "100"
        os.environ["KZOCR_TOTAL_TIMEOUT"] = "3600"
        os.environ["KZOCR_ALLOW_CLOUD_VISION"] = "1"
        os.environ["KZOCR_CROSS_CHECK"] = "1"  # wrong key (should be ENABLE_CROSS_CHECK)
        os.environ["KZOCR_ENABLE_CROSS_CHECK"] = "1"
        os.environ["KZOCR_PERSIST_DB"] = "true"
        sc = SchedulerConfig.from_env()
        assert sc.max_pages == 100
        assert sc.total_timeout_s == 3600
        assert sc.allow_cloud_vision is True
        assert sc.persist_db is True
        assert sc.cross_check is True

    def test_from_env_bad_int_fallback(self):
        os.environ["KZOCR_MAX_PAGES"] = "not_a_number"
        sc = SchedulerConfig.from_env()
        assert sc.max_pages == 50  # fallback to default

    def test_to_budget(self):
        sc = SchedulerConfig(max_pages=30, total_timeout_s=600, max_time_per_page_ms=30000, allow_cloud_vision=True)
        b = sc.to_budget()
        assert b.max_pages == 30
        assert b.max_wall_clock_ms == 600 * 1000
        assert b.max_time_per_page_ms == 30000
        assert b.allow_cloud_vision is True


class TestConfigIntegration:
    def test_load_config_includes_scheduler(self):
        cfg = load_config()
        assert hasattr(cfg, "scheduler")
        assert isinstance(cfg.scheduler, SchedulerConfig)
        assert cfg.scheduler.max_pages == 50  # default since env is clean

    def test_config_default_factory(self):
        """未调用 load_config() 时，直接 Config() 的 scheduler 字段也有默认值。"""
        from kzocr.config import Config

        cfg = Config()
        assert cfg.scheduler.max_pages == 50
        assert cfg.scheduler.persist_db is False
