"""引擎配置管理器 — 支持完整引擎操作字段。

数据模型（匹配 TraeDocu V3.5 设计）：
  name              : str  引擎唯一标识
  enabled           : bool 是否启用
  model_name        : str  模型名称（可选，默认同引擎名）
  base_url          : str  API base URL
  api_key_env       : str  API Key 环境变量名（可选）
  workers           : int  并发 Worker 数
  rate_limit        : int  每分钟请求数限制
  batch_size        : int  批处理大小
  adaptive          : dict 自适应调速配置 {enabled, min_workers, max_workers}
  prompt_overrides  : dict Prompt 覆盖（可选，如 {book_context: "..."}）
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

_CONFIG_DIR_ENV = "KZOCR_ENGINE_CONFIG_DIR"

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "model_name": None,
    "base_url": "",
    "api_key_env": "",
    "workers": 2,
    "rate_limit": 5,
    "batch_size": 10,
    "adaptive": {"enabled": True, "min_workers": 1, "max_workers": 6},
    "prompt_overrides": None,
}


def _cfg_dir() -> str:
    d = os.environ.get(_CONFIG_DIR_ENV, os.path.join(os.getcwd(), "engine_configs"))
    os.makedirs(d, exist_ok=True)
    return d


def _path(name: str) -> str:
    return os.path.join(_cfg_dir(), f"{name}.json")


def save_engine_config(name: str, config: dict[str, Any]) -> None:
    """保存引擎配置。"""
    merged = {**_DEFAULT_CONFIG, "name": name, **config}
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


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
    """列出所有引擎配置（含默认值补齐）。"""
    d = _cfg_dir()
    if not os.path.isdir(d):
        return []
    configs = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            cfg = load_engine_config(f[:-5])
            if cfg:
                # 补齐缺失的默认字段
                for k, v in _DEFAULT_CONFIG.items():
                    cfg.setdefault(k, v)
                configs.append(cfg)
    return configs


def delete_engine_config(name: str) -> None:
    """删除引擎配置。"""
    p = _path(name)
    if os.path.isfile(p):
        os.remove(p)
