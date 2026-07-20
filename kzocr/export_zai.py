"""DEPRECATED（v0.23.0）：导出模块已迁移至 ``kzocr.doc.export``。

此文件保留为向后兼容的委托层。新代码请直接导入 ``kzocr.doc``：

    from kzocr.doc.export import export_book_markdown, export_json
"""
from __future__ import annotations

import warnings

from kzocr.doc.export import export_book_markdown, export_json  # noqa: F401

warnings.warn(
    "kzocr.export_zai 已弃用，请使用 kzocr.doc.export 替代。",
    DeprecationWarning,
    stacklevel=2,
)
