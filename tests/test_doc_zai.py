"""kzocr/doc/zai.py 烘焙裁图(A)/字符框(B)/来源校验(D) 单元测试。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from kzocr.doc import zai
from kzocr.engine.types import (
    BookResult,
    LineResult,
    PageResult,
    ParagraphResult,
)


def _make_book() -> BookResult:
    line = LineResult(
        engine_texts={"mineru": "当归"},
        consensus="当归",
        final="当归",
    )
    para = ParagraphResult(lines=[line])
    page = PageResult(page_num=1, paragraphs=[para])
    return BookResult(book_code="TESTBOOK", title="测试书", pages=[page])


class _FakeDoc:
    def __getitem__(self, n):
        # _pdf_page_to_numpy 在测试中已被 patch，不读取真实内容
        return object()

    def close(self) -> None:
        pass


class _FakeBookDB:
    def __init__(self, *args, **kwargs):
        self._cbs = [[10, 10, 20, 20], [25, 10, 35, 20]]

    def get_human_final_map(self):
        return {}

    def get_line_char_boxes(self, page_num, para_seq, line_seq):
        return self._cbs

    def close(self) -> None:
        pass


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(zai.fitz, "open", lambda path: _FakeDoc())
    monkeypatch.setattr(
        "kzocr.engine.run._pdf_page_to_numpy",
        lambda page, dpi=150: np.zeros((100, 100, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        "kzocr.engine.run._crop_to_body",
        lambda img, padding=10, page_num=0: img,
    )
    import kzocr.storage.db as _sdb
    monkeypatch.setattr(_sdb, "BookDB", _FakeBookDB)
    monkeypatch.setattr(zai.os.path, "exists", lambda p: True)
    monkeypatch.delenv("KZOCR_CROP_IMG", raising=False)
    monkeypatch.delenv("KZOCR_PACKAGE_KEY", raising=False)
    yield


def _open_custom(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def test_push_writes_crop_img_and_charboxes(tmp_path, patched):
    out = tmp_path / "custom.db"
    res = zai.push_book_to_zai(
        _make_book(), zai_path=out, pdf_path=Path("/fake.pdf"),
        persist_bookdb=False, register_postgres=False,
    )
    assert res["counts"]["lines"] == 1

    conn = _open_custom(out)
    row = conn.execute(
        "SELECT crop_img, charBoxes FROM Line"
    ).fetchone()
    assert row["crop_img"] is not None  # 烘焙 PNG 非 None
    cbs = json.loads(row["charBoxes"])
    assert isinstance(cbs, list) and len(cbs) == 2
    book = conn.execute("SELECT source_pdf FROM Book").fetchone()
    assert book["source_pdf"] == "/fake.pdf"
    conn.close()


def test_push_crop_img_disabled(tmp_path, patched, monkeypatch):
    monkeypatch.setenv("KZOCR_CROP_IMG", "0")
    out = tmp_path / "custom.db"
    zai.push_book_to_zai(
        _make_book(), zai_path=out, pdf_path=Path("/fake.pdf"),
        persist_bookdb=False, register_postgres=False,
    )
    conn = _open_custom(out)
    row = conn.execute("SELECT crop_img, charBoxes FROM Line").fetchone()
    assert row["crop_img"] is None
    assert json.loads(row["charBoxes"])  # charBoxes 仍有
    conn.close()


def test_push_writes_export_meta(tmp_path, patched):
    out = tmp_path / "custom.db"
    zai.push_book_to_zai(
        _make_book(), zai_path=out, pdf_path=Path("/fake.pdf"),
        persist_bookdb=False, register_postgres=False,
    )
    conn = _open_custom(out)
    meta = conn.execute(
        "SELECT tool_version, book_code, source_hash, signature FROM ExportMeta"
    ).fetchone()
    assert meta is not None
    assert meta["book_code"] == "TESTBOOK"
    assert meta["source_hash"] and len(meta["source_hash"]) == 64
    # 无 key 时 signature == source_hash
    assert meta["signature"] == meta["source_hash"]
    assert meta["tool_version"]
    conn.close()


def test_migrate_line_adds_columns(tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(str(old))
    conn.execute(
        "CREATE TABLE Line (id TEXT PRIMARY KEY, pageNum INTEGER, "
        "bookCode TEXT, paraSeq INTEGER, seqInPara INTEGER, engineTexts TEXT, "
        "consensus TEXT, charLevelJson TEXT)"
    )
    conn.execute(
        "CREATE TABLE Book (bookCode TEXT PRIMARY KEY, title TEXT, isMock INTEGER)"
    )
    conn.commit()
    zai._migrate_line(conn)
    line_cols = {r[1] for r in conn.execute("PRAGMA table_info(Line)").fetchall()}
    assert "crop_img" in line_cols and "charBoxes" in line_cols
    book_cols = {r[1] for r in conn.execute("PRAGMA table_info(Book)").fetchall()}
    assert "source_pdf" in book_cols
    conn.close()
