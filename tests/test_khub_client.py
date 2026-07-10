"""Tests for kzocr/khub/client.py — kHUB 客户端推送/核验/异常体系。

覆盖度目标：100% of kzocr/khub/client.py (22 tests, 4 classes).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from kzocr.khub.client import (
    KHUBError,
    _validate_url,
    push_document,
    verify_in_khub,
)


# ── KHUBError ──────────────────────────────────────────────────────────────


class TestKHUBError:
    """2 tests — 异常体系基础。"""

    def test_is_runtime_error(self):
        """KHUBError 应继承自 RuntimeError。"""
        assert isinstance(KHUBError(), RuntimeError)

    def test_can_raise_and_catch(self):
        """raise KHUBError 应能被 except RuntimeError 捕获。"""
        try:
            raise KHUBError("something went wrong")
        except RuntimeError:
            pass
        else:
            pytest.fail("KHUBError was not caught by except RuntimeError")


# ── _validate_url ──────────────────────────────────────────────────────────


class TestValidateUrl:
    """7 tests — 协议/SSRF/内网拒绝。"""

    @patch("kzocr.khub.client._is_private_host")
    def test_empty_url(self, mock_private: MagicMock):
        """空 URL 应触发 KHUBError('未配置')。"""
        with pytest.raises(KHUBError, match="未配置"):
            _validate_url("")
        mock_private.assert_not_called()

    @patch("kzocr.khub.client._is_private_host")
    def test_ftp_rejected(self, mock_private: MagicMock):
        """非 http/https 协议应拒绝。"""
        with pytest.raises(KHUBError, match="协议不被允许"):
            _validate_url("ftp://host")
        mock_private.assert_not_called()

    @patch("kzocr.khub.client._is_private_host")
    def test_file_rejected(self, mock_private: MagicMock):
        """file:// 协议应拒绝（防 SSRF 本地文件读取）。"""
        with pytest.raises(KHUBError, match="协议"):
            _validate_url("file:///etc/passwd")
        mock_private.assert_not_called()

    @patch("kzocr.khub.client._is_private_host", return_value=True)
    def test_private_host(self, mock_private: MagicMock):
        """内网 host 应触发 KHUBError('内网')。"""
        with pytest.raises(KHUBError, match="内网"):
            _validate_url("http://10.0.0.1:8000")

    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_valid_https(self, mock_private: MagicMock):
        """合法 https URL 应原样返回（尾部去斜杠）。"""
        result = _validate_url("https://api.example.com")
        assert result == "https://api.example.com"

    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_valid_http_localhost(self, mock_private: MagicMock):
        """合法 http 本机 URL 应通过。"""
        result = _validate_url("http://127.0.0.1:8000")
        assert result == "http://127.0.0.1:8000"

    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_trailing_slash_stripped(self, mock_private: MagicMock):
        """尾部斜杠应被去除。"""
        result = _validate_url("http://127.0.0.1:8000/")
        assert result == "http://127.0.0.1:8000"


# ── push_document ─────────────────────────────────────────────────────────


class TestPushDocument:
    """8 tests — 文档推送。"""

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_success(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """正常推送应返回 JSON 响应。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"doc_id":"123"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = push_document("Test Title", "Test content")
        assert result == {"doc_id": "123"}

        # Request 应使用 POST method 并设置 Content-Type
        url_arg = mock_request_cls.call_args[0][0]
        assert "127.0.0.1:8000/documents" in url_arg
        assert mock_request_cls.call_args[1]["method"] == "POST"
        mock_req.add_header.assert_any_call("Content-Type", "application/json")

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_with_source_id(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """传入 source_id 应在 payload 中包含。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"doc_id":"123"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        push_document("Test", "Content", source_id="ext-001")
        payload = json.loads(mock_request_cls.call_args[1]["data"])
        assert payload["source_id"] == "ext-001"

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_with_metadata(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """传入 metadata dict 应在 payload 中包含。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"doc_id":"123"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        meta = {"pages": 3, "source": "scanner"}
        push_document("Test", "Content", metadata=meta)
        payload = json.loads(mock_request_cls.call_args[1]["data"])
        assert payload["metadata"] == meta

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_http_error(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """HTTP 4xx/5xx 应转为 KHUBError。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://127.0.0.1:8000/documents", 400, "Bad Request", {}, None
        )

        with pytest.raises(KHUBError, match="HTTP 400"):
            push_document("Test", "Content")

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_url_error(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """URLError（网络不通）应转为 KHUBError。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(KHUBError, match="无法连接"):
            push_document("Test", "Content")

    @patch.dict(os.environ, {"KHUB_API_TOKEN": "secret-token-123"}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_with_token(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """设置了 KHUB_API_TOKEN 应设置 Authorization header。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok":true}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        push_document("Test", "Content")

        mock_req.add_header.assert_any_call("Authorization", "Bearer secret-token-123")

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_without_token(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """未设置 KHUB_API_TOKEN 不应设置 Authorization。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"doc_id":"123"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        push_document("Test", "Content")

        # 验证 add_header 从未被 Authorization 调用
        for call in mock_req.add_header.call_args_list:
            args, _ = call
            assert args[0] != "Authorization", "不应设置 Authorization header"

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_push_custom_base_url(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """显式传入 base_url 应优先于 config。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"doc_id":"123"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        push_document("Test", "Content", base_url="https://khub.internal:8443")
        url_arg = mock_request_cls.call_args[0][0]
        assert url_arg.startswith("https://khub.internal:8443")


# ── verify_in_khub ────────────────────────────────────────────────────────


class TestVerifyInKhub:
    """5 tests — kHUB 文档核验。"""

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_verify_success(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """正常核验应返回 data 列表。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"data": [{"id": "1", "title": "Doc"}]}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = verify_in_khub()
        assert result == [{"id": "1", "title": "Doc"}]

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_verify_with_title(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """传入 title 应作为查询参数附加。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"data": []}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        verify_in_khub(title="中医古籍")
        url_arg = mock_request_cls.call_args[0][0]
        assert "title=" in url_arg
        assert "中医古籍" in url_arg or "%E4%B8%AD%E5%8C%BB%E5%8F%A4%E7%B1%8D" in url_arg

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_verify_404_returns_empty(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """HTTP 404 应返回空列表（文档不存在）。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://127.0.0.1:8000/documents", 404, "Not Found", {}, None
        )

        result = verify_in_khub()
        assert result == []

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_verify_http_error(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """HTTP 500 应抛出 KHUBError。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://127.0.0.1:8000/documents", 500, "Internal Error", {}, None
        )

        with pytest.raises(KHUBError, match="HTTP 500"):
            verify_in_khub()

    @patch.dict(os.environ, {}, clear=True)
    @patch("kzocr.khub.client.config.config")
    @patch("urllib.request.urlopen")
    @patch("urllib.request.Request")
    @patch("kzocr.khub.client._is_private_host", return_value=False)
    def test_verify_url_error(
        self,
        mock_private: MagicMock,
        mock_request_cls: MagicMock,
        mock_urlopen: MagicMock,
        mock_config: MagicMock,
    ):
        """网络不通应抛出 KHUBError。"""
        mock_config.khub_base_url = "http://127.0.0.1:8000"
        mock_req = MagicMock()
        mock_request_cls.return_value = mock_req

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with pytest.raises(KHUBError, match="无法连接"):
            verify_in_khub()
