"""W5 可观测性：process_book_task 结构化汇总日志回归测试。

锁定 process_book_task 结束时记的结构化汇总（页数/分歧数/VL 调用数/
BookDB 落库成功与否/耗时）落到 ``celery_task_metrics`` 字段，且落库失败路径
仍不崩溃、bookdb_persisted=False。

无需 broker / 真实引擎，CI 可跑。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

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
        ],
    }
]


# ---------------------------------------------------------------------------
# 1) _log_task_summary 字段完整性
# ---------------------------------------------------------------------------


def test_log_task_summary_fields_complete(caplog) -> None:
    """结构化汇总日志携带全部字段且值正确。"""
    with caplog.at_level(logging.INFO, logger=celery_tasks.logger.name):
        celery_tasks._log_task_summary(
            "B-1",
            status="completed",
            pages=12,
            lines=530,
            divergences=17,
            vl_calls=0,
            elapsed=42.7,
            bookdb_persisted=True,
        )

    assert caplog.records, "应产生一条汇总日志"
    record = caplog.records[-1]
    metrics = record.celery_task_metrics
    assert metrics == {
        "book_id": "B-1",
        "status": "completed",
        "pages": 12,
        "lines": 530,
        "divergences": 17,
        "vl_calls": 0,
        "elapsed_seconds": 42.7,
        "bookdb_persisted": True,
    }
    # 可读消息带 persisted 标签
    assert "bookdb_persisted=ok" in record.message


def test_log_task_summary_persist_skipped_label(caplog) -> None:
    """落库未开启时 bookdb_persisted=None，消息标记为 n/a。"""
    with caplog.at_level(logging.INFO, logger=celery_tasks.logger.name):
        celery_tasks._log_task_summary(
            "B-2",
            status="skipped",
            pages=0,
            lines=0,
            divergences=0,
            vl_calls=0,
            elapsed=0.0,
            bookdb_persisted=None,
        )
    record = caplog.records[-1]
    assert record.celery_task_metrics["bookdb_persisted"] is None
    assert "bookdb_persisted=n/a" in record.message


# ---------------------------------------------------------------------------
# 2) _persist_to_mainline_bookdb 返回三态
# ---------------------------------------------------------------------------


def test_persist_returns_true_on_success(tmp_path) -> None:
    """落库成功返回 True。"""
    ok = celery_tasks._persist_to_mainline_bookdb(
        SYNTHETIC_PAGE_RESULTS, "P-OK", str(tmp_path)
    )
    assert ok is True
    db = BookDB("P-OK", db_dir=str(tmp_path))
    assert db.get_page(1) is not None


def test_persist_returns_false_on_failure(tmp_path, monkeypatch) -> None:
    """落库失败返回 False（且不抛出）。"""
    def _fail(book, db_dir=""):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(BookDB, "persist_book_result", staticmethod(_fail))
    ok = celery_tasks._persist_to_mainline_bookdb(
        SYNTHETIC_PAGE_RESULTS, "P-FAIL", str(tmp_path)
    )
    assert ok is False


# ---------------------------------------------------------------------------
# 3) process_book_task 集成：汇总字段与 result 一致
# ---------------------------------------------------------------------------


class _FakeTask:
    """模拟 bind=True 任务实例（仅需 request.retries 与 update_state）。"""

    def __init__(self) -> None:
        self.request = SimpleNamespace(retries=0)

    def update_state(self, *args: Any, **kwargs: Any) -> None:
        pass


class _FakeBookPipeline:
    """替代真实 BookPipeline，返回可控的 result 与 page_results。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.page_results: List[Dict[str, Any]] = []

    def process_book(self, pdf_path: str, book_id: str) -> Dict[str, Any]:
        self.page_results = SYNTHETIC_PAGE_RESULTS
        return {
            "book_id": book_id,
            "pdf_path": pdf_path,
            "status": "completed",
            "pages_processed": 12,
            "lines_processed": 530,
            "disputed_lines": 17,
            "formulas_extracted": 3,
            "elapsed_seconds": 42.7,
            "outputs": {},
        }


def _run_task(pdf_path: str, book_id: str, config: Dict[str, Any], monkeypatch) -> None:
    """以 fake self 调 process_book_task 原始函数，确保 DB 分层闭环路径被真实执行。"""
    monkeypatch.setattr(celery_tasks, "BookPipeline", _FakeBookPipeline)
    raw = celery_tasks.process_book_task.__wrapped__.__func__
    raw(_FakeTask(), pdf_path, book_id, config)


def test_process_book_task_summary_emitted(caplog, tmp_path, monkeypatch) -> None:
    """任务成功时结构化汇总反映 result 的页数/分歧/耗时，落库未开启为 None。"""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    config = {
        "book_library_dir": str(tmp_path / "lib"),
        "db_dir": str(tmp_path / "db"),
    }
    monkeypatch.setenv("KZOCR_PERSIST_DB", "0")

    with caplog.at_level(logging.INFO, logger=celery_tasks.logger.name):
        _run_task(str(pdf), "INT-1", config, monkeypatch)

    summary = [r for r in caplog.records if hasattr(r, "celery_task_metrics")]
    assert summary, "应产生结构化汇总日志"
    metrics = summary[-1].celery_task_metrics
    assert metrics["status"] == "completed"
    assert metrics["pages"] == 12
    assert metrics["lines"] == 530
    assert metrics["divergences"] == 17
    assert metrics["vl_calls"] == 0
    assert metrics["elapsed_seconds"] == 42.7
    assert metrics["bookdb_persisted"] is None


def test_process_book_task_summary_persist_ok(caplog, tmp_path, monkeypatch) -> None:
    """KZOCR_PERSIST_DB=1 时落库成功，bookdb_persisted=True。"""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    config = {
        "book_library_dir": str(tmp_path / "lib"),
        "db_dir": str(tmp_path / "db"),
    }
    monkeypatch.setenv("KZOCR_PERSIST_DB", "1")

    with caplog.at_level(logging.INFO, logger=celery_tasks.logger.name):
        _run_task(str(pdf), "INT-OK", config, monkeypatch)

    metrics = [r for r in caplog.records if hasattr(r, "celery_task_metrics")][-1].celery_task_metrics
    assert metrics["bookdb_persisted"] is True


def test_process_book_task_summary_persist_fail(caplog, tmp_path, monkeypatch) -> None:
    """KZOCR_PERSIST_DB=1 且落库失败时，bookdb_persisted=False 且不抛。"""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    config = {
        "book_library_dir": str(tmp_path / "lib"),
        "db_dir": str(tmp_path / "db"),
    }
    monkeypatch.setenv("KZOCR_PERSIST_DB", "1")

    def _fail(book, db_dir=""):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(BookDB, "persist_book_result", staticmethod(_fail))

    with caplog.at_level(logging.INFO, logger=celery_tasks.logger.name):
        # 不应抛出
        _run_task(str(pdf), "INT-FAIL", config, monkeypatch)

    metrics = [r for r in caplog.records if hasattr(r, "celery_task_metrics")][-1].celery_task_metrics
    assert metrics["bookdb_persisted"] is False


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
