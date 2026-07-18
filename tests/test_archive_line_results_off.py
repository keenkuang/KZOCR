"""决策 #2 开关 + 删除 ocr_result_count 测试（无真实 Postgres）。

验证 §3.5：
- 默认 KZOCR_ARCHIVE_LINE_RESULTS 未开 → archive_to_postgresql 不写 OCRLineResultArchive；
  BookContentTree（_archive_content_tree）仍写。
- 开启后 → 写 OCRLineResultArchive。
- runtime_db.get_book_stats 不再引用已删除的 ocr_result_count 列（SQL 校验）。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from kzocr.tcm_ocr.database.postgres import runtime_db as runtime_db_mod
from kzocr.tcm_ocr.pipeline import archival


def test_archive_line_results_off_by_default():
    with patch.object(archival, "_archive_engine_results") as mock_er, \
         patch.object(archival, "_archive_content_tree") as mock_ct, \
         patch.object(archival, "_archive_proofread_records"), \
         patch.object(archival, "_archive_formula_compositions"), \
         patch.object(archival, "_archive_final_document_ref"):
        archival.archive_to_postgresql("B1", MagicMock(), MagicMock())
        mock_er.assert_not_called()      # 决策 #2：默认不写逐行 OCR
        mock_ct.assert_called_once()     # BookContentTree 仍写


def test_archive_line_results_on_when_enabled():
    with patch.dict(os.environ, {"KZOCR_ARCHIVE_LINE_RESULTS": "1"}), \
         patch.object(archival, "_archive_engine_results") as mock_er, \
         patch.object(archival, "_archive_content_tree"), \
         patch.object(archival, "_archive_proofread_records"), \
         patch.object(archival, "_archive_formula_compositions"), \
         patch.object(archival, "_archive_final_document_ref"):
        archival.archive_to_postgresql("B1", MagicMock(), MagicMock())
        mock_er.assert_called_once()     # 显式开启后写 OCRLineResultArchive


def test_get_book_stats_no_ocr_result_count():
    """get_book_stats 的 SQL 不再引用 ocr_result_count / OCRLineResultArchive。"""
    db = runtime_db_mod.RuntimeDB.__new__(runtime_db_mod.RuntimeDB)
    db._connection_pool = None

    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {
        "correction_count": 0,
        "render_count": 0,
        "formula_count": 0,
        "negation_alert_count": 0,
    }

    @contextmanager
    def fake_get_cursor(cursor_factory=None):
        yield fake_cursor

    db.get_cursor = fake_get_cursor  # type: ignore[assignment]

    result = db.get_book_stats(1)
    sql = fake_cursor.execute.call_args[0][0]
    assert "ocr_result_count" not in sql
    assert "OCRLineResultArchive" not in sql
    # 剩余 4 个子查询（correction/render/formula/negation），占位符应为 4
    assert sql.count("%s") == 4
    assert result["correction_count"] == 0
