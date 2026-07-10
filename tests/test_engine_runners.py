"""E5 引擎适配器测试 + v0.7 run_engine 集成测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kzocr.adapters.engine_runners import (
    BookPipelineAdapter,
    MockAdapter,
    VlmPageAdapter,
)
from kzocr.engine.types import AdapterPageResult, BookResult, PageInput


# ── MockAdapter ──

def test_mock_adapter_run_book():
    adapter = MockAdapter("TEST-MOCK")
    result = adapter.run_book("fake.pdf")
    assert isinstance(result, BookResult)
    assert result.book_code == "TEST-MOCK"
    assert result.is_mock


def test_mock_adapter_run_page():
    adapter = MockAdapter()
    result = adapter.run_page(PageInput(page_num=0, img=None))  # type: ignore
    assert isinstance(result, AdapterPageResult)
    assert "mock" in result.text


# ── VlmPageAdapter ──

def test_vlm_adapter_run_page_placeholder():
    adapter = VlmPageAdapter("test_vlm")
    pi = PageInput(page_num=0, img=None)  # type: ignore
    result = adapter.run_page(pi)
    assert isinstance(result, AdapterPageResult)
    assert "test_vlm" in result.text


def test_vlm_adapter_run_book_raises():
    adapter = VlmPageAdapter("test_vlm")
    with pytest.raises(NotImplementedError):
        adapter.run_book("fake.pdf")


# ── BookPipelineAdapter ──

def test_book_pipeline_adapter_run_page_raises():
    adapter = BookPipelineAdapter("test_book")
    with pytest.raises(NotImplementedError):
        adapter.run_page(PageInput(page_num=0, img=None))  # type: ignore


def test_book_pipeline_adapter_no_init_raises():
    adapter = BookPipelineAdapter("test_book")
    with pytest.raises(RuntimeError, match="not initialized"):
        adapter.run_book("fake.pdf")


def test_book_pipeline_adapter_with_mock_pipeline():
    adapter = BookPipelineAdapter("test_book")
    mock_pipeline = MagicMock()
    mock_pipeline.process_book.return_value = BookResult(book_code="test", title="test")
    adapter._pipeline = mock_pipeline
    result = adapter.run_book("fake.pdf")
    assert result.book_code == "test"


# ── v0.7 run_engine 集成 ──

@patch("kzocr.engine.run._init_v07_registry")
@patch("kzocr.engine.run.orchestrate_book")
@patch("kzocr.engine.run.enrich_book_result")
def test_run_engine_v07_disabled_by_default(
    mock_enrich, mock_orch, mock_init, monkeypatch
):
    """use_v07 默认 False 时走旧路径（不调用编排）。"""
    from kzocr.config import Config
    from kzocr.engine.run import run_engine
    cfg = Config(use_mock=True)  # v07 不会是默认 False
    result = run_engine("fake.pdf", "test", cfg)
    assert result.is_mock
    mock_orch.assert_not_called()


@patch("kzocr.engine.run._init_v07_registry")
@patch("kzocr.engine.run.orchestrate_book")
@patch("kzocr.engine.run.enrich_book_result")
def test_run_engine_v07_enabled(mock_enrich, mock_orch, mock_init, monkeypatch):
    """use_v07=True 时调用编排路径。"""
    from kzocr.config import Config
    from kzocr.engine.run import run_engine
    mock_reg = MagicMock()
    mock_init.return_value = mock_reg
    mock_orch.return_value = BookResult(book_code="test", title="test v07")
    cfg = Config(use_v07=True, use_mock=True)
    result = run_engine("fake.pdf", "test", cfg)
    assert mock_init.called
    assert mock_orch.called
    assert mock_enrich.called
    assert result.book_code == "test"


@patch("kzocr.engine.run._init_v07_registry")
def test_run_engine_v07_toc_failure_does_not_crash(
    mock_init, monkeypatch
):
    """TOC enrich 失败不拖垮主流程。"""
    from kzocr.config import Config
    from kzocr.engine.run import run_engine
    mock_reg = MagicMock()
    mock_init.return_value = mock_reg
    mock_orch = MagicMock(return_value=BookResult(book_code="test", title="test"))
    monkeypatch.setattr("kzocr.engine.run.orchestrate_book", mock_orch)
    monkeypatch.setattr("kzocr.engine.run.enrich_book_result", MagicMock(side_effect=RuntimeError("toc fail")))
    cfg = Config(use_v07=True, use_mock=True)
    result = run_engine("fake.pdf", "test", cfg)
    assert result is not None
    assert result.book_code == "test"
