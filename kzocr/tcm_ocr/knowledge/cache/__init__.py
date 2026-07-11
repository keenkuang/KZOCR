"""
TCM OCR Knowledge Cache 模块

提供三层缓存架构用于中医术语的高效查询：
- PatternCacheV2: 三层缓存架构（Layer 0 全局常驻 / Layer 1 按类型加载 / Layer 2 LRU按需）
- PatternCache: 原始LRU缓存（向后兼容）
"""

from __future__ import annotations

from kzocr.tcm_ocr.knowledge.cache.pattern_cache import PatternCache
from kzocr.tcm_ocr.knowledge.cache.pattern_cache_v2 import PatternCacheV2

__all__ = ["PatternCache", "PatternCacheV2"]
