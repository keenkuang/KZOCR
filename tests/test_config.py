"""v0.5 AMEND D0: Config 扩展测试。"""
from __future__ import annotations

import os

from kzocr.config import Config, load_config


class TestKzocrOutputDir:
    def test_default_value(self):
        """不设置环境变量时使用默认值 /tmp/kzocr/output。"""
        # 确保环境变量不存在
        os.environ.pop("KZOCR_OUTPUT_DIR", None)
        cfg = Config.from_env()
        assert cfg.kzocr_output_dir == "/tmp/kzocr/output"

    def test_env_override(self):
        """设置 KZOCR_OUTPUT_DIR 应覆盖默认值。"""
        os.environ["KZOCR_OUTPUT_DIR"] = "/custom/path"
        cfg = Config.from_env()
        assert cfg.kzocr_output_dir == "/custom/path"
        os.environ.pop("KZOCR_OUTPUT_DIR", None)

    def test_cache_ttl_default(self):
        """不设置 KZOCR_CACHE_TTL 时默认 86400。"""
        os.environ.pop("KZOCR_CACHE_TTL", None)
        cfg = Config.from_env()
        assert cfg.cache_ttl_seconds == 86400

    def test_cache_ttl_env_override(self):
        """设置 KZOCR_CACHE_TTL 应覆盖默认值。"""
        os.environ["KZOCR_CACHE_TTL"] = "3600"
        cfg = Config.from_env()
        assert cfg.cache_ttl_seconds == 3600
        os.environ.pop("KZOCR_CACHE_TTL", None)

    def test_from_env_returns_expected_dataclass(self):
        """Config.from_env() 返回正确的 dataclass 实例。"""
        cfg = Config.from_env()
        assert hasattr(cfg, "kzocr_output_dir")
        assert hasattr(cfg, "cache_ttl_seconds")
        assert isinstance(cfg.kzocr_output_dir, str)
        assert isinstance(cfg.cache_ttl_seconds, int)

    def test_load_config_preserves_new_fields(self):
        """load_config() 加载后两个新字段应存在。"""
        os.environ.pop("KZOCR_OUTPUT_DIR", None)
        os.environ.pop("KZOCR_CACHE_TTL", None)
        cfg = load_config()
        assert cfg.kzocr_output_dir == "/tmp/kzocr/output"
        assert cfg.cache_ttl_seconds == 86400
