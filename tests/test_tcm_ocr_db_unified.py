"""
W10 双 BookDB 统一（保守收口）守卫测试。

核心断言：本项目只有一个系统 of record 的 BookDB —— `kzocr.storage.db.BookDB`。
tcm_ocr 平行栈的 `BookDbConn` 只是其"知识抽取工作台"的连接契约（Protocol），
绝不应当在生产 OCR 链路（非 tcm_ocr 代码）里被当作 BookDB 引用。
"""

from __future__ import annotations

import ast
import os
import sqlite3

import pytest

from kzocr.tcm_ocr.database.sqlite.book_db import BookDB, BookDbConn

_KZOCR_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _iter_py_files(root: str, exclude_substrings: tuple[str, ...]) -> list[str]:
    """递归收集 root 下的 .py 文件，跳过含排除子串的路径。"""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if any(ex in dirpath for ex in exclude_substrings):
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                found.append(os.path.join(dirpath, fn))
    return found


def _bookdb_import_sources(path: str) -> list[str]:
    """解析文件，返回所有把名字 `BookDB` 引入作用域的模块路径（from import）。"""
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    sources: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "BookDB" or alias.asname == "BookDB":
                    sources.append(node.module or "")
    return sources


def test_only_mainline_bookdb_in_production() -> None:
    """生产 OCR 链路（非 tcm_ocr 代码）里的 BookDB 必须全部来自 kzocr.storage.db。"""
    py_files = _iter_py_files(
        os.path.join(_KZOCR_ROOT, "kzocr"),
        exclude_substrings=("tcm_ocr",),
    )
    assert py_files, "kzocr 包下应至少存在一个 .py 文件"

    offenders: list[tuple[str, str]] = []
    for path in py_files:
        for module in _bookdb_import_sources(path):
            if module != "kzocr.storage.db":
                rel = os.path.relpath(path, _KZOCR_ROOT)
                offenders.append((rel, module))

    assert not offenders, (
        "以下生产文件引用了非主线 BookDB（应只引用 kzocr.storage.db.BookDB）："
        + "; ".join(f"{rel} <- {mod}" for rel, mod in offenders)
    )


def test_bookdb_conn_protocol_contract() -> None:
    """BookDbConn 契约要求 get_cursor/execute 等方法。

    如实记录 tcm_ocr 自动发现链路的既有事实：
    - 保留的 `BookDB` 类提供 `get_cursor`（herb/meridian/context/formula 模块所需）；
    - 运行时 `book_pipeline` 传入的 raw `sqlite3.Connection` 缺 `get_cursor`，
      故不满足契约——这正是该链路运行期即坏的 latent bug（非 W10 范围）。
    """
    # BookDbConn 必须是 runtime_checkable（isinstance 检查才有意义）
    assert getattr(BookDbConn, "_is_runtime_protocol", False) or getattr(
        BookDbConn, "__runtime_checkable__", False
    )
    # 保留的 BookDB 类提供知识模块所需的 get_cursor
    assert hasattr(BookDB, "get_cursor")
    # raw sqlite3.Connection 有 execute/commit/close，但无 get_cursor → 不满足契约
    conn = sqlite3.connect(":memory:")
    try:
        assert isinstance(conn, BookDbConn) is False
    finally:
        conn.close()


def test_book_db_initialize_schema_builds_snake_tables(tmp_path) -> None:
    """修复后的 initialize_schema 不应抛 FileNotFoundError，且建出 snake_case 真实表。"""
    db_path = tmp_path / "x.db"
    book_db = BookDB(str(db_path))
    try:
        book_db.initialize_schema()  # 此前会因缺失迁移文件抛 FileNotFoundError
    finally:
        book_db.close()

    raw = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        raw.close()

    for expected in (
        "book_metadata",
        "page",
        "content_node",
        "proofread_record",
        "line_engine_result",
        "formula_composition",
        "formula_ingredient",
        "image_index",
    ):
        assert expected in tables, f"initialize_schema 未建出表: {expected}"


def test_knowledge_modules_reference_protocol() -> None:
    """4 个 knowledge 模块顶层 import 应指向 BookDbConn，而非 tcm_ocr 的 BookDB 类。"""
    module_rel_paths = [
        "kzocr/tcm_ocr/knowledge/herb_pattern/auto_discover.py",
        "kzocr/tcm_ocr/knowledge/meridian_pattern/auto_discover.py",
        "kzocr/tcm_ocr/knowledge/context_pattern/auto_discover.py",
        "kzocr/tcm_ocr/knowledge/formula/extractor.py",
    ]
    for rel in module_rel_paths:
        path = os.path.join(_KZOCR_ROOT, rel)
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)

        imports_bookdb_class = False
        imports_bookdb_conn = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "kzocr.tcm_ocr.database.sqlite.book_db"
            ):
                names = {a.name for a in node.names}
                if "BookDB" in names:
                    imports_bookdb_class = True
                if "BookDbConn" in names:
                    imports_bookdb_conn = True

        assert imports_bookdb_conn, f"{rel} 应 import BookDbConn"
        assert not imports_bookdb_class, (
            f"{rel} 不应再 import tcm_ocr 的 BookDB 类（已统一到 BookDbConn）"
        )


@pytest.mark.skipif(
    os.environ.get("KZOCR_SKIP_TCM_IMPORTS") == "1",
    reason="显式跳过 tcm_ocr 模块导入（依赖较重时）",
)
def test_manager_still_imports_bookdb_class_for_instantiation() -> None:
    """manager 仍实例化 BookDB(db_path)，因此其 import 同时保留 BookDB 与 BookDbConn。"""
    path = os.path.join(_KZOCR_ROOT, "kzocr/tcm_ocr/database/manager.py")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    bookdb_class = False
    bookdb_conn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == (
            "kzocr.tcm_ocr.database.sqlite.book_db"
        ):
            names = {a.name for a in node.names}
            bookdb_class = bookdb_class or ("BookDB" in names)
            bookdb_conn = bookdb_conn or ("BookDbConn" in names)

    assert bookdb_class and bookdb_conn, (
        "manager 需同时 import BookDB（实例化）与 BookDbConn（类型注解）"
    )
