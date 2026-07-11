"""
CacheManager - 缓存管理器

统一管理 PatternCacheV2 和各组件缓存，提供统一的初始化、查询和清理接口。
集成 BookTypeDetector 实现按书籍类型的智能缓存加载策略。

使用单例模式确保全局只有一个缓存管理器实例。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
from kzocr.tcm_ocr.knowledge.cache.pattern_cache_v2 import PatternCacheV2
from kzocr.tcm_ocr.pipeline.book_type_detector import BookTypeDetector

logger = logging.getLogger(__name__)


class CacheManager:
    """
    缓存管理器 - 统一管理 PatternCache 和各组件缓存

    职责：
    1. 根据书籍类型自动检测并初始化对应的缓存策略
    2. 管理 PatternCacheV2 的生命周期
    3. 提供统一的内存统计和报告
    4. 支持缓存清理和重建
    """

    _instance: Optional['CacheManager'] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> 'CacheManager':
        """单例模式确保全局只有一个 CacheManager 实例"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, runtime_db: RuntimeDB) -> None:
        """
        初始化 CacheManager

        Args:
            runtime_db: RuntimeDB 实例
        """
        # 避免重复初始化
        if self._initialized:
            return

        self._db = runtime_db
        self._pattern_cache: Optional[PatternCacheV2] = None
        self._current_book_type: str = 'auto'
        self._lock = threading.Lock()

        # 组件缓存字典（供其他模块注册自己的缓存）
        self._component_caches: Dict[str, Any] = {}

        self._initialized = True
        logger.info("CacheManager initialized")

    def initialize_for_book(self, book_meta: dict, toc_text: str = '') -> str:
        """
        为一本新书初始化缓存系统

        自动检测书籍类型，按类型加载对应的缓存层。

        Args:
            book_meta: 书籍元数据字典，包含 'title' 等字段
            toc_text: 目录文本，用于辅助类型检测

        Returns:
            检测到的书籍类型字符串
        """
        with self._lock:
            # 1. 检测书籍类型
            detected_type = BookTypeDetector.detect(book_meta, toc_text)
            self._current_book_type = detected_type

            logger.info(
                "CacheManager initializing for book: title='%s', detected_type='%s'",
                book_meta.get('title', 'Unknown'),
                detected_type,
            )

            # 2. 获取缓存配置
            cache_config = BookTypeDetector.get_required_caches(detected_type)
            logger.info(
                "Cache config: herb=%s, acupoint=%s (common_only=%s), meridian=%s",
                cache_config['layer_1_herb'],
                cache_config['layer_1_acupoint'],
                cache_config['acupoint_common_only'],
                cache_config['layer_1_meridian'],
            )

            # 3. 创建或复用 PatternCacheV2
            if self._pattern_cache is not None:
                # 清理之前的缓存（Layer 1 和 Layer 2）
                logger.info("Clearing previous cache before re-initialization")
                self._pattern_cache.clear()

            self._pattern_cache = PatternCacheV2(self._db, book_type=detected_type)

            # 4. 按类型预热
            self._pattern_cache.warm_up(book_type=detected_type)

            # 5. 记录初始化日志
            stats = self._pattern_cache.get_memory_stats()
            logger.info(
                "Cache initialized: L0=%d terms, L1=%d herb_primaries/%d acupoints/%d meridians, "
                "L2=%d/%d items, total=%.1f KB",
                stats['layer_0']['term_count'],
                stats['layer_1']['herb_primaries'],
                stats['layer_1']['acupoint_terms'],
                stats['layer_1']['meridian_terms'],
                stats['layer_2']['cache_size'],
                stats['layer_2']['max_size'],
                stats['overall']['total_memory_kb'],
            )

            return detected_type

    def get_pattern_cache(self) -> PatternCacheV2:
        """
        获取 PatternCacheV2 实例

        Returns:
            PatternCacheV2 实例

        Raises:
            RuntimeError: 如果缓存尚未初始化
        """
        if self._pattern_cache is None:
            raise RuntimeError(
                "PatternCache not initialized. Call initialize_for_book() first."
            )
        return self._pattern_cache

    def get_memory_report(self) -> dict:
        """
        获取完整的内存使用报告

        包含 PatternCacheV2 和各注册组件的缓存统计。

        Returns:
            内存报告字典
        """
        report: Dict[str, Any] = {
            'current_book_type': self._current_book_type,
            'pattern_cache': None,
            'component_caches': {},
            'total_memory_kb': 0.0,
        }

        # PatternCacheV2 统计
        if self._pattern_cache is not None:
            cache_stats = self._pattern_cache.get_memory_stats()
            report['pattern_cache'] = cache_stats
            report['total_memory_kb'] += cache_stats['overall']['total_memory_kb']

        # 组件缓存统计
        for name, cache in self._component_caches.items():
            if hasattr(cache, 'get_memory_stats'):
                try:
                    comp_stats = cache.get_memory_stats()
                    report['component_caches'][name] = comp_stats
                    if isinstance(comp_stats, dict) and 'memory_kb' in comp_stats:
                        report['total_memory_kb'] += comp_stats['memory_kb']
                except Exception as e:
                    report['component_caches'][name] = {'error': str(e)}
            else:
                # 尝试估算简单缓存的大小
                try:
                    size = len(cache) if hasattr(cache, '__len__') else 'unknown'
                    report['component_caches'][name] = {
                        'size': size,
                        'type': type(cache).__name__,
                    }
                except Exception:
                    report['component_caches'][name] = {
                        'type': type(cache).__name__,
                    }

        return report

    def register_component_cache(self, name: str, cache: Any) -> None:
        """
        注册组件缓存，纳入统一管理

        Args:
            name: 组件名称
            cache: 缓存对象，需实现 get_memory_stats() 方法（可选）
        """
        self._component_caches[name] = cache
        logger.info("Registered component cache: '%s' (type=%s)", name, type(cache).__name__)

    def unregister_component_cache(self, name: str) -> bool:
        """
        注销组件缓存

        Args:
            name: 组件名称

        Returns:
            是否成功注销
        """
        if name in self._component_caches:
            del self._component_caches[name]
            logger.info("Unregistered component cache: '%s'", name)
            return True
        return False

    def clear_all(self) -> None:
        """
        清空所有缓存（PatternCacheV2 和所有注册的组件缓存）
        """
        logger.info("CacheManager clearing all caches...")

        with self._lock:
            # 清空 PatternCacheV2
            if self._pattern_cache is not None:
                self._pattern_cache.clear()
                self._pattern_cache = None

            # 清空组件缓存
            for name, cache in self._component_caches.items():
                if hasattr(cache, 'clear'):
                    try:
                        cache.clear()
                        logger.info("Cleared component cache: '%s'", name)
                    except Exception as e:
                        logger.error("Failed to clear component cache '%s': %s", name, e)

            self._current_book_type = 'auto'

        logger.info("All caches cleared")

    def clear_component_cache(self, name: str) -> bool:
        """
        清空指定组件缓存

        Args:
            name: 组件名称

        Returns:
            是否成功清空
        """
        if name not in self._component_caches:
            return False

        cache = self._component_caches[name]
        if hasattr(cache, 'clear'):
            try:
                cache.clear()
                logger.info("Cleared component cache: '%s'", name)
                return True
            except Exception as e:
                logger.error("Failed to clear component cache '%s': %s", name, e)
                return False
        return False

    def get_current_book_type(self) -> str:
        """
        获取当前书籍类型

        Returns:
            当前书籍类型字符串
        """
        return self._current_book_type

    def is_initialized(self) -> bool:
        """
        检查缓存是否已初始化

        Returns:
            是否已初始化
        """
        return self._pattern_cache is not None

    @classmethod
    def reset_instance(cls) -> None:
        """
        重置单例实例。
        谨慎使用，通常在测试或系统重启时调用。
        """
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.clear_all()
                cls._instance._initialized = False
            cls._instance = None
        logger.info("CacheManager singleton instance reset")
