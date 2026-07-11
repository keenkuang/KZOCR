"""
药材别名反规范化字典 - HerbNormalizedMaps 类

提供运行时 O(1) 查询的药材别名解析能力。
在导入过程中构建，支持从别名到正名的快速查找。

内存结构说明::

    _HERB_ALIAS_MAP:       alias -> primary      通用别名映射
    _HERB_PRIMARY_SET:     set(primary names)    正名集合
    _PROCESSING_MAP:       processing -> primary  炮制变体映射
    _REGIONAL_MAP:         regional -> primary   道地/产地变体映射
    _ALIAS_MAP:            alias -> primary      通用别名映射（同 _HERB_ALIAS_MAP）
    _PRIMARY_TO_ALIASES:   primary -> set(aliases)  正名到别名集合的反向映射
    _MERIDIAN_VARIANT_MAP: variant -> standard    经络名变体映射
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)


class HerbNormalizedMaps:
    """药材别名反规范化字典 - 运行时 O(1) 查询

    提供从药材别名/炮制名/道地名到标准正名的快速解析。
    所有字典以类变量形式存储，在导入过程中一次性构建，
    后续查询为 O(1) 时间复杂度。

    Attributes:
        _HERB_ALIAS_MAP: 通用别名到正名的映射
        _HERB_PRIMARY_SET: 正名集合
        _PROCESSING_MAP: 炮制变体到正名的映射
        _REGIONAL_MAP: 道地/产地变体到正名的映射
        _ALIAS_MAP: 通用别名到正名的映射
        _PRIMARY_TO_ALIASES: 正名到别名集合的反向映射
        _MERIDIAN_VARIANT_MAP: 经络名变体到标准名的映射
    """

    _HERB_ALIAS_MAP: Dict[str, str] = {}
    _HERB_PRIMARY_SET: Set[str] = set()
    _PROCESSING_MAP: Dict[str, str] = {}
    _REGIONAL_MAP: Dict[str, str] = {}
    _ALIAS_MAP: Dict[str, str] = {}
    _PRIMARY_TO_ALIASES: Dict[str, Set[str]] = {}
    _MERIDIAN_VARIANT_MAP: Dict[str, Union[str, List[str]]] = {}

    # 炮制方法前缀（按长度降序排列，避免短前缀误匹配）
    PROCESSING_PREFIXES: List[str] = [
        '麸炒', '麸煨', '土炒', '蜜炙', '姜炙', '盐炙', '酒炙', '醋炙',
        '炒', '炙', '煅', '醋', '盐', '蜜', '酒', '炭', '焦',
        '蒸', '煮', '炖', '煨', '焙', '制', '生', '鲜',
    ]

    # 经络名标准映射
    MERIDIAN_STANDARD_MAP: Dict[str, Union[str, List[str]]] = {
        # 手三阴经
        '手太阴经': '手太阴肺经',
        '太阴经': '手太阴肺经',
        '肺经': '手太阴肺经',
        '手少阴经': '手少阴心经',
        '少阴经': '手少阴心经',
        '心经': '手少阴心经',
        '手厥阴经': '手厥阴心包经',
        '厥阴经': '手厥阴心包经',
        '心包经': '手厥阴心包经',
        # 手三阳经
        '手阳明经': '手阳明大肠经',
        '阳明经': '手阳明大肠经',
        '大肠经': '手阳明大肠经',
        '手太阳经': '手太阳小肠经',
        '太阳经': '手太阳小肠经',
        '小肠经': '手太阳小肠经',
        '手少阳经': '手少阳三焦经',
        '少阳经': '手少阳三焦经',
        '三焦经': '手少阳三焦经',
        # 足三阳经
        '足阳明经': '足阳明胃经',
        '胃经': '足阳明胃经',
        '足太阳经': '足太阳膀胱经',
        '膀胱经': '足太阳膀胱经',
        '足少阳经': '足少阳胆经',
        '胆经': '足少阳胆经',
        # 足三阴经
        '足太阴经': '足太阴脾经',
        '脾经': '足太阴脾经',
        '足少阴经': '足少阴肾经',
        '肾经': '足少阴肾经',
        '足厥阴经': '足厥阴肝经',
        '肝经': '足厥阴肝经',
        # 奇经八脉标准名
        '任脉': '任脉',
        '督脉': '督脉',
        '冲脉': '冲脉',
        '带脉': '带脉',
        '阴跷脉': '阴跷脉',
        '阳跷脉': '阳跷脉',
        '阴维脉': '阴维脉',
        '阳维脉': '阳维脉',
        # 奇经八脉别名
        '阴脉之海': '任脉',
        '阳脉之海': '督脉',
        '血海': '冲脉',
        # 集合名
        '十二经脉': '十二经脉',
        '奇经八脉': '奇经八脉',
        # 手足组合经
        '手足太阴经': ['手太阴肺经', '足太阴脾经'],
        '手足少阴经': ['手少阴心经', '足少阴肾经'],
        '手足厥阴经': ['手厥阴心包经', '足厥阴肝经'],
        '手足阳明经': ['手阳明大肠经', '足阳明胃经'],
        '手足太阳经': ['手太阳小肠经', '足太阳膀胱经'],
        '手足少阳经': ['手少阳三焦经', '足少阳胆经'],
        # 三阴三阳总称
        '手三阴经': ['手太阴肺经', '手少阴心经', '手厥阴心包经'],
        '手三阳经': ['手阳明大肠经', '手太阳小肠经', '手少阳三焦经'],
        '足三阴经': ['足太阴脾经', '足少阴肾经', '足厥阴肝经'],
        '足三阳经': ['足阳明胃经', '足太阳膀胱经', '足少阳胆经'],
    }

    @classmethod
    def build_from_db(cls, runtime_db: Any) -> None:
        """从数据库构建反规范化字典

        查询 Term 和 TermRelation 表，构建别名映射。
        要求数据库已包含导入的术语数据。

        Args:
            runtime_db: RuntimeDB 实例，用于查询数据库

        Raises:
            RuntimeError: 当数据库查询失败时
        """
        cls.clear_all()

        try:
            # 查询所有 herb 类别的术语
            with runtime_db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT t.id, t.term_text, t.safety_level, t.term_role
                    FROM Term t
                    JOIN Sublib s ON t.sublib_id = s.id
                    WHERE s.name = %s
                    """,
                    ('HERB_DICT',),
                )
                herb_records = cursor.fetchall()

                # 查询术语关系（alias/processing/regional）
                cursor.execute(
                    """
                    SELECT from_term_id, to_term_id, rel_type
                    FROM TermRelation
                    WHERE rel_type IN (%s, %s, %s)
                    """,
                    ('alias', 'processing', 'regional'),
                )
                relation_records = cursor.fetchall()

            # 构建正名集合
            primary_ids = set()
            for record in herb_records:
                if record.get('term_role') == 'primary':
                    cls._HERB_PRIMARY_SET.add(record['term_text'])
                    primary_ids.add(record['id'])

            # 构建 ID 到正名的映射
            id_to_name = {r['id']: r['term_text'] for r in herb_records}

            # 根据关系构建别名映射
            for rel in relation_records:
                from_id = rel['from_term_id']
                to_id = rel['to_term_id']
                rel_type = rel['rel_type']

                if to_id in id_to_name and from_id in id_to_name:
                    alias_name = id_to_name[from_id]
                    primary_name = id_to_name[to_id]

                    if rel_type == 'processing':
                        cls._PROCESSING_MAP[alias_name] = primary_name
                    elif rel_type == 'regional':
                        cls._REGIONAL_MAP[alias_name] = primary_name
                    elif rel_type == 'alias':
                        cls._ALIAS_MAP[alias_name] = primary_name

                    cls._HERB_ALIAS_MAP[alias_name] = primary_name

                    # 构建反向映射
                    if primary_name not in cls._PRIMARY_TO_ALIASES:
                        cls._PRIMARY_TO_ALIASES[primary_name] = set()
                    cls._PRIMARY_TO_ALIASES[primary_name].add(alias_name)

            # 构建经络名变体映射
            cls._MERIDIAN_VARIANT_MAP = dict(cls.MERIDIAN_STANDARD_MAP)

            logger.info(
                "Built HerbNormalizedMaps from DB: %d primaries, %d aliases, "
                "%d processing, %d regional, %d meridian variants",
                len(cls._HERB_PRIMARY_SET),
                len(cls._ALIAS_MAP),
                len(cls._PROCESSING_MAP),
                len(cls._REGIONAL_MAP),
                len(cls._MERIDIAN_VARIANT_MAP),
            )

        except Exception as e:
            logger.error("Failed to build HerbNormalizedMaps from DB: %s", e)
            raise RuntimeError(f"Failed to build maps from database: {e}") from e

    @classmethod
    def build_from_import(cls, import_results: dict) -> None:
        """从导入结果构建反规范化字典

        在术语导入过程中直接构建映射，无需查询数据库。

        Args:
            import_results: 导入结果字典，格式为::

                {
                    'herb_aliases': [
                        {
                            'alias': str,
                            'primary': str,
                            'alias_type': str,  # 'processing' | 'regional' | 'common'
                        },
                        ...
                    ],
                    'meridian_variants': [
                        {
                            'variant': str,
                            'standard': str,
                        },
                        ...
                    ],
                    'primary_terms': [str, ...],  # 正名列表
                }

        Example:
            >>> results = {
            ...     'herb_aliases': [
            ...         {'alias': '炒白术', 'primary': '白术', 'alias_type': 'processing'},
            ...     ],
            ...     'primary_terms': ['白术'],
            ... }
            >>> HerbNormalizedMaps.build_from_import(results)
        """
        cls.clear_all()

        if not import_results:
            return

        # 构建正名集合
        for primary in import_results.get('primary_terms', []):
            if primary:
                cls._HERB_PRIMARY_SET.add(primary)

        # 构建别名映射
        for alias_info in import_results.get('herb_aliases', []):
            alias = alias_info.get('alias', '')
            primary = alias_info.get('primary', '')
            alias_type = alias_info.get('alias_type', 'common')

            if not alias or not primary:
                continue

            cls._HERB_ALIAS_MAP[alias] = primary

            if alias_type == 'processing':
                cls._PROCESSING_MAP[alias] = primary
            elif alias_type == 'regional':
                cls._REGIONAL_MAP[alias] = primary
            else:
                cls._ALIAS_MAP[alias] = primary

            # 构建反向映射
            if primary not in cls._PRIMARY_TO_ALIASES:
                cls._PRIMARY_TO_ALIASES[primary] = set()
            cls._PRIMARY_TO_ALIASES[primary].add(alias)

        # 构建经络名变体映射
        for mv in import_results.get('meridian_variants', []):
            variant = mv.get('variant', '')
            standard = mv.get('standard', '')
            if variant and standard:
                cls._MERIDIAN_VARIANT_MAP[variant] = standard

        # 补充内置的经络名变体
        for variant, standard in cls.MERIDIAN_STANDARD_MAP.items():
            if variant not in cls._MERIDIAN_VARIANT_MAP:
                cls._MERIDIAN_VARIANT_MAP[variant] = standard

        logger.info(
            "Built HerbNormalizedMaps from import: %d primaries, %d aliases, "
            "%d processing, %d regional, %d meridian variants",
            len(cls._HERB_PRIMARY_SET),
            len(cls._ALIAS_MAP),
            len(cls._PROCESSING_MAP),
            len(cls._REGIONAL_MAP),
            len(cls._MERIDIAN_VARIANT_MAP),
        )

    @classmethod
    def resolve_alias(cls, text: str) -> Optional[str]:
        """解析药材别名为正名

        按以下顺序查找：
        1. 直接正名匹配（O(1) 集合查找）
        2. 炮制变体映射
        3. 道地/产地变体映射
        4. 通用别名映射

        Args:
            text: 待解析的别名文本

        Returns:
            正名字符串，如果无法解析则返回 None

        Example:
            >>> HerbNormalizedMaps.resolve_alias('炒白术')
            '白术'
            >>> HerbNormalizedMaps.resolve_alias('白术')
            '白术'
        """
        if not text:
            return None

        # 直接正名匹配
        if text in cls._HERB_PRIMARY_SET:
            return text

        # 按优先级查找映射
        if text in cls._PROCESSING_MAP:
            return cls._PROCESSING_MAP[text]

        if text in cls._REGIONAL_MAP:
            return cls._REGIONAL_MAP[text]

        if text in cls._ALIAS_MAP:
            return cls._ALIAS_MAP[text]

        if text in cls._HERB_ALIAS_MAP:
            return cls._HERB_ALIAS_MAP[text]

        return None

    @classmethod
    def is_primary_herb(cls, text: str) -> bool:
        """判断文本是否为药材正名

        Args:
            text: 待判断的文本

        Returns:
            True 如果文本是已注册的药材正名

        Example:
            >>> HerbNormalizedMaps.is_primary_herb('白术')
            True
            >>> HerbNormalizedMaps.is_primary_herb('炒白术')
            False
        """
        return text in cls._HERB_PRIMARY_SET if text else False

    @classmethod
    def get_aliases(cls, primary: str) -> Set[str]:
        """获取药材正名的所有别名

        Args:
            primary: 药材正名

        Returns:
            别名集合，如果正名不存在则返回空集合

        Example:
            >>> HerbNormalizedMaps.get_aliases('白术')
            {'炒白术', '麸炒白术', '土炒白术', ...}
        """
        return cls._PRIMARY_TO_ALIASES.get(primary, set()).copy()

    @classmethod
    def resolve_meridian(cls, text: str) -> Optional[Union[str, List[str]]]:
        """解析经络名变体为标准名

        Args:
            text: 经络名变体

        Returns:
            标准经络名，可能是字符串或字符串列表（如"手足太阴经"）。
            如果无法解析则返回 None。

        Example:
            >>> HerbNormalizedMaps.resolve_meridian('肺经')
            '手太阴肺经'
            >>> HerbNormalizedMaps.resolve_meridian('十二经脉')
            '十二经脉'
        """
        if not text:
            return None
        return cls._MERIDIAN_VARIANT_MAP.get(text)

    @classmethod
    def get_memory_size(cls) -> Dict[str, Any]:
        """返回各字典的内存占用估算

        Returns:
            内存占用字典，格式为::

                {
                    'herb_primary_set': int,      # 正名集合元素数
                    'alias_map_entries': int,      # 别名映射条目数
                    'processing_map_entries': int, # 炮制映射条目数
                    'regional_map_entries': int,   # 道地映射条目数
                    'primary_to_aliases_entries': int,  # 反向映射条目数
                    'meridian_variant_entries': int,    # 经络变体条目数
                    'estimated_bytes': int,        # 估算总字节数
                }
        """
        def _dict_size(d: dict) -> int:
            """估算字典内存占用"""
            total = sys.getsizeof(d)
            for k, v in d.items():
                total += sys.getsizeof(k) + sys.getsizeof(v)
                if isinstance(v, (set, list)):
                    total += sys.getsizeof(v)
                    for item in v:
                        total += sys.getsizeof(item)
            return total

        def _set_size(s: set) -> int:
            """估算集合内存占用"""
            total = sys.getsizeof(s)
            for item in s:
                total += sys.getsizeof(item)
            return total

        herb_set_size = _set_size(cls._HERB_PRIMARY_SET)
        alias_map_size = _dict_size(cls._ALIAS_MAP)
        proc_map_size = _dict_size(cls._PROCESSING_MAP)
        reg_map_size = _dict_size(cls._REGIONAL_MAP)
        pta_map_size = _dict_size(cls._PRIMARY_TO_ALIASES)
        mer_map_size = _dict_size(cls._MERIDIAN_VARIANT_MAP)

        total = (
            herb_set_size + alias_map_size + proc_map_size +
            reg_map_size + pta_map_size + mer_map_size
        )

        return {
            'herb_primary_set': len(cls._HERB_PRIMARY_SET),
            'alias_map_entries': len(cls._ALIAS_MAP),
            'processing_map_entries': len(cls._PROCESSING_MAP),
            'regional_map_entries': len(cls._REGIONAL_MAP),
            'primary_to_aliases_entries': len(cls._PRIMARY_TO_ALIASES),
            'meridian_variant_entries': len(cls._MERIDIAN_VARIANT_MAP),
            'estimated_bytes': total,
        }

    @classmethod
    def clear_all(cls) -> None:
        """清空所有字典

        在重新构建映射前调用，确保状态干净。
        """
        cls._HERB_ALIAS_MAP.clear()
        cls._HERB_PRIMARY_SET.clear()
        cls._PROCESSING_MAP.clear()
        cls._REGIONAL_MAP.clear()
        cls._ALIAS_MAP.clear()
        cls._PRIMARY_TO_ALIASES.clear()
        cls._MERIDIAN_VARIANT_MAP.clear()
        logger.debug("Cleared all HerbNormalizedMaps dictionaries")
