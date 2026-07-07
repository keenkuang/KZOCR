"""kHUB 客户端：把最终校正文档通过 HTTP API 送入 kHUB 系统。

依赖 kHUB 侧的 `POST /documents`（见 khub-m1/khub/api.py 新增接口）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import os

from .. import config


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
    url = f"{(base_url or config.config.khub_base_url).rstrip('/')}/documents"
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

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_in_khub(doc_id: Optional[str] = None, title: Optional[str] = None) -> list:
    """查询 kHUB 中是否已存在该文档（用于去重/校验）。"""
    url = f"{config.config.khub_base_url.rstrip('/')}/documents"
    if title:
        url += "?" + urllib.parse.urlencode({"title": title})
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("data", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
