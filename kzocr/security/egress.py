"""B3: 出站 egress 校验模块 — 代码级硬常量 allowlist，toml 不得增删。

裁决（v0.3 FREEZE）：
- ALLOWED_EGRESS_DOMAINS 写死在代码里，适配器 *.toml 只能配 host/port/timeout/enable
- 云端 base_url 若不在 allowlist → 启动即拒绝
- DNS 复检 + 拒 RFC1918/回环外内网
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

# ── 代码级硬常量 allowlist ──────────────────────────────────────────
# 写死在代码里，toml 不得增删。模式语法：完整域名或 *.suffix 通配前缀。
ALLOWED_EGRESS_DOMAINS: set[str] = {
    # SenseNova（商汤视觉 VLM OCR）
    "token.sensenova.cn",
    # DeepSeek（云端 LLM 校对）
    "api.deepseek.com",
    # ModelScope（免费文本推理）
    "api-inference.modelscope.cn",
    # 硅基流动（VLM / 文档 OCR / 文本校对）
    "api.siliconflow.cn",
    # z.ai（智谱代理）
    "api.z.ai",
    # 智谱（GLM 系列）
    "open.bigmodel.cn",
    # ofox（第三方聚合）
    "api.ofox.io",
}

# ── 内网/保留网段 ──────────────────────────────────────────────────
_PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),          # "this" network
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT
    ipaddress.ip_network("198.18.0.0/15"),      # benchmark
]

# 已知云 metadata IP（额外安全层，不在上列网段内）
_METADATA_IPS = {
    "169.254.169.254",      # AWS/GCP/Azure/Linode
    "metadata.google.internal",  # GCP DNS 名
}


def _is_private_ip(host: str) -> bool:
    """判断 host 是否为内网/保留地址。"""
    if host.lower() in _METADATA_IPS:
        return True

    try:
        addrs = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        logger.warning("[egress] DNS 解析失败，拒绝出站到 %s", host)
        return True

    for family, _type, _proto, _cname, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in _PRIVATE_NETS:
                if ip in net:
                    return True
        except ValueError:
            continue
    return False


def _match_domain(host: str, pattern: str) -> bool:
    """通配域名匹配。pattern 支持 *.suffix 前缀通配。"""
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".sensenova.cn"
        return host.endswith(suffix) or host == suffix[1:]
    return host == pattern


def validate_url(url: str, allowlist: Optional[set[str]] = None) -> str:
    """校验出站 URL 是否在 allowlist 内。

    检查链：
    1. 协议检查 — 仅 http/https
    2. 域名 allowlist 匹配
    3. DNS 复检 + RFC1918/回环拒绝

    Args:
        url: 待校验的 URL。
        allowlist: 可选覆盖域名集合（默认使用 ALLOWED_EGRESS_DOMAINS）。

    Returns:
        校验通过的 URL（尾部去斜杠）。

    Raises:
        ValueError: URL 不合法、不在 allowlist 内、指向内网。
    """
    if allowlist is None:
        allowlist = ALLOWED_EGRESS_DOMAINS

    if not url:
        raise ValueError("URL 为空")

    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme
    host = parsed.hostname or ""

    if scheme not in ("http", "https"):
        raise ValueError(f"不支持的协议：{scheme}（仅 http/https）")

    # 内网/保留地址先于 allowlist 检查（防 SSRF：内网地址无讨论余地）
    if _is_private_ip(host):
        raise ValueError(f"目标地址指向内网/保留地址，已拒绝：{host}")

    allowed = any(_match_domain(host, p) for p in allowlist)
    if not allowed:
        raise ValueError(
            f"域名 {host} 不在 egress allowlist 中。"
            f" 如需新增，请联系安全组修改 kzocr/security/egress.py 的 ALLOWED_EGRESS_DOMAINS"
        )

    if _is_private_ip(host):
        raise ValueError(f"目标地址指向内网/保留地址，已拒绝：{host}")

    return url.rstrip("/")
