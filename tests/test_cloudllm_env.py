"""CloudLLM 环境变量映射测试。

KZOCR 的 _map_cloudllm_env() 自动将 KZOCR_LLM_* 映射到
CloudLLMClient 使用的 GLM_* 环境变量。
"""
from __future__ import annotations

import os

from kzocr.engine.run import _map_cloudllm_env

_VARS = ("KZOCR_LLM_API_KEY", "GLM_API_KEY",
         "KZOCR_LLM_BASE_URL", "GLM_API_BASE",
         "KZOCR_LLM_MODEL", "GLM_MODEL")


def _clean_env():
    for k in _VARS:
        os.environ.pop(k, None)


def test_map_all_three():
    """KZOCR_LLM_* 三个变量都设且 GLM_* 未设 → 全部映射。"""
    _clean_env()
    os.environ["KZOCR_LLM_API_KEY"] = "kzocr-key"
    os.environ["KZOCR_LLM_BASE_URL"] = "https://kzocr.api/v1"
    os.environ["KZOCR_LLM_MODEL"] = "kzocr-model"
    _map_cloudllm_env()
    assert os.environ["GLM_API_KEY"] == "kzocr-key"
    assert os.environ["GLM_API_BASE"] == "https://kzocr.api/v1"
    assert os.environ["GLM_MODEL"] == "kzocr-model"
    _clean_env()


def test_does_not_overwrite_existing_glm():
    """GLM_* 已设 → 不覆盖。"""
    _clean_env()
    os.environ["KZOCR_LLM_API_KEY"] = "kzocr-key"
    os.environ["GLM_API_KEY"] = "existing-key"
    _map_cloudllm_env()
    assert os.environ["GLM_API_KEY"] == "existing-key"
    _clean_env()


def test_no_kzocr_vars_no_op():
    """KZOCR_LLM_* 未设 → 无副作用。"""
    _clean_env()
    _map_cloudllm_env()
    for k in _VARS:
        assert k not in os.environ
    _clean_env()


def test_partial_mapping():
    """部分 KZOCR_LLM_* 已设，对应 GLM_* 未设 → 只映射已设的。"""
    _clean_env()
    os.environ["KZOCR_LLM_API_KEY"] = "kzocr-key"
    _map_cloudllm_env()
    assert os.environ["GLM_API_KEY"] == "kzocr-key"
    assert "GLM_API_BASE" not in os.environ
    assert "GLM_MODEL" not in os.environ
    _clean_env()


def test_mixed_partial_existing():
    """部分 KZOCR_LLM_* 已设且部分 GLM_* 已设 → 只补充缺的。"""
    _clean_env()
    os.environ["KZOCR_LLM_API_KEY"] = "kzocr-key"
    os.environ["KZOCR_LLM_MODEL"] = "kzocr-model"
    os.environ["GLM_API_KEY"] = "existing-key"  # 已设，不应被覆盖
    _map_cloudllm_env()
    assert os.environ["GLM_API_KEY"] == "existing-key"  # 未覆盖
    assert os.environ["GLM_MODEL"] == "kzocr-model"     # 补充
    _clean_env()
