"""
SQLite 书籍库连接层 - BookDB 类

每本待处理书籍对应一个独立的 SQLite 数据库。
封装所有 SQLite 操作，使用 context manager 管理事务和连接。
提供页面、段落、行、引擎结果、校对记录、方剂组成等完整数据访问接口。

所有方法均使用参数化查询防止 SQL 注入。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 数据模型类
# =============================================================================

@dataclass
class PageRecord:
    """页面记录数据类"""
    id: int
    page_num: int
    pdf_page_num: int
    page_type: str = 'body'
    quality_score: Optional[float] = None
    chapter_id: Optional[int] = None
    body_region: Optional[str] = None
    page_number_printed: Optional[int] = None
    page_number_logical: Optional[int] = None


@dataclass
class LineRecord:
    """行记录数据类"""
    id: int
    paragraph_id: Optional[int]
    page_id: int
    sequence_in_paragraph: int
    final_text: Optional[str] = None
    raw_vote_text: Optional[str] = None
    llm_corrected_text: Optional[str] = None
    glyph_verified_text: Optional[str] = None
    human_final_text: Optional[str] = None
    confidence: Optional[float] = None
    disputed: int = 0
    page_num: int = 0
    verification_status: str = 'pending'
    heading_level: Optional[int] = None
    is_cross_page_line: int = 0


@dataclass
class ParagraphRecord:
    """段落记录数据类"""
    id: int
    page_id: int
    sequence_in_page: int
    node_type: str = 'body'
    is_heading: int = 0
    heading_level: Optional[int] = None
    is_formula_paragraph: int = 0
    content_node_id: Optional[int] = None
    verification_status: str = 'pending'


# =============================================================================
# BookDB 主类
# =============================================================================

class BookDB:
    """
    SQLite 书籍库管理类

    每本待处理书籍对应一个独立的 SQLite 数据库文件。
    使用连接级锁保证线程安全，通过 context manager 管理事务。

    Attributes:
        db_path: SQLite 数据库文件路径
        _local: threading.local 存储线程本地连接
    """

    def __init__(self, db_path: str) -> None:
        """
        初始化 BookDB

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._local = threading.local()
        self._ensure_directory()
        self._initialize_database()

    def _ensure_directory(self) -> None:
        """确保数据库文件所在目录存在"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.info("Created database directory: %s", db_dir)

    def _initialize_database(self) -> None:
        """初始化数据库（启用 WAL 模式）"""
        with self.get_connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
            logger.debug("Database initialized with WAL mode: %s", self.db_path)

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        """
        获取数据库连接的 context manager

        Yields:
            sqlite3.Connection: SQLite 连接对象
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            if conn is not None:
                conn.rollback()
            logger.error("SQLite error on %s: %s", self.db_path, e)
            raise
        finally:
            if conn is not None:
                conn.close()

    @contextmanager
    def get_cursor(self) -> Iterator[sqlite3.Cursor]:
        """
        获取数据库游标的 context manager

        Yields:
            sqlite3.Cursor: SQLite 游标
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            yield cursor

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """
        显式事务 context manager

        用于需要多条语句在同一事务中执行的场景。

        Yields:
            sqlite3.Connection: 事务中的连接对象

        Example:
            with book_db.transaction() as conn:
                conn.execute("INSERT INTO ...")
                conn.execute("UPDATE ...")
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            if conn is not None:
                conn.rollback()
            logger.error("Transaction error on %s: %s", self.db_path, e)
            raise
        finally:
            if conn is not None:
                conn.close()

    def execute_script(self, sql_script: str) -> None:
        """
        执行 SQL 脚本

        Args:
            sql_script: SQL 脚本内容
        """
        with self.get_connection() as conn:
            conn.executescript(sql_script)
            logger.info("SQL script executed on %s", self.db_path)

    def initialize_schema(self, schema_sql: Optional[str] = None) -> None:
        """
        初始化数据库 Schema

        Args:
            schema_sql: Schema SQL 内容，如果为 None 则读取默认迁移文件
        """
        if schema_sql is None:
            schema_path = os.path.join(
                os.path.dirname(__file__), '..', '..', '..',
                'migrations', '002_book_schema.sql',
            )
            if not os.path.exists(schema_path):
                raise FileNotFoundError(f"Schema file not found: {schema_path}")
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_sql = f.read()

        self.execute_script(schema_sql)
        logger.info("Database schema initialized: %s", self.db_path)

    def close(self) -> None:
        """关闭数据库连接（释放线程本地存储）"""
        if hasattr(self._local, 'connection') and self._local.connection is not None:
            self._local.connection.close()
            self._local.connection = None
            logger.debug("Database connection closed: %s", self.db_path)

    def __enter__(self) -> 'BookDB':
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        self.close()

    # =====================================================================
    # 工具方法
    # =====================================================================

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        """将 sqlite3.Row 转换为字典"""
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _rows_to_dicts(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
        """将 sqlite3.Row 列表转换为字典列表"""
        return [BookDB._row_to_dict(row) for row in rows if row is not None]

    # =====================================================================
    # 书籍元数据管理
    # =====================================================================

    def set_book_meta(self, **kwargs: object) -> int:
        """
        设置书籍元数据

        Args:
            title: 书名（必需）
            author: 作者
            publisher: 出版社
            pub_year: 出版年份
            edition: 版次
            isbn: ISBN
            source_declaration: 来源声明

        Returns:
            int: 元数据记录 ID（始终为 1，单条记录）
        """
        if 'title' not in kwargs:
            raise ValueError("title is required")

        allowed = {'title', 'author', 'publisher', 'pub_year', 'edition', 'isbn', 'source_declaration'}
        fields = {k: v for k, v in kwargs.items() if k in allowed}

        # 检查是否已存在
        with self.get_cursor() as cursor:
            cursor.execute("SELECT id FROM BookMeta LIMIT 1")
            existing = cursor.fetchone()

            if existing:
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                values = list(fields.values()) + [existing['id']]
                cursor.execute(f"UPDATE BookMeta SET {set_clause} WHERE id = ?", values)
                return existing['id']
            else:
                columns = list(fields.keys())
                placeholders = ["?"] * len(columns)
                values = list(fields.values())
                cursor.execute(
                    f"INSERT INTO BookMeta ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                    values,
                )
                return cursor.lastrowid or 1

    def get_book_meta(self) -> Optional[Dict[str, Any]]:
        """获取书籍元数据"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM BookMeta LIMIT 1")
            result = cursor.fetchone()
            return self._row_to_dict(result)

    # =====================================================================
    # 目录结构管理（树形）
    # =====================================================================

    def create_content_node(
        self,
        title: str,
        node_type: str,
        level: int = 0,
        parent_id: Optional[int] = None,
        order_seq: int = 0,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
        **kwargs: object,
    ) -> int:
        """
        创建目录节点

        Args:
            title: 节点标题
            node_type: 节点类型
            level: 层级深度
            parent_id: 父节点 ID
            order_seq: 同级排序序号
            start_page: 起始页码
            end_page: 结束页码
            **kwargs: 可选字段

        Returns:
            int: 节点 ID
        """
        valid_types = {'book', 'part', 'chapter', 'section', 'subsection', 'paragraph'}
        if node_type not in valid_types:
            raise ValueError(f"Invalid node_type: {node_type}")

        fields = {
            'title': title, 'node_type': node_type, 'level': level,
            'parent_id': parent_id, 'order_seq': order_seq,
            'start_page': start_page, 'end_page': end_page,
        }
        for key in ['source', 'confidence', 'bbox_y2']:
            if key in kwargs:
                fields[key] = kwargs[key]

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO ContentNode ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_content_tree(self, root_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取完整内容树

        Args:
            root_id: 根节点 ID，None 则获取整棵树

        Returns:
            List[Dict]: 扁平化的节点列表（按层级排序）
        """
        with self.get_cursor() as cursor:
            if root_id:
                cursor.execute(
                    """
                    WITH RECURSIVE tree AS (
                        SELECT *, 0 as depth FROM ContentNode WHERE id = ?
                        UNION ALL
                        SELECT cn.*, tree.depth + 1
                        FROM ContentNode cn
                        JOIN tree ON cn.parent_id = tree.id
                    )
                    SELECT * FROM tree ORDER BY depth, order_seq
                    """,
                    (root_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM ContentNode
                    ORDER BY level, order_seq
                    """
                )
            return self._rows_to_dicts(cursor.fetchall())

    def get_content_node(self, node_id: int) -> Optional[Dict[str, Any]]:
        """获取单个目录节点"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM ContentNode WHERE id = ?", (node_id,))
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def update_content_node_pages(self, node_id: int, start_page: int, end_page: int) -> bool:
        """更新目录节点的页码范围"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE ContentNode SET start_page = ?, end_page = ? WHERE id = ?",
                (start_page, end_page, node_id),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 页面管理
    # =====================================================================

    def create_page(
        self,
        page_num: int,
        pdf_page_num: int,
        page_type: str = 'body',
        chapter_id: Optional[int] = None,
        quality_score: Optional[float] = None,
        body_region: Optional[Dict[str, Any]] = None,
        page_number_printed: Optional[int] = None,
        page_number_logical: Optional[int] = None,
    ) -> int:
        """
        创建页面记录

        Args:
            page_num: 逻辑页码
            pdf_page_num: PDF 物理页码
            page_type: 页面类型
            chapter_id: 章节节点 ID
            quality_score: 页面质量评分
            body_region: 正文区域坐标
            page_number_printed: 印刷页码
            page_number_logical: 逻辑页码

        Returns:
            int: 页面 ID
        """
        valid_types = {'cover', 'toc', 'preface', 'body', 'index', 'appendix', 'blank'}
        if page_type not in valid_types:
            raise ValueError(f"Invalid page_type: {page_type}")

        body_region_json = json.dumps(body_region) if body_region else None

        with self.get_cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO Page (page_num, pdf_page_num, page_type, chapter_id,
                                      quality_score, body_region, page_number_printed, page_number_logical)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (page_num, pdf_page_num, page_type, chapter_id,
                     quality_score, body_region_json, page_number_printed, page_number_logical),
                )
                return cursor.lastrowid or 0
            except sqlite3.IntegrityError:
                logger.warning("Page %d already exists, returning existing ID", page_num)
                cursor.execute("SELECT id FROM Page WHERE page_num = ?", (page_num,))
                result = cursor.fetchone()
                return result['id'] if result else 0

    def get_page(self, page_id: int) -> Optional[Dict[str, Any]]:
        """获取页面详情"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM Page WHERE id = ?", (page_id,))
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def get_page_by_num(self, page_num: int) -> Optional[Dict[str, Any]]:
        """按页码获取页面"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM Page WHERE page_num = ?", (page_num,))
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def list_pages(self, page_type: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        列出页面

        Args:
            page_type: 按类型过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 页面列表
        """
        with self.get_cursor() as cursor:
            if page_type:
                cursor.execute(
                    "SELECT * FROM Page WHERE page_type = ? ORDER BY page_num LIMIT ?",
                    (page_type, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM Page ORDER BY page_num LIMIT ?",
                    (limit,),
                )
            return self._rows_to_dicts(cursor.fetchall())

    def update_page_quality(self, page_id: int, quality_score: float) -> bool:
        """更新页面质量评分"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Page SET quality_score = ? WHERE id = ?",
                (quality_score, page_id),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 段落管理
    # =====================================================================

    def create_paragraph(
        self,
        page_id: int,
        sequence_in_page: int,
        **kwargs: object,
    ) -> int:
        """
        创建段落

        Args:
            page_id: 页面 ID
            sequence_in_page: 页内段落序号
            **kwargs: 可选字段

        Returns:
            int: 段落 ID
        """
        fields = {
            'page_id': page_id,
            'sequence_in_page': sequence_in_page,
        }
        optional_fields = [
            'content_node_id', 'audit_source', 'cross_page_group_id',
            'cross_page_sequence', 'local_llm_raw_output', 'cloud_llm_raw_output',
            'verification_status', 'node_type', 'is_heading', 'heading_level',
            'is_formula_paragraph', 'page_position',
        ]
        for field in optional_fields:
            if field in kwargs:
                fields[field] = kwargs[field]

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO Paragraph ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_paragraph(self, paragraph_id: int) -> Optional[Dict[str, Any]]:
        """获取段落详情"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM Paragraph WHERE id = ?", (paragraph_id,))
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def list_paragraphs_by_page(self, page_id: int) -> List[Dict[str, Any]]:
        """获取页面的所有段落"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM Paragraph WHERE page_id = ? ORDER BY sequence_in_page",
                (page_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def update_paragraph_verification(
        self,
        paragraph_id: int,
        verification_status: str,
    ) -> bool:
        """更新段落验证状态"""
        valid_statuses = {'pending', 'verified', 'disputed', 'corrected'}
        if verification_status not in valid_statuses:
            raise ValueError(f"Invalid verification_status: {verification_status}")

        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Paragraph SET verification_status = ? WHERE id = ?",
                (verification_status, paragraph_id),
            )
            return cursor.rowcount > 0

    def mark_formula_paragraph(self, paragraph_id: int, is_formula: bool = True) -> bool:
        """标记/取消方剂段落"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Paragraph SET is_formula_paragraph = ? WHERE id = ?",
                (1 if is_formula else 0, paragraph_id),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 行管理（文本权威源）
    # =====================================================================

    def create_line(
        self,
        page_id: int,
        sequence_in_paragraph: int,
        page_num: int,
        paragraph_id: Optional[int] = None,
        **kwargs: object,
    ) -> int:
        """
        创建行记录

        Args:
            page_id: 页面 ID
            sequence_in_paragraph: 段落内行序号
            page_num: 页码（冗余存储便于查询）
            paragraph_id: 段落 ID
            **kwargs: 可选字段

        Returns:
            int: 行 ID
        """
        fields = {
            'page_id': page_id,
            'sequence_in_paragraph': sequence_in_paragraph,
            'page_num': page_num,
            'paragraph_id': paragraph_id,
        }
        optional_fields = [
            'bbox', 'final_text', 'raw_vote_text', 'llm_corrected_text',
            'glyph_verified_text', 'human_final_text', 'auto_corrected',
            'confidence', 'disputed', 'char_level_json', 'is_cross_page_line',
            'heading_level', 'missing_char_alert', 'verification_status',
        ]
        for field in optional_fields:
            if field in kwargs:
                value = kwargs[field]
                if field in ('bbox', 'char_level_json', 'missing_char_alert') and isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                fields[field] = value

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO Line ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_line(self, line_id: int) -> Optional[Dict[str, Any]]:
        """获取行详情"""
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM Line WHERE id = ?", (line_id,))
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def get_lines_by_paragraph(self, paragraph_id: int) -> List[Dict[str, Any]]:
        """获取段落的所有行"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM Line WHERE paragraph_id = ? ORDER BY sequence_in_paragraph",
                (paragraph_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def get_lines_by_page(self, page_id: int) -> List[Dict[str, Any]]:
        """获取页面的所有行"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM Line WHERE page_id = ? ORDER BY sequence_in_paragraph",
                (page_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def update_line_final_text(self, line_id: int, final_text: str) -> bool:
        """更新行的最终文本"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Line SET final_text = ? WHERE id = ?",
                (final_text, line_id),
            )
            return cursor.rowcount > 0

    def update_line_verification_status(self, line_id: int, status: str) -> bool:
        """更新行的验证状态"""
        valid_statuses = {
            'pending', 'vote_ok', 'llm_corrected', 'glyph_verified',
            'human_confirmed', 'disputed', 'golden_standard',
        }
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}")

        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Line SET verification_status = ? WHERE id = ?",
                (status, line_id),
            )
            return cursor.rowcount > 0

    def mark_line_disputed(self, line_id: int, disputed: bool = True) -> bool:
        """标记/取消行的争议状态"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE Line SET disputed = ? WHERE id = ?",
                (1 if disputed else 0, line_id),
            )
            return cursor.rowcount > 0

    def get_page_text(self, page_num: int) -> str:
        """
        获取页面的完整文本（由行拼接）

        Args:
            page_num: 页码

        Returns:
            str: 页面完整文本
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT final_text FROM Line
                WHERE page_num = ? AND final_text IS NOT NULL
                ORDER BY sequence_in_paragraph
                """,
                (page_num,),
            )
            lines = [row['final_text'] for row in cursor.fetchall() if row['final_text']]
            return '\n'.join(lines)

    def get_paragraph_text(self, paragraph_id: int) -> str:
        """
        获取段落的完整文本（由行拼接）

        Args:
            paragraph_id: 段落 ID

        Returns:
            str: 段落完整文本
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT final_text FROM Line
                WHERE paragraph_id = ? AND final_text IS NOT NULL
                ORDER BY sequence_in_paragraph
                """,
                (paragraph_id,),
            )
            lines = [row['final_text'] for row in cursor.fetchall() if row['final_text']]
            return '\n'.join(lines)

    def search_lines(
        self,
        text_pattern: str,
        page_num: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        搜索包含特定文本的行

        Args:
            text_pattern: 文本匹配模式（% 通配符）
            page_num: 页码过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 行列表
        """
        with self.get_cursor() as cursor:
            if page_num is not None:
                cursor.execute(
                    """
                    SELECT * FROM Line
                    WHERE final_text LIKE ? AND page_num = ?
                    ORDER BY page_num, sequence_in_paragraph
                    LIMIT ?
                    """,
                    (f"%{text_pattern}%", page_num, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM Line
                    WHERE final_text LIKE ?
                    ORDER BY page_num, sequence_in_paragraph
                    LIMIT ?
                    """,
                    (f"%{text_pattern}%", limit),
                )
            return self._rows_to_dicts(cursor.fetchall())

    def get_disputed_lines(self, page_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取有争议的行

        Args:
            page_num: 页码过滤

        Returns:
            List[Dict]: 争议行列表
        """
        with self.get_cursor() as cursor:
            if page_num is not None:
                cursor.execute(
                    "SELECT * FROM Line WHERE disputed = 1 AND page_num = ? ORDER BY sequence_in_paragraph",
                    (page_num,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM Line WHERE disputed = 1 ORDER BY page_num, sequence_in_paragraph"
                )
            return self._rows_to_dicts(cursor.fetchall())

    # =====================================================================
    # 引擎结果管理
    # =====================================================================

    def create_engine_result(
        self,
        line_id: int,
        engine_name: str,
        raw_text: Optional[str] = None,
        confidence: Optional[float] = None,
        char_level_json: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        创建引擎识别结果

        Args:
            line_id: 行 ID
            engine_name: 引擎名称
            raw_text: 原始识别文本
            confidence: 引擎置信度
            char_level_json: 字符级结果

        Returns:
            int: 结果记录 ID
        """
        char_level = json.dumps(char_level_json, ensure_ascii=False) if char_level_json else None

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO LineEngineResult (line_id, engine_name, raw_text, confidence, char_level_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (line_id, engine_name, raw_text, confidence, char_level),
            )
            return cursor.lastrowid or 0

    def get_engine_results(self, line_id: int) -> List[Dict[str, Any]]:
        """获取行的所有引擎结果"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM LineEngineResult WHERE line_id = ?",
                (line_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def get_engine_result_by_name(self, line_id: int, engine_name: str) -> Optional[Dict[str, Any]]:
        """获取指定引擎的行结果"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM LineEngineResult WHERE line_id = ? AND engine_name = ?",
                (line_id, engine_name),
            )
            result = cursor.fetchone()
            return self._row_to_dict(result)

    # =====================================================================
    # 校对记录管理
    # =====================================================================

    def create_proofread_record(
        self,
        line_id: int,
        original_text: str,
        corrected_text: str,
        corrected_by: str,
        correction_stage: str,
        **kwargs: object,
    ) -> int:
        """
        创建校对记录

        Args:
            line_id: 行 ID
            original_text: 原始文本
            corrected_text: 校正后文本
            corrected_by: 校正者
            correction_stage: 校正阶段
            **kwargs: 可选字段

        Returns:
            int: 校对记录 ID
        """
        valid_stages = {
            'auto', 'llm', 'human_level1', 'human_level2',
            'human_final', 'reviewer', 'golden',
        }
        if correction_stage not in valid_stages:
            raise ValueError(f"Invalid correction_stage: {correction_stage}")

        fields = {
            'line_id': line_id,
            'original_text': original_text,
            'corrected_text': corrected_text,
            'corrected_by': corrected_by,
            'correction_stage': correction_stage,
        }
        for key in ['paragraph_id', 'dispute_type', 'reviewer_accuracy']:
            if key in kwargs:
                fields[key] = kwargs[key]

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO ProofreadRecord ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_proofread_records(self, line_id: int) -> List[Dict[str, Any]]:
        """获取行的所有校对记录"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM ProofreadRecord WHERE line_id = ? ORDER BY created_at",
                (line_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def get_correction_history(self, line_id: int) -> List[Dict[str, Any]]:
        """获取行的完整校正历史（按时间排序）"""
        return self.get_proofread_records(line_id)

    # =====================================================================
    # 图片索引管理
    # =====================================================================

    def create_image_index(
        self,
        image_type: str,
        image_path: str,
        page_id: Optional[int] = None,
        paragraph_id: Optional[int] = None,
        line_id: Optional[int] = None,
        **kwargs: object,
    ) -> int:
        """
        创建图片索引

        Args:
            image_type: 图片类型
            image_path: 图片文件路径
            page_id: 页面 ID
            paragraph_id: 段落 ID
            line_id: 行 ID
            **kwargs: 可选字段

        Returns:
            int: 图片索引 ID
        """
        valid_types = {
            'page_scan', 'line_crop', 'char_crop', 'figure', 'table',
            'formula_image', 'header_footer', 'other',
        }
        if image_type not in valid_types:
            raise ValueError(f"Invalid image_type: {image_type}")

        fields = {
            'image_type': image_type,
            'image_path': image_path,
            'page_id': page_id,
            'paragraph_id': paragraph_id,
            'line_id': line_id,
        }
        for key in ['file_size', 'sha256', 'is_transient']:
            if key in kwargs:
                fields[key] = kwargs[key]

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO ImageIndex ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_images_by_page(self, page_id: int, image_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取页面的图片"""
        with self.get_cursor() as cursor:
            if image_type:
                cursor.execute(
                    "SELECT * FROM ImageIndex WHERE page_id = ? AND image_type = ?",
                    (page_id, image_type),
                )
            else:
                cursor.execute(
                    "SELECT * FROM ImageIndex WHERE page_id = ?",
                    (page_id,),
                )
            return self._rows_to_dicts(cursor.fetchall())

    # =====================================================================
    # 字体模板管理
    # =====================================================================

    def create_font_template(
        self,
        page_id: int,
        font_type: str,
        sample_chars: Optional[Dict[str, Any]] = None,
        template_data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        创建字体模板

        Args:
            page_id: 页面 ID
            font_type: 字体类型
            sample_chars: 采样字符
            template_data: 模板数据

        Returns:
            int: 模板记录 ID
        """
        sample_chars_json = json.dumps(sample_chars, ensure_ascii=False) if sample_chars else None
        template_data_json = json.dumps(template_data, ensure_ascii=False) if template_data else None

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO FontTemplate (page_id, font_type, sample_chars, template_data)
                VALUES (?, ?, ?, ?)
                """,
                (page_id, font_type, sample_chars_json, template_data_json),
            )
            return cursor.lastrowid or 0

    # =====================================================================
    # MinerU 映射管理
    # =====================================================================

    def create_mineru_mapping(
        self,
        block_id: str,
        line_id: Optional[int],
        block_type: str,
        page_num: int,
        bbox: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        创建 MinerU 块映射

        Args:
            block_id: MinerU 块 ID
            line_id: 对应行 ID
            block_type: 块类型
            page_num: 页码
            bbox: 边界框坐标

        Returns:
            int: 映射记录 ID
        """
        bbox_json = json.dumps(bbox, ensure_ascii=False) if bbox else None

        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO MinerUBlockMapping (block_id, line_id, block_type, bbox, page_num)
                VALUES (?, ?, ?, ?, ?)
                """,
                (block_id, line_id, block_type, bbox_json, page_num),
            )
            return cursor.lastrowid or 0

    def get_mineru_mappings_by_page(self, page_num: int) -> List[Dict[str, Any]]:
        """获取页面的 MinerU 映射"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM MinerUBlockMapping WHERE page_num = ?",
                (page_num,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    # =====================================================================
    # 方剂组成管理
    # =====================================================================

    def create_formula_composition(
        self,
        book_registry_id: int,
        formula_uuid: str,
        formula_name: str,
        **kwargs: object,
    ) -> int:
        """
        创建方剂组成

        Args:
            book_registry_id: 书籍注册 ID
            formula_uuid: 方剂 UUID
            formula_name: 方剂名称
            **kwargs: 可选字段

        Returns:
            int: 方剂组成 ID
        """
        fields = {
            'book_registry_id': book_registry_id,
            'formula_uuid': formula_uuid,
            'formula_name': formula_name,
        }
        optional_fields = [
            'formula_name_variants', 'page_id', 'paragraph_id', 'formula_sequence',
            'context_reference_type', 'referenced_formula_id', 'referenced_formula_uuid',
            'root_formula_id', 'context_description', 'extraction_status',
            'cross_page_group_id',
        ]
        for field in optional_fields:
            if field in kwargs:
                value = kwargs[field]
                if field == 'formula_name_variants' and isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                fields[field] = value

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO FormulaComposition ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_formula_composition(self, formula_id: int) -> Optional[Dict[str, Any]]:
        """获取方剂组成详情"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM FormulaComposition WHERE id = ?",
                (formula_id,),
            )
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def get_formula_by_uuid(self, formula_uuid: str) -> Optional[Dict[str, Any]]:
        """通过 UUID 获取方剂"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM FormulaComposition WHERE formula_uuid = ?",
                (formula_uuid,),
            )
            result = cursor.fetchone()
            return self._row_to_dict(result)

    def list_formulas_by_page(self, page_id: int) -> List[Dict[str, Any]]:
        """获取页面的所有方剂"""
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM FormulaComposition WHERE page_id = ? ORDER BY formula_sequence",
                (page_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def update_formula_extraction_status(
        self,
        formula_id: int,
        extraction_status: str,
    ) -> bool:
        """更新方剂提取状态"""
        valid_statuses = {'pending', 'extracted', 'verified', 'disputed', 'approved'}
        if extraction_status not in valid_statuses:
            raise ValueError(f"Invalid extraction_status: {extraction_status}")

        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE FormulaComposition SET extraction_status = ? WHERE id = ?",
                (extraction_status, formula_id),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 方剂组成明细管理
    # =====================================================================

    def create_formula_ingredient(
        self,
        formula_composition_id: int,
        herb_name: str,
        **kwargs: object,
    ) -> int:
        """
        创建方剂成分

        Args:
            formula_composition_id: 方剂组成 ID
            herb_name: 药材名
            **kwargs: 可选字段

        Returns:
            int: 成分记录 ID
        """
        fields = {
            'formula_composition_id': formula_composition_id,
            'herb_name': herb_name,
        }
        optional_fields = [
            'herb_name_standard', 'dosage_value', 'dosage_value_numeric',
            'dosage_unit', 'processing_method', 'position_in_paragraph',
            'char_span_start', 'char_span_end', 'line_id', 'line_sequence',
            'is_added', 'is_copied_from_base', 'base_formula_id',
            'herb_ocr_pattern_id', 'validation_status', 'dosage_anchor',
        ]
        for field in optional_fields:
            if field in kwargs:
                value = kwargs[field]
                if field == 'dosage_anchor' and isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                fields[field] = value

        columns = list(fields.keys())
        placeholders = ["?"] * len(columns)
        values = list(fields.values())

        with self.get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO FormulaIngredient ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.lastrowid or 0

    def get_formula_ingredients(self, formula_composition_id: int) -> List[Dict[str, Any]]:
        """获取方剂的所有成分"""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM FormulaIngredient
                WHERE formula_composition_id = ?
                ORDER BY position_in_paragraph, line_sequence
                """,
                (formula_composition_id,),
            )
            return self._rows_to_dicts(cursor.fetchall())

    def update_ingredient_validation(
        self,
        ingredient_id: int,
        validation_status: str,
    ) -> bool:
        """更新成分验证状态"""
        valid_statuses = {
            'pending', 'valid', 'invalid', 'warning', 'dosage_error',
        }
        if validation_status not in valid_statuses:
            raise ValueError(f"Invalid validation_status: {validation_status}")

        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE FormulaIngredient SET validation_status = ? WHERE id = ?",
                (validation_status, ingredient_id),
            )
            return cursor.rowcount > 0

    # =====================================================================
    # 批量操作
    # =====================================================================

    def batch_insert_lines(self, lines: List[Dict[str, Any]]) -> int:
        """
        批量插入行

        Args:
            lines: 行数据字典列表

        Returns:
            int: 插入的行数
        """
        if not lines:
            return 0

        # 确定所有字段
        all_fields = set()
        for line in lines:
            all_fields.update(line.keys())

        columns = sorted(all_fields)
        placeholders = ["?"] * len(columns)

        with self.transaction() as conn:
            cursor = conn.cursor()
            values_list = []
            for line in lines:
                row_values = []
                for col in columns:
                    value = line.get(col)
                    if col in ('bbox', 'char_level_json', 'missing_char_alert') and isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False)
                    row_values.append(value)
                values_list.append(row_values)

            cursor.executemany(
                f"INSERT INTO Line ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values_list,
            )
            inserted = cursor.rowcount
            logger.info("Batch inserted %d lines", inserted)
            return inserted

    def batch_insert_engine_results(self, results: List[Dict[str, Any]]) -> int:
        """
        批量插入引擎结果

        Args:
            results: 引擎结果字典列表

        Returns:
            int: 插入的记录数
        """
        if not results:
            return 0

        for result in results:
            if 'char_level_json' in result and isinstance(result['char_level_json'], (dict, list)):
                result['char_level_json'] = json.dumps(result['char_level_json'], ensure_ascii=False)

        columns = ['line_id', 'engine_name', 'raw_text', 'confidence', 'char_level_json']
        placeholders = ["?"] * len(columns)

        values = [
            [result.get(col) for col in columns]
            for result in results
        ]

        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                f"INSERT INTO LineEngineResult ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                values,
            )
            return cursor.rowcount

    # =====================================================================
    # 统计查询
    # =====================================================================

    def get_book_stats(self) -> Dict[str, Any]:
        """
        获取书籍统计信息

        Returns:
            Dict: 统计信息
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as page_count FROM Page")
            page_count = cursor.fetchone()['page_count']

            cursor.execute("SELECT COUNT(*) as paragraph_count FROM Paragraph")
            paragraph_count = cursor.fetchone()['paragraph_count']

            cursor.execute("SELECT COUNT(*) as line_count FROM Line")
            line_count = cursor.fetchone()['line_count']

            cursor.execute("SELECT COUNT(*) as disputed_count FROM Line WHERE disputed = 1")
            disputed_count = cursor.fetchone()['disputed_count']

            cursor.execute("SELECT COUNT(*) as formula_count FROM FormulaComposition")
            formula_count = cursor.fetchone()['formula_count']

            cursor.execute("SELECT COUNT(*) as ingredient_count FROM FormulaIngredient")
            ingredient_count = cursor.fetchone()['ingredient_count']

            cursor.execute("SELECT COUNT(*) as proofread_count FROM ProofreadRecord")
            proofread_count = cursor.fetchone()['proofread_count']

            return {
                'page_count': page_count,
                'paragraph_count': paragraph_count,
                'line_count': line_count,
                'disputed_count': disputed_count,
                'formula_count': formula_count,
                'ingredient_count': ingredient_count,
                'proofread_count': proofread_count,
            }

    def get_page_stats(self, page_num: int) -> Dict[str, Any]:
        """
        获取页面统计信息

        Args:
            page_num: 页码

        Returns:
            Dict: 页面统计信息
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) as line_count FROM Line WHERE page_num = ?",
                (page_num,),
            )
            line_count = cursor.fetchone()['line_count']

            cursor.execute(
                "SELECT COUNT(*) as disputed_count FROM Line WHERE page_num = ? AND disputed = 1",
                (page_num,),
            )
            disputed_count = cursor.fetchone()['disputed_count']

            cursor.execute(
                "SELECT AVG(confidence) as avg_confidence FROM Line WHERE page_num = ? AND confidence IS NOT NULL",
                (page_num,),
            )
            avg_confidence = cursor.fetchone()['avg_confidence']

            return {
                'page_num': page_num,
                'line_count': line_count,
                'disputed_count': disputed_count,
                'avg_confidence': round(avg_confidence, 4) if avg_confidence else None,
            }

    def get_verification_summary(self) -> Dict[str, int]:
        """
        获取验证状态汇总

        Returns:
            Dict: 各验证状态的行数
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                "SELECT verification_status, COUNT(*) as count FROM Line GROUP BY verification_status"
            )
            return {row['verification_status']: row['count'] for row in cursor.fetchall()}
