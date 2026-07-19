"""Celery 配置回归测试：broker/backend 必须尊重环境变量覆盖。

docker-compose 的 worker 服务通过 CELERY_BROKER_URL / CELERY_RESULT_BACKEND
指向 redis 服务；若配置写死 localhost，compose 内部 worker 将连不上 broker。
本测试锁定环境变量覆盖行为。CI 可跑，无需 broker。
"""

from __future__ import annotations

from kzocr.tcm_ocr.celery_tasks import config as celery_config


def test_broker_default_is_localhost(monkeypatch) -> None:
    """未设环境变量时使用本地 Redis 默认值。"""
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
    # 重新加载模块以应用删除后的默认值
    import importlib

    importlib.reload(celery_config)
    assert celery_config.broker_url.startswith("redis://")
    assert celery_config.result_backend.startswith("redis://")


def test_broker_honors_env_override(monkeypatch) -> None:
    """CELERY_BROKER_URL / CELERY_RESULT_BACKEND 必须被采纳。"""
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
    import importlib

    importlib.reload(celery_config)
    assert celery_config.broker_url == "redis://redis:6379/0"
    assert celery_config.result_backend == "redis://redis:6379/1"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
