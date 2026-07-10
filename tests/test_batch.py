"""批量处理测试。"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch, MagicMock

import fitz

from kzocr.cli import cmd_batch


def test_batch_empty_directory():
    """空目录应返回 0（无错误）。"""
    td = tempfile.mkdtemp()
    try:
        args = MagicMock(spec=["pdf_dir", "db"])
        args.pdf_dir = td
        args.db = None
        rc = cmd_batch(args)
        assert rc == 0
    finally:
        os.rmdir(td)


def test_batch_with_pdfs():
    """含 PDF 的目录应成功处理。"""
    td = tempfile.mkdtemp()
    tmp_db = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = tmp_db
    try:
        # 创建 2 个测试 PDF
        for name in ["book1.pdf", "book2.pdf"]:
            doc = fitz.open()
            doc.new_page()
            doc.save(os.path.join(td, name))
            doc.close()
        args = MagicMock(spec=["pdf_dir", "db"])
        args.pdf_dir = td
        args.db = None
        with patch("kzocr.engine.run.run_engine") as mock_run, \
             patch("kzocr.cli.load_config") as mock_cfg:
            mock_run.return_value = MagicMock(pages=[MagicMock()])
            mock_cfg.return_value = MagicMock()
            rc = cmd_batch(args)
            assert rc == 0
            assert mock_run.call_count == 2
    finally:
        for f in os.listdir(td):
            os.unlink(os.path.join(td, f))
        os.rmdir(td)
        for f in os.listdir(tmp_db):
            os.unlink(os.path.join(tmp_db, f))
        os.rmdir(tmp_db)


def test_batch_nonexistent_directory():
    """不存在的目录应返回 1。"""
    args = MagicMock(spec=["pdf_dir", "db"])
    args.pdf_dir = "/nonexistent/path"
    args.db = None
    rc = cmd_batch(args)
    assert rc == 1
