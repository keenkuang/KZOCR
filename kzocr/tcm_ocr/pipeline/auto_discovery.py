"""
自动发现模块。

对已完成校对的书籍运行三种自动发现模式：
1. HerbOCRPattern — 药材 OCR 模式发现
2. MeridianPointOCRPattern — 经络穴位模式发现
3. FormulaContextPattern — 方剂上下文模式发现

每种模式独立运行，失败不影响其他模式。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 自动发现主函数
# =============================================================================


def _run_auto_discovery(
    book_id: str,
    db_book: object,
    db_pg: object,
) -> None:
    """对指定书籍运行全部自动发现流程。

    依次调用三种自动发现模式，每个模式独立 try/except，
    单个模式失败不影响其他模式执行。

    Args:
        book_id: 书籍唯一标识
        db_book: SQLite 书籍数据库连接
        db_pg: PostgreSQL 连接（用于写入发现的模式）

    Side Effects:
        将发现的模式写入 db_pg 中的相应表
    """
    logger.info("[%s] 开始自动发现流程", book_id)

    # 1. 药材 OCR 模式发现
    try:
        auto_discover_herb_ocr_patterns(book_id, db_book, db_pg)
        logger.info("[%s] HerbOCRPattern 自动发现完成", book_id)
    except Exception as e:
        logger.error("[%s] HerbOCRPattern 自动发现失败: %s", book_id, e, exc_info=True)

    # 2. 经络穴位模式发现
    try:
        auto_discover_meridian_patterns(book_id, db_book, db_pg)
        logger.info("[%s] MeridianPointOCRPattern 自动发现完成", book_id)
    except Exception as e:
        logger.error("[%s] MeridianPointOCRPattern 自动发现失败: %s", book_id, e, exc_info=True)

    # 3. 方剂上下文模式发现
    try:
        auto_discover_context_patterns(book_id, db_book, db_pg)
        logger.info("[%s] FormulaContextPattern 自动发现完成", book_id)
    except Exception as e:
        logger.error("[%s] FormulaContextPattern 自动发现失败: %s", book_id, e, exc_info=True)

    logger.info("[%s] 自动发现流程全部结束", book_id)


# =============================================================================
# HerbOCRPattern — 药材 OCR 模式发现
# =============================================================================


def auto_discover_herb_ocr_patterns(
    book_id: str,
    db_book: object,
    db_pg: object,
) -> None:
    """自动发现药材 OCR 错误模式。

    分析校对记录中涉及药材名称的 OCR 错误，发现常见错误模式：
    - 形近字误识（如 "芪" → "茋"）
    - 音近字误识（如 "参" → "叁"）
    - 药材别名误识
    - 剂量单位误识

    Args:
        book_id: 书籍 ID
        db_book: SQLite 书籍数据库连接
        db_pg: PostgreSQL 连接
    """
    logger.info("[%s] 开始药材 OCR 模式发现", book_id)

    # 查询所有涉及药材的校对记录
    cursor = db_book.execute(
        """
        SELECT pr.original_text, pr.corrected_text, pr.confidence,
               pr.engine_results, pr.dispute_reason, pr.page_number
        FROM proofread_record pr
        WHERE pr.book_id = ? AND pr.corrected_text LIKE '%' || (
            SELECT herb_name FROM herb_reference LIMIT 1
        ) || '%'
        UNION
        SELECT pr.original_text, pr.corrected_text, pr.confidence,
               pr.engine_results, pr.dispute_reason, pr.page_number
        FROM proofread_record pr
        WHERE pr.book_id = ?
          AND pr.original_text != pr.corrected_text
          AND length(pr.original_text) > 1
        ORDER BY pr.page_number
        """,
        (book_id, book_id),
    )
    rows = cursor.fetchall()

    patterns: list = []
    for row in rows:
        original = row["original_text"] if "original_text" in row.keys() else row[0]
        corrected = row["corrected_text"] if "corrected_text" in row.keys() else row[1]

        if original == corrected:
            continue

        # 逐字符比较，找出差异模式
        diff_pattern = _extract_char_diff_pattern(original, corrected)
        if diff_pattern:
            patterns.append({
                "book_id": book_id,
                "pattern_type": "herb_ocr",
                "original_pattern": diff_pattern["from"],
                "corrected_pattern": diff_pattern["to"],
                "context_original": original,
                "context_corrected": corrected,
                "frequency": 1,
            })

    # 合并相同模式并统计频率
    merged_patterns = _merge_similar_patterns(patterns)

    # 写入 PostgreSQL
    _write_patterns_to_pg(merged_patterns, "herb_ocr_pattern", db_pg)
    logger.info("[%s] 发现 %d 个药材 OCR 模式", book_id, len(merged_patterns))


# =============================================================================
# MeridianPointOCRPattern — 经络穴位模式发现
# =============================================================================


def auto_discover_meridian_patterns(
    book_id: str,
    db_book: object,
    db_pg: object,
) -> None:
    """自动发现经络穴位 OCR 模式。

    分析校对记录中涉及穴位名的 OCR 错误，发现：
    - 穴位名形近误识
    - 经络名关联误识
    - 穴位编号误识

    Args:
        book_id: 书籍 ID
        db_book: SQLite 书籍数据库连接
        db_pg: PostgreSQL 连接
    """
    logger.info("[%s] 开始经络穴位模式发现", book_id)

    cursor = db_book.execute(
        """
        SELECT pr.original_text, pr.corrected_text, pr.confidence,
               pr.dispute_reason, pr.page_number
        FROM proofread_record pr
        WHERE pr.book_id = ?
          AND pr.original_text != pr.corrected_text
          AND (
              pr.corrected_text GLOB '*穴*'
              OR pr.corrected_text GLOB '*经*'
              OR pr.corrected_text GLOB '*俞*'
              OR pr.corrected_text GLOB '*募*'
              OR pr.corrected_text GLOB '*郄*'
              OR pr.corrected_text GLOB '*合*'
              OR pr.corrected_text GLOB '*荥*'
              OR pr.corrected_text GLOB '*输*'
          )
        ORDER BY pr.page_number
        """,
        (book_id,),
    )
    rows = cursor.fetchall()

    patterns: list = []
    for row in rows:
        original = row["original_text"] if "original_text" in row.keys() else row[0]
        corrected = row["corrected_text"] if "corrected_text" in row.keys() else row[1]

        if original == corrected:
            continue

        diff_pattern = _extract_char_diff_pattern(original, corrected)
        if diff_pattern:
            patterns.append({
                "book_id": book_id,
                "pattern_type": "meridian_point",
                "original_pattern": diff_pattern["from"],
                "corrected_pattern": diff_pattern["to"],
                "context_original": original,
                "context_corrected": corrected,
                "frequency": 1,
            })

    merged_patterns = _merge_similar_patterns(patterns)
    _write_patterns_to_pg(merged_patterns, "meridian_point_pattern", db_pg)
    logger.info("[%s] 发现 %d 个经络穴位模式", book_id, len(merged_patterns))


# =============================================================================
# FormulaContextPattern — 方剂上下文模式发现
# =============================================================================


def auto_discover_context_patterns(
    book_id: str,
    db_book: object,
    db_pg: object,
) -> None:
    """自动发现方剂上下文 OCR 模式。

    分析方剂组成提取过程中发现的 OCR 错误模式：
    - "各 X 钱" 计量模式误识
    - "水煎服" 等用法模式误识
    - 药材间连接词误识

    Args:
        book_id: 书籍 ID
        db_book: SQLite 书籍数据库连接
        db_pg: PostgreSQL 连接
    """
    logger.info("[%s] 开始方剂上下文模式发现", book_id)

    # 查询方剂组成相关记录
    cursor = db_book.execute(
        """
        SELECT pr.original_text, pr.corrected_text, pr.confidence,
               pr.dispute_reason, pr.page_number
        FROM proofread_record pr
        WHERE pr.book_id = ?
          AND pr.original_text != pr.corrected_text
          AND (
              pr.corrected_text LIKE '%钱%'
              OR pr.corrected_text LIKE '%两%'
              OR pr.corrected_text LIKE '%克%'
              OR pr.corrected_text LIKE '%分%'
              OR pr.corrected_text LIKE '%服%'
              OR pr.corrected_text LIKE '%煎%'
              OR pr.corrected_text LIKE '%汤%'
              OR pr.corrected_text LIKE '%各%'
          )
        ORDER BY pr.page_number
        """,
        (book_id,),
    )
    rows = cursor.fetchall()

    patterns: list = []
    for row in rows:
        original = row["original_text"] if "original_text" in row.keys() else row[0]
        corrected = row["corrected_text"] if "corrected_text" in row.keys() else row[1]

        if original == corrected:
            continue

        # 方剂上下文特殊模式检测
        ctx_pattern = _extract_formula_context_pattern(original, corrected)
        if ctx_pattern:
            patterns.append({
                "book_id": book_id,
                "pattern_type": "formula_context",
                "original_pattern": ctx_pattern["from"],
                "corrected_pattern": ctx_pattern["to"],
                "context_original": original,
                "context_corrected": corrected,
                "frequency": 1,
            })

    merged_patterns = _merge_similar_patterns(patterns)
    _write_patterns_to_pg(merged_patterns, "formula_context_pattern", db_pg)
    logger.info("[%s] 发现 %d 个方剂上下文模式", book_id, len(merged_patterns))


# =============================================================================
# 辅助函数
# =============================================================================


def _extract_char_diff_pattern(
    original: str,
    corrected: str,
) -> Optional[dict]:
    """提取两个字符串之间的字符级差异模式。

    使用简单的 LCS（最长公共子序列）方法定位差异区域。

    Args:
        original: 原始字符串
        corrected: 校正后字符串

    Returns:
        {"from": 差异前文本, "to": 差异后文本} 或 None
    """
    if not original or not corrected:
        return None

    # 简单实现：找出第一个和最后一个不同字符的位置
    min_len = min(len(original), len(corrected))

    start = 0
    while start < min_len and original[start] == corrected[start]:
        start += 1

    if start == len(original) and start == len(corrected):
        return None  # 完全相同

    end_orig = len(original) - 1
    end_corr = len(corrected) - 1
    while (
        end_orig >= start
        and end_corr >= start
        and original[end_orig] == corrected[end_corr]
    ):
        end_orig -= 1
        end_corr -= 1

    diff_from = original[start : end_orig + 1]
    diff_to = corrected[start : end_corr + 1]

    if not diff_from and not diff_to:
        return None

    return {
        "from": diff_from if diff_from else "(缺失)",
        "to": diff_to if diff_to else "(缺失)",
    }


def _extract_formula_context_pattern(
    original: str,
    corrected: str,
) -> Optional[dict]:
    """提取方剂上下文特有的差异模式。

    Args:
        original: 原始文本
        corrected: 校正后文本

    Returns:
        差异模式字典或 None
    """
    import re

    # 检测剂量模式差异
    dose_pattern = re.compile(r"各?\s*[一二三四五六七八九十百\d]+\s*[钱两克分]")

    orig_dose = dose_pattern.findall(original)
    corr_dose = dose_pattern.findall(corrected)

    if orig_dose != corr_dose:
        return {
            "from": original,
            "to": corrected,
        }

    # 检测用法模式差异
    usage_words = {"水煎服", "口服", "温服", "冲服", "研末", "烊化", "另煎"}
    orig_usage = [w for w in usage_words if w in original]
    corr_usage = [w for w in usage_words if w in corrected]

    if orig_usage != corr_usage:
        return {
            "from": original,
            "to": corrected,
        }

    # 回退到通用差异提取
    return _extract_char_diff_pattern(original, corrected)


def _merge_similar_patterns(patterns: list) -> list:
    """合并相似的模式并统计频率。

    Args:
        patterns: 原始模式列表

    Returns:
        合并后的模式列表
    """
    if not patterns:
        return []

    merged: dict = {}

    for p in patterns:
        key = (p["original_pattern"], p["corrected_pattern"])
        if key in merged:
            merged[key]["frequency"] = merged[key].get("frequency", 1) + 1
            # 保留更长的上下文
            if len(p.get("context_original", "")) > len(
                merged[key].get("context_original", "")
            ):
                merged[key]["context_original"] = p["context_original"]
                merged[key]["context_corrected"] = p["context_corrected"]
        else:
            merged[key] = dict(p)

    return list(merged.values())


def _write_patterns_to_pg(
    patterns: list,
    table_name: str,
    db_pg: object,
) -> None:
    """将发现的模式写入 PostgreSQL。

    Args:
        patterns: 模式列表
        table_name: 目标表名
        db_pg: PostgreSQL 连接
    """
    if not patterns:
        return

    pg_cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg

    # 动态构建 INSERT 语句
    insert_sql = f"""
        INSERT INTO {table_name} (
            book_id, pattern_type, original_pattern, corrected_pattern,
            context_original, context_corrected, frequency, discovered_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (book_id, original_pattern, corrected_pattern)
        DO UPDATE SET
            frequency = {table_name}.frequency + EXCLUDED.frequency,
            discovered_at = NOW()
    """

    batch = []
    for p in patterns:
        batch.append((
            p["book_id"],
            p["pattern_type"],
            p["original_pattern"],
            p["corrected_pattern"],
            p.get("context_original", ""),
            p.get("context_corrected", ""),
            p.get("frequency", 1),
        ))

        if len(batch) >= 100:
            pg_cursor.executemany(insert_sql, batch)
            db_pg.commit() if hasattr(db_pg, "commit") else None
            batch = []

    if batch:
        pg_cursor.executemany(insert_sql, batch)
        db_pg.commit() if hasattr(db_pg, "commit") else None
