"""适配器：从 ``kzocr.doc`` 模块委托的向后兼容层。

v0.23.0 重构：文档模块已移至 ``kzocr/doc/``。本文件保留为
向后兼容导入路径（``from kzocr.adapter import push_book_to_zai`` 仍可用）。
新代码应直接 ``from kzocr.doc import ...``。
"""
from __future__ import annotations

from kzocr.doc.zai import push_book_to_zai
from kzocr.doc.export import export_markdown
from kzocr.doc.proofread import import_proofread_package, validate_proofread_package
from kzocr.doc.freeze import freeze_custom_db

__all__ = [
    "push_book_to_zai",
    "export_markdown",
    "import_proofread_package",
    "validate_proofread_package",
    "freeze_custom_db",
]
