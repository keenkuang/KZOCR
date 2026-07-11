"""
PostgreSQL 运行库连接层 - RuntimeDB 类

封装所有 PostgreSQL 操作，使用 psycopg2 连接池管理连接。
提供书籍注册、药典查询、术语库管理、错误映射库、用户权限、
系统配置、归档管理等核心功能的完整数据库访问接口。

所有方法均使用参数化查询防止 SQL 注入，并通过 context manager 管理事务。

注意：psycopg2 为可选依赖，仅在实例化 RuntimeDB 时按需导入。
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# =============================================================================
# 数据模型类
# =============================================================================

@dataclass
class BookRegistry:
    """书籍注册记录数据类"""
    id: int
    status: str
    pdf_path: str
    db_path: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class BookMeta:
    """书籍元数据记录数据类"""
    id: int
    book_registry_id: int
    title: str
    author: Optional[str] = None
    publisher: Optional[str] = None
    pub_year: Optional[int] = None
    edition: Optional[str] = None
    isbn: Optional[str] = None
    source_declaration: Optional[str] = None


@dataclass
class PharmacopoeiaVersion:
    """药典版本记录数据类"""
    id: int
    pharmacopoeia_version: str
    effective_year: int
    expiry_year: Optional[int] = None
    is_current: bool = False


@dataclass
class HerbDosage:
    """药材剂量标准记录数据类"""
    id: int
    herb_name: str
    pharmacopoeia_version_id: int
    dosage_min: Optional[float] = None
    dosage_max: Optional[float] = None
    unit: str = 'g'
    severe_threshold: Optional[float] = None
    toxicity_level: Optional[str] = None
    note: Optional[str] = None


@dataclass
class TermRecord:
    """术语记录数据类"""
    id: int
    term_text: str
    sublib_id: Optional[int] = None
    semantic_category: Optional[str] = None
    source_authority: Optional[str] = None
    effective_pub_year: Optional[int] = None
    expiry_pub_year: Optional[int] = None
    status: str = 'active'
    scope: Optional[str] = None
    publisher: Optional[str] = None
    pub_era: Optional[str] = None
    error_pattern: Optional[str] = None
    confidence: float = 1.0
    frequency: int = 0


@dataclass
class CorrectionKnowledgeRecord:
    """错误映射记录数据类"""
    id: int
    original_text: str
    corrected_text: str
    pattern_type: str
    source_books: List[str]
    evidence_count: int
    status: str
    review_status: str


@dataclass
class UserRecord:
    """用户记录数据类"""
    id: int
    username: str
    role_id: int
    email: Optional[str] = None


# =============================================================================
# RuntimeDB 主类
# =============================================================================

class RuntimeDB:
    """
    PostgreSQL 运行库管理类

    封装所有 PostgreSQL 数据库操作，使用连接池管理数据库连接。
    提供完整的 CRUD 操作和事务管理功能。

    Attributes:
        _connection_pool: psycopg2 连接池实例
        _dsn: 数据库连接字符串
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        dsn: Optional[str] = None,
        min_conn: int = 1,
        max_conn: int = 10,
    ) -> None:
        """初始化 RuntimeDB 连接池（惰性导入 psycopg2）。"""
        # 惰性导入 psycopg2（可选依赖）
        try:
            import psycopg2  # noqa: F401
            from psycopg2 import Error as Psycopg2Error
            from psycopg2 import pool
            from psycopg2.extras import RealDictCursor, Json
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for RuntimeDB. "
                "Install with: pip install psycopg2-binary"
            ) from exc
        self._Psycopg2Error = Psycopg2Error
        self._pool_module = pool
        self._RealDictCursor = RealDictCursor
        self._Json = Json

        self._dsn = dsn or self._build_dsn(host, port, database, user, password)
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._connection_pool: Optional[self._pool_module.ThreadedConnectionPool] = None
        self._initialize_pool()

    @staticmethod
    def _build_dsn(
        host: Optional[str],
        port: Optional[int],
        database: Optional[str],
        user: Optional[str],
        password: Optional[str],
    ) -> str:
        """从环境变量或参数构建 DSN 字符串"""
        host = host or os.environ.get('TCM_POSTGRES_HOST', 'localhost')
        port = port or int(os.environ.get('TCM_POSTGRES_PORT', '5432'))
        database = database or os.environ.get('TCM_POSTGRES_DB', 'tcm_ocr_runtime')
        user = user or os.environ.get('TCM_POSTGRES_USER', 'tcm_ocr')
        password = password or os.environ.get('TCM_POSTGRES_PASSWORD', '')
        return f"host={host} port={port} dbname={database} user={user} password={password}"

    def _initialize_pool(self) -> None:
        """初始化数据库连接池"""
        try:
            self._connection_pool = self._pool_module.ThreadedConnectionPool(
                minconn=self._min_conn,
                maxconn=self._max_conn,
                dsn=self._dsn,
            )
            logger.info("PostgreSQL connection pool initialized successfully")
        except self._Psycopg2Error as e:
            logger.error("Failed to initialize PostgreSQL connection pool: %s", e)
            raise RuntimeError(f"Failed to initialize database connection pool: {e}") from e

    @contextmanager
    def get_cursor(self, cursor_factory=None) -> Iterator:
        """
        获取数据库游标的 context manager

        Yields:
            字典类型的数据库游标

        Raises:
            RuntimeError: 当连接池未初始化时
        """
        if self._connection_pool is None:
            raise RuntimeError("Connection pool not initialized")

        conn = None
        try:
            conn = self._connection_pool.getconn()
            cursor = conn.cursor(cursor_factory=cursor_factory)
            yield cursor
            conn.commit()
        except self._Psycopg2Error as e:
            if conn is not None:
                conn.rollback()
            logger.error("Database error: %s", e)
            raise
        finally:
            if conn is not None:
                self._connection_pool.putconn(conn)

    @contextmanager
    def get_connection(self) -> Iterator[psycopg2.extensions.connection]:
        """
        获取原始数据库连接的 context manager

        Yields:
            psycopg2 connection: 数据库连接对象
        """
        if self._connection_pool is None:
            raise RuntimeError("Connection pool not initialized")

        conn = None
        try:
            conn = self._connection_pool.getconn()
            yield conn
            conn.commit()
        except self._Psycopg2Error as e:
            if conn is not None:
                conn.rollback()
            logger.error("Database error: %s", e)
            raise
        finally:
            if conn is not None:
                self._connection_pool.putconn(conn)

    def close(self) -> None:
        """关闭连接池并释放所有资源"""
        if self._connection_pool is not None:
            self._connection_pool.closeall()
            self._connection_pool = None
            logger.info("PostgreSQL connection pool closed")

    def __enter__(self) -> 'RuntimeDB':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # =====================================================================
    # 书籍注册与元数据管理
    # =====================================================================

    def register_book(self, pdf_path: str, db_path: Optional[str] = None) -> int:
        """
        注册一本新书

        Args:
            pdf_path: PDF 源文件路径
            db_path: SQLite 书籍库文件路径

        Returns:
            int: 新注册书籍的 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO BookRegistry (status, pdf_path, db_path, created_at)
                VALUES (%s, %s, %s, NOW())
                RETURNING id
                """,
                ('pending', pdf_path, db_path),
            )
            result = cursor.fetchone()
            book_id = result['id']
            logger.info("Book registered with ID: %d", book_id)
            return book_id

    def update_book_status(self, book_id: int, status: str) -> bool:
        """
        更新书籍处理状态

        Args:
            book_id: 书籍注册 ID
            status: 新状态值

        Returns:
            bool: 是否更新成功
        """
        valid_statuses = {'pending', 'processing', 'proofreading', 'completed', 'archived', 'error'}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE BookRegistry
                SET status = %s,
                    started_at = CASE WHEN %s = 'processing' AND started_at IS NULL THEN NOW() ELSE started_at END,
                    completed_at = CASE WHEN %s = 'completed' THEN NOW() ELSE completed_at END,
                    archived_at = CASE WHEN %s = 'archived' THEN NOW() ELSE archived_at END
                WHERE id = %s
                """,
                (status, status, status, status, book_id),
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.info("Book %d status updated to '%s'", book_id, status)
            return updated

    def get_book_registry(self, book_id: int) -> Optional[Dict[str, Any]]:
        """
        获取书籍注册信息

        Args:
            book_id: 书籍注册 ID

        Returns:
            Dict: 书籍注册信息，如果不存在则返回 None
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, pdf_path, db_path,
                       started_at, completed_at, archived_at, created_at
                FROM BookRegistry
                WHERE id = %s
                """,
                (book_id,),
            )
            result = cursor.fetchone()
            return dict(result) if result else None

    def list_books(self, status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        列出书籍注册记录

        Args:
            status: 按状态过滤
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            List[Dict]: 书籍注册记录列表
        """
        with self.get_cursor() as cursor:
            if status:
                cursor.execute(
                    """
                    SELECT id, status, pdf_path, db_path,
                           started_at, completed_at, archived_at, created_at
                    FROM BookRegistry
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (status, limit, offset),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, status, pdf_path, db_path,
                           started_at, completed_at, archived_at, created_at
                    FROM BookRegistry
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
            return [dict(row) for row in cursor.fetchall()]

    def set_book_meta(self, book_registry_id: int, **kwargs) -> int:
        """
        设置或更新书籍元数据

        Args:
            book_registry_id: 书籍注册 ID
            **kwargs: 元数据字段（title, author, publisher, pub_year, edition, isbn, source_declaration）

        Returns:
            int: 元数据记录 ID
        """
        allowed_fields = {'title', 'author', 'publisher', 'pub_year', 'edition', 'isbn', 'source_declaration'}
        fields = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if 'title' not in fields:
            raise ValueError("Title is required")

        with self.get_cursor() as cursor:
            # 检查是否已存在
            cursor.execute(
                "SELECT id FROM BookMeta WHERE book_registry_id = %s",
                (book_registry_id,),
            )
            existing = cursor.fetchone()

            if existing:
                # 更新
                set_clause = ", ".join(f"{k} = %s" for k in fields)
                values = list(fields.values()) + [existing['id']]
                cursor.execute(
                    f"UPDATE BookMeta SET {set_clause} WHERE id = %s RETURNING id",
                    values,
                )
            else:
                # 插入
                columns = ['book_registry_id'] + list(fields.keys())
                placeholders = ["%s"] * len(columns)
                values = [book_registry_id] + list(fields.values())
                cursor.execute(
                    f"""
                    INSERT INTO BookMeta ({', '.join(columns)})
                    VALUES ({', '.join(placeholders)})
                    RETURNING id
                    """,
                    values,
                )
            result = cursor.fetchone()
            return result['id']

    def get_book_meta(self, book_registry_id: int) -> Optional[Dict[str, Any]]:
        """获取书籍元数据"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, book_registry_id, title, author, publisher,
                       pub_year, edition, isbn, source_declaration
                FROM BookMeta
                WHERE book_registry_id = %s
                """,
                (book_registry_id,),
            )
            result = cursor.fetchone()
            return dict(result) if result else None

    # =====================================================================
    # 药典与剂量标准查询
    # =====================================================================

    def get_current_pharmacopoeia_id(self) -> Optional[int]:
        """获取当前生效的药典版本 ID"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM PharmacopoeiaTimeline WHERE is_current = TRUE"
            )
            result = cursor.fetchone()
            return result['id'] if result else None

    def get_pharmacopoeia_versions(self) -> List[Dict[str, Any]]:
        """获取所有药典版本列表"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, pharmacopoeia_version, effective_year, expiry_year, is_current
                FROM PharmacopoeiaTimeline
                ORDER BY effective_year
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_herb_dosage(self, herb_name: str, version_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        查询药材剂量标准

        Args:
            herb_name: 药材名称
            version_id: 药典版本 ID（默认使用当前版本）

        Returns:
            Dict: 剂量标准信息，如果不存在则返回 None
        """
        if version_id is None:
            version_id = self.get_current_pharmacopoeia_id()

        if version_id is None:
            return None

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, herb_name, pharmacopoeia_version_id,
                       dosage_min, dosage_max, unit, severe_threshold, toxicity_level, note
                FROM HerbDosageStandard
                WHERE herb_name = %s AND pharmacopoeia_version_id = %s
                """,
                (herb_name, version_id),
            )
            result = cursor.fetchone()
            return dict(result) if result else None

    def search_herb_dosages(
        self,
        herb_name_pattern: Optional[str] = None,
        version_id: Optional[int] = None,
        toxicity_level: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        搜索药材剂量标准

        Args:
            herb_name_pattern: 药材名称模糊匹配模式
            version_id: 药典版本 ID
            toxicity_level: 毒性等级过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 剂量标准列表
        """
        conditions = []
        params = []

        if herb_name_pattern:
            conditions.append("h.herb_name ILIKE %s")
            params.append(f"%{herb_name_pattern}%")
        if version_id:
            conditions.append("h.pharmacopoeia_version_id = %s")
            params.append(version_id)
        if toxicity_level:
            conditions.append("h.toxicity_level = %s")
            params.append(toxicity_level)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT h.id, h.herb_name, h.pharmacopoeia_version_id,
                       h.dosage_min, h.dosage_max, h.unit,
                       h.severe_threshold, h.toxicity_level, h.note,
                       p.pharmacopoeia_version
                FROM HerbDosageStandard h
                JOIN PharmacopoeiaTimeline p ON h.pharmacopoeia_version_id = p.id
                {where_clause}
                ORDER BY h.herb_name
                LIMIT %s
                """,
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def validate_dosage(
        self,
        herb_name: str,
        dosage_value: float,
        unit: str = 'g',
        version_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        验证药材剂量是否在法定范围内

        Args:
            herb_name: 药材名称
            dosage_value: 待验证的剂量值
            unit: 剂量单位
            version_id: 药典版本 ID

        Returns:
            Dict: 验证结果，包含 status 字段（valid/warning/invalid/error）
        """
        standard = self.get_herb_dosage(herb_name, version_id)

        if standard is None:
            return {
                'status': 'unknown_herb',
                'herb_name': herb_name,
                'input_dosage': dosage_value,
                'message': f'No dosage standard found for herb: {herb_name}',
            }

        if unit != standard['unit']:
            return {
                'status': 'unit_mismatch',
                'herb_name': herb_name,
                'input_dosage': dosage_value,
                'input_unit': unit,
                'standard_unit': standard['unit'],
                'message': f'Unit mismatch: input {unit}, standard {standard["unit"]}',
            }

        dosage_min = standard['dosage_min'] or 0
        dosage_max = standard['dosage_max']
        severe_threshold = standard['severe_threshold']

        if dosage_max is not None and dosage_value > dosage_max:
            if severe_threshold is not None and dosage_value >= severe_threshold:
                return {
                    'status': 'severe_overdose',
                    'herb_name': herb_name,
                    'input_dosage': dosage_value,
                    'standard_max': dosage_max,
                    'severe_threshold': severe_threshold,
                    'toxicity_level': standard.get('toxicity_level'),
                    'message': f'Dosage {dosage_value}{unit} exceeds severe threshold {severe_threshold}{unit}',
                }
            return {
                'status': 'overdose',
                'herb_name': herb_name,
                'input_dosage': dosage_value,
                'standard_max': dosage_max,
                'message': f'Dosage {dosage_value}{unit} exceeds maximum {dosage_max}{unit}',
            }

        if dosage_value < dosage_min:
            return {
                'status': 'underdose',
                'herb_name': herb_name,
                'input_dosage': dosage_value,
                'standard_min': dosage_min,
                'message': f'Dosage {dosage_value}{unit} below minimum {dosage_min}{unit}',
            }

        return {
            'status': 'valid',
            'herb_name': herb_name,
            'input_dosage': dosage_value,
            'standard_min': dosage_min,
            'standard_max': dosage_max,
            'message': 'Dosage within standard range',
        }

    # =====================================================================
    # OCR 处理日志
    # =====================================================================

    def create_ocr_log(
        self,
        book_registry_id: int,
        total_pages: int = 0,
        total_lines: int = 0,
    ) -> int:
        """
        创建 OCR 处理日志记录

        Args:
            book_registry_id: 书籍注册 ID
            total_pages: 总页数
            total_lines: 总行数

        Returns:
            int: 日志记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO OCRProcessingLog (book_registry_id, total_pages, total_lines, started_at)
                VALUES (%s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, total_pages, total_lines),
            )
            result = cursor.fetchone()
            return result['id']

    def complete_ocr_log(self, log_id: int, total_pages: int, total_lines: int) -> bool:
        """
        标记 OCR 处理日志为完成状态

        Args:
            log_id: 日志记录 ID
            total_pages: 最终总页数
            total_lines: 最终总行数

        Returns:
            bool: 是否更新成功
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE OCRProcessingLog
                SET total_pages = %s,
                    total_lines = %s,
                    completed_at = NOW(),
                    total_duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at))::INTEGER
                WHERE id = %s
                """,
                (total_pages, total_lines, log_id),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 阶段 CER 统计
    # =====================================================================

    def record_cer_stats(
        self,
        book_registry_id: int,
        stage_name: str,
        cer_value: float,
        pages_valid: int = 0,
        notes: Optional[str] = None,
    ) -> int:
        """
        记录阶段 CER 统计

        Args:
            book_registry_id: 书籍注册 ID
            stage_name: 阶段名称
            cer_value: CER 值（0-1）
            pages_valid: 有效页面数
            notes: 备注

        Returns:
            int: 统计记录 ID
        """
        valid_stages = {
            'raw_ocr', 'vote_consensus', 'llm_correction',
            'glyph_verification', 'auto_proofread', 'human_final',
        }
        if stage_name not in valid_stages:
            raise ValueError(f"Invalid stage: {stage_name}")

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO StageCERStats (book_registry_id, stage_name, cer_value, pages_valid, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, stage_name, cer_value, pages_valid, notes),
            )
            result = cursor.fetchone()
            return result['id']

    def get_cer_stats(self, book_registry_id: int) -> List[Dict[str, Any]]:
        """获取书籍的所有阶段 CER 统计"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, stage_name, cer_value, pages_valid, notes, created_at
                FROM StageCERStats
                WHERE book_registry_id = %s
                ORDER BY created_at
                """,
                (book_registry_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # =====================================================================
    # 人工校对统计
    # =====================================================================

    def record_proofread_stats(
        self,
        book_registry_id: int,
        total_corrections: int = 0,
        reviewer_accuracy: Optional[float] = None,
        golden_standard_pass_rate: Optional[float] = None,
    ) -> int:
        """
        记录人工校对统计

        Args:
            book_registry_id: 书籍注册 ID
            total_corrections: 总校正次数
            reviewer_accuracy: 审校者准确率
            golden_standard_pass_rate: 金标准通过率

        Returns:
            int: 统计记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ProofreadStats (book_registry_id, total_corrections,
                                            reviewer_accuracy, golden_standard_pass_rate, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, total_corrections, reviewer_accuracy, golden_standard_pass_rate),
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 术语库管理
    # =====================================================================

    def create_sublib(self, name: str, description: Optional[str] = None) -> int:
        """创建术语子库"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO Sublib (name, description)
                VALUES (%s, %s)
                ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
                RETURNING id
                """,
                (name, description),
            )
            result = cursor.fetchone()
            return result['id']

    def list_sublibs(self) -> List[Dict[str, Any]]:
        """列出所有术语子库"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT id, name, description FROM Sublib ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def create_term(self, **kwargs) -> int:
        """
        创建术语记录

        Args:
            term_text: 术语文本（必需）
            sublib_id: 子库 ID
            semantic_category: 语义类别
            source_authority: 来源权威
            effective_pub_year: 生效出版年份
            expiry_pub_year: 失效出版年份
            status: 状态
            scope: 适用范围
            publisher: 特定出版社
            pub_era: 出版时代
            error_pattern: 常见 OCR 错误模式
            confidence: 置信度
            frequency: 使用频次

        Returns:
            int: 术语记录 ID
        """
        if 'term_text' not in kwargs:
            raise ValueError("term_text is required")

        allowed_fields = {
            'term_text', 'sublib_id', 'semantic_category', 'source_authority',
            'effective_pub_year', 'expiry_pub_year', 'status', 'scope',
            'publisher', 'pub_era', 'error_pattern', 'confidence', 'frequency',
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed_fields}

        columns = list(fields.keys())
        placeholders = ["%s"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO Term ({', '.join(columns)}, created_at)
                VALUES ({', '.join(placeholders)}, NOW())
                RETURNING id
                """,
                values,
            )
            result = cursor.fetchone()
            return result['id']

    def get_term(self, term_id: int) -> Optional[Dict[str, Any]]:
        """获取术语详情"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT t.*, s.name as sublib_name
                FROM Term t
                LEFT JOIN Sublib s ON t.sublib_id = s.id
                WHERE t.id = %s
                """,
                (term_id,),
            )
            result = cursor.fetchone()
            return dict(result) if result else None

    def search_terms(
        self,
        term_text: Optional[str] = None,
        sublib_id: Optional[int] = None,
        semantic_category: Optional[str] = None,
        status: str = 'active',
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        搜索术语

        Args:
            term_text: 术语文本模糊匹配
            sublib_id: 子库 ID
            semantic_category: 语义类别
            status: 状态过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 术语列表
        """
        conditions = ["t.status = %s"]
        params = [status]

        if term_text:
            conditions.append("t.term_text ILIKE %s")
            params.append(f"%{term_text}%")
        if sublib_id:
            conditions.append("t.sublib_id = %s")
            params.append(sublib_id)
        if semantic_category:
            conditions.append("t.semantic_category = %s")
            params.append(semantic_category)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT t.*, s.name as sublib_name
                FROM Term t
                LEFT JOIN Sublib s ON t.sublib_id = s.id
                {where_clause}
                ORDER BY t.confidence DESC, t.frequency DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_term_frequency(self, term_id: int, increment: int = 1) -> bool:
        """增加术语使用频次"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Term SET frequency = frequency + %s WHERE id = %s",
                (increment, term_id),
            )
            return cursor.rowcount > 0

    def log_term_usage(self, term_id: int, book_id: int, line_id: int, matched_text: str) -> int:
        """
        记录术语使用日志

        Args:
            term_id: 术语 ID
            book_id: 书籍 ID
            line_id: 行 ID
            matched_text: 匹配到的文本

        Returns:
            int: 日志记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO TermUsageLog (term_id, book_id, line_id, matched_text, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (term_id, book_id, line_id, matched_text),
            )
            result = cursor.fetchone()
            # 同时更新频次
            self.update_term_frequency(term_id, 1)
            return result['id']

    # =====================================================================
    # 错误映射库管理
    # =====================================================================

    def create_correction_knowledge(
        self,
        original_text: str,
        corrected_text: str,
        pattern_type: str = 'ocr_error',
        source_books: Optional[List[str]] = None,
    ) -> int:
        """
        创建错误映射知识

        Args:
            original_text: 原始 OCR 错误文本
            corrected_text: 校正后文本
            pattern_type: 错误模式类型
            source_books: 来源书籍列表

        Returns:
            int: 知识记录 ID
        """
        valid_types = {'ocr_error', 'typo', 'formatting', 'semantic', 'herb_name', 'dosage', 'other'}
        if pattern_type not in valid_types:
            raise ValueError(f"Invalid pattern_type: {pattern_type}")

        source_books_json = json.dumps(source_books or [])

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO CorrectionKnowledge (original_text, corrected_text, pattern_type,
                                                 source_books, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (original_text, corrected_text, pattern_type, Json(source_books) if source_books else '[]'),
            )
            result = cursor.fetchone()
            if result:
                return result['id']
            # 如果已存在，返回现有记录的 ID
            cursor.execute(
                """
                SELECT id FROM CorrectionKnowledge
                WHERE original_text = %s AND corrected_text = %s AND pattern_type = %s
                """,
                (original_text, corrected_text, pattern_type),
            )
            existing = cursor.fetchone()
            if existing:
                return existing['id']
            raise RuntimeError("Failed to create or find correction knowledge")

    def find_correction(self, original_text: str, pattern_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        查找错误映射

        Args:
            original_text: 原始文本
            pattern_type: 模式类型过滤

        Returns:
            Dict: 校正记录，如果不存在则返回 None
        """
        with self.get_cursor() as cursor:
            if pattern_type:
                cursor.execute(
                    """
                    SELECT id, original_text, corrected_text, pattern_type,
                           source_books, evidence_count, status, review_status
                    FROM CorrectionKnowledge
                    WHERE original_text = %s AND pattern_type = %s AND status = 'active'
                    ORDER BY evidence_count DESC
                    LIMIT 1
                    """,
                    (original_text, pattern_type),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, original_text, corrected_text, pattern_type,
                           source_books, evidence_count, status, review_status
                    FROM CorrectionKnowledge
                    WHERE original_text = %s AND status = 'active'
                    ORDER BY evidence_count DESC
                    LIMIT 1
                    """,
                    (original_text,),
                )
            result = cursor.fetchone()
            return dict(result) if result else None

    def increment_evidence(self, knowledge_id: int) -> bool:
        """增加错误映射的证据计数"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE CorrectionKnowledge
                SET evidence_count = evidence_count + 1
                WHERE id = %s
                """,
                (knowledge_id,),
            )
            return cursor.rowcount > 0

    def search_correction_knowledge(
        self,
        original_pattern: Optional[str] = None,
        corrected_pattern: Optional[str] = None,
        pattern_type: Optional[str] = None,
        status: str = 'active',
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """搜索错误映射知识"""
        conditions = ["status = %s"]
        params = [status]

        if original_pattern:
            conditions.append("original_text ILIKE %s")
            params.append(f"%{original_pattern}%")
        if corrected_pattern:
            conditions.append("corrected_text ILIKE %s")
            params.append(f"%{corrected_pattern}%")
        if pattern_type:
            conditions.append("pattern_type = %s")
            params.append(pattern_type)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, original_text, corrected_text, pattern_type,
                       source_books, evidence_count, status, review_status, created_at
                FROM CorrectionKnowledge
                {where_clause}
                ORDER BY evidence_count DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    # =====================================================================
    # 候选规则/术语管理
    # =====================================================================

    def create_candidate_rule(self, rule_data: Dict[str, Any]) -> int:
        """创建候选规则"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO CandidateRule (rule_data, created_at)
                VALUES (%s, NOW())
                RETURNING id
                """,
                (Json(rule_data),),
            )
            result = cursor.fetchone()
            return result['id']

    def create_candidate_term(self, term_data: Dict[str, Any]) -> int:
        """创建候选术语"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO CandidateTerm (term_data, created_at)
                VALUES (%s, NOW())
                RETURNING id
                """,
                (Json(term_data),),
            )
            result = cursor.fetchone()
            return result['id']

    def list_candidates(
        self,
        item_type: str,
        status: str = 'pending',
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        列出候选规则或术语

        Args:
            item_type: 'rule' 或 'term'
            status: 状态过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 候选列表
        """
        with self.get_cursor() as cursor:
            if item_type == 'rule':
                cursor.execute(
                    """
                    SELECT id, rule_data as item_data, status, review_status, created_at
                    FROM CandidateRule
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status, limit),
                )
            elif item_type == 'term':
                cursor.execute(
                    """
                    SELECT id, term_data as item_data, status, review_status, created_at
                    FROM CandidateTerm
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status, limit),
                )
            else:
                raise ValueError(f"Invalid item_type: {item_type}")
            return [dict(row) for row in cursor.fetchall()]

    def approve_candidate(self, item_type: str, candidate_id: int) -> bool:
        """
        批准候选规则或术语

        Args:
            item_type: 'rule' 或 'term'
            candidate_id: 候选 ID

        Returns:
            bool: 是否批准成功
        """
        table = 'CandidateRule' if item_type == 'rule' else 'CandidateTerm'
        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {table}
                SET status = 'approved', review_status = 'approved'
                WHERE id = %s
                """,
                (candidate_id,),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 否定词拦截日志
    # =====================================================================

    def log_negation_alert(
        self,
        line_id: int,
        alert_type: str,
        alert_detail: Dict[str, Any],
    ) -> int:
        """
        记录否定词拦截日志

        Args:
            line_id: 行 ID
            alert_type: 警报类型
            alert_detail: 警报详情

        Returns:
            int: 日志记录 ID
        """
        valid_types = {'contraindication', 'pregnancy', 'dosage_limit', 'incompatibility', 'other'}
        if alert_type not in valid_types:
            raise ValueError(f"Invalid alert_type: {alert_type}")

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO NegationAlertLog (line_id, alert_type, alert_detail, created_at)
                VALUES (%s, %s, %s, NOW())
                RETURNING id
                """,
                (line_id, alert_type, Json(alert_detail)),
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 知识版本管理
    # =====================================================================

    def create_knowledge_version(self, version_number: str, change_summary: str) -> int:
        """
        创建知识版本

        Args:
            version_number: 版本号
            change_summary: 变更摘要

        Returns:
            int: 版本记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO KnowledgeVersion (version_number, change_summary, applied_at)
                VALUES (%s, %s, NOW())
                RETURNING id
                """,
                (version_number, change_summary),
            )
            result = cursor.fetchone()
            return result['id']

    def log_knowledge_audit(
        self,
        knowledge_type: str,
        action: str,
        details: Dict[str, Any],
        reviewer_id: Optional[int] = None,
    ) -> int:
        """
        记录知识审计日志

        Args:
            knowledge_type: 知识类型
            action: 操作类型
            details: 操作详情
            reviewer_id: 审核者 ID

        Returns:
            int: 日志记录 ID
        """
        valid_types = {'term', 'correction', 'rule', 'pattern', 'dosage'}
        valid_actions = {'create', 'update', 'delete', 'approve', 'reject'}

        if knowledge_type not in valid_types:
            raise ValueError(f"Invalid knowledge_type: {knowledge_type}")
        if action not in valid_actions:
            raise ValueError(f"Invalid action: {action}")

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO KnowledgeAuditLog (knowledge_type, action, details, reviewer_id, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (knowledge_type, action, Json(details), reviewer_id),
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 出版社记录
    # =====================================================================

    def upsert_publisher_record(
        self,
        publisher: str,
        era_group: str,
        pages_processed: int = 0,
        dispute_rate: float = 0.0,
        accuracy_bonus: float = 0.0,
    ) -> int:
        """
        插入或更新出版社记录

        Args:
            publisher: 出版社名称
            era_group: 时代分组
            pages_processed: 已处理页数
            dispute_rate: 争议率
            accuracy_bonus: 准确率加成

        Returns:
            int: 记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO PublisherAccuracyRecord (publisher, era_group, pages_processed,
                                                      dispute_rate, accuracy_bonus, last_updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (publisher, era_group) DO UPDATE
                SET pages_processed = PublisherAccuracyRecord.pages_processed + EXCLUDED.pages_processed,
                    dispute_rate = EXCLUDED.dispute_rate,
                    accuracy_bonus = EXCLUDED.accuracy_bonus,
                    last_updated_at = NOW()
                RETURNING id
                """,
                (publisher, era_group, pages_processed, dispute_rate, accuracy_bonus),
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 用户权限管理
    # =====================================================================

    def create_user(self, username: str, password_hash: str, role_id: int, email: Optional[str] = None) -> int:
        """
        创建用户

        Args:
            username: 用户名
            password_hash: 密码哈希（bcrypt）
            role_id: 角色 ID
            email: 邮箱

        Returns:
            int: 用户 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO UserAccount (username, password_hash, role_id, email, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (username, password_hash, role_id, email),
            )
            result = cursor.fetchone()
            return result['id']

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """通过用户名获取用户信息"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.username, u.password_hash, u.role_id, u.email,
                       r.name as role_name
                FROM UserAccount u
                JOIN Role r ON u.role_id = r.id
                WHERE u.username = %s
                """,
                (username,),
            )
            result = cursor.fetchone()
            return dict(result) if result else None

    def get_user_permissions(self, role_id: int) -> List[Dict[str, Any]]:
        """获取角色的权限列表"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, resource, action FROM Permission WHERE role_id = %s",
                (role_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_roles(self) -> List[Dict[str, Any]]:
        """列出所有角色"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT id, name, description FROM Role ORDER BY id")
            return [dict(row) for row in cursor.fetchall()]

    # =====================================================================
    # 系统配置管理
    # =====================================================================

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        获取系统配置项

        Args:
            key: 配置键
            default: 默认值

        Returns:
            配置值，如果不存在则返回 default
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT value FROM Config WHERE key = %s",
                (key,),
            )
            result = cursor.fetchone()
            if result:
                return result['value']
            return default

    def set_config(self, key: str, value: Any, description: Optional[str] = None) -> bool:
        """
        设置系统配置项

        Args:
            key: 配置键
            value: 配置值（将被 JSON 序列化）
            description: 配置说明

        Returns:
            bool: 是否设置成功
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO Config (key, value, description, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    description = COALESCE(EXCLUDED.description, Config.description),
                    updated_at = NOW()
                """,
                (key, Json(value), description),
            )
            return cursor.rowcount > 0

    def list_configs(self) -> List[Dict[str, Any]]:
        """列出所有配置项"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, key, value, description, updated_at FROM Config ORDER BY key"
            )
            return [dict(row) for row in cursor.fetchall()]

    # =====================================================================
    # 最终文档记录
    # =====================================================================

    def create_final_document(
        self,
        book_registry_id: int,
        file_type: str,
        file_path: str,
        sha256: Optional[str] = None,
    ) -> int:
        """
        创建最终文档记录

        Args:
            book_registry_id: 书籍注册 ID
            file_type: 文件类型
            file_path: 文件路径
            sha256: SHA256 哈希

        Returns:
            int: 记录 ID
        """
        valid_types = {'txt', 'xml', 'json', 'pdf', 'epub'}
        if file_type not in valid_types:
            raise ValueError(f"Invalid file_type: {file_type}")

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO FinalDocumentRecord (book_registry_id, file_type, file_path, sha256, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, file_type, file_path, sha256),
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 归档管理
    # =====================================================================

    def archive_line_correction(
        self,
        book_registry_id: int,
        original_line_id: int,
        original_text: Optional[str] = None,
        corrected_text: Optional[str] = None,
        corrected_by: Optional[str] = None,
        correction_stage: str = 'auto',
    ) -> int:
        """
        归档行校正记录

        Args:
            book_registry_id: 书籍注册 ID
            original_line_id: 原始行 ID
            original_text: 原始文本
            corrected_text: 校正后文本
            corrected_by: 校正者
            correction_stage: 校正阶段

        Returns:
            int: 归档记录 ID
        """
        valid_stages = {'auto', 'llm', 'human', 'golden'}
        if correction_stage not in valid_stages:
            raise ValueError(f"Invalid correction_stage: {correction_stage}")

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO LineCorrectionArchive (book_registry_id, original_line_id,
                                                    original_text, corrected_text, corrected_by,
                                                    correction_stage, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, original_line_id, original_text,
                 corrected_text, corrected_by, correction_stage),
            )
            result = cursor.fetchone()
            return result['id']

    def archive_ocr_line_result(
        self,
        book_registry_id: int,
        original_line_id: int,
        engine_name: str,
        raw_text: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> int:
        """
        归档 OCR 行结果

        Args:
            book_registry_id: 书籍注册 ID
            original_line_id: 原始行 ID
            engine_name: 引擎名称
            raw_text: 原始文本
            confidence: 置信度

        Returns:
            int: 归档记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO OCRLineResultArchive (book_registry_id, original_line_id,
                                                   engine_name, raw_text, confidence, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, original_line_id, engine_name, raw_text, confidence),
            )
            result = cursor.fetchone()
            return result['id']

    def archive_content_tree(self, book_registry_id: int, content_tree: Dict[str, Any]) -> int:
        """
        归档内容树

        Args:
            book_registry_id: 书籍注册 ID
            content_tree: 内容树字典

        Returns:
            int: 归档记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO BookContentTree (book_registry_id, content_tree, created_at)
                VALUES (%s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, Json(content_tree)),
            )
            result = cursor.fetchone()
            return result['id']

    def log_page_render(
        self,
        book_registry_id: int,
        page_num: int,
        render_duration_ms: Optional[int] = None,
        cache_hit: bool = False,
    ) -> int:
        """
        记录页面渲染日志

        Args:
            book_registry_id: 书籍注册 ID
            page_num: 页码
            render_duration_ms: 渲染耗时（毫秒）
            cache_hit: 是否缓存命中

        Returns:
            int: 日志记录 ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO PageRenderLog (book_registry_id, page_num, render_duration_ms, cache_hit, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (book_registry_id, page_num, render_duration_ms, cache_hit),
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 方剂组成归档
    # =====================================================================

    def archive_formula_composition(
        self,
        book_registry_id: int,
        formula_uuid: str,
        formula_name: str,
        page_num: int,
        **kwargs,
    ) -> int:
        """
        归档方剂组成

        Args:
            book_registry_id: 书籍注册 ID
            formula_uuid: 方剂 UUID
            formula_name: 方剂名称
            page_num: 页码
            **kwargs: 可选字段

        Returns:
            int: 归档记录 ID
        """
        fields = {
            'book_registry_id': book_registry_id,
            'formula_uuid': formula_uuid,
            'formula_name': formula_name,
            'page_num': page_num,
        }
        optional_fields = [
            'cross_page', 'pages', 'context_reference_type',
            'referenced_formula_uuid', 'root_formula_id', 'context_description',
        ]
        for field in optional_fields:
            if field in kwargs:
                fields[field] = kwargs[field]

        columns = list(fields.keys())
        placeholders = ["%s"] * len(columns)
        values = list(fields.values())

        # 处理 JSONB 字段
        if 'pages' in fields and isinstance(fields['pages'], (list, dict)):
            values[columns.index('pages')] = Json(fields['pages'])

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO FormulaCompositionArchive ({', '.join(columns)}, created_at)
                VALUES ({', '.join(placeholders)}, NOW())
                RETURNING id
                """,
                values,
            )
            result = cursor.fetchone()
            return result['id']

    def archive_formula_ingredient(
        self,
        book_registry_id: int,
        formula_composition_id: int,
        herb_name: str,
        **kwargs,
    ) -> int:
        """
        归档方剂成分

        Args:
            book_registry_id: 书籍注册 ID
            formula_composition_id: 方剂组成归档 ID
            herb_name: 药材名
            **kwargs: 可选字段

        Returns:
            int: 归档记录 ID
        """
        fields = {
            'book_registry_id': book_registry_id,
            'formula_composition_id': formula_composition_id,
            'herb_name': herb_name,
        }
        optional_fields = [
            'herb_name_standard', 'dosage_value', 'dosage_value_numeric',
            'dosage_unit', 'processing_method', 'position_in_paragraph',
            'char_span_start', 'char_span_end', 'herb_ocr_pattern_id',
            'validation_status',
        ]
        for field in optional_fields:
            if field in kwargs:
                fields[field] = kwargs[field]

        columns = list(fields.keys())
        placeholders = ["%s"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO FormulaIngredientArchive ({', '.join(columns)}, created_at)
                VALUES ({', '.join(placeholders)}, NOW())
                RETURNING id
                """,
                values,
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 中药名 OCR 范式库
    # =====================================================================

    def create_herb_ocr_pattern(
        self,
        correct_herb: str,
        ocr_error_pattern: str,
        error_type: str = 'similar_glyph',
        **kwargs,
    ) -> int:
        """
        创建中药名 OCR 范式

        Args:
            correct_herb: 正确药材名
            ocr_error_pattern: OCR 错误模式
            error_type: 错误类型
            **kwargs: 可选字段

        Returns:
            int: 范式记录 ID
        """
        valid_types = {'similar_glyph', 'stroke_error', 'component_swap', 'split_merge', 'other'}
        if error_type not in valid_types:
            raise ValueError(f"Invalid error_type: {error_type}")

        fields = {
            'correct_herb': correct_herb,
            'ocr_error_pattern': ocr_error_pattern,
            'error_type': error_type,
        }
        optional_fields = [
            'toxicity_level', 'source_books', 'evidence_count',
            'auto_discovered', 'confidence_score', 'review_status',
        ]
        for field in optional_fields:
            if field in kwargs:
                fields[field] = kwargs[field]

        columns = list(fields.keys())
        placeholders = ["%s"] * len(columns)
        values = list(fields.values())

        # 处理 JSONB 字段
        if 'source_books' in fields and isinstance(fields['source_books'], (list, dict)):
            values[columns.index('source_books')] = Json(fields['source_books'])

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO HerbOCRPattern ({', '.join(columns)}, created_at, updated_at)
                VALUES ({', '.join(placeholders)}, NOW(), NOW())
                RETURNING id
                """,
                values,
            )
            result = cursor.fetchone()
            return result['id']

    def find_herb_ocr_patterns(
        self,
        correct_herb: Optional[str] = None,
        ocr_error_pattern: Optional[str] = None,
        error_type: Optional[str] = None,
        review_status: str = 'approved',
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查找中药名 OCR 范式

        Args:
            correct_herb: 正确药材名过滤
            ocr_error_pattern: OCR 错误模式过滤
            error_type: 错误类型过滤
            review_status: 审核状态过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: OCR 范式列表
        """
        conditions = []
        params = []

        if correct_herb:
            conditions.append("correct_herb = %s")
            params.append(correct_herb)
        if ocr_error_pattern:
            conditions.append("ocr_error_pattern = %s")
            params.append(ocr_error_pattern)
        if error_type:
            conditions.append("error_type = %s")
            params.append(error_type)
        if review_status:
            conditions.append("review_status = %s")
            params.append(review_status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, correct_herb, ocr_error_pattern, error_type, toxicity_level,
                       source_books, evidence_count, auto_discovered, confidence_score,
                       review_status, is_permanent, created_at, updated_at
                FROM HerbOCRPattern
                {where_clause}
                ORDER BY confidence_score DESC, evidence_count DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    # =====================================================================
    # 经络穴位 OCR 范式库
    # =====================================================================

    def create_meridian_point_pattern(
        self,
        correct_name: str,
        ocr_error_pattern: str,
        entity_type: str = 'acupoint',
        **kwargs,
    ) -> int:
        """
        创建经络穴位 OCR 范式

        Args:
            correct_name: 正确穴位名
            ocr_error_pattern: OCR 错误模式
            entity_type: 实体类型
            **kwargs: 可选字段

        Returns:
            int: 范式记录 ID
        """
        valid_types = {'acupoint', 'meridian', 'extra_point', 'other'}
        if entity_type not in valid_types:
            raise ValueError(f"Invalid entity_type: {entity_type}")

        fields = {
            'correct_name': correct_name,
            'ocr_error_pattern': ocr_error_pattern,
            'entity_type': entity_type,
        }
        for key in ['meridian_belonging', 'body_region', 'source_books',
                    'evidence_count', 'auto_discovered', 'confidence_score', 'review_status']:
            if key in kwargs:
                fields[key] = kwargs[key]

        columns = list(fields.keys())
        placeholders = ["%s"] * len(columns)
        values = list(fields.values())

        if 'source_books' in fields and isinstance(fields['source_books'], (list, dict)):
            values[columns.index('source_books')] = Json(fields['source_books'])

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO MeridianPointOCRPattern ({', '.join(columns)}, created_at, updated_at)
                VALUES ({', '.join(placeholders)}, NOW(), NOW())
                RETURNING id
                """,
                values,
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 上下文衔接模式库
    # =====================================================================

    def create_formula_context_pattern(
        self,
        pattern_text: str,
        pattern_type: str = 'heading_prefix',
        **kwargs,
    ) -> int:
        """
        创建方剂上下文衔接模式

        Args:
            pattern_text: 模式文本
            pattern_type: 模式类型
            **kwargs: 可选字段

        Returns:
            int: 模式记录 ID
        """
        valid_types = {
            'heading_prefix', 'ingredient_list', 'dosage_suffix',
            'cross_reference', 'modification_note', 'other',
        }
        if pattern_type not in valid_types:
            raise ValueError(f"Invalid pattern_type: {pattern_type}")

        fields = {
            'pattern_text': pattern_text,
            'pattern_type': pattern_type,
        }
        for key in ['regex', 'example', 'discovered_count', 'source_books',
                    'auto_discovered', 'review_status']:
            if key in kwargs:
                fields[key] = kwargs[key]

        columns = list(fields.keys())
        placeholders = ["%s"] * len(columns)
        values = list(fields.values())

        if 'source_books' in fields and isinstance(fields['source_books'], (list, dict)):
            values[columns.index('source_books')] = Json(fields['source_books'])

        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO FormulaContextPattern ({', '.join(columns)}, created_at, updated_at)
                VALUES ({', '.join(placeholders)}, NOW(), NOW())
                RETURNING id
                """,
                values,
            )
            result = cursor.fetchone()
            return result['id']

    # =====================================================================
    # 统计与汇总
    # =====================================================================

    def get_book_stats(self, book_registry_id: int) -> Dict[str, Any]:
        """
        获取书籍的完整统计信息

        Args:
            book_registry_id: 书籍注册 ID

        Returns:
            Dict: 统计信息汇总
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM LineCorrectionArchive WHERE book_registry_id = %s) as correction_count,
                    (SELECT COUNT(*) FROM OCRLineResultArchive WHERE book_registry_id = %s) as ocr_result_count,
                    (SELECT COUNT(*) FROM PageRenderLog WHERE book_registry_id = %s) as render_count,
                    (SELECT COUNT(*) FROM FormulaCompositionArchive WHERE book_registry_id = %s) as formula_count,
                    (SELECT COUNT(*) FROM NegationAlertLog WHERE line_id IN (
                        SELECT original_line_id FROM LineCorrectionArchive WHERE book_registry_id = %s
                    )) as negation_alert_count
                """,
                (book_registry_id,) * 5,
            )
            result = cursor.fetchone()
            return dict(result) if result else {}
