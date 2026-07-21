"""阶段 1：交付式校对前端（方案 B / Route 1）单测。

验证 CustomDbProofread API + proofread app 路由，
mock 不依赖真实 custom.db。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from kzocr.proofread.api import CustomDbProofread, LineItem


def _make_custom_db(path: Path, book_code: str = "TCM-PF-001",
                    lines_per_page: int = 3, pages: int = 2) -> None:
    """创建可用的 custom.db 测试数据。"""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Book (
            bookCode TEXT PRIMARY KEY, title TEXT, author TEXT, publisher TEXT,
            pubYear INTEGER, pubEra TEXT, bookType TEXT, source TEXT,
            pageCount INTEGER, lineCount INTEGER, cerValue REAL, lineAccuracy REAL,
            isMock INTEGER);
        CREATE TABLE IF NOT EXISTS Page (
            pageNum INTEGER, bookCode TEXT, paragraphCount INTEGER, lineCount INTEGER);
        CREATE TABLE IF NOT EXISTS Paragraph (
            id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT,
            seqInPage INTEGER, isFormulaParagraph INTEGER, verificationStatus TEXT);
        CREATE TABLE IF NOT EXISTS Line (
            id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, paraSeq INTEGER,
            seqInPara INTEGER, engineTexts TEXT, consensus TEXT, llmCorrected TEXT,
            glyphVerified TEXT, final TEXT, humanFinal TEXT, confidence REAL,
            auditSource TEXT, headingLevel INTEGER, disputed INTEGER,
            missingCharAlert TEXT, extraCharAlert TEXT, charLevelJson TEXT);
        CREATE TABLE IF NOT EXISTS Proofread (
            id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, paraSeq INTEGER,
            seqInPara INTEGER, lineId TEXT, originalText TEXT, correctedText TEXT,
            changeType TEXT, severity TEXT, notes TEXT, triggeredPattern TEXT);
    """)
    conn.execute("INSERT INTO Book (bookCode, title, isMock) VALUES (?,?,0)",
                 (book_code, f"{book_code} title"))
    for p in range(1, pages + 1):
        conn.execute("INSERT INTO Page (pageNum, bookCode) VALUES (?,?)", (p, book_code))
        conn.execute(
            "INSERT INTO Paragraph (id, pageNum, bookCode, seqInPage) VALUES (?,?,?,1)",
            (f"{book_code}-P{p}", p, book_code),
        )
        for i in range(1, lines_per_page + 1):
            line_id = f"{book_code}-L{p}-{i}"
            human_final = f"终校文本 P{p} L{i}" if (p == 1 and i <= 2) else None
            conn.execute(
                "INSERT INTO Line (id, pageNum, bookCode, paraSeq, seqInPara,"
                "engineTexts, consensus, humanFinal, confidence, headingLevel, disputed) "
                "VALUES (?,?,?,1,?,?,?,?,0.95,0,0)",
                (line_id, p, book_code, i,
                 json.dumps({"engine_a": f"引擎A P{p} L{i}"}, ensure_ascii=False),
                 f"共识文本 P{p} L{i}",
                 human_final),
            )
    conn.commit()
    conn.close()


# =============================================================================
# CustomDbProofread API
# =============================================================================


class TestCustomDbProofread:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.pkg = Path(tmp_path) / "test.db"
        _make_custom_db(self.pkg, "TCM-PF-001")
        self.db = CustomDbProofread(self.pkg)
        yield
        self.pkg.unlink(missing_ok=True)

    def test_list_books(self):
        books = self.db.list_books()
        assert len(books) == 1
        b = books[0]
        assert b.book_code == "TCM-PF-001"
        assert b.title == "TCM-PF-001 title"
        assert b.page_count == 2
        assert b.line_count == 6
        assert b.proofread_count == 2  # page 1 的前两行

    def test_get_book_info(self):
        b = self.db.get_book_info("TCM-PF-001")
        assert b is not None
        assert b.book_code == "TCM-PF-001"
        assert self.db.get_book_info("NONEXIST") is None

    def test_get_pages(self):
        pages = self.db.get_pages("TCM-PF-001")
        assert pages == [1, 2]

    def test_list_lines_all(self):
        lines = self.db.list_lines("TCM-PF-001")
        assert len(lines) == 6
        assert isinstance(lines[0], LineItem)
        assert lines[0].page_num == 1

    def test_list_lines_by_page(self):
        lines = self.db.list_lines("TCM-PF-001", page=2)
        assert len(lines) == 3
        assert all(ln.page_num == 2 for ln in lines)

    def test_list_lines_pending(self):
        lines = self.db.list_lines("TCM-PF-001", status="pending")
        assert len(lines) == 4  # 6 total - 2 done = 4 pending
        assert all(ln.proofread_status == "pending" for ln in lines)

    def test_list_lines_done(self):
        lines = self.db.list_lines("TCM-PF-001", status="done")
        assert len(lines) == 2
        assert all(ln.proofread_status == "done" for ln in lines)

    def test_list_lines_pagination(self):
        lines = self.db.list_lines("TCM-PF-001", limit=2, offset=0)
        assert len(lines) == 2

    def test_get_line(self):
        line = self.db.get_line("TCM-PF-001", "TCM-PF-001-L1-1")
        assert line is not None
        assert line.page_num == 1
        self.db.get_line("TCM-PF-001", "NONEXIST") is None

    def test_save_human_final(self):
        ok = self.db.save_human_final("TCM-PF-001", "TCM-PF-001-L2-1",
                                      "人工终校 第2页第1行")
        assert ok is True
        line = self.db.get_line("TCM-PF-001", "TCM-PF-001-L2-1")
        assert line is not None
        assert line.human_final == "人工终校 第2页第1行"

    def test_save_empty_clears_human_final(self):
        ok = self.db.save_human_final("TCM-PF-001", "TCM-PF-001-L1-1", "")
        assert ok is True
        line = self.db.get_line("TCM-PF-001", "TCM-PF-001-L1-1")
        assert line.human_final == ""

    def test_count_lines(self):
        assert self.db.count_lines("TCM-PF-001") == 6
        assert self.db.count_lines("TCM-PF-001", page=1) == 3
        assert self.db.count_lines("TCM-PF-001", status="pending") == 4
        assert self.db.count_lines("TCM-PF-001", page=2, status="pending") == 3
        assert self.db.count_lines("TCM-PF-001", status="done") == 2

    def test_get_page_line_count(self):
        assert self.db.get_page_line_count("TCM-PF-001", 1) == 3
        assert self.db.get_page_line_count("TCM-PF-001", 1, status="done") == 2

    def test_constructor_missing_file(self):
        with pytest.raises(FileNotFoundError):
            CustomDbProofread("/nonexistent/path.db")


