"""阶段 0：导入安全校验 + CLI/Web 入口测试（mock 隔离）。

验证：
- ``validate_proofread_package`` 只读 schema + 行数上限校验
- CLI ``kzocr import`` 子命令
- Web ``POST /import`` 路由
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from kzocr.doc import validate_proofread_package, import_proofread_package
from kzocr.doc.proofread import _compute_source_hash
from kzocr.engine.mock import mock_book_result


def _create_minimal_custom_db(path: Path, book_code: str = "TCM-SAFE-001",
                               line_count: int = 2, proofread_count: int = 2) -> None:
    """创建最小可用的 custom.db，schema 对齐 prisma 子集。"""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Book (bookCode TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE IF NOT EXISTS Page (pageNum INTEGER, bookCode TEXT);
        CREATE TABLE IF NOT EXISTS Paragraph (id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, seqInPage INTEGER);
        CREATE TABLE IF NOT EXISTS Line (
            id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, paraSeq INTEGER,
            seqInPara INTEGER, humanFinal TEXT, consensus TEXT, engineTexts TEXT);
        CREATE TABLE IF NOT EXISTS Proofread (
            id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, paraSeq INTEGER,
            seqInPara INTEGER, lineId TEXT, originalText TEXT, correctedText TEXT,
            changeType TEXT, severity TEXT);
        CREATE TABLE IF NOT EXISTS ExportMeta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_version TEXT, book_code TEXT,
            source_hash TEXT, signature TEXT);
    """)
    conn.execute("INSERT INTO Book (bookCode, title) VALUES (?, ?)", (book_code, "测试"))
    conn.execute("INSERT INTO Page (pageNum, bookCode) VALUES (1, ?)", (book_code,))
    conn.execute("INSERT INTO Page (pageNum, bookCode) VALUES (2, ?)", (book_code,))
    conn.execute("INSERT INTO Paragraph (id, pageNum, bookCode, seqInPage) VALUES (?, 1, ?, 1)",
                 (f"{book_code}-P1", book_code))
    for i in range(line_count):
        conn.execute(
            "INSERT INTO Line (id, pageNum, bookCode, paraSeq, seqInPara, humanFinal, consensus, engineTexts) "
            "VALUES (?, 1, ?, 1, ?, '终校文本', '引擎文本', '引擎文本')",
            (f"{book_code}-L{i}", book_code, i + 1),
        )
    for i in range(proofread_count):
        conn.execute(
            "INSERT INTO Proofread (id, pageNum, bookCode, paraSeq, seqInPara, lineId, "
            "originalText, correctedText, changeType, severity) "
            "VALUES (?, 1, ?, 1, ?, ?, '原文本', '修正文本', 'herb', 'low')",
            (f"{book_code}-PR{i}", book_code, i + 1, f"{book_code}-L{i}"),
        )
    # B.1 来源校验：写入与生产 push_book_to_zai 一致的 source_hash（仅哈希不可变源内容）
    conn.row_factory = sqlite3.Row
    src_hash = _compute_source_hash(conn)
    conn.execute(
        "INSERT INTO ExportMeta (tool_version, book_code, source_hash, signature) "
        "VALUES (?,?,?,?)",
        ("test", book_code, src_hash, src_hash),
    )
    conn.commit()
    conn.close()


# =============================================================================
# validate_proofread_package
# =============================================================================


def test_validate_valid_package() -> None:
    """有效包应通过验证并返回计数。"""
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        _create_minimal_custom_db(pkg)
        result = validate_proofread_package(pkg)
        assert result["valid"] is True
        assert result["line_count"] == 2
        assert result["proofread_count"] == 2
    finally:
        pkg.unlink(missing_ok=True)


def test_validate_missing_file() -> None:
    """不存在的文件应抛出 FileNotFoundError。"""
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        if pkg.exists():
            pkg.unlink()
        with pytest.raises(FileNotFoundError, match="不存在"):
            validate_proofread_package(pkg)
    finally:
        pkg.unlink(missing_ok=True)


