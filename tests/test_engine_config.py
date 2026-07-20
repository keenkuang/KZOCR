"""kzocr/engine/engine_config.py 纯逻辑单测（零网络/零引擎，仅文件 I/O）。

覆盖路径解析、默认值合并、缺失/损坏文件回退、列表补齐默认字段、删除幂等。
"""

from __future__ import annotations

from kzocr.engine.engine_config import (
    delete_engine_config,
    list_engine_configs,
    load_engine_config,
    save_engine_config,
)


def test_save_then_load_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path))
    save_engine_config("paddle", {"base_url": "http://x", "workers": 4})
    cfg = load_engine_config("paddle")
    assert cfg is not None
    assert cfg["name"] == "paddle"
    assert cfg["base_url"] == "http://x"
    assert cfg["workers"] == 4
    # 未提供字段补默认
    assert cfg["enabled"] is True
    assert cfg["adaptive"] == {"enabled": True, "min_workers": 1, "max_workers": 6}


def test_save_merges_without_clobbering_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path))
    save_engine_config("r", {"enabled": False})
    cfg = load_engine_config("r")
    assert cfg["enabled"] is False
    # 其它默认保留
    assert cfg["rate_limit"] == 5
    assert cfg["batch_size"] == 10


def test_load_missing_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path))
    assert load_engine_config("nope") is None


def test_load_corrupt_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_engine_config("bad") is None


def test_list_empty_when_dir_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path / "none"))
    assert list_engine_configs() == []


def test_list_backfills_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path))
    save_engine_config("a", {"workers": 8})
    cfgs = list_engine_configs()
    assert len(cfgs) == 1
    assert cfgs[0]["name"] == "a"
    assert cfgs[0]["batch_size"] == 10  # 默认补齐


def test_delete_existing_and_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(tmp_path))
    save_engine_config("d", {})
    delete_engine_config("d")
    assert load_engine_config("d") is None
    # 删除不存在不报错
    delete_engine_config("ghost")
