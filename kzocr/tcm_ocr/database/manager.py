"""
数据库管理器 - DatabaseManager 类

统一管理 PostgreSQL 运行库和 SQLite 书籍库的连接。
提供工厂方法创建 BookDB 实例，以及双库协调操作。

使用示例:
    with DatabaseManager() as db_manager:
        # 注册新书
        book_id = db_manager.register_new_book("/path/to/book.pdf")
        # 获取书籍库
        book_db = db_manager.get_book_db(book_id)
        # 使用 book_db 进行操作...
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
from kzocr.tcm_ocr.database.sqlite.book_db import BookDB

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    数据库管理器

    统一管理 PostgreSQL 运行库（RuntimeDB）和多个 SQLite 书籍库（BookDB）。
    负责书籍注册、数据库生命周期管理和双库协调操作。

    Attributes:
        runtime_db: PostgreSQL 运行库连接实例
        _book_dbs: 已打开的 SQLite 书籍库连接缓存
        _books_dir: SQLite 书籍库文件存储目录
    """

    def __init__(
        self,
        postgres_host: Optional[str] = None,
        postgres_port: Optional[int] = None,
        postgres_db: Optional[str] = None,
        postgres_user: Optional[str] = None,
        postgres_password: Optional[str] = None,
        postgres_dsn: Optional[str] = None,
        books_dir: Optional[str] = None,
        runtime_db: Optional[RuntimeDB] = None,
    ) -> None:
        """
        初始化数据库管理器

        Args:
            postgres_host: PostgreSQL 主机地址
            postgres_port: PostgreSQL 端口
            postgres_db: PostgreSQL 数据库名
            postgres_user: PostgreSQL 用户名
            postgres_password: PostgreSQL 密码
            postgres_dsn: PostgreSQL 完整 DSN（优先使用）
            books_dir: SQLite 书籍库存储目录，默认从环境变量获取
            runtime_db: 已有的 RuntimeDB 实例（用于依赖注入）
        """
        if runtime_db is not None:
            self.runtime_db = runtime_db
        else:
            self.runtime_db = RuntimeDB(
                host=postgres_host,
                port=postgres_port,
                database=postgres_db,
                user=postgres_user,
                password=postgres_password,
                dsn=postgres_dsn,
            )

        self._books_dir = books_dir or os.environ.get(
            'TCM_BOOKS_DIR',
            os.path.join(os.getcwd(), 'data', 'books'),
        )
        self._book_dbs: Dict[int, BookDB] = {}

        # 确保书籍存储目录存在
        os.makedirs(self._books_dir, exist_ok=True)
        logger.info("DatabaseManager initialized, books_dir: %s", self._books_dir)

    def _get_book_db_path(self, book_registry_id: int) -> str:
        """
        根据书籍注册 ID 生成 SQLite 数据库文件路径

        Args:
            book_registry_id: 书籍注册 ID

        Returns:
            str: SQLite 数据库文件绝对路径
        """
        return os.path.join(self._books_dir, f"book_{book_registry_id}.db")

    def register_new_book(
        self,
        pdf_path: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        publisher: Optional[str] = None,
        pub_year: Optional[int] = None,
        edition: Optional[str] = None,
        isbn: Optional[str] = None,
        source_declaration: Optional[str] = None,
    ) -> int:
        """
        注册一本新书并创建对应的 SQLite 数据库

        流程:
        1. 在 PostgreSQL 运行库中注册书籍
        2. 生成 SQLite 数据库文件路径
        3. 更新 PostgreSQL 中的 db_path
        4. 创建并初始化 SQLite 数据库
        5. 设置书籍元数据

        Args:
            pdf_path: PDF 源文件路径
            title: 书名
            author: 作者
            publisher: 出版社
            pub_year: 出版年份
            edition: 版次
            isbn: ISBN
            source_declaration: 来源声明

        Returns:
            int: 新注册书籍的 ID

        Raises:
            RuntimeError: 当数据库操作失败时
        """
        try:
            # 1. 在 PostgreSQL 中注册书籍
            book_id = self.runtime_db.register_book(pdf_path)

            # 2. 生成数据库路径
            db_path = self._get_book_db_path(book_id)

            # 3. 更新 db_path
            with self.runtime_db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE BookRegistry SET db_path = %s WHERE id = %s",
                    (db_path, book_id),
                )

            # 4. 创建并初始化 SQLite 数据库
            book_db = BookDB(db_path)
            book_db.initialize_schema()

            # 5. 设置元数据
            if title:
                book_db.set_book_meta(
                    title=title,
                    author=author,
                    publisher=publisher,
                    pub_year=pub_year,
                    edition=edition,
                    isbn=isbn,
                    source_declaration=source_declaration,
                )

            # 缓存 book_db 实例
            self._book_dbs[book_id] = book_db

            # 创建 OCR 处理日志
            self.runtime_db.create_ocr_log(book_id)

            logger.info(
                "New book registered: id=%d, pdf=%s, db=%s",
                book_id, pdf_path, db_path,
            )
            return book_id

        except Exception as e:
            logger.error("Failed to register new book: %s", e)
            raise RuntimeError(f"Failed to register new book: {e}") from e

    def get_book_db(self, book_registry_id: int, auto_create: bool = False) -> BookDB:
        """
        获取指定书籍的 SQLite 数据库连接

        Args:
            book_registry_id: 书籍注册 ID
            auto_create: 如果数据库不存在是否自动创建

        Returns:
            BookDB: 书籍数据库连接实例

        Raises:
            FileNotFoundError: 当数据库文件不存在且 auto_create=False 时
        """
        # 检查缓存
        if book_registry_id in self._book_dbs:
            return self._book_dbs[book_registry_id]

        # 从 PostgreSQL 获取 db_path
        book_info = self.runtime_db.get_book_registry(book_registry_id)
        if book_info and book_info.get('db_path'):
            db_path = book_info['db_path']
        else:
            db_path = self._get_book_db_path(book_registry_id)

        if not os.path.exists(db_path):
            if auto_create:
                os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
                book_db = BookDB(db_path)
                book_db.initialize_schema()
                # 更新 PostgreSQL 中的 db_path
                with self.runtime_db.get_cursor() as cursor:
                    cursor.execute(
                        "UPDATE BookRegistry SET db_path = %s WHERE id = %s",
                        (db_path, book_registry_id),
                    )
            else:
                raise FileNotFoundError(
                    f"Book database not found for book_registry_id={book_registry_id}: {db_path}"
                )
        else:
            book_db = BookDB(db_path)

        # 缓存并返回
        self._book_dbs[book_registry_id] = book_db
        return book_db

    def open_book_db(self, book_registry_id: int) -> BookDB:
        """
        打开书籍数据库（同 get_book_db，语义更清晰）

        Args:
            book_registry_id: 书籍注册 ID

        Returns:
            BookDB: 书籍数据库连接实例
        """
        return self.get_book_db(book_registry_id)

    def close_book_db(self, book_registry_id: int) -> None:
        """
        关闭指定书籍的数据库连接

        Args:
            book_registry_id: 书籍注册 ID
        """
        if book_registry_id in self._book_dbs:
            self._book_dbs[book_registry_id].close()
            del self._book_dbs[book_registry_id]
            logger.debug("Book database closed: book_id=%d", book_registry_id)

    def list_registered_books(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        列出已注册的书籍

        Args:
            status: 按状态过滤
            limit: 返回数量限制

        Returns:
            List[Dict]: 书籍注册信息列表
        """
        return self.runtime_db.list_books(status=status, limit=limit)

    def update_book_status(self, book_registry_id: int, status: str) -> bool:
        """
        更新书籍处理状态

        Args:
            book_registry_id: 书籍注册 ID
            status: 新状态

        Returns:
            bool: 是否更新成功
        """
        return self.runtime_db.update_book_status(book_registry_id, status)

    def get_book_stats(self, book_registry_id: int) -> Dict[str, Any]:
        """
        获取书籍的完整统计信息（合并双库数据）

        Args:
            book_registry_id: 书籍注册 ID

        Returns:
            Dict: 统计信息汇总
        """
        # PostgreSQL 侧统计
        pg_stats = self.runtime_db.get_book_stats(book_registry_id)

        # SQLite 侧统计
        try:
            book_db = self.get_book_db(book_registry_id)
            sqlite_stats = book_db.get_book_stats()
        except FileNotFoundError:
            sqlite_stats = {}

        return {
            'book_registry_id': book_registry_id,
            'postgresql': pg_stats,
            'sqlite': sqlite_stats,
        }

    def get_book_full_info(self, book_registry_id: int) -> Dict[str, Any]:
        """
        获取书籍的完整信息（注册信息 + 元数据 + 统计）

        Args:
            book_registry_id: 书籍注册 ID

        Returns:
            Dict: 完整信息
        """
        registry = self.runtime_db.get_book_registry(book_registry_id)
        meta = self.runtime_db.get_book_meta(book_registry_id)
        stats = self.get_book_stats(book_registry_id)

        return {
            'registry': registry,
            'meta': meta,
            'stats': stats,
        }

    def archive_book(self, book_registry_id: int) -> bool:
        """
        归档书籍

        将书籍状态更新为 archived，并关闭对应的数据库连接。

        Args:
            book_registry_id: 书籍注册 ID

        Returns:
            bool: 是否归档成功
        """
        try:
            # 关闭数据库连接
            self.close_book_db(book_registry_id)

            # 更新状态
            success = self.runtime_db.update_book_status(book_registry_id, 'archived')

            if success:
                logger.info("Book archived: book_id=%d", book_registry_id)
            return success

        except Exception as e:
            logger.error("Failed to archive book %d: %s", book_registry_id, e)
            return False

    def delete_book(self, book_registry_id: int, delete_db_file: bool = False) -> bool:
        """
        删除书籍记录

        Args:
            book_registry_id: 书籍注册 ID
            delete_db_file: 是否同时删除 SQLite 数据库文件

        Returns:
            bool: 是否删除成功
        """
        try:
            # 关闭并移除缓存
            self.close_book_db(book_registry_id)

            # 获取数据库路径
            book_info = self.runtime_db.get_book_registry(book_registry_id)
            db_path = book_info.get('db_path') if book_info else None

            # PostgreSQL 中的记录会通过外键级联删除
            with self.runtime_db.get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM BookRegistry WHERE id = %s",
                    (book_registry_id,),
                )
                deleted = cursor.rowcount > 0

            # 删除数据库文件
            if delete_db_file and db_path and os.path.exists(db_path):
                os.remove(db_path)
                logger.info("Book database file deleted: %s", db_path)

            if deleted:
                logger.info("Book deleted: book_id=%d", book_registry_id)
            return deleted

        except Exception as e:
            logger.error("Failed to delete book %d: %s", book_registry_id, e)
            return False

    def close_all(self) -> None:
        """关闭所有数据库连接"""
        # 关闭所有书籍数据库
        for book_id in list(self._book_dbs.keys()):
            self.close_book_db(book_id)
        self._book_dbs.clear()

        # 关闭运行库连接池
        self.runtime_db.close()

        logger.info("All database connections closed")

    def __enter__(self) -> 'DatabaseManager':
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        self.close_all()

    # =====================================================================
    # 便捷方法：OCR 处理流程集成
    # =====================================================================

    def record_ocr_progress(
        self,
        book_registry_id: int,
        total_pages: int,
        total_lines: int,
    ) -> bool:
        """
        记录 OCR 处理进度

        Args:
            book_registry_id: 书籍注册 ID
            total_pages: 已处理页数
            total_lines: 已处理行数

        Returns:
            bool: 是否记录成功
        """
        try:
            with self.runtime_db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE OCRProcessingLog
                    SET total_pages = %s, total_lines = %s
                    WHERE book_registry_id = %s AND completed_at IS NULL
                    """,
                    (total_pages, total_lines, book_registry_id),
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error("Failed to record OCR progress: %s", e)
            return False

    def complete_ocr_processing(
        self,
        book_registry_id: int,
        total_pages: int,
        total_lines: int,
    ) -> bool:
        """
        标记 OCR 处理完成

        Args:
            book_registry_id: 书籍注册 ID
            total_pages: 总页数
            total_lines: 总行数

        Returns:
            bool: 是否更新成功
        """
        try:
            # 获取最新的 OCR 日志 ID
            with self.runtime_db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM OCRProcessingLog
                    WHERE book_registry_id = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (book_registry_id,),
                )
                result = cursor.fetchone()
                if result:
                    self.runtime_db.complete_ocr_log(result['id'], total_pages, total_lines)

            # 更新书籍状态
            self.runtime_db.update_book_status(book_registry_id, 'proofreading')

            logger.info("OCR processing completed for book %d", book_registry_id)
            return True

        except Exception as e:
            logger.error("Failed to complete OCR processing: %s", e)
            return False

    def save_page_data(
        self,
        book_registry_id: int,
        page_num: int,
        pdf_page_num: int,
        lines_data: List[Dict[str, Any]],
        engine_results: Optional[List[Dict[str, Any]]] = None,
        page_type: str = 'body',
        quality_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        保存页面 OCR 数据到 SQLite 书籍库

        这是一个便捷方法，用于一次性保存页面及其行数据。

        Args:
            book_registry_id: 书籍注册 ID
            page_num: 逻辑页码
            pdf_page_num: PDF 物理页码
            lines_data: 行数据列表
            engine_results: 引擎结果列表
            page_type: 页面类型
            quality_score: 页面质量评分

        Returns:
            Dict: 保存结果统计
        """
        book_db = self.get_book_db(book_registry_id)

        # 创建页面
        page_id = book_db.create_page(
            page_num=page_num,
            pdf_page_num=pdf_page_num,
            page_type=page_type,
            quality_score=quality_score,
        )

        # 创建段落（如果行数据有段落信息）
        paragraph_id = None
        if lines_data and any('paragraph_sequence' in line for line in lines_data):
            # 使用第一个行的段落序号
            para_seq = lines_data[0].get('paragraph_sequence', 0)
            paragraph_id = book_db.create_paragraph(
                page_id=page_id,
                sequence_in_page=para_seq,
            )

        # 创建行
        line_count = 0
        for seq, line_data in enumerate(lines_data):
            line_fields = {
                'page_id': page_id,
                'sequence_in_paragraph': line_data.get('sequence', seq),
                'page_num': page_num,
                'paragraph_id': paragraph_id,
                'final_text': line_data.get('final_text'),
                'raw_vote_text': line_data.get('raw_vote_text'),
                'llm_corrected_text': line_data.get('llm_corrected_text'),
                'confidence': line_data.get('confidence'),
                'bbox': line_data.get('bbox'),
                'char_level_json': line_data.get('char_level_json'),
            }
            # 过滤 None 值
            line_fields = {k: v for k, v in line_fields.items() if v is not None}
            book_db.create_line(**line_fields)
            line_count += 1

        # 保存引擎结果
        engine_count = 0
        if engine_results:
            for result in engine_results:
                book_db.create_engine_result(
                    line_id=result['line_id'],
                    engine_name=result['engine_name'],
                    raw_text=result.get('raw_text'),
                    confidence=result.get('confidence'),
                    char_level_json=result.get('char_level_json'),
                )
                engine_count += 1

        return {
            'page_id': page_id,
            'paragraph_id': paragraph_id,
            'lines_created': line_count,
            'engine_results_created': engine_count,
        }

    # =====================================================================
    # 健康检查
    # =====================================================================

    def health_check(self) -> Dict[str, Any]:
        """
        数据库健康检查

        Returns:
            Dict: 健康状态报告
        """
        status = {
            'postgresql': {'status': 'unknown', 'details': {}},
            'books_dir': {'status': 'unknown', 'path': self._books_dir},
            'open_book_dbs': len(self._book_dbs),
        }

        # 检查 PostgreSQL
        try:
            with self.runtime_db.get_cursor() as cursor:
                cursor.execute("SELECT version()")
                result = cursor.fetchone()
                status['postgresql'] = {
                    'status': 'healthy',
                    'version': result['version'] if result else 'unknown',
                }
        except Exception as e:
            status['postgresql'] = {
                'status': 'error',
                'error': str(e),
            }

        # 检查书籍目录
        if os.path.exists(self._books_dir) and os.path.isdir(self._books_dir):
            status['books_dir']['status'] = 'healthy'
            db_files = [f for f in os.listdir(self._books_dir) if f.endswith('.db')]
            status['books_dir']['database_count'] = len(db_files)
        else:
            status['books_dir']['status'] = 'error'
            status['books_dir']['error'] = f'Directory not found: {self._books_dir}'

        # 整体状态
        all_healthy = all(
            s['status'] == 'healthy'
            for s in [status['postgresql'], status['books_dir']]
        )
        status['overall'] = 'healthy' if all_healthy else 'degraded'

        return status
