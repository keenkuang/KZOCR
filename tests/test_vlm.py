"""VLM 直接集成模式测试。

测试策略：
- 用 unittest.mock 替换 PaddleOCRVl16Adapter 和 fitz，不启动真实 llama-server
- 路由测试验证 run_engine() 的 use_vlm/use_mock 分支正确性
- 逻辑测试验证 PDF 渲染→VLM 识别→Markdown 拼接→BookResult 的正确性
- 回归测试验证 _run_real 路径不受 VLM 代码影响
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kzocr.config import Config
from kzocr.engine.run import (
    VLM_ENGINE_LABEL,
    _vlm_markdown_to_pages,
    run_engine,
)


# =============================================================================
# run_engine() 路由测试
# =============================================================================


def test_routes_to_vlm_when_use_vlm_is_true():
    """use_vlm=True 且 use_mock=False 时应走 _run_vlm 路径。"""
    cfg = Config(use_vlm=True, use_mock=False)
    with patch("kzocr.engine.run._run_vlm") as mock_vlm:
        mock_vlm.return_value = MagicMock(book_code="VLM-TEST")
        result = run_engine("/fake.pdf", config=cfg)
        mock_vlm.assert_called_once()
        assert result.book_code == "VLM-TEST"


def test_routes_to_real_when_use_vlm_is_false():
    """use_vlm=False 且 use_mock=False 时应走 _run_real 路径。"""
    cfg = Config(use_vlm=False, use_mock=False)
    with patch("kzocr.engine.run._run_real") as mock_real:
        mock_real.return_value = MagicMock(book_code="REAL-TEST")
        result = run_engine("/fake.pdf", config=cfg)
        mock_real.assert_called_once()
        assert result.book_code == "REAL-TEST"


def test_mock_takes_precedence_over_vlm():
    """use_mock=True 时即使 use_vlm=True 也走 mock。"""
    cfg = Config(use_vlm=True, use_mock=True)
    with patch("kzocr.engine.run._run_vlm") as mock_vlm:
        with patch("kzocr.engine.run.build_mock_book") as mock_mock:
            mock_mock.return_value = MagicMock(book_code="MOCK-TEST", is_mock=True)
            result = run_engine("/fake.pdf", config=cfg)
            mock_mock.assert_called_once()
            mock_vlm.assert_not_called()
            assert result.is_mock is True


def test_vlm_failure_falls_back_to_mock():
    """VLM 失败时应降级到 mock（require_real=False）。"""
    cfg = Config(use_vlm=True, use_mock=False, require_real=False)
    with patch("kzocr.engine.run._run_vlm", side_effect=RuntimeError("VLM crashed")):
        with patch("kzocr.engine.run.build_mock_book") as mock_mock:
            mock_mock.return_value = MagicMock(is_mock=True, book_code="MOCK-FALLBACK")
            result = run_engine("/fake.pdf", config=cfg)
            assert result.is_mock is True


def test_vlm_failure_with_require_real_raises():
    """VLM 失败且 require_real=True 时应抛出异常，不降级。"""
    cfg = Config(use_vlm=True, use_mock=False, require_real=True)
    with patch("kzocr.engine.run._run_vlm", side_effect=RuntimeError("VLM crashed")):
        with pytest.raises(RuntimeError, match="VLM crashed"):
            run_engine("/fake.pdf", config=cfg)


# =============================================================================
# _run_vlm 逻辑测试（mock 外部依赖）
# =============================================================================


@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_renders_pdf_pages_to_markdown(mock_init_vlm, mock_fitz_open):
    """验证 PDF 渲染→VLM 识别→Markdown 拼接→BookResult 完整流程。"""
    # Mock PDF: 2 pages
    mock_page = MagicMock()
    mock_page.get_pixmap.return_value = MagicMock(
        samples=b"\xff" * (100 * 200 * 3),
        n=3,
        height=100,
        width=200,
    )

    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 2
    # 使 mock_doc 可迭代（for i, page in enumerate(doc) 需要）
    mock_doc.__iter__.return_value = iter([mock_page, mock_page])
    mock_fitz_open.return_value = mock_doc

        # Mock VLM adapter
        mock_vlm = MagicMock()
        mock_vlm.recognize_page.side_effect = [
            "方用白术三钱，茯苓二钱。",
            "取足三里、合谷以调气和胃。",
        ]
        mock_vlm.recognize_pages.side_effect = [
            "方用白术三钱，茯苓二钱。",
            "取足三里、合谷以调气和胃。",
        ]
    mock_init_vlm.return_value = mock_vlm

    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake_vlm_engine")
    result = run_engine("/fake/book.pdf", book_code="VLM-001", config=cfg)

    assert result.book_code == "VLM-001"
    assert result.engine_label == VLM_ENGINE_LABEL
    assert "## 第 1 页" in result.final_markdown
    assert "## 第 2 页" in result.final_markdown
    assert "方用白术三钱" in result.final_markdown
    assert "取足三里" in result.final_markdown

    # 验证结构化 pages 填充（pages_text 已不含页标题）
    assert len(result.pages) == 2
    assert len(result.pages[0].paragraphs) == 1
    lines_p1 = result.pages[0].paragraphs[0].lines
    # 跨页合并未触发（页末是句号），所以第 1 页只有原文
    assert len(lines_p1) == 1
    assert lines_p1[0].final == "方用白术三钱，茯苓二钱。"
    lines_p2 = result.pages[1].paragraphs[0].lines
    assert len(lines_p2) == 1
    assert lines_p2[0].final == "取足三里、合谷以调气和胃。"

    assert mock_vlm.recognize_page.call_count == 2


@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_multi_line_page(mock_init_vlm, mock_fitz_open):
    """VLM 返回多行文本时应正确拆分为多行 LineResult。"""
    mock_page = MagicMock()
    mock_page.get_pixmap.return_value = MagicMock(
        samples=b"\xff" * (100 * 200 * 3),
        n=3, height=100, width=200,
    )

    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 1
    mock_doc.__iter__.return_value = iter([mock_page])
    mock_fitz_open.return_value = mock_doc

        mock_vlm = MagicMock()
        mock_vlm.recognize_page.return_value = "第一行\n第二行\n第三行"
        mock_vlm.recognize_pages.return_value = "第一行\n第二行\n第三行"
    mock_init_vlm.return_value = mock_vlm

    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
    result = run_engine("/fake.pdf", book_code="MULTI", config=cfg)

    lines = result.pages[0].paragraphs[0].lines
    # 3 行内容（跨页合并未触发，因为页末是"。"）
    assert len(lines) == 3
    assert lines[0].final == "第一行"
    assert lines[1].final == "第二行"
    assert lines[2].final == "第三行"


@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_handles_empty_pdf(mock_init_vlm, mock_fitz_open):
    """空 PDF（0 页）应降级到 mock（不抛异常）。"""
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 0
    mock_fitz_open.return_value = mock_doc

    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
    with patch("kzocr.engine.run.build_mock_book") as mock_mock:
        mock_mock.return_value = MagicMock(is_mock=True, book_code="FALLBACK")
        result = run_engine("/fake/empty.pdf", config=cfg)
        assert result.is_mock is True


# =============================================================================
# _vlm_markdown_to_pages 单元测试
# =============================================================================


def test_vlm_markdown_to_pages_empty():
    """空输入应返回空列表。"""
    pages = _vlm_markdown_to_pages([""])
    assert len(pages) == 1
    assert len(pages[0].paragraphs) == 0


def test_vlm_markdown_to_pages_normal():
    """2 页文本应返回 2 个 PageResult，每页的行数正确。"""
    pages = _vlm_markdown_to_pages(["第一行\n第二行", "第三行"])
    assert len(pages) == 2
    assert len(pages[0].paragraphs[0].lines) == 2
    assert pages[0].paragraphs[0].lines[0].final == "第一行"
    assert pages[0].paragraphs[0].lines[1].final == "第二行"
    assert pages[1].paragraphs[0].lines[0].final == "第三行"


# =============================================================================
# 回归测试：_run_real 不受 VLM 代码影响
# =============================================================================


def test_run_real_regression_unaffected():
    """验证 _run_real 的路径不受 VLM 分支存在的影响。"""
    cfg = Config(use_vlm=False, use_mock=False, require_real=False)
    with patch("kzocr.engine.run._run_real") as mock_real:
        mock_real.return_value = MagicMock(
            book_code="REGR-001", title="回归测试书名",
            engine_label="kimi", final_markdown="回归测试内容",
        )
        with patch("kzocr.engine.run._run_vlm") as mock_vlm:
            result = run_engine("/fake/book.pdf", book_code="REGR-001", config=cfg)
            mock_real.assert_called_once()
            mock_vlm.assert_not_called()
            assert result.book_code == "REGR-001"
            assert result.engine_label == "kimi"
