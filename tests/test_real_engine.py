"""纯 mock 测试：真实引擎路径 (_run_real) 的路由分发与内部执行。

所有外部依赖均通过 unittest.mock 隔离，不依赖 kimi 引擎包。
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

from kzocr.engine.run import run_engine, _run_real


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def real_cfg():
    """最小 Config 用于路由测试（run_engine dispatcher）。"""
    cfg = MagicMock()
    cfg.use_mock = False
    cfg.use_vlm = False
    cfg.require_real = False
    cfg.kimi_engine_dir = "/fake/engine"
    cfg.khub_base_url = "http://127.0.0.1:8000"
    return cfg


@pytest.fixture
def real_cfg_full():
    """完整字段的 Config，用于 _run_real 内部测试。"""
    cfg = MagicMock()
    cfg.kimi_engine_dir = "/fake/engine"
    cfg.khub_base_url = "http://127.0.0.1:8000"
    cfg.khub_db = ""
    cfg.zai_dir = "/fake/zai"
    cfg.zai_db = ""
    cfg.use_mock = False
    cfg.use_vlm = False
    cfg.require_real = False
    cfg.vlm_engine = "auto"
    cfg.vlm_host = "127.0.0.1"
    cfg.vlm_port = 18080
    cfg.sensenova_api_key = ""
    cfg.sensenova_model = ""
    cfg.sensenova_base_url = ""
    cfg.sensenova_timeout = 180
    cfg.deepseek_api_key = ""
    cfg.deepseek_model = ""
    cfg.deepseek_base_url = ""
    cfg.deepseek_rpm = 20
    cfg.allow_cloud_vision = False
    cfg.kzocr_output_dir = ""
    cfg.cache_ttl_seconds = 86400
    return cfg


@pytest.fixture
def mock_real_env():
    """完整 mock 环境：隔离 _run_real 的所有外部依赖。

    启用后 _run_real 可以无真实引擎包直接调用。
    """
    mock_pipeline = MagicMock()
    mock_pipeline.process_book.return_value = {
        "outputs": {"page1": "/tmp/page1.md"},
    }
    mock_pipeline.current_book_meta = {"title": "测试书籍"}

    mock_mod = types.ModuleType("tcm_ocr.pipeline.book_pipeline")
    mock_mod.BookPipeline = MagicMock(return_value=mock_pipeline)

    patches = [
        patch.dict("sys.modules", {"tcm_ocr.pipeline.book_pipeline": mock_mod}),
        patch("kzocr.engine.run.Path.exists", return_value=True),
        patch("kzocr.engine.run.sys.path", new_callable=list),
        patch("kzocr.engine.run._build_engine_config", return_value={
            "book_library_dir": "/tmp/lib",
            "output_dir": "/tmp/out",
        }),
        patch("kzocr.engine.run._map_cloudllm_env"),
        patch("kzocr.engine.run._read_deliverable",
              return_value="# 测试\n\n正文内容。\n\n第二段。"),
    ]
    for p in patches:
        p.start()
    yield mock_pipeline
    for p in patches:
        p.stop()


# =============================================================================
# TestRunRealRouting — 验证 run_engine 的路由逻辑
# =============================================================================

class TestRunRealRouting:
    """run_engine() 的路由分发测试。"""

    @patch("kzocr.engine.run._run_real")
    def test_routes_to_real(self, mock_run_real, real_cfg):
        """use_mock=False, use_vlm=False → 走 _run_real。"""
        mock_run_real.return_value = MagicMock(is_mock=False, book_code="test")
        result = run_engine("test.pdf", config=real_cfg)
        mock_run_real.assert_called_once()
        assert result.book_code == "test"

    @patch("kzocr.engine.run._run_real")
    def test_real_failure_falls_back_to_mock(self, mock_run_real, real_cfg):
        """_run_real 抛异常 → 降级返回 mock BookResult。"""
        mock_run_real.side_effect = RuntimeError("引擎异常")
        result = run_engine("test.pdf", config=real_cfg)
        assert result.is_mock is True

    @patch("kzocr.engine.run._run_real")
    def test_real_failure_with_require_real_raises(self, mock_run_real, real_cfg):
        """require_real=True + _run_real 抛异常 → 异常透传。"""
        mock_run_real.side_effect = RuntimeError("引擎异常")
        real_cfg.require_real = True
        with pytest.raises(RuntimeError, match="引擎异常"):
            run_engine("test.pdf", config=real_cfg)

    @patch("kzocr.engine.run._run_real")
    def test_mock_takes_precedence_over_real(self, mock_run_real, real_cfg):
        """use_mock=True → 不调用 _run_real。"""
        real_cfg.use_mock = True
        result = run_engine("test.pdf", config=real_cfg)
        mock_run_real.assert_not_called()
        assert result.is_mock is True


# =============================================================================
# TestRunRealInternal — 直接测试 _run_real 的内部逻辑
# =============================================================================

class TestRunRealInternal:
    """_run_real() 内部行为测试（全 mock 环境）。"""

    def test_real_success(self, mock_real_env, real_cfg_full):
        """正常链路：返回 BookResult，包含正确标题和 book_code。"""
        book = _run_real("test.pdf", real_cfg_full, "TCM-001")
        assert book.book_code == "TCM-001"
        assert book.title == "测试书籍"
        assert book.engine_label == "kimi"

    def test_real_reads_deliverable(self, mock_real_env, real_cfg_full):
        """_read_deliverable 返回的内容出现在 final_markdown 中。"""
        expected_md = "# 专用标题\n\n专用正文。"
        with patch("kzocr.engine.run._read_deliverable",
                   return_value=expected_md):
            book = _run_real("test.pdf", real_cfg_full, "TCM-002")
        assert book.final_markdown == expected_md

    def test_real_engine_dir_not_found(self, real_cfg_full):
        """引擎目录不存在 → RuntimeError。"""
        with patch("kzocr.engine.run.Path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="未找到 kimi 引擎目录"):
                _run_real("test.pdf", real_cfg_full)

    def test_real_reconstructs_pages_from_markdown(
            self, mock_real_env, real_cfg_full):
        """无结构化 pages 时从 final_markdown 重建。"""
        md_with_pages = (
            "## 第 1 页\n\n内容1\n\n## 第 2 页\n\n内容2"
        )
        with patch("kzocr.engine.run._read_deliverable",
                   return_value=md_with_pages):
            book = _run_real("test.pdf", real_cfg_full, "TCM-003")
        assert len(book.pages) == 2
        assert book.pages[0].page_num == 1
        assert book.pages[1].page_num == 2

    def test_real_no_pages_no_markdown(self, mock_real_env, real_cfg_full):
        """_read_deliverable 返回空 → pages 也为空。"""
        with patch("kzocr.engine.run._read_deliverable",
                   return_value=""):
            book = _run_real("test.pdf", real_cfg_full, "TCM-004")
        assert book.final_markdown == ""
        assert book.pages == []
