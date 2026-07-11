"""
PatternCacheV2 - 三层缓存架构模块

提供改进的三层缓存架构，支持按书籍类型选择性加载缓存层：
- Layer 0 (全局常驻): critical级别术语 - 所有书籍共享
- Layer 1 (按类型加载): 药材库/穴位库/经络库 - 根据书籍类型选择性加载
- Layer 2 (LRU按需): medium/low级别术语 - 运行期动态淘汰

使用 threading.Lock 保证线程安全，支持精确的内存统计。
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB

logger = logging.getLogger(__name__)


class PatternCacheV2:
    """
    三层缓存架构

    Layer 0 (全局常驻): critical级别术语 - 所有书籍共享
    Layer 1 (按类型加载): 药材库/穴位库/经络库 - 根据书籍类型选择性加载
    Layer 2 (LRU按需): medium/low级别术语 - 运行期动态淘汰
    """

    # ========================================================================
    # Layer 0: 全局常驻 - 所有书籍共享的critical级别术语
    # ========================================================================
    _CRITICAL_TERMS: dict = {}
    _CRITICAL_SET: set = set()
    _LAYER0_LOADED: bool = False

    # ========================================================================
    # Layer 1: 按书籍类型加载
    # ========================================================================
    _HERB_ALIAS_MAP: dict = {}           # alias -> primary
    _HERB_PRIMARY_SET: set = set()       # primary names
    _PROCESSING_MAP: dict = {}           # processing -> primary
    _REGIONAL_MAP: dict = {}             # regional -> primary
    _PRIMARY_TO_ALIASES: dict = {}       # primary -> set(aliases)
    _ACUPOINT_TERMS: dict = {}           # acupoint terms
    _MERIDIAN_TERMS: dict = {}           # meridian terms

    # ========================================================================
    # Layer 2: LRU按需缓存
    # ========================================================================
    _lru_cache: OrderedDict = OrderedDict()
    _lru_maxsize: int = 1000

    # ========================================================================
    # 常用穴位（内科书轻量模式）
    # ========================================================================
    COMMON_ACUPOINTS = {
        '百会穴', '关元', '气海', '足三里', '三阴交', '合谷',
        '太冲', '内关', '外关', '神门', '大椎', '命门',
        '肾俞', '脾俞', '肝俞', '心俞', '肺俞', '中脘',
        '膻中', '天枢', '血海', '阴陵泉', '阳陵泉', '曲池',
        '太阳穴', '风池', '风府', '涌泉',
    }

    # 有毒/高风险药材critical列表
    CRITICAL_HERBS = {
        '砒霜', '雄黄', '朱砂', '轻粉', '红粉', '白降丹',
        '生川乌', '生草乌', '生附子', '生半夏', '生南星',
        '生甘遂', '生狼毒', '生藤黄', '雪上一枝蒿',
        '斑蝥', '青娘虫', '红娘虫', '蟾酥', '马钱子',
        '巴豆', '巴豆霜', '牵牛子', '千金子', '千金子霜',
        '天仙子', '洋金花', '闹羊花', '麻黄根',
    }

    # 关键穴位critical列表
    CRITICAL_ACUPOINTS = {
        '人中穴', '百会穴', '涌泉穴', '关元穴', '气海穴',
        '神阙穴', '大椎穴', '命门穴', '心俞穴', '膈俞穴',
    }

    # 书籍类型到加载策略的映射
    BOOK_TYPE_LOAD_STRATEGY = {
        'formula': {
            'layer1': ['herb'],
            'acupoint_common_only': False,
            'load_meridian': False,
        },
        'acupuncture': {
            'layer1': ['acupoint', 'meridian'],
            'acupoint_common_only': False,
            'load_meridian': True,
        },
        'internal_medicine': {
            'layer1': ['herb', 'acupoint'],
            'acupoint_common_only': True,
            'load_meridian': False,
        },
        'tcm_monograph': {
            'layer1': ['herb', 'acupoint', 'meridian'],
            'acupoint_common_only': False,
            'load_meridian': True,
        },
    }

    def __init__(self, runtime_db: RuntimeDB, book_type: str = 'auto') -> None:
        """
        初始化 PatternCacheV2

        Args:
            runtime_db: RuntimeDB 实例
            book_type: 书籍类型，'auto' | 'formula' | 'acupuncture' |
                       'internal_medicine' | 'tcm_monograph'
        """
        self._db = runtime_db
        self._book_type = book_type

        # 线程安全锁
        self._lock = threading.Lock()
        self._lru_lock = threading.Lock()

        # 统计计数器
        self._hit_count_l0 = 0
        self._hit_count_l1 = 0
        self._hit_count_l2 = 0
        self._miss_count = 0

        # 当前实例的Layer 1加载状态
        self._layer1_loaded: Set[str] = set()

        logger.info("PatternCacheV2 initialized for book_type='%s'", book_type)

    def warm_up(self, book_type: str = 'auto') -> None:
        """
        预热缓存 - 按书籍类型选择性加载各层

        Args:
            book_type: 书籍类型，覆盖初始化时的设置
        """
        self._book_type = book_type
        logger.info("PatternCacheV2 warming up for book_type='%s'...", book_type)

        # Layer 0: 全局常驻（只加载一次）
        self._load_critical_terms()

        # Layer 1: 按类型加载
        strategy = self.BOOK_TYPE_LOAD_STRATEGY.get(book_type, self.BOOK_TYPE_LOAD_STRATEGY['tcm_monograph'])

        for component in strategy['layer1']:
            if component == 'herb':
                self._load_herb_maps()
            elif component == 'acupoint':
                self._load_acupoint_terms(common_only=strategy['acupoint_common_only'])
            elif component == 'meridian':
                if strategy['load_meridian']:
                    self._load_meridian_terms()

        logger.info(
            "PatternCacheV2 warm-up complete: L0=%d, L1_herb=%d, L1_acupoint=%d, L1_meridian=%d, L2=%d",
            len(self._CRITICAL_SET),
            len(self._HERB_PRIMARY_SET),
            len(self._ACUPOINT_TERMS),
            len(self._MERIDIAN_TERMS),
            len(self._lru_cache),
        )

    # ========================================================================
    # Layer 0 加载 - 全局常驻
    # ========================================================================

    def _load_critical_terms(self) -> None:
        """
        加载 Layer 0 (全局常驻) critical级别术语。
        只加载一次，所有书籍共享。
        """
        if self._LAYER0_LOADED:
            logger.debug("Layer 0 already loaded, skipping")
            return

        with self._lock:
            if self._LAYER0_LOADED:
                return

            logger.info("Loading Layer 0 (critical terms)...")

            # 加载内置critical药材和穴位
            for herb in self.CRITICAL_HERBS:
                self._CRITICAL_SET.add(herb)
                self._CRITICAL_TERMS[herb] = {
                    'type': 'herb',
                    'level': 'critical',
                    'category': 'toxic_herb',
                    'source': 'built_in',
                }

            for acupoint in self.CRITICAL_ACUPOINTS:
                self._CRITICAL_SET.add(acupoint)
                self._CRITICAL_TERMS[acupoint] = {
                    'type': 'acupoint',
                    'level': 'critical',
                    'category': 'critical_acupoint',
                    'source': 'built_in',
                }

            # 从数据库加载additional critical terms
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT term_text, semantic_category, source_authority,
                               confidence, sublib_name
                        FROM Term t
                        LEFT JOIN Sublib s ON t.sublib_id = s.id
                        WHERE t.status = 'active'
                          AND (t.semantic_category LIKE '%%critical%%'
                               OR t.semantic_category IN ('toxic_herb', 'forbidden_herb',
                                                          'critical_acupoint', 'contraindicated'))
                        ORDER BY t.confidence DESC
                        LIMIT 500
                        """,
                    )
                    for row in cursor.fetchall():
                        term_text = row['term_text']
                        self._CRITICAL_SET.add(term_text)
                        self._CRITICAL_TERMS[term_text] = {
                            'type': row.get('semantic_category', 'unknown'),
                            'level': 'critical',
                            'category': row.get('semantic_category'),
                            'source': row.get('source_authority', 'database'),
                            'sublib': row.get('sublib_name'),
                            'confidence': row.get('confidence', 1.0),
                        }
            except Exception as e:
                logger.error("Failed to load critical terms from database: %s", e)

            self._LAYER0_LOADED = True
            logger.info("Layer 0 loaded: %d critical terms", len(self._CRITICAL_SET))

    # ========================================================================
    # Layer 1 加载 - 按书籍类型
    # ========================================================================

    def _load_herb_maps(self) -> None:
        """
        加载 Layer 1 药材映射数据。
        包括别名映射、正名集合、炮制映射、地域映射。
        """
        if 'herb' in self._layer1_loaded:
            logger.debug("Herb maps already loaded, skipping")
            return

        with self._lock:
            if 'herb' in self._layer1_loaded:
                return

            logger.info("Loading Layer 1 (herb maps)...")

            # 内置常见药材别名映射
            built_in_aliases = {
                # alias -> primary
                '川军': '大黄', '生军': '大黄', '锦纹': '大黄',
                '北芪': '黄芪', '绵芪': '黄芪', '黄耆': '黄芪',
                '浙贝': '浙贝母', '象贝': '浙贝母',
                '川贝': '川贝母', '松贝': '川贝母', '青贝': '川贝母',
                '二花': '金银花', '双花': '金银花', '忍冬花': '金银花',
                '忍冬': '金银花',
                '滁菊': '菊花', '杭菊': '菊花', '亳菊': '菊花', '贡菊': '菊花',
                '白参': '人参', '红参': '人参', '生晒参': '人参', '高丽参': '人参',
                '潞党': '党参', '台党': '党参', '东党': '党参',
                '炙草': '甘草', '生草': '甘草', '皮草': '甘草', '国老': '甘草',
                '淮山': '山药', '怀山': '山药',
                '云苓': '茯苓', '白茯苓': '茯苓', '茯灵': '茯苓',
                '台乌': '乌药', '矮樟': '乌药',
                '文术': '莪术', '蓬术': '莪术',
                '白朮': '白术',
                '全当归': '当归', '秦归': '当归', '云归': '当归',
                '蝉衣': '蝉蜕', '蝉退': '蝉蜕',
                '白附片': '附子', '黑顺片': '附子', '淡附片': '附子', '炮附子': '附子',
                '江枳壳': '枳壳', '酸橙': '枳壳',
                '广木香': '木香', '云木香': '木香',
                '明天麻': '天麻', '赤箭': '天麻',
                '益智': '益智仁',
                '补骨脂': '破故纸', '故纸': '破故纸',
                '牛七': '牛膝', '怀牛膝': '牛膝',
                '丹皮': '牡丹皮', '粉丹皮': '牡丹皮',
                '萸肉': '山茱萸', '枣皮': '山茱萸',
                '元参': '玄参', '黑参': '玄参',
                '坤草': '益母草', '茺蔚': '益母草',
                '公丁香': '丁香', '母丁香': '丁香',
                '川断': '续断', '六汗': '续断',
                '二丑': '牵牛子', '黑丑': '牵牛子', '白丑': '牵牛子',
                '仙灵脾': '淫羊藿', '羊角花': '淫羊藿',
                '首乌': '何首乌', '地精': '何首乌',
                '白芍药': '白芍', '白芪': '白芍',
                '赤芍药': '赤芍',
                '北沙参': '沙参', '南沙参': '南沙参',
                '寸冬': '麦冬', '麦门冬': '麦冬',
                '门冬': '天冬', '明天冬': '天冬',
                '辽细辛': '细辛', '华细辛': '细辛',
                '茜草根': '茜草', '血见愁': '茜草',
                '地龙': '蚯蚓', '曲蟮': '蚯蚓',
                '白夕': '白芷', '香白芷': '白芷',
                '茅根': '白茅根', '白茅': '白茅根',
                '紫丹参': '丹参', '赤参': '丹参',
                '白扁豆': '扁豆', '峨眉豆': '扁豆',
                '连翘壳': '连翘',
                '苦桔梗': '桔梗', '白桔梗': '桔梗',
                '广陈皮': '陈皮', '新会皮': '陈皮',
                '鲜姜': '生姜', '老姜': '干姜',
            }

            built_in_processing = {
                # processing_method -> primary
                '炙甘草': '甘草', '蜜炙甘草': '甘草', '炒甘草': '甘草',
                '生甘草': '甘草',
                '炙黄芪': '黄芪', '蜜黄芪': '黄芪',
                '焦白术': '白术', '土炒白术': '白术', '麸炒白术': '白术',
                '炒白芍': '白芍', '酒白芍': '白芍',
                '炒当归': '当归', '酒当归': '当归',
                '制附子': '附子', '淡附片': '附子',
                '醋柴胡': '柴胡', '炒柴胡': '柴胡',
                '炒黄芩': '黄芩', '酒黄芩': '黄芩',
                '炒黄连': '黄连', '姜黄连': '黄连',
                '炒苍术': '苍术', '麸炒苍术': '苍术',
                '炒枳壳': '枳壳', '麸炒枳壳': '枳壳',
                '炒枳实': '枳实', '麸炒枳实': '枳实',
                '炒山楂': '山楂', '焦山楂': '山楂',
                '炒麦芽': '麦芽', '焦麦芽': '麦芽',
                '炒神曲': '神曲', '焦神曲': '神曲',
                '炒谷芽': '谷芽', '焦谷芽': '谷芽',
                '炒酸枣仁': '酸枣仁',
                '盐黄柏': '黄柏', '酒黄柏': '黄柏',
                '盐知母': '知母', '酒知母': '知母',
                '盐杜仲': '杜仲', '炒杜仲': '杜仲',
                '煅龙骨': '龙骨', '煅牡蛎': '牡蛎',
                '炒牛膝': '牛膝', '酒牛膝': '牛膝',
                '制香附': '香附', '醋香附': '香附',
                '制川乌': '川乌', '制草乌': '草乌',
                '制半夏': '半夏', '法半夏': '半夏', '姜半夏': '半夏', '清半夏': '半夏',
                '制南星': '天南星', '胆南星': '天南星',
                '炙麻黄': '麻黄', '蜜麻黄': '麻黄',
                '炒杏仁': '杏仁', '苦杏仁': '杏仁', '甜杏仁': '杏仁',
                '桃仁泥': '桃仁',
                '炒薏仁': '薏苡仁', '炒苡仁': '薏苡仁',
                '炒芡实': '芡实',
                '炙远志': '远志', '制远志': '远志',
                '炙百部': '百部', '蜜百部': '百部',
                '炙款冬花': '款冬花', '蜜款冬花': '款冬花',
                '炙紫菀': '紫菀', '蜜紫菀': '紫菀',
                '炙枇杷叶': '枇杷叶', '蜜枇杷叶': '枇杷叶',
                '炒莱菔子': '莱菔子',
                '炒白芥子': '白芥子',
                '炒紫苏子': '紫苏子',
                '盐菟丝子': '菟丝子',
                '盐沙苑子': '沙苑子',
                '炒决明子': '决明子',
                '炒蔓荆子': '蔓荆子',
                '炒苍耳子': '苍耳子',
                '炒五味子': '五味子', '醋五味子': '五味子',
                '炒山茱萸': '山茱萸', '酒山茱萸': '山茱萸',
                '炒鸡内金': '鸡内金', '醋鸡内金': '鸡内金',
                '炒王不留行': '王不留行',
                '炒蒲黄': '蒲黄', '炭蒲黄': '蒲黄',
            }

            # 加载内置数据
            for alias, primary in built_in_aliases.items():
                self._HERB_ALIAS_MAP[alias] = primary
                self._HERB_PRIMARY_SET.add(primary)
                if primary not in self._PRIMARY_TO_ALIASES:
                    self._PRIMARY_TO_ALIASES[primary] = set()
                self._PRIMARY_TO_ALIASES[primary].add(alias)

            for processing, primary in built_in_processing.items():
                self._PROCESSING_MAP[processing] = primary
                self._HERB_PRIMARY_SET.add(primary)

            # 从数据库加载药材数据
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT correct_herb, ocr_error_pattern, error_type,
                               toxicity_level, confidence_score
                        FROM HerbOCRPattern
                        WHERE review_status = 'approved' AND status = 'active'
                        ORDER BY confidence_score DESC
                        LIMIT 2000
                        """,
                    )
                    for row in cursor.fetchall():
                        correct = row['correct_herb']
                        error_pattern = row['ocr_error_pattern']
                        if correct and error_pattern and correct != error_pattern:
                            self._HERB_ALIAS_MAP[error_pattern] = correct
                            self._HERB_PRIMARY_SET.add(correct)
                            if correct not in self._PRIMARY_TO_ALIASES:
                                self._PRIMARY_TO_ALIASES[correct] = set()
                            self._PRIMARY_TO_ALIASES[correct].add(error_pattern)
            except Exception as e:
                logger.error("Failed to load herb OCR patterns: %s", e)

            # 从数据库加载药典标准药材名
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT DISTINCT herb_name
                        FROM HerbDosageStandard
                        ORDER BY herb_name
                        LIMIT 1000
                        """,
                    )
                    for row in cursor.fetchall():
                        self._HERB_PRIMARY_SET.add(row['herb_name'])
            except Exception as e:
                logger.error("Failed to load herb dosage standards: %s", e)

            self._layer1_loaded.add('herb')
            logger.info(
                "Layer 1 (herb) loaded: %d aliases, %d primaries, %d processing maps",
                len(self._HERB_ALIAS_MAP),
                len(self._HERB_PRIMARY_SET),
                len(self._PROCESSING_MAP),
            )

    def _load_acupoint_terms(self, common_only: bool = False) -> None:
        """
        加载 Layer 1 穴位术语。

        Args:
            common_only: 如果为 True，只加载常用穴位（内科书轻量模式）
        """
        if 'acupoint' in self._layer1_loaded:
            logger.debug("Acupoint terms already loaded, skipping")
            return

        with self._lock:
            if 'acupoint' in self._layer1_loaded:
                return

            logger.info("Loading Layer 1 (acupoint terms, common_only=%s)...", common_only)

            # 加载常用穴位
            acupoints_to_load = self.COMMON_ACUPOINTS if common_only else set()

            # 将常用穴位加入缓存
            for acupoint in acupoints_to_load:
                self._ACUPOINT_TERMS[acupoint] = {
                    'name': acupoint,
                    'type': 'acupoint',
                    'source': 'common_set',
                }

            if not common_only:
                # 从数据库加载所有穴位
                try:
                    with self._db.get_cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT DISTINCT correct_name, entity_type,
                                   meridian_belonging, body_region
                            FROM MeridianPointOCRPattern
                            WHERE review_status = 'approved' AND status = 'active'
                              AND entity_type = 'acupoint'
                            ORDER BY correct_name
                            LIMIT 2000
                            """,
                        )
                        for row in cursor.fetchall():
                            name = row['correct_name']
                            self._ACUPOINT_TERMS[name] = {
                                'name': name,
                                'type': row.get('entity_type', 'acupoint'),
                                'meridian': row.get('meridian_belonging'),
                                'region': row.get('body_region'),
                                'source': 'database',
                            }
                except Exception as e:
                    logger.error("Failed to load acupoint terms from database: %s", e)

            self._layer1_loaded.add('acupoint')
            logger.info("Layer 1 (acupoint) loaded: %d terms", len(self._ACUPOINT_TERMS))

    def _load_meridian_terms(self) -> None:
        """
        加载 Layer 1 经络术语。
        """
        if 'meridian' in self._layer1_loaded:
            logger.debug("Meridian terms already loaded, skipping")
            return

        with self._lock:
            if 'meridian' in self._layer1_loaded:
                return

            logger.info("Loading Layer 1 (meridian terms)...")

            # 内置十二正经和奇经八脉
            meridians = {
                '手太阴肺经', '手阳明大肠经', '足阳明胃经', '足太阴脾经',
                '手少阴心经', '手太阳小肠经', '足太阳膀胱经', '足少阴肾经',
                '手厥阴心包经', '手少阳三焦经', '足少阳胆经', '足厥阴肝经',
                '督脉', '任脉', '冲脉', '带脉',
                '阴跷脉', '阳跷脉', '阴维脉', '阳维脉',
            }

            for meridian in meridians:
                self._MERIDIAN_TERMS[meridian] = {
                    'name': meridian,
                    'type': 'meridian',
                    'source': 'built_in',
                }

            # 从数据库加载经络穴位数据
            try:
                with self._db.get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT DISTINCT correct_name, entity_type,
                               meridian_belonging, body_region
                        FROM MeridianPointOCRPattern
                        WHERE review_status = 'approved' AND status = 'active'
                          AND entity_type = 'meridian'
                        ORDER BY correct_name
                        LIMIT 500
                        """,
                    )
                    for row in cursor.fetchall():
                        name = row['correct_name']
                        self._MERIDIAN_TERMS[name] = {
                            'name': name,
                            'type': row.get('entity_type', 'meridian'),
                            'meridian': row.get('meridian_belonging'),
                            'region': row.get('body_region'),
                            'source': 'database',
                        }
            except Exception as e:
                logger.error("Failed to load meridian terms from database: %s", e)

            self._layer1_loaded.add('meridian')
            logger.info("Layer 1 (meridian) loaded: %d terms", len(self._MERIDIAN_TERMS))

    # ========================================================================
    # 查询接口
    # ========================================================================

    def resolve_herb_alias(self, text: str) -> Optional[str]:
        """
        解析药材别名 -> 正名

        查询顺序: Layer 1 别名映射 -> Layer 1 炮制映射 ->
                  Layer 1 地域映射 -> Layer 0 critical ->
                  Layer 2 LRU -> 数据库

        Args:
            text: 待解析的文本

        Returns:
            正名字符串，无法解析返回 None
        """
        if not text:
            return None

        text = text.strip()

        # Layer 1: 别名映射
        if text in self._HERB_ALIAS_MAP:
            self._hit_count_l1 += 1
            return self._HERB_ALIAS_MAP[text]

        # Layer 1: 炮制映射
        if text in self._PROCESSING_MAP:
            self._hit_count_l1 += 1
            return self._PROCESSING_MAP[text]

        # Layer 1: 地域映射
        if text in self._REGIONAL_MAP:
            self._hit_count_l1 += 1
            return self._REGIONAL_MAP[text]

        # Layer 0: critical检查（本身就是正名）
        if text in self._CRITICAL_SET and text in self._HERB_PRIMARY_SET:
            self._hit_count_l0 += 1
            return text

        # Layer 1: 正名直接命中
        if text in self._HERB_PRIMARY_SET:
            self._hit_count_l1 += 1
            return text

        # Layer 2: LRU缓存
        cached = self._lru_get(text)
        if cached and cached.get('resolved'):
            self._hit_count_l2 += 1
            return cached['resolved']

        self._miss_count += 1
        return None

    def is_primary_herb(self, text: str) -> bool:
        """
        判断文本是否为药材正名。

        Args:
            text: 待检查的文本

        Returns:
            是否为药材正名
        """
        if not text:
            return False

        text = text.strip()

        # Layer 1 正名集合
        if text in self._HERB_PRIMARY_SET:
            return True

        # Layer 0 critical药材
        if text in self.CRITICAL_HERBS:
            return True

        return False

    def is_critical_field(self, text: str) -> bool:
        """
        判断文本是否为 critical 级别术语。

        Args:
            text: 待检查的文本

        Returns:
            是否为 critical 级别术语
        """
        if not text:
            return False

        return text.strip() in self._CRITICAL_SET

    def get_term(self, text: str) -> Optional[dict]:
        """
        三层查询术语 - 按优先级逐层查找。

        查询顺序:
        1. Layer 0 (全局常驻): critical级别术语
        2. Layer 1 (按类型加载): 药材/穴位/经络库
        3. Layer 2 (LRU按需): 运行期动态缓存

        Args:
            text: 待查询的文本

        Returns:
            术语字典，未找到返回 None
        """
        if not text:
            return None

        text = text.strip()

        # Layer 0: critical术语
        if text in self._CRITICAL_TERMS:
            self._hit_count_l0 += 1
            return dict(self._CRITICAL_TERMS[text])

        # Layer 1: 药材别名解析
        resolved = self.resolve_herb_alias(text)
        if resolved:
            result = {
                'term': text,
                'resolved': resolved,
                'type': 'herb',
                'layer': 1,
            }
            # 附加别名信息
            if resolved in self._PRIMARY_TO_ALIASES:
                result['aliases'] = list(self._PRIMARY_TO_ALIASES[resolved])
            return result

        # Layer 1: 穴位术语
        if text in self._ACUPOINT_TERMS:
            self._hit_count_l1 += 1
            return dict(self._ACUPOINT_TERMS[text])

        # Layer 1: 经络术语
        if text in self._MERIDIAN_TERMS:
            self._hit_count_l1 += 1
            return dict(self._MERIDIAN_TERMS[text])

        # Layer 1: 正名命中
        if text in self._HERB_PRIMARY_SET:
            self._hit_count_l1 += 1
            return {
                'term': text,
                'type': 'herb',
                'is_primary': True,
                'layer': 1,
            }

        # Layer 2: LRU缓存
        cached = self._lru_get(text)
        if cached:
            self._hit_count_l2 += 1
            return cached

        self._miss_count += 1
        return None

    def match_terms_in_text(self, text: str) -> list:
        """
        在文本中匹配所有已知术语。

        按 Layer 0 -> Layer 1 的顺序匹配，返回命中的术语列表。

        Args:
            text: 待匹配的文本

        Returns:
            命中的术语列表，每项包含 term 和匹配信息
        """
        if not text:
            return []

        results = []
        matched_positions = set()

        # Layer 0: 匹配 critical 术语（最长匹配优先）
        critical_terms = sorted(self._CRITICAL_SET, key=len, reverse=True)
        for term in critical_terms:
            if term in text:
                # 检查是否有重叠
                start = text.index(term)
                end = start + len(term)
                overlap = False
                for pos in range(start, end):
                    if pos in matched_positions:
                        overlap = True
                        break
                if not overlap:
                    results.append({
                        'term': term,
                        'start': start,
                        'end': end,
                        'layer': 0,
                        'info': self._CRITICAL_TERMS.get(term, {}),
                    })
                    for pos in range(start, end):
                        matched_positions.add(pos)

        # Layer 1: 匹配药材正名（最长匹配优先）
        primary_herbs = sorted(self._HERB_PRIMARY_SET, key=len, reverse=True)
        for term in primary_herbs:
            if term in text:
                start = text.index(term)
                end = start + len(term)
                overlap = False
                for pos in range(start, end):
                    if pos in matched_positions:
                        overlap = True
                        break
                if not overlap:
                    results.append({
                        'term': term,
                        'start': start,
                        'end': end,
                        'layer': 1,
                        'type': 'herb_primary',
                    })
                    for pos in range(start, end):
                        matched_positions.add(pos)

        # Layer 1: 匹配别名
        for alias, primary in sorted(self._HERB_ALIAS_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            if alias in text:
                start = text.index(alias)
                end = start + len(alias)
                overlap = False
                for pos in range(start, end):
                    if pos in matched_positions:
                        overlap = True
                        break
                if not overlap:
                    results.append({
                        'term': alias,
                        'start': start,
                        'end': end,
                        'layer': 1,
                        'type': 'herb_alias',
                        'resolved': primary,
                    })
                    for pos in range(start, end):
                        matched_positions.add(pos)

        # Layer 1: 匹配穴位
        for term in sorted(self._ACUPOINT_TERMS.keys(), key=len, reverse=True):
            if term in text:
                start = text.index(term)
                end = start + len(term)
                overlap = False
                for pos in range(start, end):
                    if pos in matched_positions:
                        overlap = True
                        break
                if not overlap:
                    results.append({
                        'term': term,
                        'start': start,
                        'end': end,
                        'layer': 1,
                        'type': 'acupoint',
                        'info': self._ACUPOINT_TERMS[term],
                    })
                    for pos in range(start, end):
                        matched_positions.add(pos)

        # 按位置排序
        results.sort(key=lambda x: x['start'])
        return results

    # ========================================================================
    # LRU 操作
    # ========================================================================

    def _lru_get(self, key: str) -> Optional[Any]:
        """
        从 LRU 缓存中获取值。

        Args:
            key: 缓存键

        Returns:
            缓存值，未命中返回 None
        """
        with self._lru_lock:
            if key in self._lru_cache:
                self._lru_cache.move_to_end(key)
                return dict(self._lru_cache[key]) if isinstance(self._lru_cache[key], dict) else self._lru_cache[key]
        return None

    def _lru_set(self, key: str, value: Any) -> None:
        """
        设置 LRU 缓存值，超出容量时淘汰最久未使用的条目。

        Args:
            key: 缓存键
            value: 缓存值
        """
        with self._lru_lock:
            if key in self._lru_cache:
                self._lru_cache.move_to_end(key)
                self._lru_cache[key] = value
                return

            # 淘汰最久未使用的
            while len(self._lru_cache) >= self._lru_maxsize:
                oldest_key, _ = self._lru_cache.popitem(last=False)
                logger.debug("LRU cache evicted key: %s", oldest_key)

            self._lru_cache[key] = value

    # ========================================================================
    # 内存统计
    # ========================================================================

    def get_memory_stats(self) -> dict:
        """
        获取三层缓存的详细内存统计。

        Returns:
            包含各层大小、命中率、内存估算等信息的字典
        """
        # 估算各层内存占用（字节）
        l0_size = self._estimate_dict_size(self._CRITICAL_TERMS)
        l0_size += self._estimate_set_size(self._CRITICAL_SET)

        l1_herb_size = self._estimate_dict_size(self._HERB_ALIAS_MAP)
        l1_herb_size += self._estimate_set_size(self._HERB_PRIMARY_SET)
        l1_herb_size += self._estimate_dict_size(self._PROCESSING_MAP)
        l1_herb_size += self._estimate_dict_size(self._REGIONAL_MAP)
        l1_herb_size += self._estimate_dict_size(self._PRIMARY_TO_ALIASES, nested_set=True)
        l1_acupoint_size = self._estimate_dict_size(self._ACUPOINT_TERMS)
        l1_meridian_size = self._estimate_dict_size(self._MERIDIAN_TERMS)

        l2_size = self._estimate_dict_size(self._lru_cache)

        total_hits = self._hit_count_l0 + self._hit_count_l1 + self._hit_count_l2
        total_access = total_hits + self._miss_count

        return {
            'layer_0': {
                'term_count': len(self._CRITICAL_SET),
                'memory_bytes': l0_size,
                'memory_kb': round(l0_size / 1024, 2),
                'hit_count': self._hit_count_l0,
            },
            'layer_1': {
                'herb_aliases': len(self._HERB_ALIAS_MAP),
                'herb_primaries': len(self._HERB_PRIMARY_SET),
                'processing_maps': len(self._PROCESSING_MAP),
                'acupoint_terms': len(self._ACUPOINT_TERMS),
                'meridian_terms': len(self._MERIDIAN_TERMS),
                'memory_herb_bytes': l1_herb_size,
                'memory_acupoint_bytes': l1_acupoint_size,
                'memory_meridian_bytes': l1_meridian_size,
                'memory_total_kb': round((l1_herb_size + l1_acupoint_size + l1_meridian_size) / 1024, 2),
                'loaded_components': list(self._layer1_loaded),
                'hit_count': self._hit_count_l1,
            },
            'layer_2': {
                'cache_size': len(self._lru_cache),
                'max_size': self._lru_maxsize,
                'utilization_rate': round(len(self._lru_cache) / self._lru_maxsize * 100, 2) if self._lru_maxsize > 0 else 0,
                'memory_bytes': l2_size,
                'memory_kb': round(l2_size / 1024, 2),
                'hit_count': self._hit_count_l2,
            },
            'overall': {
                'total_hit_count': total_hits,
                'total_miss_count': self._miss_count,
                'hit_rate': round(total_hits / total_access, 4) if total_access > 0 else 0.0,
                'total_memory_kb': round((l0_size + l1_herb_size + l1_acupoint_size + l1_meridian_size + l2_size) / 1024, 2),
                'book_type': self._book_type,
            },
        }

    @staticmethod
    def _estimate_dict_size(d: dict, nested_set: bool = False) -> int:
        """
        估算字典的内存占用。

        Args:
            d: 字典
            nested_set: 值是否包含集合

        Returns:
            估算的字节数
        """
        size = sys.getsizeof(d)
        for k, v in d.items():
            size += sys.getsizeof(k)
            if nested_set and isinstance(v, set):
                size += sys.getsizeof(v)
                for item in v:
                    size += sys.getsizeof(item)
            else:
                size += sys.getsizeof(v)
        return size

    @staticmethod
    def _estimate_set_size(s: set) -> int:
        """
        估算集合的内存占用。

        Args:
            s: 集合

        Returns:
            估算的字节数
        """
        size = sys.getsizeof(s)
        for item in s:
            size += sys.getsizeof(item)
        return size

    def clear(self) -> None:
        """
        清空当前实例的所有缓存层。
        注意：Layer 0 全局数据不清空（跨书籍共享）。
        """
        with self._lock:
            self._layer1_loaded.clear()

        with self._lru_lock:
            self._lru_cache.clear()

        self._hit_count_l0 = 0
        self._hit_count_l1 = 0
        self._hit_count_l2 = 0
        self._miss_count = 0

        logger.info("PatternCacheV2 cleared (Layer 1 and Layer 2)")

    def clear_all_layers(self) -> None:
        """
        清空所有三层缓存（包括全局 Layer 0）。
        谨慎使用，通常在系统重启或知识库重大更新时调用。
        """
        with self._lock:
            self._CRITICAL_TERMS.clear()
            self._CRITICAL_SET.clear()
            self._LAYER0_LOADED = False

            self._HERB_ALIAS_MAP.clear()
            self._HERB_PRIMARY_SET.clear()
            self._PROCESSING_MAP.clear()
            self._REGIONAL_MAP.clear()
            self._PRIMARY_TO_ALIASES.clear()
            self._ACUPOINT_TERMS.clear()
            self._MERIDIAN_TERMS.clear()
            self._layer1_loaded.clear()

        with self._lru_lock:
            self._lru_cache.clear()

        self._hit_count_l0 = 0
        self._hit_count_l1 = 0
        self._hit_count_l2 = 0
        self._miss_count = 0

        logger.info("PatternCacheV2 all layers cleared completely")
