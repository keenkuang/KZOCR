"""tcm_ocr BookResult 走通 push/import 闭环（G5，复用既有导出/导入）。

验证：转换器产 BookResult → push_book_to_zai（落 BookDB）→ 改 humanFinal
→ import_proofread_package 写回 BookDB。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from kzocr.adapter.to_zai_prisma import import_proofread_package, push_book_to_zai
from kzocr.storage.db import BookDB
from kzocr.tcm_ocr.pipeline.book_result_convert import book_result_from_tcm_ocr

PAGE_RESULTS = [
    {
        "page_number": 1,
        "lines": [
            {
                "bbox": [10, 100, 200, 120],
                "fused_text": "附子一两",  # 故意误认，供人工校正
                "confidence": 0.85,
            },
            {
                "bbox": [10, 130, 200, 150],
                "fused_text": "干姜五钱",
                "confidence": 0.9,
            },
        ],
    },
]


def test_push_loop_imports_back():
    book = book_result_from_tcm_ocr(PAGE_RESULTS, book_code="TCM-LOOP-001")
    tmp = tempfile.mkdtemp()
    old_db_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = tmp
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = tempfile.mktemp(suffix=".db")
    try:
        # 1) 导出校对包（同时落 BookDB 系统 of record）
        res = push_book_to_zai(book, db_path=pkg, skip_prisma_marker=True)
        assert res["book_code"] == "TCM-LOOP-001"

        # 2) 模拟人工终校：改正 "附子" → "白术"（段1行1）
        con = sqlite3.connect(pkg)
        con.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1",
            ("白术一两（人工终校）",),
        )
        con.commit()
        con.close()

        # 3) 导入回写 BookDB
        imp = import_proofread_package(db_path=pkg, book_code="TCM-LOOP-001")
        assert imp["book_code"] == "TCM-LOOP-001"
        assert imp["imported_lines"] == 1

        # 4) BookDB 收到人工终校
        db = BookDB("TCM-LOOP-001", db_dir=tmp)
        try:
            hf = db.get_line_human_final(1, 1, 1)
            assert hf == "白术一两（人工终校）"
            # 未校对的行保持不变
            assert db.get_line_human_final(1, 1, 2) == ""
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
        for f in Path(tmp).glob("TCM-LOOP-001.db*"):
            f.unlink()
        Path(pkg).unlink(missing_ok=True)
