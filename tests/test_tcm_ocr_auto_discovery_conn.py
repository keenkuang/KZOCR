"""
tcm_ocr 自动发现链路连接契约测试（W10 闭环后补）。

验证 `BookConnAdapter` 让 `book_pipeline` 的 raw `sqlite3.Connection` 满足
`BookDbConn` 契约（补充 `get_cursor`），从而关闭 db-layering.md §7.4 记录的
契约缺口；并反向守护「raw `sqlite3.Connection` 仍不满足契约」这一既有事实。

零外部资源：全部用内存 SQLite + MagicMock PG，不跑真实 OCR / Postgres / LLM。
"""

import sqlite3
from unittest import mock

from kzocr.tcm_ocr.database.sqlite.book_db import (
    BookConnAdapter,
    BookDbConn,
    _BOOK_SCHEMA_SQL,
)
from kzocr.tcm_ocr.knowledge.context_pattern.auto_discover import (
    auto_discover_context_patterns,
)
from kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover import (
    auto_discover_herb_ocr_patterns,
)
from kzocr.tcm_ocr.knowledge.meridian_pattern.auto_discover import (
    auto_discover_meridian_patterns,
)
from kzocr.tcm_ocr.pipeline.auto_discovery import _run_auto_discovery


def _make_adapter() -> BookConnAdapter:
    """建内存库（含全部 snake_case 表）并包成 BookConnAdapter。

    模拟 `book_pipeline` 运行时的连接：设好 row_factory 再包装。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_BOOK_SCHEMA_SQL)
    return BookConnAdapter(conn)


def test_raw_conn_does_not_satisfy_contract() -> None:
    """反向守护：raw `sqlite3.Connection` 仍不满足 BookDbConn（契约缺口事实）。"""
    raw = sqlite3.connect(":memory:")
    try:
        assert isinstance(raw, BookDbConn) is False
    finally:
        raw.close()


def test_adapter_satisfies_contract() -> None:
    """BookConnAdapter 满足 BookDbConn（补充 get_cursor 后）。"""
    adapter = _make_adapter()
    try:
        assert isinstance(adapter, BookDbConn) is True
    finally:
        adapter.close()


def test_adapter_delegates_execute_and_commit() -> None:
    """execute / commit / cursor 等经 __getattr__ 委托给底层连接。"""
    adapter = _make_adapter()
    try:
        adapter.execute(
            "INSERT INTO proofread_record (book_id, line_id, original_text, corrected_text) "
            "VALUES (?, ?, ?, ?)",
            ("b1", "l1", "原", "正"),
        )
        adapter.commit()
        rows = adapter.execute(
            "SELECT original_text, corrected_text FROM proofread_record"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["original_text"] == "原"
        # row_factory 也委托生效
        assert rows[0]["corrected_text"] == "正"
    finally:
        adapter.close()


def test_adapter_get_cursor_works_and_closes() -> None:
    """get_cursor 返回可工作的游标，退出 context 后关闭。"""
    adapter = _make_adapter()
    try:
            with adapter.get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM proofread_record")
                assert cur.fetchone()["n"] == 0
            # 退出 with 块后，contextmanager 已关闭游标（再次使用应报错）
            try:
                cur.execute("SELECT 1")
                raise AssertionError("cursor 未关闭")
            except sqlite3.ProgrammingError:
                pass
    finally:
        adapter.close()


def test_run_auto_discovery_with_adapter_no_error() -> None:
    """运行时入口 _run_auto_discovery 用适配器连接不抛 AttributeError。"""
    adapter = _make_adapter()
    try:
        with mock.patch("kzocr.tcm_ocr.pipeline.auto_discovery.logger"):
            # db_pg 用 MagicMock：桩函数的 create_*_pattern 调用被吸收
            _run_auto_discovery("b1", adapter, mock.MagicMock())
    finally:
        adapter.close()


def test_knowledge_modules_get_cursor_no_attribute_error() -> None:
    """4 个休眠 get_cursor 调用点中的 3 个可调用模块：用适配器不再潜在崩溃。

    这些函数在记录表为空时早期返回，不会触碰 db_pg 的 create_* 方法，
    因此 MagicMock 即可；重点验证 `db_book.get_cursor()` 路径在适配器下可用。
    """
    adapter = _make_adapter()
    try:
        pg = mock.MagicMock()
        # 校对记录 / 公式表为空 → 各函数早期返回，不触发 AttributeError
        assert auto_discover_herb_ocr_patterns("b1", adapter, pg) == []
        assert auto_discover_meridian_patterns("b1", adapter, pg) == []
        assert auto_discover_context_patterns("b1", adapter, pg) == []
    finally:
        adapter.close()