def test_validate_missing_tables() -> None:
    """缺表的包应抛出 ValueError。"""
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        conn = sqlite3.connect(str(pkg))
        conn.execute("CREATE TABLE Line (id TEXT PRIMARY KEY)")
        conn.close()
        with pytest.raises(ValueError, match="schema 不完整"):
            validate_proofread_package(pkg)
    finally:
        pkg.unlink(missing_ok=True)


def test_validate_too_many_lines() -> None:
    """行数超限应抛出 ValueError。"""
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        # 创建带 Line+Proofread 超过上限的库
        conn = sqlite3.connect(str(pkg))
        conn.execute("CREATE TABLE Book (bookCode TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE Page (pageNum INTEGER, bookCode TEXT)")
        conn.execute("CREATE TABLE Paragraph (id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, seqInPage INTEGER)")
        conn.execute("CREATE TABLE Line (id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT)")
        conn.execute("CREATE TABLE Proofread (id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT)")
        conn.execute("INSERT INTO Book (bookCode) VALUES ('OVERSIZED')")
        # 插入超过上限的行（max_lines=10）
        for i in range(11):
            conn.execute("INSERT INTO Line (id, pageNum, bookCode) VALUES (?, 1, 'OVERSIZED')", (f"L{i}",))
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="行数超限"):
            validate_proofread_package(pkg, max_lines=10)
    finally:
        pkg.unlink(missing_ok=True)


def test_validate_corrupt_db() -> None:
    """损坏的 SQLite 文件应抛出 DatabaseError。"""
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        pkg.write_bytes(b"not a sqlite database")
        with pytest.raises(sqlite3.DatabaseError):
            validate_proofread_package(pkg)
    finally:
        pkg.unlink(missing_ok=True)


def test_validate_readonly_does_not_modify() -> None:
    """校验不应修改源包。"""
    pkg = Path(tempfile.mktemp(suffix=".db"))
    try:
        _create_minimal_custom_db(pkg)
        original = pkg.read_bytes()
        validate_proofread_package(pkg)
        # 校验后文件内容应不变
        assert pkg.read_bytes() == original
    finally:
        pkg.unlink(missing_ok=True)


# =============================================================================
# import_proofread_package 集成验证（默认校验 + register_postgres=False）
# =============================================================================


def test_import_with_default_validation(tmp_path) -> None:
    """import_proofread_package 默认启用前置校验 + register_postgres=False。"""
    book = mock_book_result("TCM-SAFE-002")
    book.is_mock = False
    from kzocr.doc import push_book_to_zai

    old_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = str(tmp_path)
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = Path(tmp_path) / "test.db"
    try:
        res = push_book_to_zai(book, db_path=str(pkg), skip_prisma_marker=True)
        assert res["book_code"] == "TCM-SAFE-002"

        # 修改 humanFinal 模拟人工终校
        conn = sqlite3.connect(str(pkg))
        conn.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1",
            ("方用白术三钱。（终校）",),
        )
        conn.commit()
        conn.close()

        # 不传 skip_validation=True，应自动校验通过
        imp = import_proofread_package(db_path=pkg, book_code="TCM-SAFE-002")
        assert imp["book_code"] == "TCM-SAFE-002"
        assert imp["imported_lines"] >= 1
    finally:
        if old_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist


def test_import_rejects_invalid_package(tmp_path) -> None:
    """import_proofread_package 应拒绝带缺表的不可信包。"""
    pkg = Path(tmp_path) / "bad.db"
    conn = sqlite3.connect(str(pkg))
    conn.execute("CREATE TABLE Line (id TEXT PRIMARY KEY)")
    conn.close()

    with pytest.raises(ValueError, match="schema 不完整"):
        import_proofread_package(db_path=pkg, book_code="TCM-BAD-001", skip_validation=False)


# =============================================================================
# CLI kzocr import
# =============================================================================


