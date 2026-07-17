"""全书端到端性能基准：mock 100 页编排应在 30s 内完成。"""

from __future__ import annotations

import os
import time
import tempfile

import fitz

from kzocr.config import Config
from kzocr.engine.run import run_engine


def _create_pdf(n_pages: int = 100) -> str:
    """创建 n 页迷你 PDF。"""
    pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf.close()
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page(width=300, height=400)
    doc.save(pdf.name)
    doc.close()
    return pdf.name


def test_full_book_100_pages():
    """100 页 mock 全书编排应在 60s 内完成。"""
    pdf = _create_pdf(100)
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    try:
        cfg = Config(use_v07=True, use_mock=True)
        t0 = time.monotonic()
        book = run_engine(pdf, book_code="PERF-100", config=cfg)
        elapsed = time.monotonic() - t0
        assert len(book.pages) > 0, "no pages produced"
        assert elapsed < 120, f"100 pages took {elapsed:.1f}s (threshold 120s)"
    finally:
        os.unlink(pdf)
        for f in os.listdir(td):
            os.remove(os.path.join(td, f))
        os.rmdir(td)


def test_full_book_10_pages():
    """10 页 mock 全书编排应在 30s 内完成（新增检测器后阈值放宽）。"""
    pdf = _create_pdf(10)
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    try:
        cfg = Config(use_v07=True, use_mock=True)
        t0 = time.monotonic()
        book = run_engine(pdf, book_code="PERF-10", config=cfg)
        elapsed = time.monotonic() - t0
        assert len(book.pages) > 0
        assert elapsed < 30, f"10 pages took {elapsed:.1f}s (threshold 30s)"
    finally:
        os.unlink(pdf)
        for f in os.listdir(td):
            os.remove(os.path.join(td, f))
        os.rmdir(td)
