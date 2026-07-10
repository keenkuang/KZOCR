"""引擎配置管理器。"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

_CONFIG_DIR_ENV = "KZOCR_ENGINE_CONFIG_DIR"


def _cfg_dir() -> str:
    d = os.environ.get(_CONFIG_DIR_ENV, os.path.join(os.getcwd(), "engine_configs"))
    os.makedirs(d, exist_ok=True)
    return d


def _path(name: str) -> str:
    return os.path.join(_cfg_dir(), f"{name}.json")


def save_engine_config(name: str, config: dict[str, Any]) -> None:
    """保存引擎配置。"""
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump({"name": name, **config}, f, ensure_ascii=False, indent=2)


def load_engine_config(name: str) -> Optional[dict[str, Any]]:
    """加载引擎配置。"""
    p = _path(name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_engine_configs() -> list[dict[str, Any]]:
    """列出所有引擎配置。"""
    d = _cfg_dir()
    if not os.path.isdir(d):
        return []
    configs = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            cfg = load_engine_config(f[:-5])
            if cfg:
                configs.append(cfg)
    return configs


def delete_engine_config(name: str) -> None:
    """删除引擎配置。"""
    p = _path(name)
    if os.path.isfile(p):
        os.remove(p)