def test_cli_import_success(tmp_path) -> None:
    """CLI import 应成功导入并输出结果。"""
    from kzocr.cli import main

    book = mock_book_result("TCM-CLI-001")
    book.is_mock = False
    from kzocr.doc import push_book_to_zai

    old_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = str(tmp_path)
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = Path(tmp_path) / "cli_test.db"
    try:
        push_book_to_zai(book, db_path=str(pkg), skip_prisma_marker=True)
        # 模拟人工终校
        conn = sqlite3.connect(str(pkg))
        conn.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1",
            ("cli 终校文本",),
        )
        conn.commit()
        conn.close()

        rc = main(["import", str(pkg), "--book-code", "TCM-CLI-001"])
        assert rc == 0
    finally:
        if old_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist


def test_cli_import_missing_file() -> None:
    """CLI import 对不存在的文件应返回 1。"""
    from kzocr.cli import main
    rc = main(["import", "/nonexistent/path.db", "--book-code", "NOPE"])
    assert rc == 1


def test_cli_import_path_traversal_rejected(tmp_path) -> None:
    """CLI import 应拒绝路径穿越（路径不在 KZOCR_DB_DIR 内）。"""
    from kzocr.cli import main

    old_dir = os.environ.get("KZOCR_DB_DIR")
    os.environ["KZOCR_DB_DIR"] = str(tmp_path)
    try:
        # 在 KZOCR_DB_DIR 外创建文件
        outside = Path(tempfile.gettempdir()) / "outside_import_test.db"
        try:
            _create_minimal_custom_db(outside)
            rc = main(["import", str(outside), "--book-code", "TCM-TRAV-001"])
            assert rc == 1
        finally:
            outside.unlink(missing_ok=True)
    finally:
        if old_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_dir


# =============================================================================
# Web POST /import
# =============================================================================


@pytest.mark.asyncio
async def test_web_import_missing_file(tmp_path) -> None:
    """Web import 无文件上传时应返回错误。"""
    from kzocr.web.app import app
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=app)  # noqa: F841
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/import")
        assert resp.status_code == 200
        assert "请选择要上传的校对包文件" in resp.text


@pytest.mark.asyncio
async def test_web_import_success(tmp_path) -> None:
    """Web import 上传有效包应返回导入结果。"""
    from kzocr.web.app import app
    from httpx import AsyncClient, ASGITransport
    from kzocr.doc import push_book_to_zai

    book = mock_book_result("TCM-WEB-001")
    book.is_mock = False

    old_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = str(tmp_path)
    os.environ["KZOCR_PERSIST_DB"] = "1"
    pkg = Path(tmp_path) / "web_test.db"
    try:
        push_book_to_zai(book, db_path=str(pkg), skip_prisma_marker=True)
        # 模拟人工终校
        conn = sqlite3.connect(str(pkg))
        conn.execute(
            "UPDATE Line SET humanFinal=? WHERE pageNum=1 AND paraSeq=1 AND seqInPara=1",
            ("web 终校文本",),
        )
        conn.commit()
        conn.close()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with open(pkg, "rb") as f:
                resp = await client.post("/import", files={"file": ("custom.db", f, "application/octet-stream")})
            assert resp.status_code == 200
            assert "TCM-WEB-001" in resp.text
            assert "导入成功" in resp.text
    finally:
        if old_dir is None:
            os.environ.pop("KZOCR_DB_DIR", None)
        else:
            os.environ["KZOCR_DB_DIR"] = old_dir
        if old_persist is None:
            os.environ.pop("KZOCR_PERSIST_DB", None)
        else:
            os.environ["KZOCR_PERSIST_DB"] = old_persist


@pytest.mark.asyncio
async def test_web_import_invalid_package(tmp_path) -> None:
    """Web import 上传 schema 不全的包应返回错误。"""
    from kzocr.web.app import app
    from httpx import AsyncClient, ASGITransport

    pkg = Path(tmp_path) / "bad.db"
    conn = sqlite3.connect(str(pkg))
    conn.execute("CREATE TABLE Line (id TEXT PRIMARY KEY)")
    conn.close()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with open(pkg, "rb") as f:
            resp = await client.post("/import", files={"file": ("bad.db", f, "application/octet-stream")})
        assert resp.status_code == 200
        assert "校对包校验失败" in resp.text
