"""KZOCR 回归测试：mock 引擎 → 适配器写 zai 库 → 导出 Markdown。

不依赖 MinerU/PaddleOCR/torch/LLM，也不依赖外部 kHUB 服务。
"""
from __future__ import annotations

import sqlite3
import tempfile

from kzocr.engine.mock import mock_book_result
from kzocr.adapter.to_zai_prisma import push_book_to_zai, export_markdown
from kzocr.export_zai import export_book_markdown


def test_mock_engine_produces_expected_book():
    book = mock_book_result("TCM-TEST-001")
    assert book.is_mock is True
    assert book.book_code == "TCM-TEST-001"
    assert len(book.pages) == 2
    assert len(book.herb_patterns) == 2
    assert len(book.meridian_patterns) == 1
    assert len(book.context_patterns) == 1
    assert len(book.formulas) == 1


def test_push_to_zai_writes_all_tables():
    book = mock_book_result("TCM-TEST-001")
    db = tempfile.mktemp(suffix=".db")
    res = push_book_to_zai(book, db_path=db, skip_prisma_marker=True)
    assert res["book_code"] == "TCM-TEST-001"

    con = sqlite3.connect(db)
    try:
        counts = {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("Book", "Page", "Line", "Paragraph", "Proofread",
                      "Pattern", "Term", "Formula", "FormulaIngredient")
        }
    finally:
        con.close()

    assert counts["Book"] == 1
    assert counts["Page"] == 2
    assert counts["Line"] == 3
    assert counts["Proofread"] == 2
    assert counts["Pattern"] == 4
    assert counts["Term"] == 2
    assert counts["Formula"] == 1
    assert counts["FormulaIngredient"] == 2


def test_export_markdown_roundtrip():
    book = mock_book_result("TCM-TEST-001")
    db = tempfile.mktemp(suffix=".db")
    push_book_to_zai(book, db_path=db, skip_prisma_marker=True)

    md = export_book_markdown("TCM-TEST-001", db_path=db)
    assert "白术" in md
    assert "足三里" in md
    assert "三大永久范式库" in md


def test_adapter_export_markdown_from_object():
    book = mock_book_result("TCM-TEST-001")
    md = export_markdown(book)
    assert "取足三里" in md


if __name__ == "__main__":
    test_mock_engine_produces_expected_book()
    test_push_to_zai_writes_all_tables()
    test_export_markdown_roundtrip()
    test_adapter_export_markdown_from_object()
    print("全部测试通过 ✅")
