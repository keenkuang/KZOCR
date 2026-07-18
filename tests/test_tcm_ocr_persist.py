"""tcm_ocr BookResult 落主线 BookDB 闭环测试（无引擎/Postgres）。

验证 §3.4：转换器产 BookResult（含 char_boxes）→ BookDB.persist_book_result
→ get_page_char_boxes 读回一致。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from kzocr.storage.db import BookDB
from kzocr.tcm_ocr.pipeline.book_result_convert import book_result_from_tcm_ocr

PAGE_RESULTS = [
    {
        "page_number": 1,
        "lines": [
            {
                "bbox": [10, 100, 200, 120],
                "fused_text": "当归一两",
                "confidence": 0.95,
                "char_bboxes": [
                    {"char": "当", "conf": 0.9, "bbox": [10.0, 100, 28.0, 120]},
                    {"char": "归", "conf": 0.9, "bbox": [29.0, 100, 47.0, 120]},
                ],
            },
            {
                "bbox": [10, 130, 200, 150],
                "fused_text": "黄芪五钱",
                "confidence": 0.92,
                "char_bboxes": [
                    {"char": "黄", "conf": 0.9, "bbox": [10.0, 130, 28.0, 150]},
                ],
            },
        ],
    },
]


def test_persist_then_read_char_boxes():
    book = book_result_from_tcm_ocr(PAGE_RESULTS, book_code="TCM-PERSIST-001")
    tmp = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp
    os.environ["KZOCR_PERSIST_DB"] = "1"
    try:
        BookDB.persist_book_result(book, db_dir=tmp)

        db = BookDB("TCM-PERSIST-001", db_dir=tmp)
        try:
            # 页级 char_boxes 读回与转换器一致
            cb = db.get_page_char_boxes(1)
            assert cb is not None
            assert cb == [
                [[10, 100, 28, 120], [29, 100, 47, 120]],
                [[10, 130, 28, 150]],
            ]
            # 行文本落库
            pages = db.get_book_pages()
            assert len(pages) == 1
        finally:
            db.close()
    finally:
        if old_db_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_db_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist
        for f in Path(tmp).glob("TCM-PERSIST-001.db*"):
            f.unlink()
