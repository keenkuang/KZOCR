"""缺口②：tcm_ocr RuntimeDB 接回 book_pipeline 的接线与守卫测试（零外部资源）。

验证 book_pipeline 在受控开关（KZOCR_TCM_KNOWLEDGE）下构造 RuntimeDB 并把三个知识
抽取模块接入自动发现链路；默认关闭时完全不进入知识路径（冻结栈行为不变）；RuntimeDB
构造失败无害降级。全部用 MagicMock PG + 内存 SQLite，不跑真实 psycopg2 / Postgres / OCR。
"""

from __future__ import annotations

import sqlite3
from unittest import mock

from kzocr.tcm_ocr.database.sqlite.book_db import BookConnAdapter, BookDbConn
from kzocr.tcm_ocr.pipeline import book_pipeline


def _make_book_adapter() -> BookConnAdapter:
    """建内存库并包成 BookConnAdapter（满足 BookDbConn 契约）。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return BookConnAdapter(conn)


def _fake_self(**kwargs: object) -> object:
    """构造一个仅含所需属性的轻量对象，用于直接调用类方法（避免重 __init__）。"""

    class _Fake:
        pass

    fake = _Fake()
    for key, value in kwargs.items():
        setattr(fake, key, value)
    return fake


# ── 阶段1：_init_runtime_db ────────────────────────────────────────────────

def test_runtime_db_constructed_when_gate_on(monkeypatch) -> None:
    """开关开启 + pg_dsn 非空 → 构造 RuntimeDB 实例。"""
    monkeypatch.setenv("KZOCR_TCM_KNOWLEDGE", "1")
    mock_rt = mock.MagicMock()
    monkeypatch.setattr(
        "kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB", mock_rt
    )
    result = book_pipeline.BookPipeline._init_runtime_db(_fake_self(), "dsn://pg")
    assert result is mock_rt.return_value
    mock_rt.assert_called_once_with(dsn="dsn://pg")


def test_runtime_db_not_constructed_when_gate_off(monkeypatch) -> None:
    """开关关闭（默认）→ 不构造 RuntimeDB，返回 None。"""
    monkeypatch.delenv("KZOCR_TCM_KNOWLEDGE", raising=False)
    mock_rt = mock.MagicMock()
    monkeypatch.setattr(
        "kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB", mock_rt
    )
    result = book_pipeline.BookPipeline._init_runtime_db(_fake_self(), "dsn://pg")
    assert result is None
    mock_rt.assert_not_called()


def test_runtime_db_none_when_no_dsn(monkeypatch) -> None:
    """开关开启但 pg_dsn 为空 → 返回 None，不构造。"""
    monkeypatch.setenv("KZOCR_TCM_KNOWLEDGE", "1")
    mock_rt = mock.MagicMock()
    monkeypatch.setattr(
        "kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB", mock_rt
    )
    assert book_pipeline.BookPipeline._init_runtime_db(_fake_self(), "") is None
    mock_rt.assert_not_called()


def test_runtime_db_construct_failure_degraded(monkeypatch) -> None:
    """RuntimeDB 构造失败（如 psycopg2 缺失）→ 无害降级返回 None。"""
    monkeypatch.setenv("KZOCR_TCM_KNOWLEDGE", "1")
    mock_rt = mock.MagicMock(side_effect=RuntimeError("pool init failed"))
    monkeypatch.setattr(
        "kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB", mock_rt
    )
    assert book_pipeline.BookPipeline._init_runtime_db(_fake_self(), "dsn://pg") is None


# ── 阶段2：_run_knowledge_auto_discovery ───────────────────────────────────

def test_knowledge_path_calls_create_patterns(monkeypatch) -> None:
    """db_runtime 已构造 → 三个知识模块被调用，传入 book_id / db_book / db_runtime。"""
    herb = mock.MagicMock()
    meridian = mock.MagicMock()
    context = mock.MagicMock()
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover.auto_discover_herb_ocr_patterns",
        herb,
    )
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.meridian_pattern.auto_discover.auto_discover_meridian_patterns",
        meridian,
    )
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.context_pattern.auto_discover.auto_discover_context_patterns",
        context,
    )
    db_book = _make_book_adapter()
    rt = mock.MagicMock()
    fake = _fake_self(current_db_book=db_book, db_runtime=rt)

    book_pipeline.BookPipeline._run_knowledge_auto_discovery(fake, "BOOK-1")

    herb.assert_called_once_with("BOOK-1", db_book, rt)
    meridian.assert_called_once_with("BOOK-1", db_book, rt)
    context.assert_called_once_with("BOOK-1", db_book, rt)


def test_knowledge_path_wraps_raw_sqlite(monkeypatch) -> None:
    """current_db_book 为原始 sqlite3.Connection → 经 BookConnAdapter 包装后传入。"""
    herb = mock.MagicMock()
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover.auto_discover_herb_ocr_patterns",
        herb,
    )
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.meridian_pattern.auto_discover.auto_discover_meridian_patterns",
        mock.MagicMock(),
    )
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.context_pattern.auto_discover.auto_discover_context_patterns",
        mock.MagicMock(),
    )
    raw_conn = sqlite3.connect(":memory:")
    rt = mock.MagicMock()
    fake = _fake_self(current_db_book=raw_conn, db_runtime=rt)

    book_pipeline.BookPipeline._run_knowledge_auto_discovery(fake, "BOOK-2")

    called_book = herb.call_args.args[1]
    assert isinstance(called_book, BookDbConn)
    assert not isinstance(raw_conn, BookDbConn)
    raw_conn.close()


def test_knowledge_failure_non_fatal(monkeypatch) -> None:
    """任一知识模块抛异常 → 方法捕获，不向上传播（不阻断主 OCR 闭环）。"""
    herb = mock.MagicMock(side_effect=RuntimeError("db error"))
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover.auto_discover_herb_ocr_patterns",
        herb,
    )
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.meridian_pattern.auto_discover.auto_discover_meridian_patterns",
        mock.MagicMock(),
    )
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.context_pattern.auto_discover.auto_discover_context_patterns",
        mock.MagicMock(),
    )
    fake = _fake_self(current_db_book=_make_book_adapter(), db_runtime=mock.MagicMock())
    # 不应抛异常
    book_pipeline.BookPipeline._run_knowledge_auto_discovery(fake, "BOOK-3")


def test_knowledge_skipped_when_db_book_none(monkeypatch) -> None:
    """current_db_book 为空 → 跳过知识路径，不调用任何知识模块。"""
    herb = mock.MagicMock()
    monkeypatch.setattr(
        "kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover.auto_discover_herb_ocr_patterns",
        herb,
    )
    fake = _fake_self(current_db_book=None, db_runtime=mock.MagicMock())
    book_pipeline.BookPipeline._run_knowledge_auto_discovery(fake, "BOOK-4")
    herb.assert_not_called()


# ── 硬守卫：知识模块必须被 book_pipeline 引用（固化「已接回」事实） ───────────

def test_book_pipeline_references_knowledge_modules() -> None:
    """AST 扫描：book_pipeline.py 必须 import/调用三个知识模块，防止回归断线。"""
    import ast

    src = (
        book_pipeline.__file__
        if book_pipeline.__file__.endswith(".py")
        else book_pipeline.__file__[:-1]
    )
    tree = ast.parse(open(src, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    for needed in (
        "auto_discover_herb_ocr_patterns",
        "auto_discover_meridian_patterns",
        "auto_discover_context_patterns",
    ):
        assert needed in names, f"book_pipeline 未引用知识模块 {needed}（缺口② 接线丢失）"
