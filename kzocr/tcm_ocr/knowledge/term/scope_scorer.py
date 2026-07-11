"""
Scope 优先级评分模块

提供术语 scope 级别的数值评分和模式排序功能。
用于 TermKB 查询结果排序和 PatternCache 的优先级管理。
"""

from __future__ import annotations

from typing import List, Optional


# Scope 优先级映射表，数值越大优先级越高
SCOPE_SCORE_MAP = {
    'book': 1000,
    'publisher': 100,
    'era': 10,
    'global': 1,
}


def effective_scope_score(scope: Optional[str]) -> int:
    """
    将 scope 字符串转换为数值优先级分数

    优先级规则：
        - book      → 1000  (最高，单本书级定制)
        - publisher → 100   (出版社级)
        - era       → 10    (时代级)
        - global    → 1     (全局默认)
        - None/其他 → 1     (按 global 处理)

    Args:
        scope: scope 级别字符串

    Returns:
        对应的数值优先级分数

    Example:
        >>> effective_scope_score('book')
        1000
        >>> effective_scope_score('publisher')
        100
        >>> effective_scope_score(None)
        1
    """
    if not scope:
        return SCOPE_SCORE_MAP['global']
    return SCOPE_SCORE_MAP.get(scope.lower(), SCOPE_SCORE_MAP['global'])


def sort_patterns_by_priority(patterns: list) -> list:
    """
    按优先级多维排序模式列表

    排序维度（降序）：
        1. effective_scope_score DESC  — scope 级别越高越靠前
        2. confidence DESC             — 置信度越高越靠前
        3. frequency DESC              — 使用频次越高越靠前

    Args:
        patterns: 模式字典列表，每个字典至少包含::

            {
                'scope': str,       # 可选，默认 'global'
                'confidence': float, # 可选，默认 0.0
                'frequency': int,    # 可选，默认 0
                # ... 其他字段
            }

    Returns:
        排序后的模式列表（新列表，原列表不被修改）

    Example:
        >>> patterns = [
        ...     {'id': 1, 'scope': 'global', 'confidence': 0.9, 'frequency': 5},
        ...     {'id': 2, 'scope': 'book', 'confidence': 0.8, 'frequency': 3},
        ...     {'id': 3, 'scope': 'publisher', 'confidence': 0.95, 'frequency': 10},
        ... ]
        >>> sorted_patterns = sort_patterns_by_priority(patterns)
        >>> [p['id'] for p in sorted_patterns]
        [2, 3, 1]
    """
    def sort_key(pattern: dict) -> tuple:
        scope = pattern.get('scope', 'global') if pattern.get('scope') else 'global'
        confidence = pattern.get('confidence', 0.0)
        frequency = pattern.get('frequency', 0)

        # 处理 confidence 可能为 None 的情况
        if confidence is None:
            confidence = 0.0
        if frequency is None:
            frequency = 0

        # 返回三元组用于排序（降序，所以取负数）
        return (
            -effective_scope_score(scope),
            -float(confidence),
            -int(frequency),
        )

    # 创建新列表进行排序，不修改原列表
    return sorted(patterns, key=sort_key)
