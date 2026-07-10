"""VLM 直接集成模式测试。

测试策略：
- 用 unittest.mock 替换 PaddleOCRVl16Adapter 和 fitz，不启动真实 llama-server
- 路由测试验证 run_engine() 的 use_vlm/use_mock 分支正确性
- 逻辑测试验证 PDF 渲染→VLM 识别→Markdown 拼接→BookResult 的正确性
- 回归测试验证 _run_real 路径不受 VLM 代码影响
- D2 测试验证 VLM 主循环重试 + 失败分类增强
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from kzocr.config import Config
from kzocr.engine.run import (
    VLM_ENGINE_LABEL,
    _run_vlm,
    _vlm_markdown_to_pages,
    _compute_config_hash,
    _get_vlm_cache_dir,
)
from kzocr.engines.errors import ApiError, OcrError, OverSizeError






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
    result = _run_vlm("/fake/book.pdf", cfg, "VLM-001")

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
    result = _run_vlm("/fake.pdf", cfg, "MULTI")

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
    with pytest.raises(ValueError, match="PDF 文件为空"):
        _run_vlm("/fake/empty.pdf", cfg, None)


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
    """验证 _run_real 函数可被直接调用不受 VLM 分支影响。"""
    cfg = Config(use_vlm=False, use_mock=False, require_real=False)
    with patch("kzocr.engine.run._run_real") as mock_real:
        mock_real.return_value = MagicMock(
            book_code="REGR-001", title="回归测试书名",
            engine_label="kimi", final_markdown="回归测试内容",
        )
        with patch("kzocr.engine.run._run_vlm") as mock_vlm:
            from kzocr.engine.run import _run_real
            result = _run_real("/fake/book.pdf", cfg, "REGR-001")
            mock_vlm.assert_not_called()
            assert result.book_code == "REGR-001"


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
            result = _run_vlm("/fake/doc.pdf", cfg, "D2-RETRY-OK")

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
            result = _run_vlm("/fake/doc.pdf", cfg, "D2-OS-RETRY")

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
            result = _run_vlm("/fake/doc.pdf", cfg, "D2-MULTI")

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
            result = _run_vlm("/fake/doc.pdf", cfg, "D2-DPI72")

            assert result.book_code == "D2-DPI72"
            assert "dpi72 text" in result.final_markdown
            assert len(result.failed_pages) == 0
            # _pdf_page_to_numpy 至少被调用了 2 次（正常 + 降 DPI）
            assert mock_pdf2np.call_count >= 2
            # 最后一次调用应使用 dpi=72
            last_call = mock_pdf2np.call_args_list[-1]
            _args, kwargs = last_call
            assert kwargs.get("dpi") == 72, f"expected dpi=72, got {kwargs}"


# =============================================================================
# D3: VLM 逐页缓存（断点续跑）
# =============================================================================


class TestD3VlmCache:
    """D3: VLM 逐页缓存测试。

    通过 tmp_path 创建缓存目录，模拟缓存命中/未命中/过期/配置变更等场景。
    """

    @staticmethod
    def _create_cache_file(cache_dir: Path, page_num: int, text: str, config_hash: str):
        """在测试中创建缓存文件。"""
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"page_{page_num}.txt").write_text(text, encoding="utf-8")
        (cache_dir / "config_hash").write_text(config_hash, encoding="utf-8")

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
        doc.__iter__.return_value = iter([TestD3VlmCache._make_mock_page() for _ in range(n_pages)])
        mock_fitz_open.return_value = doc
        return doc

    # ------------------------------------------------------------------
    # 1) 缓存命中 → 跳过 VLM
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_hit_skips_vlm(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """缓存文件存在且 config_hash 匹配 → VLM adapter 不被调用。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )
        config_hash = _compute_config_hash(cfg)
        cache_dir = _get_vlm_cache_dir(cfg, "D3-HIT")
        self._create_cache_file(cache_dir, 1, "缓存页正文。", config_hash)

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-HIT")

        assert "缓存页正文。" in result.final_markdown
        # VLM 不应被调用（缓存命中）
        mock_vlm.recognize_pages.assert_not_called()
        mock_vlm.recognize_page.assert_not_called()
        assert len(result.failed_pages) == 0

    # ------------------------------------------------------------------
    # 2) 缓存未命中 → 正常处理
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_miss_processes_page(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """无缓存文件 → VLM 正常处理。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_vlm.recognize_pages.return_value = "来自VLM的正文。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-MISS")

        assert "来自VLM的正文。" in result.final_markdown
        mock_vlm.recognize_pages.assert_called_once()
        assert len(result.failed_pages) == 0

    # ------------------------------------------------------------------
    # 3) 配置变更 → 缓存失效 → 重新处理
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_config_mismatch_reprocesses(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """缓存存在但 config_hash 不匹配 → VLM 被调用。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_vlm.recognize_pages.return_value = "新配置下识别的正文。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )
        # 使用错误的 hash 创建缓存文件
        cache_dir = _get_vlm_cache_dir(cfg, "D3-CFG")
        self._create_cache_file(cache_dir, 1, "旧缓存的正文。", "wronghash1234567")

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-CFG")

        # 应该是新配置下识别出的正文，不是旧缓存
        assert "新配置下识别的正文。" in result.final_markdown
        assert "旧缓存的正文。" not in result.final_markdown
        mock_vlm.recognize_pages.assert_called_once()

    # ------------------------------------------------------------------
    # 4) TTL 过期 → 缓存失效
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_ttl_expiry(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """缓存文件 mtime 早于 TTL → VLM 被调用。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_vlm.recognize_pages.return_value = "缓存过期后重新识别的正文。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )
        config_hash = _compute_config_hash(cfg)
        cache_dir = _get_vlm_cache_dir(cfg, "D3-TTL")
        self._create_cache_file(cache_dir, 1, "过期缓存正文。", config_hash)
        # 将缓存文件 mtime 设为 epoch（1970-01-01），确保远超 TTL
        page_path = cache_dir / "page_1.txt"
        os.utime(str(page_path), (0, 0))

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-TTL")

        # 应为新识别结果
        assert "缓存过期后重新识别的正文。" in result.final_markdown
        mock_vlm.recognize_pages.assert_called_once()

    # ------------------------------------------------------------------
    # 5) KZOCR_CLEAR_CACHE=1 → 清除旧缓存
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    @patch.dict(os.environ, {"KZOCR_CLEAR_CACHE": "1"})
    def test_clear_cache_env_var(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """KZOCR_CLEAR_CACHE=1 → 已有缓存被清除，VLM 重新处理。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_vlm.recognize_pages.return_value = "清除后重新识别的正文。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )
        config_hash = _compute_config_hash(cfg)
        cache_dir = _get_vlm_cache_dir(cfg, "D3-CLR")
        self._create_cache_file(cache_dir, 1, "将被清除的缓存正文。", config_hash)

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-CLR")

        assert "清除后重新识别的正文。" in result.final_markdown
        mock_vlm.recognize_pages.assert_called_once()

    # ------------------------------------------------------------------
    # 6) kzocr_output_dir 有值 → 启用缓存
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_nonempty_output_dir(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """kzocr_output_dir 有值 → 缓存生效，命中后跳过 VLM。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_vlm.recognize_pages.return_value = "不应被调用。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )
        config_hash = _compute_config_hash(cfg)
        cache_dir = _get_vlm_cache_dir(cfg, "D3-OUTDIR")
        self._create_cache_file(cache_dir, 1, "缓存命中正文。", config_hash)

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-OUTDIR")

        assert "缓存命中正文。" in result.final_markdown
        mock_vlm.recognize_pages.assert_not_called()

    # ------------------------------------------------------------------
    # 7) kzocr_output_dir 为空 → 无缓存行为
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_empty_output_dir(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """kzocr_output_dir="" → 即使目录下有缓存文件也不使用。"""
        self._mock_pdf(mock_fitz_open, n_pages=1)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        mock_vlm.recognize_pages.return_value = "VLM 正常处理结果。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            # kzocr_output_dir 默认为 ""（无缓存）
        )

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-EMPTY")

        assert "VLM 正常处理结果。" in result.final_markdown
        mock_vlm.recognize_pages.assert_called_once()

    # ------------------------------------------------------------------
    # 8) 部分缓存恢复
    # ------------------------------------------------------------------

    @patch("kzocr.engine.run.fitz.open")
    @patch("kzocr.engine.run._init_vlm_adapter")
    def test_cache_partial_recovery(self, mock_init_vlm, mock_fitz_open, tmp_path):
        """前 2 页有缓存，第 3 页无缓存 → 前 2 页跳过 VLM，第 3 页正常处理。"""
        self._mock_pdf(mock_fitz_open, n_pages=3)
        mock_vlm = MagicMock()
        mock_vlm.engine_label = VLM_ENGINE_LABEL
        # 第 3 页的结果
        mock_vlm.recognize_pages.return_value = "第三页正文。"
        mock_init_vlm.return_value = mock_vlm

        cfg = Config(
            use_vlm=True,
            kimi_engine_dir="/tmp/fake",
            kzocr_output_dir=str(tmp_path),
        )
        config_hash = _compute_config_hash(cfg)
        cache_dir = _get_vlm_cache_dir(cfg, "D3-PARTIAL")
        # 只缓存前 2 页
        self._create_cache_file(cache_dir, 1, "第一页缓存。", config_hash)
        self._create_cache_file(cache_dir, 2, "第二页缓存。", config_hash)

        result = _run_vlm("/fake/doc.pdf", cfg, "D3-PARTIAL")

        assert "第一页缓存。" in result.final_markdown
        assert "第二页缓存。" in result.final_markdown
        assert "第三页正文。" in result.final_markdown
        # VLM 只被调用一次（第 3 页）
        assert mock_vlm.recognize_pages.call_count == 1
        assert len(result.failed_pages) == 0
