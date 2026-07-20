"""DEPRECATED（v0.23.0）：文档模块已迁移至 ``kzocr/doc/``。

此文件保留为向后兼容的委托层。新代码请直接导入 ``kzocr.doc``：

    from kzocr.doc.zai import push_book_to_zai
    from kzocr.doc.export import export_markdown
    from kzocr.doc.proofread import import_proofread_package
    from kzocr.doc.freeze import freeze_custom_db
"""
from __future__ import annotations

import logging
import warnings

from kzocr.doc.zai import (  # noqa: F401 — 向后兼容 re-export
    push_book_to_zai,
    _uid,
    _restrict_db_perms,
    _resolve_db,
    _resolve_bookdb_path,
    _register_postgres_meta,
    _SCHEMA_DDL,
)
from kzocr.doc.export import export_markdown  # noqa: F401
from kzocr.doc.proofread import (  # noqa: F401
    import_proofread_package,
    _import_proofreads_to_postgres,
)
from kzocr.doc.freeze import freeze_custom_db  # noqa: F401

warnings.warn(
    "kzocr.adapter.to_zai_prisma 已弃用，请使用 kzocr.doc 替代。",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)