# =============================================================================
# CustomDbProofread 多书
# =============================================================================


def test_multiple_books(tmp_path):
    pkg = Path(tmp_path) / "multi.db"
    _make_custom_db(pkg, "TCM-BOOK-A", lines_per_page=2, pages=1)
    # 追加第二本书
    conn = sqlite3.connect(str(pkg))
    conn.execute("INSERT INTO Book (bookCode, title, isMock) VALUES (?,?,0)",
                 ("TCM-BOOK-B", "Book B"))
    conn.execute("INSERT INTO Page (pageNum, bookCode) VALUES (1,'TCM-BOOK-B')")
    conn.execute("INSERT INTO Paragraph (id, pageNum, bookCode, seqInPage) VALUES ('pb1',1,'TCM-BOOK-B',1)")
    conn.execute(
        "INSERT INTO Line (id, pageNum, bookCode, paraSeq, seqInPara, consensus) "
        "VALUES ('B-L1',1,'TCM-BOOK-B',1,1,'Book B line 1')"
    )
    conn.commit()
    conn.close()

    db = CustomDbProofread(str(pkg))
    books = db.list_books()
    assert len(books) == 2
    codes = [b.book_code for b in books]
    assert "TCM-BOOK-A" in codes
    assert "TCM-BOOK-B" in codes

    lines_b = db.list_lines("TCM-BOOK-B")
    assert len(lines_b) == 1


# =============================================================================
# FastAPI 路由
# =============================================================================


@pytest.mark.asyncio
async def test_proofread_index(tmp_path):
    pkg = Path(tmp_path) / "web_test.db"
    _make_custom_db(pkg)
    from kzocr.proofread.app import app_factory
    app = app_factory(pkg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/")
        assert resp.status_code == 200
        assert "TCM-PF-001" in resp.text
        assert "2/6" in resp.text


@pytest.mark.asyncio
async def test_proofread_book_review(tmp_path):
    pkg = Path(tmp_path) / "web_test2.db"
    _make_custom_db(pkg)
    from kzocr.proofread.app import app_factory
    app = app_factory(pkg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/book/TCM-PF-001?page=1&status=pending")
        assert resp.status_code == 200
        assert "待校" in resp.text


@pytest.mark.asyncio
async def test_proofread_save_line(tmp_path):
    pkg = Path(tmp_path) / "web_test3.db"
    _make_custom_db(pkg)
    from kzocr.proofread.app import app_factory
    app = app_factory(pkg)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/book/TCM-PF-001/line/TCM-PF-001-L2-1/save",
            data={"human_final": "通过API终校"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # 验证已持久化：查该行现在为 done
        resp2 = await c.get("/book/TCM-PF-001?page=2&status=done")
        assert resp2.status_code == 200
        assert "通过API终校" in resp2.text


@pytest.mark.asyncio
async def test_proofread_export(tmp_path):
    pkg = Path(tmp_path) / "web_test4.db"
    _make_custom_db(pkg)
    from kzocr.proofread.app import app_factory
    app = app_factory(pkg)
    # 先设 KZOCR_PERSIST_DB、KZOCR_DB_DIR 使 import 可写 BookDB
    old_dir = os.environ.get("KZOCR_DB_DIR")
    old_persist = os.environ.get("KZOCR_PERSIST_DB")
    os.environ["KZOCR_DB_DIR"] = str(tmp_path)
    os.environ["KZOCR_PERSIST_DB"] = "1"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/book/TCM-PF-001/export")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["book_code"] == "TCM-PF-001"
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
async def test_proofread_redirect_on_missing_db():
    """未提供 --db 时应有合理错误。"""
    from kzocr.proofread.app import main
    rc = main(["--db", "/nonexistent/custom.db"])
    assert rc == 1
