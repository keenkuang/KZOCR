"""
方剂上下文衔接模式自动发现模块

通过分析 FormulaComposition 中 context_reference_type != NULL 的记录，
自动发现高频衔接表达，生成候选 FormulaContextPattern 插入数据库待审核。

核心流程：
1. 从 FormulaComposition 查询 context_reference_type != NULL 的记录
2. 统计高频衔接表达
3. 归一化处理（替换药材名为 {HERB}，数字为 {NUM}）
4. 插入 FormulaContextPattern 表（review_status='pending'）
5. 返回发现的范式列表
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, List

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
from kzocr.tcm_ocr.database.sqlite.book_db import BookDB

logger = logging.getLogger(__name__)

# 衔接表达关键词（用于初步筛选）
REFERENCE_KEYWORDS = [
    '同前', '同上', '如前', '如上方', '上方', '前方', '加味',
    '加', '减', '去', '除', '即前方', '于上方', '续前',
    '方见', '方见前', '方见上',
]

# 模式类型映射
PATTERN_TYPE_MAP = {
    'heading_prefix': ['组成', '方药', '处方', '用药', '方剂', '药味'],
    'ingredient_list': [
        '各', '共', '等分', '等份', '适量', '少许', '微炒',
        '捣碎', '切', '片', '段', '枚', '个', '两', '钱',
    ],
    'dosage_suffix': ['g', '克', '钱', '两', '分', '斤', '毫升', 'ml'],
    'cross_reference': ['同前', '同上', '如前', '如上方', '方见'],
    'modification_note': ['加', '减', '去', '除', '即前方加', '上方加味'],
}


def normalize_context_description(desc: str) -> str:
    """
    归一化上下文描述

    将描述中的具体药材名替换为 {HERB}，剂量数字替换为 {NUM}，
    以便泛化为可复用的模式。

    替换规则：
        - 药材名 → {HERB}
        - 阿拉伯数字 + 单位 → {NUM}{UNIT}
        - 中文数字 → {NUM}
        - 连续空格/空白 → 单空格

    Args:
        desc: 原始描述文本

    Returns:
        归一化后的描述文本

    Example:
        >>> normalize_context_description("上方加甘草3g、黄芩9g")
        "上方加{HERB}{NUM}g、{HERB}{NUM}g"
    """
    if not desc:
        return ''

    normalized = desc

    # 1. 替换中文数字
    chinese_nums = '零一二三四五六七八九十百千万两半'
    normalized = re.sub(
        f'[{chinese_nums}]+',
        '{NUM}',
        normalized,
    )

    # 2. 替换阿拉伯数字（包括小数）
    normalized = re.sub(r'\d+\.?\d*', '{NUM}', normalized)

    # 3. 替换常见药材名（从内置词典）
    from kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover import COMMON_HERB_NAMES

    # 按长度降序排序，避免短名替换干扰长名
    sorted_herbs = sorted(COMMON_HERB_NAMES, key=len, reverse=True)
    for herb in sorted_herbs:
        if herb in normalized:
            normalized = normalized.replace(herb, '{HERB}')

    # 4. 替换连续空白为单空格
    normalized = re.sub(r'\s+', ' ', normalized)

    # 5. 替换剂量单位（紧跟 {NUM} 后面的单位）
    unit_pattern = r'\{NUM\}\s*(g|克|钱|两|分|斤|毫升|ml|mg|μg|片|枚|个|条|只)'
    normalized = re.sub(unit_pattern, r'{NUM}\1', normalized)

    return normalized.strip()


def infer_pattern_type(pattern_text: str) -> str:
    """
    推断模式类型

    根据模式文本的关键词推断其所属类型。

    Args:
        pattern_text: 模式文本

    Returns:
        模式类型：'heading_prefix' | 'ingredient_list' | 'dosage_suffix' |
                  'cross_reference' | 'modification_note' | 'other'
    """
    if not pattern_text:
        return 'other'

    text_lower = pattern_text.lower()

    for ptype, keywords in PATTERN_TYPE_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                return ptype

    # 特殊规则检测
    cross_ref_patterns = [
        r'同[前上]', r'如[前上方]', r'方见[上前]',
    ]
    for pat in cross_ref_patterns:
        if re.search(pat, pattern_text):
            return 'cross_reference'

    mod_patterns = [
        r'[加减去除]', r'加味', r'前方', r'上方',
    ]
    for pat in mod_patterns:
        if re.search(pat, pattern_text):
            return 'modification_note'

    return 'other'


def auto_discover_context_patterns(
    book_id: str,
    db_book: BookDB,
    db_pg: RuntimeDB,
) -> List[dict]:
    """
    自动发现方剂上下文衔接模式

    核心流程：
        1. 从 FormulaComposition 查询 context_reference_type != NULL 的记录
        2. 统计高频衔接表达
        3. 归一化处理（替换药材名为 {HERB}，数字为 {NUM}）
        4. 插入 FormulaContextPattern 表（review_status='pending'）
        5. 返回发现的范式列表

    Args:
        book_id: 书籍 ID
        db_book: BookDB 实例（SQLite 书籍库）
        db_pg: RuntimeDB 实例（PostgreSQL 运行库）

    Returns:
        发现的范式列表，每个元素为 dict::

            [
                {
                    'id': int,
                    'pattern_text': str,
                    'pattern_type': str,
                    'regex': str,
                    'example': str,
                    'discovered_count': int,
                    'review_status': 'pending',
                    # ...
                },
                ...
            ]
    """
    discovered: List[dict] = []

    try:
        # 1. 从 BookDB 查询有上下文引用的方剂记录
        with db_book.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, formula_name, context_reference_type,
                       referenced_formula_id, context_description,
                       formula_name_variants
                FROM FormulaComposition
                WHERE context_reference_type IS NOT NULL
                  AND context_description IS NOT NULL
                ORDER BY id DESC
                LIMIT 2000
                """,
            )
            formulas = [dict(row) for row in cursor.fetchall()]

        if not formulas:
            logger.info("No context-reference formulas found for book %s", book_id)
            return discovered

        logger.info(
            "Processing %d context-reference formulas for pattern discovery",
            len(formulas),
        )

        # 2. 收集所有 context_description
        descriptions: List[str] = []
        for formula in formulas:
            desc = formula.get('context_description', '')
            if desc:
                descriptions.append(str(desc))

        # 也收集 formula_name_variants 中可能的衔接表达
        for formula in formulas:
            variants = formula.get('formula_name_variants', '')
            if variants:
                try:
                    import json
                    variant_list = json.loads(variants) if isinstance(variants, str) else variants
                    if isinstance(variant_list, list):
                        for v in variant_list:
                            if v and isinstance(v, str):
                                descriptions.append(v)
                except (json.JSONDecodeError, TypeError):
                    pass

        # 3. 提取高频衔接表达
        # 先归一化，然后统计频次
        normalized_counter: Counter = Counter()
        example_map: Dict[str, str] = {}  # normalized -> original example

        for desc in descriptions:
            normalized = normalize_context_description(desc)
            if normalized and normalized != desc and '{HERB}' in normalized:
                normalized_counter[normalized] += 1
                if normalized not in example_map:
                    example_map[normalized] = desc

        # 4. 筛选高频模式（出现 2 次以上）
        min_frequency = 2
        candidate_patterns: Dict[str, dict] = {}

        for normalized_text, count in normalized_counter.most_common(200):
            if count < min_frequency:
                break

            pattern_type = infer_pattern_type(normalized_text)

            # 生成正则表达式
            regex = _normalized_to_regex(normalized_text)

            cache_key = normalized_text
            candidate_patterns[cache_key] = {
                'pattern_text': normalized_text,
                'pattern_type': pattern_type,
                'regex': regex,
                'example': example_map.get(normalized_text, normalized_text),
                'discovered_count': count,
                'source_books': [str(book_id)],
                'auto_discovered': True,
                'review_status': 'pending',
                'status': 'active',
            }

        # 5. 插入 FormulaContextPattern 表
        for pattern in candidate_patterns.values():
            try:
                pattern_id = db_pg.create_formula_context_pattern(
                    pattern_text=pattern['pattern_text'],
                    pattern_type=pattern['pattern_type'],
                    regex=pattern['regex'],
                    example=pattern['example'],
                    discovered_count=pattern['discovered_count'],
                    source_books=pattern['source_books'],
                    auto_discovered=True,
                    review_status='pending',
                )
                pattern['id'] = pattern_id
                discovered.append(pattern)
                logger.debug(
                    "Discovered context pattern: %s (type=%s, count=%d)",
                    pattern['pattern_text'],
                    pattern['pattern_type'],
                    pattern['discovered_count'],
                )
            except Exception as e:
                logger.error(
                    "Failed to insert context pattern '%s': %s",
                    pattern['pattern_text'], e,
                )

        logger.info(
            "Auto-discovered %d context patterns from book %s",
            len(discovered), book_id,
        )

    except Exception as e:
        logger.error("Error in auto_discover_context_patterns: %s", e)

    return discovered


def _normalized_to_regex(normalized: str) -> str:
    """
    将归一化文本转换为正则表达式

    将 {HERB} 替换为药材名匹配模式，{NUM} 替换为数字匹配模式。

    Args:
        normalized: 归一化文本

    Returns:
        正则表达式字符串
    """
    regex = normalized

    # 转义正则特殊字符（除占位符外）
    regex = re.escape(regex)

    # 恢复占位符为正则模式
    regex = regex.replace(r'\{HERB\}', r'[\u4e00-\u9fff]{2,6}')
    regex = regex.replace(r'\{NUM\}', r'[\d零一二三四五六七八九十百千万两半.]+')

    # 处理量词
    regex = regex.replace(r'\*', r'.*')
    regex = regex.replace(r'\+', r'.+')

    return f'^{regex}$'
