"""
PatternCache 内存缓存模块

提供三大范式库（HerbOCRPattern、MeridianPointOCRPattern、FormulaContextPattern）
的 LRU 内存缓存，支持：
- 预热加载（从 PostgreSQL 加载已审核数据）
- 线程安全的 CRUD 操作
- 有毒/高风险药材和关键穴位的快速查询
- 缓存失效和重建

使用 threading.Lock 保证线程安全。
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Optional, Set

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB

logger = logging.getLogger(__name__)


class PatternCache:
    """
    范式内存缓存类（LRU）

    缓存 HerbOCRPattern、MeridianPointOCRPattern、FormulaContextPattern
    三个表的热点数据，减少数据库查询压力。

    Attributes:
        _db: RuntimeDB 实例
        _maxsize: 每个子缓存的最大条目数
        _herb_cache: 中药名范式缓存 {ocr_error_pattern: pattern_dict}
        _meridian_cache: 经络穴位范式缓存 {ocr_error_pattern: pattern_dict}
        _context_cache: 上下文衔接模式缓存 {pattern_text: pattern_dict}
        _critical_herbs: 有毒/高风险药材名集合
        _critical_meridians: 关键穴位名集合
        _lock: 线程锁
        _hit_count: 缓存命中次数
        _miss_count: 缓存未命中次数
    """

    def __init__(self, runtime_db: RuntimeDB, maxsize: int = 1000) -> None:
        """
        初始化 PatternCache

        Args:
            runtime_db: RuntimeDB 实例
            maxsize: 每个子缓存的最大条目数，默认 1000
        """
        self._db = runtime_db
        self._maxsize = maxsize

        # LRU 缓存：OrderedDict，最近访问的在尾部
        self._herb_cache: OrderedDict[str, dict] = OrderedDict()
        self._meridian_cache: OrderedDict[str, dict] = OrderedDict()
        self._context_cache: OrderedDict[str, dict] = OrderedDict()

        # 关键集合
        self._critical_herbs: Set[str] = set()
        self._critical_meridians: Set[str] = set()

        # 线程安全
        self._lock = threading.Lock()

        # 统计
        self._hit_count = 0
        self._miss_count = 0

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _make_key(self, text: str) -> str:
        """
        规范化缓存键

        Args:
            text: 原始键文本

        Returns:
            规范化后的键（小写、去空白）
        """
        return text.strip().lower()

    def _touch(self, cache: OrderedDict[str, dict], key: str) -> None:
        """
        LRU 访问标记：将 key 移到尾部（最近使用）

        Args:
            cache: 目标 OrderedDict 缓存
            key: 访问的键
        """
        if key in cache:
            cache.move_to_end(key)

    def _insert(self, cache: OrderedDict[str, dict], key: str, value: dict) -> None:
        """
        插入缓存，超出容量时淘汰最久未使用的条目

        Args:
            cache: 目标 OrderedDict 缓存
            key: 键
            value: 值
        """
        if key in cache:
            cache.move_to_end(key)
            cache[key] = value
            return

        # 淘汰最久未使用的
        while len(cache) >= self._maxsize:
            oldest_key, _ = cache.popitem(last=False)
            logger.debug("Cache evicted key: %s", oldest_key)

        cache[key] = value

    def _get_cache_stats(self) -> dict:
        """
        获取缓存统计信息

        Returns:
            统计字典
        """
        total = self._hit_count + self._miss_count
        hit_rate = self._hit_count / total if total > 0 else 0.0
        return {
            'herb_cache_size': len(self._herb_cache),
            'meridian_cache_size': len(self._meridian_cache),
            'context_cache_size': len(self._context_cache),
            'critical_herbs_count': len(self._critical_herbs),
            'critical_meridians_count': len(self._critical_meridians),
            'hit_count': self._hit_count,
            'miss_count': self._miss_count,
            'hit_rate': round(hit_rate, 4),
        }

    # ------------------------------------------------------------------
    # 预热
    # ------------------------------------------------------------------

    def warm_up(self) -> None:
        """
        预热缓存

        从 PostgreSQL 的 HerbOCRPattern、MeridianPointOCRPattern、
        FormulaContextPattern 三个表中加载 review_status='approved' 的数据到缓存。
        同时加载有毒/高风险药材和关键穴位集合。
        """
        logger.info("PatternCache warming up...")

        with self._lock:
            self._herb_cache.clear()
            self._meridian_cache.clear()
            self._context_cache.clear()
            self._critical_herbs.clear()
            self._critical_meridians.clear()
            self._hit_count = 0
            self._miss_count = 0

            # 1. 加载 HerbOCRPattern
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, correct_herb, ocr_error_pattern, error_type,
                               toxicity_level, source_books, evidence_count,
                               confidence_score, review_status, is_permanent, scope,
                               hit_count, miss_count, last_triggered_at,
                               pharmacopoeia_version
                        FROM HerbOCRPattern
                        WHERE review_status = 'approved' AND status = 'active'
                        ORDER BY confidence_score DESC, evidence_count DESC
                        LIMIT %s
                        """,
                        (self._maxsize,),
                    )
                    for row in cursor.fetchall():
                        pattern = dict(row)
                        key = self._make_key(pattern['ocr_error_pattern'])
                        self._herb_cache[key] = pattern

                        # 收集有毒/高风险药材
                        toxicity = pattern.get('toxicity_level')
                        if toxicity and toxicity in ('high', 'severe', 'toxic'):
                            self._critical_herbs.add(pattern['correct_herb'])

                logger.info("Loaded %d herb patterns into cache", len(self._herb_cache))
            except Exception as e:
                logger.error("Failed to load herb patterns: %s", e)

            # 2. 加载 MeridianPointOCRPattern
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, correct_name, ocr_error_pattern, entity_type,
                               meridian_belonging, body_region, source_books,
                               evidence_count, confidence_score, review_status,
                               is_permanent, scope,
                               hit_count, miss_count, last_triggered_at
                        FROM MeridianPointOCRPattern
                        WHERE review_status = 'approved' AND status = 'active'
                        ORDER BY confidence_score DESC, evidence_count DESC
                        LIMIT %s
                        """,
                        (self._maxsize,),
                    )
                    for row in cursor.fetchall():
                        pattern = dict(row)
                        key = self._make_key(pattern['ocr_error_pattern'])
                        self._meridian_cache[key] = pattern

                        # 收集关键穴位
                        self._critical_meridians.add(pattern['correct_name'])

                logger.info("Loaded %d meridian patterns into cache", len(self._meridian_cache))
            except Exception as e:
                logger.error("Failed to load meridian patterns: %s", e)

            # 3. 加载 FormulaContextPattern
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, pattern_text, pattern_type, regex, example,
                               discovered_count, source_books, review_status,
                               is_permanent, scope
                        FROM FormulaContextPattern
                        WHERE review_status = 'approved' AND status = 'active'
                        ORDER BY discovered_count DESC
                        LIMIT %s
                        """,
                        (self._maxsize,),
                    )
                    for row in cursor.fetchall():
                        pattern = dict(row)
                        key = self._make_key(pattern['pattern_text'])
                        self._context_cache[key] = pattern

                logger.info("Loaded %d context patterns into cache", len(self._context_cache))
            except Exception as e:
                logger.error("Failed to load context patterns: %s", e)

        logger.info("PatternCache warm-up complete: %s", self._get_cache_stats())

    # ------------------------------------------------------------------
    # 公开查询接口
    # ------------------------------------------------------------------

    def get_critical_herbs(self) -> Set[str]:
        """
        获取所有有毒/高风险药材名

        Returns:
            有毒/高风险药材名集合
        """
        with self._lock:
            return set(self._critical_herbs)

    def get_critical_meridians(self) -> Set[str]:
        """
        获取所有关键穴位名

        Returns:
            关键穴位名集合
        """
        with self._lock:
            return set(self._critical_meridians)

    def get_herb_pattern(self, ocr_error_pattern: str) -> Optional[dict]:
        """
        查询中药名范式

        先从缓存查找，未命中则从数据库加载并加入缓存。

        Args:
            ocr_error_pattern: OCR 错误模式文本

        Returns:
            范式字典，未找到返回 None::

                {
                    'id': int,
                    'correct_herb': str,
                    'ocr_error_pattern': str,
                    'error_type': str,
                    'confidence_score': float,
                    'toxicity_level': str,
                    # ...
                }
        """
        key = self._make_key(ocr_error_pattern)

        with self._lock:
            if key in self._herb_cache:
                self._touch(self._herb_cache, key)
                self._hit_count += 1
                return dict(self._herb_cache[key])
            self._miss_count += 1

        # 缓存未命中，查询数据库
        try:
            patterns = self._db.find_herb_ocr_patterns(
                ocr_error_pattern=ocr_error_pattern,
                review_status='approved',
                limit=1,
            )
            if patterns:
                pattern = patterns[0]
                with self._lock:
                    self._insert(self._herb_cache, key, pattern)
                return pattern
        except Exception as e:
            logger.error("Database error querying herb pattern '%s': %s", ocr_error_pattern, e)

        return None

    def get_meridian_pattern(self, ocr_error_pattern: str) -> Optional[dict]:
        """
        查询经络穴位范式

        先从缓存查找，未命中则从数据库加载并加入缓存。

        Args:
            ocr_error_pattern: OCR 错误模式文本

        Returns:
            范式字典，未找到返回 None::

                {
                    'id': int,
                    'correct_name': str,
                    'ocr_error_pattern': str,
                    'entity_type': str,
                    'confidence_score': float,
                    'meridian_belonging': str,
                    # ...
                }
        """
        key = self._make_key(ocr_error_pattern)

        with self._lock:
            if key in self._meridian_cache:
                self._touch(self._meridian_cache, key)
                self._hit_count += 1
                return dict(self._meridian_cache[key])
            self._miss_count += 1

        # 缓存未命中，查询数据库
        try:
            with self._db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, correct_name, ocr_error_pattern, entity_type,
                           meridian_belonging, body_region, source_books,
                           evidence_count, confidence_score, review_status,
                           is_permanent, scope
                    FROM MeridianPointOCRPattern
                    WHERE ocr_error_pattern = %s
                      AND review_status = 'approved' AND status = 'active'
                    ORDER BY confidence_score DESC
                    LIMIT 1
                    """,
                    (ocr_error_pattern,),
                )
                result = cursor.fetchone()
                if result:
                    pattern = dict(result)
                    with self._lock:
                        self._insert(self._meridian_cache, key, pattern)
                    return pattern
        except Exception as e:
            logger.error("Database error querying meridian pattern '%s': %s", ocr_error_pattern, e)

        return None

    def get_context_pattern(self, pattern_text: str) -> Optional[dict]:
        """
        查询上下文衔接模式

        先从缓存查找，未命中则从数据库加载并加入缓存。

        Args:
            pattern_text: 模式文本

        Returns:
            范式字典，未找到返回 None::

                {
                    'id': int,
                    'pattern_text': str,
                    'pattern_type': str,
                    'regex': str,
                    'example': str,
                    'discovered_count': int,
                    # ...
                }
        """
        key = self._make_key(pattern_text)

        with self._lock:
            if key in self._context_cache:
                self._touch(self._context_cache, key)
                self._hit_count += 1
                return dict(self._context_cache[key])
            self._miss_count += 1

        # 缓存未命中，查询数据库
        try:
            with self._db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, pattern_text, pattern_type, regex, example,
                           discovered_count, source_books, review_status,
                           is_permanent, scope
                    FROM FormulaContextPattern
                    WHERE pattern_text = %s
                      AND review_status = 'approved' AND status = 'active'
                    LIMIT 1
                    """,
                    (pattern_text,),
                )
                result = cursor.fetchone()
                if result:
                    pattern = dict(result)
                    with self._lock:
                        self._insert(self._context_cache, key, pattern)
                    return pattern
        except Exception as e:
            logger.error("Database error querying context pattern '%s': %s", pattern_text, e)

        return None

    # ------------------------------------------------------------------
    # 修改接口
    # ------------------------------------------------------------------

    def add_pattern(self, pattern_type: str, pattern: dict) -> None:
        """
        添加新模式到缓存

        Args:
            pattern_type: 范式类型，'herb' | 'meridian' | 'context'
            pattern: 范式字典，必须包含对应缓存的键字段

        Raises:
            ValueError: pattern_type 不合法
        """
        if pattern_type == 'herb':
            ocr_error = pattern.get('ocr_error_pattern', '')
            if not ocr_error:
                logger.warning("Cannot cache herb pattern without ocr_error_pattern")
                return
            key = self._make_key(ocr_error)
            with self._lock:
                self._insert(self._herb_cache, key, pattern)
                # 更新关键药材集合
                toxicity = pattern.get('toxicity_level')
                if toxicity and toxicity in ('high', 'severe', 'toxic'):
                    self._critical_herbs.add(pattern.get('correct_herb', ''))

        elif pattern_type == 'meridian':
            ocr_error = pattern.get('ocr_error_pattern', '')
            if not ocr_error:
                logger.warning("Cannot cache meridian pattern without ocr_error_pattern")
                return
            key = self._make_key(ocr_error)
            with self._lock:
                self._insert(self._meridian_cache, key, pattern)
                self._critical_meridians.add(pattern.get('correct_name', ''))

        elif pattern_type == 'context':
            pattern_text = pattern.get('pattern_text', '')
            if not pattern_text:
                logger.warning("Cannot cache context pattern without pattern_text")
                return
            key = self._make_key(pattern_text)
            with self._lock:
                self._insert(self._context_cache, key, pattern)

        else:
            raise ValueError(f"Invalid pattern_type: {pattern_type}. Must be 'herb', 'meridian', or 'context'")

        logger.debug("Added %s pattern to cache: %s", pattern_type, pattern)

    def invalidate(self) -> None:
        """
        清空缓存

        清空所有三个子缓存和关键集合，重置统计计数器。
        通常用于知识库更新后重建缓存。
        """
        with self._lock:
            self._herb_cache.clear()
            self._meridian_cache.clear()
            self._context_cache.clear()
            self._critical_herbs.clear()
            self._critical_meridians.clear()
            self._hit_count = 0
            self._miss_count = 0

        logger.info("PatternCache invalidated (all caches cleared)")
