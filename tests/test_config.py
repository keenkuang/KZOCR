"""v0.6: Config 验证增强测试。"""
from __future__ import annotations

import os

import pytest
from unittest.mock import patch

from kzocr.config import Config, load_config, _safe_int


class TestSafeInt:
    def test_valid_int(self):
        assert _safe_int("18080", 1234, "TEST") == 18080

    def test_invalid_string_falls_back(self):
        assert _safe_int("not_a_number", 1234, "TEST") == 1234

    def test_empty_string_falls_back(self):
        assert _safe_int("", 5678, "TEST") == 5678

    def test_float_string_truncated(self):
        """float 字符串（"12.5"）int() 会抛 ValueError → fallback。"""
        assert _safe_int("12.5", 0, "TEST") == 0


class TestConfigDefaults:
    def test_default_vlm_port(self):
        cfg = Config()
        assert cfg.vlm_port == 18080

    def test_default_sensenova_timeout(self):
        cfg = Config()
        assert cfg.sensenova_timeout == 180

    def test_default_deepseek_rpm(self):
        cfg = Config()
        assert cfg.deepseek_rpm == 20

    def test_default_cache_ttl(self):
        cfg = Config()
        assert cfg.cache_ttl_seconds == 86400


class TestConfigFromEnv:
    def test_env_override(self):
        with patch.dict(os.environ, {"KZOCR_VLM_PORT": "19090"}, clear=False):
            cfg = Config.from_env()
            assert cfg.vlm_port == 19090

    def test_bad_env_falls_back(self):
        with patch.dict(os.environ, {"KZOCR_VLM_PORT": "not_a_port"}, clear=False):
            cfg = Config.from_env()
            assert cfg.vlm_port == 18080  # fallback to default

    def test_bad_sensenova_timeout(self):
        with patch.dict(os.environ, {"SENSENOVA_TIMEOUT": "oops"}, clear=False):
            cfg = Config.from_env()
            assert cfg.sensenova_timeout == 180

    def test_bad_deepseek_rpm(self):
        with patch.dict(os.environ, {"DEEPSEEK_RPM": "-5"}, clear=False):
            cfg = Config.from_env()
            assert cfg.deepseek_rpm == -5  # valid int, just negative

    def test_bad_cache_ttl(self):
        with patch.dict(os.environ, {"KZOCR_CACHE_TTL": "invalid"}, clear=False):
            cfg = Config.from_env()
            assert cfg.cache_ttl_seconds == 86400

    def test_all_int_fields_not_set(self):
        """未设置任何 int 环境变量时使用默认值。"""
        with patch.dict(os.environ, {}, clear=True):
            cfg = Config.from_env()
            assert cfg.vlm_port == 18080
            assert cfg.sensenova_timeout == 180
            assert cfg.deepseek_rpm == 20
            assert cfg.cache_ttl_seconds == 86400


class TestConfigOutputDir:
    def test_default_value(self):
        cfg = Config()
        assert cfg.kzocr_output_dir == ""

    def test_env_override(self):
        with patch.dict(os.environ, {"KZOCR_OUTPUT_DIR": "/custom/path"}, clear=False):
            cfg = Config.from_env()
            assert cfg.kzocr_output_dir == "/custom/path"


class TestLoadConfig:
    def test_load_config_propagates(self):
        with patch.dict(os.environ, {
            "KZOCR_USE_MOCK": "1",
            "KZOCR_CACHE_TTL": "3600",
        }, clear=False):
            cfg = load_config()
            assert cfg.use_mock is True
            assert cfg.cache_ttl_seconds == 3600

    def test_load_config_with_bad_cache_ttl(self):
        with patch.dict(os.environ, {"KZOCR_CACHE_TTL": "bad"}, clear=False):
            cfg = load_config()
            assert cfg.cache_ttl_seconds == 86400  # fallback
