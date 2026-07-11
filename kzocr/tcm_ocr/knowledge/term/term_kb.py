"""
术语库管理层 - TermKB 类

提供中医 OCR 校对系统的核心术语管理能力，包括：
- 错误模式在上下文中的匹配与纠错
- 术语冲突检测
- 按 scope 优先级链查询术语
- 术语使用日志记录

所有数据库查询使用参数化查询防止 SQL 注入。
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB

logger = logging.getLogger(__name__)


# Scope 优先级常量，数值越大优先级越高
SCOPE_PRIORITY_BOOK = 1000
SCOPE_PRIORITY_PUBLISHER = 100
SCOPE_PRIORITY_ERA = 10
SCOPE_PRIORITY_GLOBAL = 1


def _scope_priority(scope: Optional[str]) -> int:
    """将 scope 字符串映射为优先级数值"""
    if not scope:
        return SCOPE_PRIORITY_GLOBAL
    mapping = {
        'book': SCOPE_PRIORITY_BOOK,
        'publisher': SCOPE_PRIORITY_PUBLISHER,
        'era': SCOPE_PRIORITY_ERA,
        'global': SCOPE_PRIORITY_GLOBAL,
    }
    return mapping.get(scope.lower(), SCOPE_PRIORITY_GLOBAL)


class TermKB:
    """
    术语知识库管理类

    封装术语的查询、匹配、冲突检测和使用日志记录。
    通过 RuntimeDB 连接 PostgreSQL 运行库获取术语数据。

    Attributes:
        _runtime_db: RuntimeDB 实例
    """

    def __init__(self, runtime_db: RuntimeDB) -> None:
        """
        初始化 TermKB

        Args:
            runtime_db: RuntimeDB 实例，用于连接 PostgreSQL 运行库
        """
        self._runtime_db = runtime_db

    # ------------------------------------------------------------------
    # 错误模式匹配
    # ------------------------------------------------------------------

    def match_error_pattern_in_context(self, context: str, pos: int) -> Optional[dict]:
        """
        在上下文中匹配 error_pattern，返回最可能的纠错结果

        在 position 附近搜索上下文字符串，查找已注册的 OCR 错误模式。
        优先匹配最长模式，返回置信度最高的纠错结果。

        Args:
            context: 上下文字符串
            pos: 目标字符位置（在 context 中的索引）

        Returns:
            包含纠错信息的字典，格式为::

                {
                    'term_id': int,
                    'term_text': str,
                    'error_pattern': str,
                    'corrected_text': str,
                    'confidence': float,
                    'scope': str,
                    'span': Tuple[int, int],  # 在 context 中的匹配位置
                }

            如果没有匹配到，返回 None。
        """
        if not context or pos < 0 or pos >= len(context):
            return None

        # 提取位置附近的上下文窗口（前后各 20 字符）
        window_start = max(0, pos - 20)
        window_end = min(len(context), pos + 20)
        context_window = context[window_start:window_end]

        # 获取所有活跃的、包含 error_pattern 的术语
        active_patterns = self.get_active_patterns()

        best_match: Optional[dict] = None
        best_score = 0.0

        for pattern in active_patterns:
            error_pat = pattern.get('error_pattern')
            if not error_pat:
                continue

            # 在上下文窗口中搜索 error_pattern
            for match in re.finditer(re.escape(error_pat), context_window):
                match_start = window_start + match.start()
                match_end = window_start + match.end()

                # 计算匹配分数：confidence * scope_priority * (pattern_length)
                confidence = float(pattern.get('confidence', 0.5))
                scope = pattern.get('scope', 'global')
                scope_score = _scope_priority(scope)
                pattern_len = len(error_pat)

                score = confidence * scope_score * pattern_len

                if score > best_score:
                    best_score = score
                    best_match = {
                        'term_id': pattern['id'],
                        'term_text': pattern['term_text'],
                        'error_pattern': error_pat,
                        'corrected_text': pattern['term_text'],
                        'confidence': confidence,
                        'scope': scope,
                        'span': (match_start, match_end),
                    }

        return best_match

    def find_patterns_with_error(self, error_char: str) -> list:
        """
        查找包含指定错误字符的所有模式

        搜索 Term 表中 error_pattern 包含 error_char 的所有活跃记录。

        Args:
            error_char: 错误字符（单字或多字）

        Returns:
            包含该错误字符的模式列表，每个元素为 dict::

                [
                    {
                        'id': int,
                        'term_text': str,
                        'error_pattern': str,
                        'confidence': float,
                        'scope': str,
                        'frequency': int,
                    },
                    ...
                ]
        """
        with self._runtime_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, term_text, error_pattern, confidence, scope, frequency
                FROM Term
                WHERE error_pattern ILIKE %s
                  AND status = 'active'
                ORDER BY confidence DESC, frequency DESC
                """,
                (f"%{error_char}%",),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_active_patterns(self) -> list:
        """
        获取所有活跃规则

        返回 Term 表中 status='active' 且 error_pattern IS NOT NULL 的所有记录。

        Returns:
            活跃模式列表，每个元素为 dict::

                [
                    {
                        'id': int,
                        'term_text': str,
                        'error_pattern': str,
                        'confidence': float,
                        'scope': str,
                        'frequency': int,
                        'semantic_category': str,
                    },
                    ...
                ]
        """
        with self._runtime_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, term_text, error_pattern, confidence, scope,
                       frequency, semantic_category
                FROM Term
                WHERE status = 'active'
                  AND error_pattern IS NOT NULL
                ORDER BY confidence DESC, frequency DESC
                """,
            )
            return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # 术语冲突检测
    # ------------------------------------------------------------------

    def check_term_conflict(self, text_a: str, text_b: str) -> bool:
        """
        检查两文本是否存在术语冲突

        术语冲突定义为：两文本都包含 active 术语，但这些术语在
        语义类别 (semantic_category) 上互斥（如药材名 vs 穴位名）。

        Args:
            text_a: 第一个文本
            text_b: 第二个文本

        Returns:
            True 如果存在冲突，False 否则
        """
        # 提取 text_a 中的所有术语
        terms_a = self._extract_terms_from_text(text_a)
        # 提取 text_b 中的所有术语
        terms_b = self._extract_terms_from_text(text_b)

        # 获取语义类别
        categories_a = {t.get('semantic_category') for t in terms_a if t.get('semantic_category')}
        categories_b = {t.get('semantic_category') for t in terms_b if t.get('semantic_category')}

        # 定义互斥类别对
        conflicting_pairs = {
            ('herb_name', 'acupoint_name'),
            ('herb_name', 'meridian_name'),
            ('formula_name', 'herb_name'),
            ('acupoint_name', 'formula_name'),
            ('disease_name', 'syndrome_name'),
        }

        for cat_a in categories_a:
            for cat_b in categories_b:
                if (cat_a, cat_b) in conflicting_pairs or (cat_b, cat_a) in conflicting_pairs:
                    logger.debug(
                        "Term conflict detected: %s (%s) vs %s (%s)",
                        text_a, cat_a, text_b, cat_b,
                    )
                    return True

        return False

    def _extract_terms_from_text(self, text: str) -> List[dict]:
        """
        从文本中提取所有匹配的活跃术语

        Args:
            text: 输入文本

        Returns:
            匹配到的术语列表
        """
        results: List[dict] = []
        active_patterns = self.get_active_patterns()

        for pattern in active_patterns:
            term_text = pattern.get('term_text', '')
            if not term_text:
                continue
            if term_text in text:
                results.append(pattern)

        return results

    # ------------------------------------------------------------------
    # 术语查询（scope 优先级链）
    # ------------------------------------------------------------------

    def query_term(
        self,
        term_text: str,
        scope: str = 'global',
        publisher: Optional[str] = None,
        pub_era: Optional[str] = None,
    ) -> list:
        """
        按 scope 优先级链查询术语

        查询优先级：book > publisher > era > global。
        高优先级匹配到则返回，否则降级到低优先级继续查询。

        Args:
            term_text: 要查询的术语文本
            scope: 起始 scope 级别（'book', 'publisher', 'era', 'global'）
            publisher: 出版社名称（scope >= publisher 时使用）
            pub_era: 出版时代（scope >= era 时使用）

        Returns:
            匹配到的术语列表，按优先级和置信度排序::

                [
                    {
                        'id': int,
                        'term_text': str,
                        'semantic_category': str,
                        'source_authority': str,
                        'confidence': float,
                        'scope': str,
                        'error_pattern': str,
                    },
                    ...
                ]
        """
        scope_order = ['book', 'publisher', 'era', 'global']
        start_idx = scope_order.index(scope.lower()) if scope.lower() in scope_order else 3

        results: List[dict] = []

        for s in scope_order[start_idx:]:
            query_results = self._query_term_at_scope(term_text, s, publisher, pub_era)
            if query_results:
                results.extend(query_results)
                # 如果 book/publisher 级别匹配到，直接返回不再降级
                if s in ('book', 'publisher'):
                    break

        # 按 (scope_priority DESC, confidence DESC) 排序
        results.sort(
            key=lambda x: (_scope_priority(x.get('scope')), x.get('confidence', 0)),
            reverse=True,
        )

        return results

    def _query_term_at_scope(
        self,
        term_text: str,
        scope: str,
        publisher: Optional[str],
        pub_era: Optional[str],
    ) -> List[dict]:
        """
        在指定 scope 级别查询术语

        Args:
            term_text: 术语文本
            scope: scope 级别
            publisher: 出版社
            pub_era: 出版时代

        Returns:
            该 scope 下的匹配术语列表
        """
        with self._runtime_db.get_cursor() as cursor:
            if scope == 'book':
                cursor.execute(
                    """
                    SELECT id, term_text, semantic_category, source_authority,
                           confidence, scope, error_pattern
                    FROM Term
                    WHERE term_text = %s AND scope = 'book' AND status = 'active'
                    ORDER BY confidence DESC
                    """,
                    (term_text,),
                )
            elif scope == 'publisher' and publisher:
                cursor.execute(
                    """
                    SELECT id, term_text, semantic_category, source_authority,
                           confidence, scope, error_pattern
                    FROM Term
                    WHERE term_text = %s AND scope = 'publisher'
                      AND publisher = %s AND status = 'active'
                    ORDER BY confidence DESC
                    """,
                    (term_text, publisher),
                )
            elif scope == 'era' and pub_era:
                cursor.execute(
                    """
                    SELECT id, term_text, semantic_category, source_authority,
                           confidence, scope, error_pattern
                    FROM Term
                    WHERE term_text = %s AND scope = 'era'
                      AND pub_era = %s AND status = 'active'
                    ORDER BY confidence DESC
                    """,
                    (term_text, pub_era),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, term_text, semantic_category, source_authority,
                           confidence, scope, error_pattern
                    FROM Term
                    WHERE term_text = %s AND (scope = 'global' OR scope IS NULL)
                      AND status = 'active'
                    ORDER BY confidence DESC
                    """,
                    (term_text,),
                )
            return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # 术语使用日志
    # ------------------------------------------------------------------

    def log_term_usage(
        self,
        term_id: int,
        book_id: str,
        line_id: int,
        matched_text: str,
    ) -> int:
        """
        记录术语在某行文本中的使用日志

        调用 RuntimeDB.log_term_usage() 记录使用，并增加术语频次计数。

        Args:
            term_id: 术语 ID
            book_id: 书籍 ID（字符串形式）
            line_id: 行 ID
            matched_text: 匹配到的原始文本

        Returns:
            日志记录 ID
        """
        try:
            # book_id 可能是字符串，需要转换为 int 如果数据库期望 int
            book_id_int = int(book_id) if isinstance(book_id, str) and book_id.isdigit() else book_id
            log_id = self._runtime_db.log_term_usage(term_id, book_id_int, line_id, matched_text)
            logger.debug(
                "Term %d usage logged: book=%s, line=%d, matched_text='%s'",
                term_id, book_id, line_id, matched_text,
            )
            return log_id
        except Exception as e:
            logger.error("Failed to log term usage: %s", e)
            # 失败时返回 0 但不抛异常，避免阻塞主流程
            return 0
