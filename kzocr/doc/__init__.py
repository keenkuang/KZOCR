"""kzocr.doc — 文档模块（zai 校对台 / 导出 / 校对导入 / 冻结）。

v0.23.0 重构：从 ``kzocr/adapter/to_zai_prisma.py`` 拆分而来。
"""
from __future__ import annotations

from kzocr.doc.zai import push_book_to_zai
from kzocr.doc.export import export_markdown, export_book_markdown, export_json
from kzocr.doc.proofread import import_proofread_package
from kzocr.doc.freeze import freeze_custom_db

__all__ = [
    "push_book_to_zai",
    "export_markdown",
    "export_book_markdown",
    "export_json",
    "import_proofread_package",
    "freeze_custom_db",
]
