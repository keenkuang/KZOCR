"""Postgres 元数据注册/校对归档 mock 单测（不依赖真实 PG 服务）。

验证 ``push_book_to_zai(register_postgres=True)`` 调用
``register_book`` / ``set_book_meta`` / ``update_book_status``，
及 ``import_proofread_package(register_postgres=True)`` 调用
``archive_line_correction``。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from kzocr.doc import import_proofread_package, push_book_to_zai
from kzocr.engine.mock import mock_book_result
from kzocr.storage.db import BookDB


def _make_realistic_book(book_code: str):
    """构造含一行内容的 BookResult，确保 push 写库时有实际数据。"""
    book = mock_book_result(book_code)
    book.is_mock = False
    return book


@patch("kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB")
def test_push_registers_new_book(mock_runtime_db, tmp_path) -> None:
    """无已有记录时调用 register_book + set_book_meta + update_book_status。"""
    mock_inst = MagicMock()
    mock_runtime_db.return_value = mock_inst
    mock_cursor = MagicMock()
    mock_inst.get_cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None  # 无已有记录

    book = _make_realistic_book("pg-new")
    push_book_to_zai(book, db_path=Path(tmp_path) / "n.db", register_postgres=True,
                     persist_bookdb=False)

    mock_inst.register_book.assert_called_once()
    mock_inst.set_book_meta.assert_called_once()
    mock_inst.update_book_status.assert_called_once_with(ANY, "proofreading")


@patch("kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB")
def test_push_updates_existing_book(mock_runtime_db, tmp_path) -> None:
    """已有记录时调用 set_book_meta + update_book_status（不调用 register_book）。"""
    mock_inst = MagicMock()
    mock_runtime_db.return_value = mock_inst
    mock_cursor = MagicMock()
    mock_inst.get_cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": 42}

    book = _make_realistic_book("pg-existing")
    push_book_to_zai(book, db_path=Path(tmp_path) / "e.db", register_postgres=True,
                     persist_bookdb=False)

    mock_inst.register_book.assert_not_called()
    mock_inst.set_book_meta.assert_called_once()
    mock_inst.update_book_status.assert_called_once_with(42, "proofreading")


@patch("kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB")
def test_push_skipped_when_register_false(mock_runtime_db, tmp_path) -> None:
    """register_postgres=False 时 RuntimeDB 不应被调用。"""
    mock_inst = MagicMock()
    mock_runtime_db.return_value = mock_inst

    book = _make_realistic_book("pg-skip")
    push_book_to_zai(book, db_path=Path(tmp_path) / "s.db", register_postgres=False,
                     persist_bookdb=False)

    mock_inst.register_book.assert_not_called()


@patch("kzocr.tcm_ocr.database.postgres.runtime_db.RuntimeDB")
def test_import_archives_proofreads(mock_runtime_db, monkeypatch, tmp_path) -> None:
    """import_proofread_package 在 register_postgres=True 时调用 archive_line_correction。"""
    monkeypatch.setenv("KZOCR_PERSIST_DB", "1")
    monkeypatch.setenv("KZOCR_DB_DIR", str(tmp_path))
    mock_inst = MagicMock()
    mock_runtime_db.return_value = mock_inst
    mock_cursor = MagicMock()
    mock_inst.get_cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"id": 7}

    book = _make_realistic_book("pg-archive")
    zai_db = Path(tmp_path) / "z.db"
    push_book_to_zai(book, db_path=zai_db, register_postgres=False,
                     persist_bookdb=True)
    # 模拟人工终校 + 校对记录
    conn = sqlite3.connect(str(zai_db))
    conn.execute("UPDATE Line SET humanFinal=consensus WHERE bookCode=?", (book.book_code,))
    # 插入一条 Proofread 记录（import 时才会触发 archive）
    line_id = f"{book.book_code}-P0-1-1"
    conn.execute(
        "INSERT INTO Proofread (id,pageNum,bookCode,paraSeq,seqInPara,lineId,"
        "originalText,correctedText,changeType,severity) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("pr1", 0, book.book_code, 1, 1, line_id,
         "原文本", "修正文本", "replace", "low"),
    )
    conn.commit()
    conn.close()

    result = import_proofread_package(
        zai_db, register_postgres=True, book_code=book.book_code,
        db_dir=str(tmp_path),
    )
    assert result["imported_lines"] >= 1
    assert result["imported_proofreads"] >= 1
    mock_inst.archive_line_correction.assert_called()
