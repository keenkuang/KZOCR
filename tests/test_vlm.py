"""VLM 直接集成模式测试。

测试策略：
- 用 unittest.mock 替换 PaddleOCRVl16Adapter 和 fitz，不启动真实 llama-server
- 路由测试验证 run_engine() 的 use_vlm/use_mock 分支正确性
- 逻辑测试验证 PDF 渲染→VLM 识别→Markdown 拼接→BookResult 的正确性
- 回归测试验证 _run_real 路径不受 VLM 代码影响
- D2 测试验证 VLM 主循环重试 + 失败分类增强
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kzocr.config import Config
from kzocr.engine.run import (
    VLM_ENGINE_LABEL,
    _vlm_markdown_to_pages,
    run_engine,
)
from kzocr.engines.errors import ApiError, OcrError, OverSizeError


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
    mock_vlm.engine_label = VLM_ENGINE_LABEL  # 真实 _init_vlm_adapter 会设置该属性
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

    assert mock_vlm.recognize_pages.call_count == 2


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


# =============================================================================
# D2: VLM 主循环重试 + 失败分类增强
# =============================================================================


class TestD2VlmRetry:
    """D2: VLM 主循环重试与失败分类测试。

    通过 mock _process_vlm_page 模拟各异常场景，验证重试/跳过/记录逻辑。
    使用 1 页 PDF 避免 side_effect 耗尽；全失败场景直接测试 _run_vlm。
    """

    # ------------------------------------------------------------------
    # 辅助构建 mock PDF
    # ------------------------------------------------------------------

    @staticmethod
    def _make_mock_page() -> MagicMock:
        p = MagicMock()
        p.get_pixmap.return_value = MagicMock(
            samples=b"\xff" * (100 * 200 * 3), n=3, height=100, width=200,
        )
        return p

    @staticmethod
    def _mock_pdf(mock_fitz_open, n_pages: int = 1) -> MagicMock:
        doc = MagicMock()
        doc.__len__.return_value = n_pages
        doc.__iter__.return_value = iter([TestD2VlmRetry._make_mock_page() for _ in range(n_pages)])
        mock_fitz_open.return_value = doc
        return doc

    @staticmethod
    def _numpy_img() -> np.ndarray:
        """返回真实 numpy 数组，避免 _crop_to_body 报错。"""
        return np.zeros((100, 200, 3), dtype=np.uint8)

    # ------------------------------------------------------------------
    # 1) ApiError 重试成功
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    @patch("kzocr.engines.ratelimit.time.sleep")
    def test_api_error_retry_succeeds(self, mock_sleep, mock_init_vlm, mock_fitz_open):
        """ApiError 首次失败 → 重试成功 → 页被处理，无 failed_pages。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._process_vlm_page") as mock_process,
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            mock_process.side_effect = [ApiError("timeout"), "retry ok text"]

            cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
            result = run_engine("/fake/doc.pdf", book_code="D2-RETRY-OK", config=cfg)

            assert result.book_code == "D2-RETRY-OK"
            assert "retry ok text" in result.final_markdown
            assert len(result.failed_pages) == 0, f"got {result.failed_pages}"

    # ------------------------------------------------------------------
    # 2) ApiError 重试耗尽 → 跳过
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    @patch("kzocr.engines.ratelimit.time.sleep")
    def test_api_error_retry_exhausted_skips_page(self, mock_sleep, mock_init_vlm, mock_fitz_open):
        """ApiError 始终失败 → 重试耗尽 → 跳过并记录。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            with patch("kzocr.engine.run._process_vlm_page") as mock_process:
                mock_process.side_effect = ApiError("persistent failure")

                from kzocr.engine.run import _run_vlm
                cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
                with pytest.raises(RuntimeError, match="VLM 全部 1 页识别均失败"):
                    _run_vlm("/fake/doc.pdf", cfg, book_code="D2-RETRY-EXH")

    # ------------------------------------------------------------------
    # 3) OverSizeError 降低 DPI 重试成功
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    @patch("kzocr.engines.ratelimit.time.sleep")
    def test_oversize_error_reduced_dpi(self, mock_sleep, mock_init_vlm, mock_fitz_open):
        """OverSizeError → 降低 DPI 重试 → 成功处理。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._process_vlm_page") as mock_process,
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            mock_process.side_effect = [OverSizeError("too long"), "low dpi text"]

            cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
            result = run_engine("/fake/doc.pdf", book_code="D2-OS-RETRY", config=cfg)

            assert result.book_code == "D2-OS-RETRY"
            assert "low dpi text" in result.final_markdown
            assert len(result.failed_pages) == 0, f"got {result.failed_pages}"

    # ------------------------------------------------------------------
    # 4) OverSizeError 重试耗尽
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    @patch("kzocr.engines.ratelimit.time.sleep")
    def test_oversize_error_exhausted(self, mock_sleep, mock_init_vlm, mock_fitz_open):
        """OverSizeError 始终失败 → 降低 DPI 后仍超长 → 跳页。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            with patch("kzocr.engine.run._process_vlm_page") as mock_process:
                mock_process.side_effect = OverSizeError("persistent oversize")

                from kzocr.engine.run import _run_vlm
                cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
                with pytest.raises(RuntimeError, match="VLM 全部 1 页识别均失败"):
                    _run_vlm("/fake/doc.pdf", cfg, book_code="D2-OS-EXH")

    # ------------------------------------------------------------------
    # 5) OcrError 立即跳过
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_ocr_error_skips_immediately(self, mock_init_vlm, mock_fitz_open):
        """OcrError（非 ApiError/OverSizeError）→ 不重试，立即跳过。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._process_vlm_page") as mock_process,
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            mock_process.side_effect = OcrError("corrupt image")

            from kzocr.engine.run import _run_vlm
            cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
            with pytest.raises(RuntimeError, match="VLM 全部 1 页识别均失败"):
                _run_vlm("/fake/doc.pdf", cfg, book_code="D2-OCR-SKIP")

    # ------------------------------------------------------------------
    # 6) 未知异常跳过
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_unexpected_error_skips(self, mock_init_vlm, mock_fitz_open):
        """Exception（非 OcrError）→ 记录 Unexpected error 并跳过。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._process_vlm_page") as mock_process,
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            mock_process.side_effect = RuntimeError("something unexpected")

            from kzocr.engine.run import _run_vlm
            cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
            with pytest.raises(RuntimeError, match="VLM 全部 1 页识别均失败"):
                _run_vlm("/fake/doc.pdf", cfg, book_code="D2-UNEX")

    # ------------------------------------------------------------------
    # 7) 多页混合失败 → BookResult.failed_pages 含全部记录
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_failed_pages_in_book_result(self, mock_init_vlm, mock_fitz_open):
        """多页混合失败+成功 → failed_pages 含全部失败页。"""
        self._mock_pdf(mock_fitz_open, n_pages=3)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._process_vlm_page") as mock_process,
            patch("kzocr.engine.run._crop_to_body", return_value=self._numpy_img()),
        ):
            # 第1页: OcrError(立即跳过); 第2页: 成功; 第3页: OcrError(立即跳过)
            mock_process.side_effect = [OcrError("fail p1"), "page 2 ok", OcrError("fail p3")]

            cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
            result = run_engine("/fake/doc.pdf", book_code="D2-MULTI", config=cfg)

            assert result.book_code == "D2-MULTI"
            assert 1 in result.failed_pages, f"missing page 1 in {result.failed_pages}"
            assert 2 not in result.failed_pages, f"page 2 should not be in {result.failed_pages}"
            assert 3 in result.failed_pages, f"missing page 3 in {result.failed_pages}"
            assert "OCR failed" in result.failed_pages[1]
            assert "OCR failed" in result.failed_pages[3]
            assert "page 2 ok" in result.final_markdown

    # ------------------------------------------------------------------
    # 8) OverSize 重试时使用 low DPI
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    @patch("kzocr.engines.ratelimit.time.sleep")
    def test_page_retry_uses_lower_dpi_on_oversize(self, mock_sleep, mock_init_vlm, mock_fitz_open):
        """OverSize 重试时验证 _pdf_page_to_numpy 使用 dpi=72。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        with (
            patch("kzocr.engine.run._process_vlm_page") as mock_process,
            patch("kzocr.engine.run._crop_to_body") as mock_crop,
            patch("kzocr.engine.run._pdf_page_to_numpy") as mock_pdf2np,
        ):
            # OverSizeError 触发降 DPI
            mock_process.side_effect = [OverSizeError("too long"), "dpi72 text"]
            mock_pdf2np.return_value = self._numpy_img()
            mock_crop.side_effect = lambda x: x  # 透传

            cfg = Config(use_vlm=True, kimi_engine_dir="/tmp/fake")
            result = run_engine("/fake/doc.pdf", book_code="D2-DPI72", config=cfg)

            assert result.book_code == "D2-DPI72"
            assert "dpi72 text" in result.final_markdown
            assert len(result.failed_pages) == 0
            # _pdf_page_to_numpy 至少被调用了 2 次（正常 + 降 DPI）
            assert mock_pdf2np.call_count >= 2
            # 最后一次调用应使用 dpi=72
            last_call = mock_pdf2np.call_args_list[-1]
            _args, kwargs = last_call
            assert kwargs.get("dpi") == 72, f"expected dpi=72, got {kwargs}"
