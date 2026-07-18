"""GLM-4V / Celery 生产接线回归测试。

锁定 process_book_task 在 KZOCR_PERSIST_DB=1 时调用
``_persist_to_mainline_bookdb`` 的接线：tcm_ocr 的 page_results →
``book_result_from_tcm_ocr`` 转换 → ``BookDB.persist_book_result`` 落库。
直接测试提取出的 helper（Celery 的 bind=True 包装无法直接调用），覆盖真实
的「转换 + 落库」危险区，并读回校验层级与字符级 bbox 完整无损。

无需 broker / 真实引擎，CI 可跑。
"""

from __future__ import annotations

from typing import Any, Dict, List

from kzocr.storage.db import BookDB
from kzocr.tcm_ocr.celery_tasks import tasks as celery_tasks


# 合成 tcm_ocr BookPipeline.page_results（结构以 book_result_convert.py 为准）
SYNTHETIC_PAGE_RESULTS: List[Dict[str, Any]] = [
    {
        "page_number": 1,
        "lines": [
            {
                "bbox": [10, 20, 100, 40],
                "fused_text": "岐黄之术",
                "confidence": 0.95,
                "char_bboxes": [
                    {"bbox": [10, 20, 20, 40]},
                    {"bbox": [25, 20, 35, 40]},
                    {"bbox": [40, 20, 50, 40]},
                    {"bbox": [55, 20, 65, 40]},
                ],
                "engine_results": {"paddle": {"text": "岐黄之术"}},
            },
            {
                "bbox": [10, 50, 100, 70],
                "fused_text": "本草纲目",
                "confidence": 0.92,
                "char_bboxes": [],
                "engine_results": {},
            },
        ],
    }
]


def test_persist_wiring_converts_and_stores(tmp_path) -> None:
    """合成 page_results 经接线转换后落库，读回层级文本与逐字框一致。"""
    db_dir = str(tmp_path)
    book_id = "TEST-BOOK-001"

    celery_tasks._persist_to_mainline_bookdb(
        SYNTHETIC_PAGE_RESULTS, book_id, db_dir
    )

    db = BookDB(book_id, db_dir=db_dir)
    page = db.get_page(1)
    assert page is not None
    assert "岐黄之术" in page["text"]
    assert "本草纲目" in page["text"]

    char_boxes = db.get_page_char_boxes(1)
    assert char_boxes is not None
    # 第一行 4 个逐字框，第二行无字符框
    assert len(char_boxes[0]) == 4
    assert char_boxes[1] == []


def test_persist_wiring_is_nonblocking_on_failure(tmp_path, monkeypatch) -> None:
    """落库异常时不抛出（仅 log），主流程不因落库失败中断。"""
    db_dir = str(tmp_path)
    book_id = "TEST-BOOK-FAIL"

    # 破坏 BookDB.persist 制造落库失败
    def _fail_persist(book, db_dir=""):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(BookDB, "persist_book_result", staticmethod(_fail_persist))

    # helper 内部捕获异常，不应抛出
    celery_tasks._persist_to_mainline_bookdb(
        SYNTHETIC_PAGE_RESULTS, book_id, db_dir
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
