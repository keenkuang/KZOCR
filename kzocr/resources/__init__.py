"""
kzocr.resources — 内置种子资源，启动时一次性进程内镜像。

提供 4 类中医知识文件的内存取访问，支持 KZOCR_TERM_KB_PATH 可选叠加层。

B3 安全兼容：KZOCR_TERM_KB_PATH 路径须经过 resolve() + 基目录校验。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── 资源文件列表 ──────────────────────────────────────────────────────────
_RESOURCE_FILES = {
    "variant_map": "variant_map.json",
    "confusion_set": "confusion_set.json",
    "rare_allowlist": "rare_allowlist.json",
    "toxic_herbs": "toxic_herbs.json",
}

_RESOURCE_DIR = Path(__file__).resolve().parent


# ── 加载器 ─────────────────────────────────────────────────────────────────

class ResourceStore:
    """进程内资源镜像，随包 4 个 JSON 文件 + 可选叠加载层。"""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        """加载种子资源 + 可选叠加层。幂等，只加载一次。"""
        if self._loaded:
            return

        # 1. 加载种子资源
        for name, filename in _RESOURCE_FILES.items():
            path = _RESOURCE_DIR / filename
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data[name] = json.load(f)
                logger.debug("[resources] 已加载 %s (%s)", name, path)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("[resources] 加载失败 %s: %s", path, e)
                self._data[name] = _empty_for(name)

        # 2. 加载可选的 KZOCR_TERM_KB_PATH 叠加层
        overlay_path = os.environ.get("KZOCR_TERM_KB_PATH")
        if overlay_path:
            self._load_overlay(overlay_path)

        self._loaded = True

    def _load_overlay(self, overlay_path: str) -> None:
        """加载并合入叠加层文件（JSON），路径须在受控目录下。"""
        path = Path(overlay_path).resolve()
        base = _RESOURCE_DIR.resolve()

        # 安全校验：叠加层必须在 resources 目录或其子目录下
        try:
            path.relative_to(base)
        except ValueError:
            logger.warning(
                "[resources] KZOCR_TERM_KB_PATH=%s 不在受控目录 %s 下，已忽略",
                path, base,
            )
            return

        if not path.exists():
            logger.warning("[resources] KZOCR_TERM_KB_PATH=%s 不存在，已忽略", path)
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                overlay = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("[resources] KZOCR_TERM_KB_PATH 加载失败: %s", e)
            return

        if not isinstance(overlay, dict):
            logger.warning("[resources] 叠加层须为 JSON 对象，已忽略")
            return

        for key, value in overlay.items():
            if key in self._data:
                existing = self._data[key]
                if isinstance(existing, dict) and isinstance(value, dict):
                    existing.update(value)
                elif isinstance(existing, list) and isinstance(value, list):
                    existing.extend(value)
                else:
                    self._data[key] = value
                logger.info("[resources] 叠加层合并 %s (+%d 条)", key,
                           len(value) if isinstance(value, list) else "N/A")

    # ── 访问器 ─────────────────────────────────────────────────────────

    def variant_map(self) -> Dict[str, str]:
        """繁→简 + 异体→正体 映射表。"""
        return dict(self._data.get("variant_map", {}))

    def confusion_set(self) -> List[Dict[str, Any]]:
        """中医形似混淆列表。"""
        return list(self._data.get("confusion_set", []))

    def rare_allowlist(self) -> List[Dict[str, Any]]:
        """罕见但正确的中医字/词白名单。"""
        return list(self._data.get("rare_allowlist", []))

    def toxic_herbs(self) -> List[Dict[str, Any]]:
        """毒性药材 + 用量红线。"""
        return list(self._data.get("toxic_herbs", []))


# ── 模块级单例 ────────────────────────────────────────────────────────────

_STORE = ResourceStore()


def load() -> None:
    """显式加载（幂等）。可在应用启动时调用以确保初始化顺序。"""
    _STORE.load()


def get() -> ResourceStore:
    """获取单例，自动按需加载。"""
    _STORE.load()
    return _STORE


def _empty_for(name: str) -> Any:
    """加载失败时返回的空值。"""
    if name == "variant_map":
        return {}
    return []


# ── 自动加载（模块导入时） ────────────────────────────────────────────
_STORE.load()
