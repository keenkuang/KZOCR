"""BookDB 内容表（book/page/line）落库单测 —— mock 数据，无真实引擎/图像依赖。

验证：BookResult（含 PaddleOCR 产出的字符级 bbox）→ save_book_result → 读回一致。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from kzocr.engine.types import BookResult, PageResult
from kzocr.storage.db import BookDB


def _make_book(char_boxes_page0) -> BookResult:
    pages = [
        PageResult(
            page_num=0,
            text="补气方用",
            confidence=0.95,
            char_boxes=char_boxes_page0,
        ),
        PageResult(page_num=1, text="海藻昆布", confidence=0.90, char_boxes=None),
    ]
    return BookResult(
        book_code="TEST01", title="测试书", author="佚名",
        publisher="未知", pages=pages,
    )


def test_save_and_read_book_result():
    char_boxes = [[[10, 20, 30, 40], [12, 20, 28, 40]], [[50, 20, 70, 40]]]
    book = _make_book(char_boxes)

    tmp = tempfile.mkdtemp()
    db = BookDB("TEST01", db_dir=tmp)
    try:
        db.save_book_result(book)

        # book 表
        brow = db._conn.execute(
            "SELECT book_code,title FROM book"
        ).fetchone()
        assert brow["book_code"] == "TEST01"
        assert brow["title"] == "测试书"

        # page 表：char_count = len(text)
        prow = db.get_page(0)
        assert prow["text"] == "补气方用"
        assert prow["char_count"] == 4

        # page.char_boxes 读回一致
        got = db.get_page_char_boxes(0)
        assert got == char_boxes

        # 不支持字符级 bbox 的页（RapidOCR）→ None
        assert db.get_page_char_boxes(1) is None

        # line 表：page0 两行
        lines = db._conn.execute(
            "SELECT line_seq, char_boxes FROM line WHERE page_num=0 ORDER BY line_seq"
        ).fetchall()
        assert len(lines) == 2
        assert lines[0]["char_boxes"] == "[[10, 20, 30, 40], [12, 20, 28, 40]]"
    finally:
        db.close()
        for f in Path(tmp).glob("TEST01.db*"):
            f.unlink()


def test_save_page_idempotent_upsert():
    """重复 save 应幂等（ON CONFLICT 覆盖，不重复插入）。"""
    tmp = tempfile.mkdtemp()
    db = BookDB("TEST02", db_dir=tmp)
    try:
        db.save_page("TEST02", 0, text="甲", confidence=0.9, char_boxes=[[[1, 1, 2, 2]]])
        db.save_page("TEST02", 0, text="乙", confidence=0.8, char_boxes=[[[3, 3, 4, 4]]])
        prow = db.get_page(0)
        assert prow["text"] == "乙"  # 覆盖
        assert db.get_page_char_boxes(0) == [[[3, 3, 4, 4]]]
        # page 表仅 1 行
        cnt = db._conn.execute("SELECT count(*) FROM page").fetchone()[0]
        assert cnt == 1
        # line 表不再由 save_page 展开（行展开已移至 save_book_result 的层级展开）
        cnt_line = db._conn.execute("SELECT count(*) FROM line").fetchone()[0]
        assert cnt_line == 0
    finally:
        db.close()
        for f in Path(tmp).glob("TEST02.db*"):
            f.unlink()
