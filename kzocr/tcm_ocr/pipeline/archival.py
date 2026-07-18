"""
数据归档模块。

实现从 SQLite 书籍库到 PostgreSQL 的长期归档：
- ProofreadRecord → LineCorrectionArchive
- LineEngineResult → OCRLineResultArchive
- ContentNode 树 → BookContentTree
- FormulaComposition + FormulaIngredient 归档
- final_document.json 归档
- 清理书籍库临时文件
"""

import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from kzocr.tcm_ocr.config.constants import (
    TABLE_BOOK_CONTENT_TREE,
    TABLE_FORMULA_COMPOSITION,
    TABLE_FORMULA_INGREDIENT,
    TABLE_LINE_CORRECTION_ARCHIVE,
    TABLE_OCR_LINE_RESULT_ARCHIVE,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 公共辅助函数
# =============================================================================


def _get_sqlite_connection(book_library_dir: Path, book_id: str) -> sqlite3.Connection:
    """获取指定书籍的 SQLite 连接。

    Args:
        book_library_dir: 书籍库目录
        book_id: 书籍 ID

    Returns:
        sqlite3 连接对象

    Raises:
        FileNotFoundError: 数据库文件不存在
        sqlite3.Error: 连接失败
    """
    db_path = book_library_dir / f"{book_id}.db"
    if not db_path.exists():
        raise FileNotFoundError(f"书籍数据库不存在: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _pg_execute(
    db_pg: Any,
    sql: str,
    params: tuple = (),
) -> Any:
    """在 PostgreSQL 上执行 SQL。

    Args:
        db_pg: PostgreSQL 连接/游标对象
        sql: SQL 语句
        params: 参数

    Returns:
        执行结果

    Raises:
        Exception: 执行失败
    """
    try:
        cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg
        cursor.execute(sql, params)
        return cursor
    except Exception as e:
        logger.error("PostgreSQL 执行失败: %s | SQL: %s | params: %s", e, sql[:200], params)
        raise


# =============================================================================
# 主归档函数
# =============================================================================


def archive_to_postgresql(
    book_id: str,
    db_book: Any,
    db_pg: Any,
) -> None:
    """将书籍数据从 SQLite 归档到 PostgreSQL。

    归档流程：
    1. ProofreadRecord → LineCorrectionArchive
    2. LineEngineResult → OCRLineResultArchive
    3. ContentNode 树 → BookContentTree
    4. FormulaComposition + FormulaIngredient 归档
    5. final_document.json 路径记录

    Args:
        book_id: 书籍唯一标识
        db_book: SQLite 书籍数据库连接（或路径/对象）
        db_pg: PostgreSQL 连接对象

    Raises:
        FileNotFoundError: 数据库文件不存在
        sqlite3.Error: SQLite 查询失败
        Exception: PostgreSQL 写入失败
    """
    logger.info("开始归档书籍 %s 到 PostgreSQL", book_id)
    start_time = datetime.now()

    try:
        # 1. 归档校对记录
        _archive_proofread_records(book_id, db_book, db_pg)
        logger.info("[%s] ProofreadRecord 归档完成", book_id)

        # 2. 归档引擎结果（决策 #2：默认停写逐行 OCR，仅留 BookContentTree）
        if os.environ.get("KZOCR_ARCHIVE_LINE_RESULTS", "0") in ("1", "true", "True"):
            _archive_engine_results(book_id, db_book, db_pg)
            logger.info("[%s] LineEngineResult 归档完成", book_id)
        else:
            logger.info(
                "[%s] 跳过逐行 OCR 归档（KZOCR_ARCHIVE_LINE_RESULTS 未启用，决策 #2）",
                book_id,
            )

        # 3. 归档 ContentNode 树
        _archive_content_tree(book_id, db_book, db_pg)
        logger.info("[%s] ContentNode 树归档完成", book_id)

        # 4. 归档方剂组成
        _archive_formula_compositions(book_id, db_book, db_pg)
        logger.info("[%s] FormulaComposition 归档完成", book_id)

        # 5. 记录 final_document.json 归档路径
        _archive_final_document_ref(book_id, db_pg)
        logger.info("[%s] final_document.json 归档引用完成", book_id)

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("[%s] 归档完成，耗时 %.1f 秒", book_id, elapsed)

    except Exception as e:
        logger.error("[%s] 归档失败: %s", book_id, e)
        raise


# =============================================================================
# 子归档函数
# =============================================================================


def _archive_proofread_records(
    book_id: str,
    db_book: Any,
    db_pg: Any,
) -> None:
    """归档 ProofreadRecord → LineCorrectionArchive。

    Args:
        book_id: 书籍 ID
        db_book: SQLite 连接
        db_pg: PostgreSQL 连接
    """
    # 从 SQLite 读取校对记录
    cursor = db_book.execute(
        "SELECT * FROM proofread_record WHERE book_id = ? ORDER BY id",
        (book_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        logger.warning("[%s] 无校对记录可归档", book_id)
        return

    # 批量插入 PostgreSQL
    insert_sql = f"""
        INSERT INTO {TABLE_LINE_CORRECTION_ARCHIVE} (
            archive_id, book_id, page_number, paragraph_id, line_id,
            line_number, original_text, corrected_text, confidence,
            engine_results, llm_decision, llm_decision_level,
            disputed, dispute_reason, human_verified, human_final_text,
            cer_before, cer_after, correction_type,
            created_at, archived_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (archive_id) DO NOTHING
    """

    pg_cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg

    batch: List[tuple] = []
    for row in rows:
        row_dict = dict(row)
        archive_id = f"{book_id}_lc_{row_dict.get('id', 0)}"

        engine_results = row_dict.get("engine_results", "")
        if isinstance(engine_results, dict):
            engine_results = json.dumps(engine_results, ensure_ascii=False)

        batch.append((
            archive_id,
            book_id,
            row_dict.get("page_number", 0),
            row_dict.get("paragraph_id", ""),
            row_dict.get("line_id", ""),
            row_dict.get("line_number", 0),
            row_dict.get("original_text", ""),
            row_dict.get("corrected_text", ""),
            row_dict.get("confidence", 0.0),
            engine_results,
            row_dict.get("llm_decision", ""),
            row_dict.get("llm_decision_level", 0),
            row_dict.get("disputed", False),
            row_dict.get("dispute_reason", ""),
            row_dict.get("human_verified", False),
            row_dict.get("human_final_text", ""),
            row_dict.get("cer_before", None),
            row_dict.get("cer_after", None),
            row_dict.get("correction_type", ""),
            row_dict.get("created_at", datetime.now().isoformat()),
        ))

        if len(batch) >= 100:
            pg_cursor.executemany(insert_sql, batch)
            db_pg.commit() if hasattr(db_pg, "commit") else None
            batch = []

    if batch:
        pg_cursor.executemany(insert_sql, batch)
        db_pg.commit() if hasattr(db_pg, "commit") else None


def _archive_engine_results(
    book_id: str,
    db_book: Any,
    db_pg: Any,
) -> None:
    """归档 LineEngineResult → OCRLineResultArchive。

    Args:
        book_id: 书籍 ID
        db_book: SQLite 连接
        db_pg: PostgreSQL 连接
    """
    cursor = db_book.execute(
        "SELECT * FROM line_engine_result WHERE book_id = ? ORDER BY id",
        (book_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        logger.warning("[%s] 无引擎结果可归档", book_id)
        return

    insert_sql = f"""
        INSERT INTO {TABLE_OCR_LINE_RESULT_ARCHIVE} (
            archive_id, book_id, page_number, line_id,
            engine_name, raw_text, confidence, char_confidences,
            bbox, processing_time_ms, created_at, archived_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (archive_id) DO NOTHING
    """

    pg_cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg
    batch: List[tuple] = []

    for row in rows:
        row_dict = dict(row)
        archive_id = f"{book_id}_er_{row_dict.get('id', 0)}"

        char_conf = row_dict.get("char_confidences", "")
        if isinstance(char_conf, (list, dict)):
            char_conf = json.dumps(char_conf, ensure_ascii=False)

        bbox = row_dict.get("bbox", "")
        if isinstance(bbox, (list, tuple)):
            bbox = json.dumps(bbox)

        batch.append((
            archive_id,
            book_id,
            row_dict.get("page_number", 0),
            row_dict.get("line_id", ""),
            row_dict.get("engine_name", ""),
            row_dict.get("raw_text", ""),
            row_dict.get("confidence", 0.0),
            char_conf,
            bbox,
            row_dict.get("processing_time_ms", 0),
            row_dict.get("created_at", datetime.now().isoformat()),
        ))

        if len(batch) >= 200:
            pg_cursor.executemany(insert_sql, batch)
            db_pg.commit() if hasattr(db_pg, "commit") else None
            batch = []

    if batch:
        pg_cursor.executemany(insert_sql, batch)
        db_pg.commit() if hasattr(db_pg, "commit") else None


def _archive_content_tree(
    book_id: str,
    db_book: Any,
    db_pg: Any,
) -> None:
    """归档 ContentNode 树 → BookContentTree。

    Args:
        book_id: 书籍 ID
        db_book: SQLite 连接
        db_pg: PostgreSQL 连接
    """
    cursor = db_book.execute(
        "SELECT * FROM content_node WHERE book_id = ? ORDER BY node_order",
        (book_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        logger.warning("[%s] 无内容节点可归档", book_id)
        return

    insert_sql = f"""
        INSERT INTO {TABLE_BOOK_CONTENT_TREE} (
            archive_id, book_id, node_id, parent_id, node_type,
            node_level, title, content, page_start, page_end,
            node_order, metadata, created_at, archived_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (archive_id) DO NOTHING
    """

    pg_cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg
    batch: List[tuple] = []

    for row in rows:
        row_dict = dict(row)
        archive_id = f"{book_id}_cn_{row_dict.get('node_id', 'unknown')}"

        metadata = row_dict.get("metadata", "")
        if isinstance(metadata, dict):
            metadata = json.dumps(metadata, ensure_ascii=False)

        batch.append((
            archive_id,
            book_id,
            row_dict.get("node_id", ""),
            row_dict.get("parent_id", ""),
            row_dict.get("node_type", ""),
            row_dict.get("node_level", 0),
            row_dict.get("title", ""),
            row_dict.get("content", ""),
            row_dict.get("page_start", 0),
            row_dict.get("page_end", 0),
            row_dict.get("node_order", 0),
            metadata,
            row_dict.get("created_at", datetime.now().isoformat()),
        ))

        if len(batch) >= 200:
            pg_cursor.executemany(insert_sql, batch)
            db_pg.commit() if hasattr(db_pg, "commit") else None
            batch = []

    if batch:
        pg_cursor.executemany(insert_sql, batch)
        db_pg.commit() if hasattr(db_pg, "commit") else None


def _archive_formula_compositions(
    book_id: str,
    db_book: Any,
    db_pg: Any,
) -> None:
    """跨页方剂合并归档到 PostgreSQL。

    归档 FormulaComposition 和 FormulaIngredient 两张表。

    Args:
        book_id: 书籍 ID
        db_book: SQLite 连接
        db_pg: PostgreSQL 连接
    """
    # 1. 归档方剂组成
    cursor = db_book.execute(
        "SELECT * FROM formula_composition WHERE book_id = ? ORDER BY id",
        (book_id,),
    )
    comp_rows = cursor.fetchall()

    if not comp_rows:
        logger.warning("[%s] 无方剂组成可归档", book_id)
        return

    pg_cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg

    # 插入方剂组成
    comp_insert_sql = f"""
        INSERT INTO {TABLE_FORMULA_COMPOSITION} (
            archive_id, book_id, formula_id, formula_name, formula_sequence,
            page_numbers, paragraph_ids, source_text, extracted_by,
            verification_status, created_at, archived_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (archive_id) DO NOTHING
    """

    formula_id_map: Dict[str, str] = {}  # 原 formula_id -> archive_id
    batch: List[tuple] = []

    for row in comp_rows:
        row_dict = dict(row)
        original_formula_id = row_dict.get("formula_id", "")
        archive_id = f"{book_id}_fc_{row_dict.get('id', 0)}"
        formula_id_map[original_formula_id] = archive_id

        page_nums = row_dict.get("page_numbers", "")
        if isinstance(page_nums, list):
            page_nums = ",".join(map(str, page_nums))

        para_ids = row_dict.get("paragraph_ids", "")
        if isinstance(para_ids, list):
            para_ids = ",".join(para_ids)

        batch.append((
            archive_id,
            book_id,
            original_formula_id,
            row_dict.get("formula_name", ""),
            row_dict.get("formula_sequence", 0),
            page_nums,
            para_ids,
            row_dict.get("source_text", ""),
            row_dict.get("extracted_by", ""),
            row_dict.get("verification_status", "pending"),
            row_dict.get("created_at", datetime.now().isoformat()),
        ))

        if len(batch) >= 100:
            pg_cursor.executemany(comp_insert_sql, batch)
            db_pg.commit() if hasattr(db_pg, "commit") else None
            batch = []

    if batch:
        pg_cursor.executemany(comp_insert_sql, batch)
        db_pg.commit() if hasattr(db_pg, "commit") else None

    # 2. 归档药材成分
    if not formula_id_map:
        return

    cursor = db_book.execute(
        "SELECT * FROM formula_ingredient WHERE book_id = ? ORDER BY id",
        (book_id,),
    )
    ing_rows = cursor.fetchall()

    if not ing_rows:
        logger.warning("[%s] 无药材成分可归档", book_id)
        return

    ing_insert_sql = f"""
        INSERT INTO {TABLE_FORMULA_INGREDIENT} (
            archive_id, composition_archive_id, book_id, formula_id,
            herb_name, dosage, dosage_unit, processing_note,
            ingredient_order, created_at, archived_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (archive_id) DO NOTHING
    """

    batch = []
    for row in ing_rows:
        row_dict = dict(row)
        original_formula_id = row_dict.get("formula_id", "")
        comp_archive_id = formula_id_map.get(original_formula_id, "")

        archive_id = f"{book_id}_fi_{row_dict.get('id', 0)}"

        batch.append((
            archive_id,
            comp_archive_id,
            book_id,
            original_formula_id,
            row_dict.get("herb_name", ""),
            row_dict.get("dosage", None),
            row_dict.get("dosage_unit", ""),
            row_dict.get("processing_note", ""),
            row_dict.get("ingredient_order", 0),
            row_dict.get("created_at", datetime.now().isoformat()),
        ))

        if len(batch) >= 200:
            pg_cursor.executemany(ing_insert_sql, batch)
            db_pg.commit() if hasattr(db_pg, "commit") else None
            batch = []

    if batch:
        pg_cursor.executemany(ing_insert_sql, batch)
        db_pg.commit() if hasattr(db_pg, "commit") else None


def _archive_final_document_ref(
    book_id: str,
    db_pg: Any,
) -> None:
    """记录 final_document.json 的归档引用。

    Args:
        book_id: 书籍 ID
        db_pg: PostgreSQL 连接
    """
    pg_cursor = db_pg.cursor() if hasattr(db_pg, "cursor") else db_pg

    insert_sql = """
        INSERT INTO document_archive_ref (
            book_id, final_document_path, archived_at
        ) VALUES (%s, %s, NOW())
        ON CONFLICT (book_id) DO UPDATE SET
            final_document_path = EXCLUDED.final_document_path,
            archived_at = NOW()
    """

    # 假设 final_document.json 存储在输出目录
    output_path = f"/mnt/agents/output/tcm_ocr_results/{book_id}/final_document.json"
    pg_cursor.execute(insert_sql, (book_id, output_path))
    db_pg.commit() if hasattr(db_pg, "commit") else None


# =============================================================================
# 清理函数
# =============================================================================


def cleanup_book_directory(
    book_id: str,
    book_library_dir: str = "/mnt/agents/output/tcm_ocr_library",
) -> None:
    """清理书籍库 SQLite 文件和临时图片。

    归档完成后调用，释放磁盘空间。

    Args:
        book_id: 书籍 ID
        book_library_dir: 书籍库目录路径

    Raises:
        OSError: 删除失败（权限不足等）
    """
    lib_path = Path(book_library_dir)
    if not lib_path.exists():
        logger.warning("书籍库目录不存在: %s", book_library_dir)
        return

    removed_items: List[str] = []

    # 1. 删除 SQLite 数据库文件
    db_file = lib_path / f"{book_id}.db"
    if db_file.exists():
        try:
            db_file.unlink()
            removed_items.append(str(db_file))
        except OSError as e:
            logger.error("删除数据库文件失败 %s: %s", db_file, e)

    # 2. 删除临时图片目录
    img_dir = lib_path / f"{book_id}_images"
    if img_dir.exists() and img_dir.is_dir():
        try:
            shutil.rmtree(img_dir)
            removed_items.append(str(img_dir))
        except OSError as e:
            logger.error("删除图片目录失败 %s: %s", img_dir, e)

    # 3. 删除其他临时文件（如 .db-journal, .db-wal 等）
    for ext in [".db-journal", ".db-wal", ".db-shm"]:
        temp_file = lib_path / f"{book_id}{ext}"
        if temp_file.exists():
            try:
                temp_file.unlink()
                removed_items.append(str(temp_file))
            except OSError as e:
                logger.error("删除临时文件失败 %s: %s", temp_file, e)

    logger.info(
        "[%s] 清理完成，删除 %d 项: %s",
        book_id,
        len(removed_items),
        removed_items[:5],
    )
