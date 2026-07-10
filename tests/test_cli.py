"""CLI 入口测试：命令行解析、路由、错误处理。

测试策略：
- 用 unittest.mock 替换所有外部依赖（engine、adapter、export、khub、config）
- 使用 tmp_path 管理临时文件与工作目录
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kzocr.cli import (
    _safe_out_path,
    build_parser,
    cmd_export,
    cmd_pipeline,
    cmd_push,
    cmd_smoke,
    main,
)
from kzocr.khub.client import KHUBError


# =============================================================================
# TestBuildParser: 命令行参数解析
# =============================================================================


class TestBuildParser:
    """验证 build_parser() 对 4 个子命令及 --version 的参数解析正确性。"""

    def test_pipeline_subcommand(self):
        """pipeline 子命令：仅传 pdf，其余为默认值。"""
        parser = build_parser()
        args = parser.parse_args(["pipeline", "test.pdf"])
        assert args.command == "pipeline"
        assert args.pdf == "test.pdf"
        assert args.book_code is None
        assert args.db is None
        assert args.func == cmd_pipeline

    def test_pipeline_with_options(self):
        """pipeline 子命令：传递 --book-code 和 --db。"""
        parser = build_parser()
        args = parser.parse_args(["pipeline", "test.pdf", "--book-code", "TCM-001", "--db", "/tmp/test.db"])
        assert args.command == "pipeline"
        assert args.pdf == "test.pdf"
        assert args.book_code == "TCM-001"
        assert args.db == "/tmp/test.db"
        assert args.func == cmd_pipeline

    def test_export_subcommand(self):
        """export 子命令：基本用法与选项。"""
        parser = build_parser()
        # 基本用法
        args = parser.parse_args(["export", "TCM-001"])
        assert args.command == "export"
        assert args.book_code == "TCM-001"
        assert args.out is None
        assert args.db is None
        assert args.func == cmd_export
        # 带选项
        args2 = parser.parse_args(["export", "TCM-001", "--out", "out.md", "--db", "x.db"])
        assert args2.out == "out.md"
        assert args2.db == "x.db"

    def test_push_subcommand(self):
        """push 子命令：基本用法与选项。"""
        parser = build_parser()
        # 基本用法
        args = parser.parse_args(["push", "out.md"])
        assert args.command == "push"
        assert args.file == "out.md"
        assert args.title is None
        assert args.source_id is None
        assert args.khub_url is None
        assert args.func == cmd_push
        # 带选项
        args2 = parser.parse_args(["push", "out.md", "--title", "My Doc", "--source-id", "src-1", "--khub-url", "http://khub:8000"])
        assert args2.title == "My Doc"
        assert args2.source_id == "src-1"
        assert args2.khub_url == "http://khub:8000"

    def test_smoke_subcommand(self):
        """smoke 子命令：基本用法与选项。"""
        parser = build_parser()
        # 基本用法
        args = parser.parse_args(["smoke"])
        assert args.command == "smoke"
        assert args.db is None
        assert args.khub_url is None
        assert args.skip_push is False
        assert args.verify is False
        assert args.func == cmd_smoke
        # 带选项
        args2 = parser.parse_args(["smoke", "--skip-push", "--verify", "--db", "smoke_test.db"])
        assert args2.skip_push is True
        assert args2.verify is True
        assert args2.db == "smoke_test.db"

    def test_version(self):
        """--version 应触发 SystemExit 并输出版本字符串。"""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        # --version 通过 argparse 的 action="version" 实现，调用 parser.print_version → sys.exit(0)
        assert exc.value.code == 0


# =============================================================================
# TestSafeOutPath: 导出路径安全限制
# =============================================================================


class TestSafeOutPath:
    """验证 _safe_out_path 把路径限制在 exports/ 基目录下。"""

    def test_basic_path(self, tmp_path):
        """无 out 参数时返回 exports/<book_code>.md。"""
        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            result = _safe_out_path(None, "TCM-001")
            assert result == os.path.join("exports", "TCM-001.md").replace("\\", "/") or os.path.join("exports", "TCM-001.md")
        finally:
            os.chdir(str(cwd))

    def test_with_out(self, tmp_path):
        """有 out 参数时返回 exports/<basename(out)>。"""
        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            result = _safe_out_path("output.md", "TCM-001")
            assert result == os.path.join("exports", "output.md").replace("\\", "/") or os.path.join("exports", "output.md")
        finally:
            os.chdir(str(cwd))

    def test_path_traversal_prevented(self, tmp_path):
        """路径穿越：out="../../etc/passwd" 应只取 basename → exports/passwd。"""
        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            result = _safe_out_path("../../etc/passwd", "TCM-001")
            assert result == os.path.join("exports", "passwd").replace("\\", "/") or os.path.join("exports", "passwd")
        finally:
            os.chdir(str(cwd))


# =============================================================================
# TestCmdPipeline: pipeline 子命令
# =============================================================================


class TestCmdPipeline:
    """验证 cmd_pipeline 配置覆盖、引擎调用与返回值。"""

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.engine_run.run_engine")
    @patch("kzocr.cli.push_book_to_zai")
    def test_pipeline_success(self, mock_push_zai, mock_run_engine, mock_load_config):
        """完整成功路径：引擎运行 → 写入 zai → 返回 0。"""
        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_load_config.return_value = mock_cfg

        mock_book = MagicMock()
        mock_run_engine.return_value = mock_book

        mock_push_zai.return_value = {"book_code": "TCM-001", "counts": "5 页 / 12 行"}

        args = MagicMock(spec=["pdf", "book_code", "db"])
        args.pdf = "/tmp/test.pdf"
        args.book_code = None
        args.db = None
        rc = cmd_pipeline(args)

        assert rc == 0
        mock_run_engine.assert_called_once_with("/tmp/test.pdf", book_code=None, config=mock_cfg)
        mock_push_zai.assert_called_once_with(mock_book, db_path="kzocr.db", skip_prisma_marker=True)

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.engine_run.run_engine")
    @patch("kzocr.cli.push_book_to_zai")
    def test_pipeline_with_db(self, mock_push_zai, mock_run_engine, mock_load_config):
        """--db 指定时应覆盖 cfg.zai_db。"""
        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_load_config.return_value = mock_cfg

        mock_book = MagicMock()
        mock_run_engine.return_value = mock_book
        mock_push_zai.return_value = {"book_code": "TCM-002", "counts": "3 页"}

        args = MagicMock(spec=["pdf", "book_code", "db"])
        args.pdf = "/tmp/test.pdf"
        args.book_code = "TCM-002"
        args.db = "/custom/zai.db"
        rc = cmd_pipeline(args)

        assert rc == 0
        # db 被覆盖
        assert mock_cfg.zai_db == "/custom/zai.db"
        mock_run_engine.assert_called_once_with("/tmp/test.pdf", book_code="TCM-002", config=mock_cfg)
        mock_push_zai.assert_called_once_with(mock_book, db_path="/custom/zai.db", skip_prisma_marker=True)

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.engine_run.run_engine")
    @patch("kzocr.cli.push_book_to_zai")
    def test_pipeline_default_db(self, mock_push_zai, mock_run_engine, mock_load_config):
        """未指定 --db 时 cfg.zai_db 应回退到 "kzocr.db"。"""
        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_load_config.return_value = mock_cfg

        mock_book = MagicMock()
        mock_run_engine.return_value = mock_book
        mock_push_zai.return_value = {"book_code": "TCM-003", "counts": "1 页"}

        args = MagicMock(spec=["pdf", "book_code", "db"])
        args.pdf = "/tmp/test.pdf"
        args.book_code = None
        args.db = None
        rc = cmd_pipeline(args)

        assert rc == 0
        assert mock_cfg.zai_db == "kzocr.db"


# =============================================================================
# TestCmdExport: export 子命令
# =============================================================================


class TestCmdExport:
    """验证 cmd_export 导出流程、文件写入与配置覆盖。"""

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.export_book_markdown")
    def test_export_success(self, mock_export_md, mock_load_config, tmp_path):
        """markdown 导出并写入 exports/ 目录。"""
        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_load_config.return_value = mock_cfg

        mock_export_md.return_value = "# 校正后文档\n\n这是校正后的正文。"
        mock_export_md.return_value = "# 校正后文档"

        args = MagicMock(spec=["book_code", "out", "db"])
        args.book_code = "TCM-001"
        args.out = None
        args.db = None

        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            rc = cmd_export(args)
        finally:
            os.chdir(str(cwd))

        assert rc == 0
        mock_export_md.assert_called_once_with("TCM-001", db_path="kzocr.db")
        # exports 目录应被创建，文件被写入
        exported = tmp_path / "exports" / "TCM-001.md"
        assert exported.exists()
        assert exported.read_text(encoding="utf-8") == "# 校正后文档"

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.export_book_markdown")
    def test_export_with_custom_db(self, mock_export_md, mock_load_config, tmp_path):
        """--db 指定时 cfg.zai_db 被覆盖。"""
        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_load_config.return_value = mock_cfg

        mock_export_md.return_value = "# 自定义库导出"

        args = MagicMock(spec=["book_code", "out", "db"])
        args.book_code = "TCM-002"
        args.out = "custom.md"
        args.db = "/custom/zai.db"

        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            rc = cmd_export(args)
        finally:
            os.chdir(str(cwd))

        assert rc == 0
        assert mock_cfg.zai_db == "/custom/zai.db"
        mock_export_md.assert_called_once_with("TCM-002", db_path="/custom/zai.db")
        exported = tmp_path / "exports" / "custom.md"
        assert exported.exists()


# =============================================================================
# TestCmdPush: push 子命令
# =============================================================================


class TestCmdPush:
    """验证 cmd_push 推送流程、错误处理与标题默认值。"""

    @patch("kzocr.cli.khub_client.push_document")
    def test_push_success(self, mock_push_doc, tmp_path, caplog):
        """推送成功应返回 0 并打印 doc_id。"""
        caplog.set_level("INFO")
        mock_push_doc.return_value = {"doc_id": "doc-123"}

        test_file = tmp_path / "test.md"
        test_file.write_text("# 文档正文", encoding="utf-8")

        args = MagicMock(spec=["file", "title", "source_id", "khub_url"])
        args.file = str(test_file)
        args.title = None
        args.source_id = None
        args.khub_url = None

        rc = cmd_push(args)

        assert rc == 0
        mock_push_doc.assert_called_once()
        call_kwargs = mock_push_doc.call_args[1]
        assert call_kwargs["title"] == "test"  # basename without extension
        assert call_kwargs["content"] == "# 文档正文"
        assert call_kwargs["source"] == "KZOCR"
        assert call_kwargs["source_id"] is None
        assert "已推送至 kHUB（doc_id=doc-123）" in caplog.text

    @patch("kzocr.cli.khub_client.push_document")
    def test_push_khub_error(self, mock_push_doc, tmp_path, caplog):
        """KHUBError 应被捕获并返回 1。"""
        caplog.set_level("ERROR")
        mock_push_doc.side_effect = KHUBError("连接被拒绝")

        test_file = tmp_path / "test.md"
        test_file.write_text("some content", encoding="utf-8")

        args = MagicMock(spec=["file", "title", "source_id", "khub_url"])
        args.file = str(test_file)
        args.title = None
        args.source_id = None
        args.khub_url = None

        rc = cmd_push(args)

        assert rc == 1
        assert "推送失败：连接被拒绝" in caplog.text

    @patch("kzocr.cli.khub_client.push_document")
    def test_push_with_title(self, mock_push_doc, tmp_path):
        """--title 指定时使用自定义 title。"""
        mock_push_doc.return_value = {"doc_id": "doc-456"}

        test_file = tmp_path / "my_doc.md"
        test_file.write_text("正文内容", encoding="utf-8")

        args = MagicMock(spec=["file", "title", "source_id", "khub_url"])
        args.file = str(test_file)
        args.title = "自定义标题"
        args.source_id = "src-001"
        args.khub_url = "http://khub:8000"

        rc = cmd_push(args)

        assert rc == 0
        call_kwargs = mock_push_doc.call_args[1]
        assert call_kwargs["title"] == "自定义标题"
        assert call_kwargs["source_id"] == "src-001"
        assert call_kwargs["base_url"] == "http://khub:8000"


# =============================================================================
# TestCmdSmoke: smoke 子命令
# =============================================================================


class TestCmdSmoke:
    """验证 cmd_smoke 端到端冒烟流程。"""

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.engine_run.run_engine")
    @patch("kzocr.cli.push_book_to_zai")
    @patch("kzocr.cli.export_book_markdown")
    @patch("kzocr.cli.khub_client.push_document")
    def test_smoke_success(
        self, mock_push_doc, mock_export_md, mock_push_zai, mock_run_engine, mock_load_config,
        tmp_path, caplog,
    ):
        """冒烟测试完整 4 步成功，跳过核验。"""
        caplog.set_level("INFO")

        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_cfg.khub_base_url = "http://127.0.0.1:8000"
        mock_load_config.return_value = mock_cfg

        mock_book = MagicMock()
        mock_book.is_mock = True
        mock_book.book_code = "TCM-SMOKE-001"
        mock_book.title = "Smoke Test"
        mock_run_engine.return_value = mock_book

        mock_push_zai.return_value = {"book_code": "TCM-SMOKE-001", "counts": "1 页 / 2 行"}
        mock_export_md.return_value = "# Smoke Test"
        mock_push_doc.return_value = {"doc_id": "smoke-doc-001"}

        args = MagicMock(spec=["db", "khub_url", "skip_push", "verify"])
        args.db = None
        args.khub_url = None
        args.skip_push = False
        args.verify = False

        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            rc = cmd_smoke(args)
        finally:
            os.chdir(str(cwd))

        assert rc == 0
        # 配置：zai_db 设为 smoke.db，use_mock=True
        assert mock_cfg.zai_db == "smoke.db"
        assert mock_cfg.use_mock is True
        # 引擎使用 mock.pdf
        mock_run_engine.assert_called_once_with("mock.pdf", book_code="TCM-SMOKE-001", config=mock_cfg)
        # 推送到 zai
        mock_push_zai.assert_called_once()
        # 导出
        mock_export_md.assert_called_once_with("TCM-SMOKE-001", db_path="smoke.db")
        # 推送 kHUB
        mock_push_doc.assert_called_once()
        # 验证烟雾测试完成
        assert "冒烟测试完成" in caplog.text

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.engine_run.run_engine")
    @patch("kzocr.cli.push_book_to_zai")
    @patch("kzocr.cli.export_book_markdown")
    @patch("kzocr.cli.khub_client.push_document")
    def test_smoke_skip_push(
        self, mock_push_doc, mock_export_md, mock_push_zai, mock_run_engine, mock_load_config,
        tmp_path, caplog,
    ):
        """--skip-push 时不应调用 push_document。"""
        caplog.set_level("INFO")

        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_cfg.khub_base_url = "http://127.0.0.1:8000"
        mock_load_config.return_value = mock_cfg

        mock_book = MagicMock()
        mock_book.is_mock = True
        mock_book.book_code = "TCM-SMOKE-002"
        mock_book.title = "Smoke Test"
        mock_run_engine.return_value = mock_book

        mock_push_zai.return_value = {"book_code": "TCM-SMOKE-002", "counts": "1 页"}
        mock_export_md.return_value = "# Smoke Test"

        args = MagicMock(spec=["db", "khub_url", "skip_push", "verify"])
        args.db = None
        args.khub_url = None
        args.skip_push = True
        args.verify = False

        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            rc = cmd_smoke(args)
        finally:
            os.chdir(str(cwd))

        assert rc == 0
        mock_push_doc.assert_not_called()
        assert "跳过推送" in caplog.text

    @patch("kzocr.cli.load_config")
    @patch("kzocr.cli.engine_run.run_engine")
    @patch("kzocr.cli.push_book_to_zai")
    @patch("kzocr.cli.export_book_markdown")
    @patch("kzocr.cli.khub_client.push_document")
    @patch("kzocr.cli.khub_client.verify_in_khub")
    def test_smoke_verify(
        self, mock_verify, mock_push_doc, mock_export_md, mock_push_zai, mock_run_engine, mock_load_config,
        tmp_path, caplog,
    ):
        """--verify 时应在推送后调用 verify_in_khub。"""
        caplog.set_level("INFO")

        mock_cfg = MagicMock()
        mock_cfg.zai_db = "original.db"
        mock_cfg.khub_base_url = "http://127.0.0.1:8000"
        mock_load_config.return_value = mock_cfg

        mock_book = MagicMock()
        mock_book.is_mock = True
        mock_book.book_code = "TCM-SMOKE-003"
        mock_book.title = "Smoke Test With Verify"
        mock_run_engine.return_value = mock_book

        mock_push_zai.return_value = {"book_code": "TCM-SMOKE-003", "counts": "1 页"}
        mock_export_md.return_value = "# Verify Test"
        mock_push_doc.return_value = {"doc_id": "verify-doc-001"}
        mock_verify.return_value = [{"id": "verify-doc-001", "title": "Smoke Test With Verify"}]

        args = MagicMock(spec=["db", "khub_url", "skip_push", "verify"])
        args.db = None
        args.khub_url = None
        args.skip_push = False
        args.verify = True

        cwd = Path.cwd()
        os.chdir(str(tmp_path))
        try:
            rc = cmd_smoke(args)
        finally:
            os.chdir(str(cwd))

        assert rc == 0
        mock_push_doc.assert_called_once()
        mock_verify.assert_called_once_with("verify-doc-001")
        assert "本地核验" in caplog.text


# =============================================================================
# TestMain: 入口函数错误处理
# =============================================================================


class TestMain:
    """验证 main() 的 dispatch 与异常捕获。"""

    def test_main_pipeline(self):
        """main(["pipeline", "x.pdf"]) 应路由到 cmd_pipeline 并返回 0。"""
        with (
            patch("kzocr.cli.load_config") as mock_load,
            patch("kzocr.cli.engine_run.run_engine") as mock_run,
            patch("kzocr.cli.push_book_to_zai") as mock_push,
        ):
            mock_cfg = MagicMock()
            mock_cfg.zai_db = "original.db"
            mock_load.return_value = mock_cfg
            mock_run.return_value = MagicMock()
            mock_push.return_value = {"book_code": "X-001", "counts": "1 页"}

            rc = main(["pipeline", "x.pdf"])
            assert rc == 0
            mock_run.assert_called_once()

    def test_main_khub_error(self):
        """cmd_push 抛出 KHUBError → main 返回 1。"""
        with (
            patch("kzocr.cli.khub_client.push_document", side_effect=KHUBError("服务不可用")),
        ):
            test_file = Path("/tmp/_test_push_khub_error.md")
            test_file.write_text("content", encoding="utf-8")
            try:
                rc = main(["push", str(test_file)])
                assert rc == 1
            finally:
                if test_file.exists():
                    test_file.unlink()

    def test_main_unexpected_error(self):
        """cmd_pipeline 抛出普通 Exception → main 返回 1。"""
        with (
            patch("kzocr.cli.load_config", side_effect=ValueError("配置文件无效")),
        ):
            rc = main(["pipeline", "x.pdf"])
            assert rc == 1
