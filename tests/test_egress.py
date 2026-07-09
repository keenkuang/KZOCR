"""B3: egress allowlist 测试。"""
from __future__ import annotations

import pytest

from kzocr.security.egress import (
    ALLOWED_EGRESS_DOMAINS,
    _is_private_ip,
    _match_domain,
    validate_url,
)


class TestMatchDomain:
    def test_exact_match(self):
        assert _match_domain("api.deepseek.com", "api.deepseek.com") is True

    def test_wildcard_match(self):
        assert _match_domain("token.sensenova.cn", "*.sensenova.cn") is True

    def test_subdomain_wildcard(self):
        assert _match_domain("foo.sensenova.cn", "*.sensenova.cn") is True

    def test_no_match(self):
        assert _match_domain("evil.com", "api.deepseek.com") is False

    def test_wildcard_no_match(self):
        assert _match_domain("evil.com", "*.sensenova.cn") is False


class TestIsPrivateIp:
    def test_loopback(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_rfc1918(self):
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("192.168.1.1") is True

    def test_metadata(self):
        assert _is_private_ip("169.254.169.254") is True

    def test_public_ip(self):
        # api.deepseek.com should resolve to a public IP
        assert _is_private_ip("api.deepseek.com") is False


class TestValidateUrl:
    def test_valid_sensenova(self):
        result = validate_url("https://token.sensenova.cn/v1/chat")
        assert "token.sensenova.cn" in result

    def test_valid_deepseek(self):
        result = validate_url("https://api.deepseek.com/v1/chat")
        assert "api.deepseek.com" in result

    def test_empty_url(self):
        with pytest.raises(ValueError, match="URL 为空"):
            validate_url("")

    def test_invalid_scheme(self):
        with pytest.raises(ValueError, match="不支持的协议"):
            validate_url("file:///etc/passwd")

    def test_domain_not_in_allowlist(self):
        with pytest.raises(ValueError, match="不在 egress allowlist"):
            validate_url("https://evil.com/api")

    def test_private_ip_rejected(self):
        with pytest.raises(ValueError, match="内网/保留地址"):
            validate_url("https://10.0.0.1/api")

    def test_loopback_rejected(self):
        with pytest.raises(ValueError, match="内网/保留地址"):
            validate_url("https://127.0.0.1/api")

    def test_custom_allowlist(self):
        custom = {"api.deepseek.com"}
        result = validate_url("https://api.deepseek.com/v1/chat", allowlist=custom)
        assert "api.deepseek.com" in result

    def test_allowed_domains_are_valid(self):
        """所有 allowlist 域名应能 DNS 解析且非内网。"""
        for domain in ALLOWED_EGRESS_DOMAINS:
            try:
                validate_url(f"https://{domain}")
            except ValueError as e:
                pytest.fail(f"allowlist 域名 {domain} 校验失败：{e}")
