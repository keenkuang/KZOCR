"""BookPipelineAdapter.run_book 闭环测试（无真实 kimi 引擎）。

验证 §3.2：注入 pipeline_config + book_code 后返回主线 BookResult，
book_code 正确，且 process_book 收到真实 book_code（未再误传 engine_name）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kzocr.adapters.engine_runners import BookPipelineAdapter
from kzocr.engine.types import BookResult

PAGE_RESULTS = [
    {
        "page_number": 1,
        "lines": [
            {"bbox": [10, 100, 200, 120], "fused_text": "当归一两", "confidence": 0.95},
        ],
    },
]


def _make_adapter() -> BookPipelineAdapter:
    # 不传 pipeline_config，避免触发真实 BookPipeline.__init__（写盘）；
    # 直接注入 mock _pipeline。run_book 见 _pipeline 已存在即跳过初始化检查。
    adapter = BookPipelineAdapter("kimi")
    mock_pipeline = MagicMock()
    mock_pipeline.page_results = PAGE_RESULTS
    adapter._pipeline = mock_pipeline
    return adapter


def test_run_book_returns_book_result_with_book_code():
    adapter = _make_adapter()
    result = adapter.run_book("fake.pdf", book_code="TCM-ADP-001")
    assert isinstance(result, BookResult)
    assert result.book_code == "TCM-ADP-001"
    assert len(result.pages) == 1
    assert result.pages[0].page_num == 1
    assert result.pages[0].paragraphs[0].lines[0].final == "当归一两"


def test_run_book_passes_book_code_to_process_book():
    adapter = _make_adapter()
    adapter.run_book("fake.pdf", book_code="TCM-ADP-002")
    # process_book 收到真实 book_code，而非 engine_name "kimi"（修复旧 bug）
    args, kwargs = adapter._pipeline.process_book.call_args
    assert args[0] == "fake.pdf"
    assert args[1] == "TCM-ADP-002"


def test_run_book_requires_pipeline_config():
    adapter = BookPipelineAdapter("kimi")  # 无 pipeline_config
    with pytest.raises(RuntimeError, match="pipeline_config"):
        adapter.run_book("fake.pdf", book_code="X")


def test_run_book_defaults_unknown_book_code():
    adapter = _make_adapter()
    # book_code 为空字符串 → 降级 "TCM-UNK"，不崩溃
    result = adapter.run_book("fake.pdf", book_code="")
    assert result.book_code == "TCM-UNK"
