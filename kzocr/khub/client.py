"""kHUB 客户端：把最终校正文档通过 HTTP API 送入 kHUB 系统。

依赖 kHUB 侧的 `POST /documents`（见 khub-m1/khub/api.py 新增接口）。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .. import config

logger = logging.getLogger(__name__)


class KHUBError(RuntimeError):
    """kHUB 客户端统一异常（继承自 RuntimeError，供 smoke 优雅捕获）。"""


def _validate_url(base_url: str) -> str:
    """校验 kHUB 基址协议与主机，防止 SSRF / 本地文件读取。

    仅允许 http/https；拒绝 file:/ftp: 等非 HTTP 协议与保留/元数据网段。
    """
    if not base_url:
        raise KHUBError("未配置 kHUB 地址（KHUB_BASE_URL）")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise KHUBError(f"kHUB 地址协议不被允许（仅支持 http/https）：{base_url}")
    host = urllib.parse.urlparse(base_url).hostname or ""
    if host in ("169.254.169.254", "metadata.google.internal"):
        raise KHUBError(f"kHUB 地址指向保留/元数据网段，已拒绝：{base_url}")
    if base_url.startswith("http://"):
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "[kHUB] 使用明文 http 且非本机，传输内容（含校正文本）可能被窃听；"
                "建议改用 https 并仅限可信网络"
            )
    return base_url.rstrip("/")


def push_document(
    title: str,
    content: str,
    *,
    source: str = "KZOCR",
    format: str = "markdown",
    source_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    base_url: Optional[str] = None,
) -> dict:
    """推送一份文档到 kHUB，返回其响应（含 version_id）。"""
    url = _validate_url(base_url or config.config.khub_base_url) + "/documents"
    payload: dict = {"title": title, "content": content, "format": format, "source": source}
    if source_id:
        payload["source_id"] = source_id
    if metadata:
        payload["metadata"] = metadata

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    token = os.environ.get("KHUB_API_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    else:
        logger.warning("[kHUB] 未设置 KHUB_API_TOKEN，将以未鉴权方式推送（任何能访问该端口者均可写入）")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise KHUBError(f"kHUB 返回 HTTP {e.code}：{e.reason}") from e
    except urllib.error.URLError as e:
        raise KHUBError(f"无法连接 kHUB（{url}）：{e.reason}") from e


def verify_in_khub(doc_id: Optional[str] = None, title: Optional[str] = None) -> list:
    """查询 kHUB 中是否已存在该文档（用于去重/校验）。"""
    url = _validate_url(config.config.khub_base_url) + "/documents"
    if title:
        url += "?" + urllib.parse.urlencode({"title": title})
    req = urllib.request.Request(url, method="GET")
    token = os.environ.get("KHUB_API_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("data", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise KHUBError(f"kHUB 核验返回 HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise KHUBError(f"无法连接 kHUB：{e.reason}") from e
