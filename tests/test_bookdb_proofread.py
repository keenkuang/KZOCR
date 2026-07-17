"""BookDB 层级 human_final / proofread 落库与回读单测（mock 数据，无引擎）。

验证：层级唯一键 (page_num, para_seq, line_seq) + 人工终校写回 + 校对记录导入。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from kzocr.engine.types import (
    BookResult, PageResult, ParagraphResult, LineResult,
)
from kzocr.storage.db import BookDB


def _book() -> BookResult:
    pages = [
        PageResult(
            page_num=0,
            text="甲\n乙",
            confidence=0.9,
            paragraphs=[
                ParagraphResult(sequence_in_page=1, lines=[
                    LineResult(sequence_in_paragraph=1, final="甲", consensus="甲"),
                    LineResult(sequence_in_paragraph=2, final="乙", consensus="乙"),
                ]),
            ],
        ),
    ]
    return BookResult(book_code="PR1", title="层级测试书", pages=pages)


def test_hierarchical_line_unique_key():
    book = _book()
    tmp = tempfile.mkdtemp()
    db = BookDB("PR1", db_dir=tmp)
    try:
        db.save_book_result(book)
        rows = db._conn.execute(
            "SELECT page_num, para_seq, line_seq FROM line ORDER BY para_seq, line_seq"
        ).fetchall()
        assert len(rows) == 2
        # 段序号按位置派生（1-based，save_book_result 用 enumerate），行序同理
        assert rows[0]["para_seq"] == 1 and rows[0]["line_seq"] == 1
        assert rows[1]["para_seq"] == 1 and rows[1]["line_seq"] == 2
    finally:
        db.close()
        for f in Path(tmp).glob("PR1.db*"):
            f.unlink()


def test_human_final_and_proofread_roundtrip():
    book = _book()
    tmp = tempfile.mkdtemp()
    db = BookDB("PR1", db_dir=tmp)
    try:
        db.save_book_result(book)
        # 人工终校写回（层级键）
        db.save_line_human_final(0, 1, 1, "甲（终校）")
        hf = db.get_line_human_final(0, 1, 1)
        assert hf == "甲（终校）"
        # 未校对行应为空
        assert db.get_line_human_final(0, 1, 2) == ""

        # 校对记录导入回写
        saved = db.save_proofreads([
            {"page_num": 0, "para_seq": 1, "line_seq": 2,
             "line_id": "PR1-P0-1-2", "original_text": "乙",
             "corrected_text": "己", "change_type": "glyph", "severity": "critical",
             "notes": "乙→己", "triggered_pattern": "X"},
        ])
        assert saved == 1
        proof = db.get_proofreads(page_num=0)
        assert len(proof) == 1
        assert proof[0]["corrected_text"] == "己"
        assert proof[0]["change_type"] == "glyph"
    finally:
        db.close()
        for f in Path(tmp).glob("PR1.db*"):
            f.unlink()


def test_get_proofreads_empty():
    tmp = tempfile.mkdtemp()
    db = BookDB("PR2", db_dir=tmp)
    try:
        assert db.get_proofreads() == []
    finally:
        db.close()
        for f in Path(tmp).glob("PR2.db*"):
            f.unlink()
