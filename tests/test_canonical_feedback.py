"""stage 3 feedback loop acceptance: error_record -> confusion candidates -> learned_confusion.json.

Covers:
- ``BookDB.get_confusion_candidates`` frequency / single-char filtering.
- ``scripts/feedback_canonical_errors.py`` driver dry-run (no disk write) vs apply (writes
  and takes effect downstream).

Note: CJK test data is written as ``\\u`` escapes so the source stays ASCII (the logic is
encoding-agnostic and compares the decoded strings exactly as real e2e data would).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# scripts/ is not a package; inject into sys.path to import the driver by module name
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from kzocr.scheduler.canonical import ErrorRecord
from kzocr.scheduler.cross_align import load_confusion_set
from kzocr.storage.db import BookDB

# canonical confusion test data (decoded from \u escapes at runtime)
QIN = "\u82b9"  # U+82B9
LING = "\u82d3"  # U+82D3
ZHI = "\u7098"  # U+7098
JIU = "\u708e"  # U+708E
MULTI = "\u4f5c\u4e3a\u4e00\u4e2a"  # U+4F5C U+4E3A U+4E00 U+4E2A
MULTI_C = "\u4f5c"  # U+4F5C
ZHUBIAN = "\u4e3b\u7f16"  # U+4E3B U+7F16
ZhuangZhuang = "\u75c7\u72b6\u6076\u98ce\u5bd2"  # U+75C7 U+72B6 U+6076 U+98CE U+5BD2


def _tmp_db(book_code: str = "testfb") -> tuple[BookDB, str]:
    import tempfile

    d = tempfile.mkdtemp()
    return BookDB(book_code, db_dir=d), d


def _save(recs: list[ErrorRecord]) -> BookDB:
    db, _ = _tmp_db()
    db.save_error_records(recs)
    return db


def test_get_confusion_candidates_filters():
    """Single-char high-freq replace qualifies; low-freq/multi-char/delete/insert filtered."""
    db = _save([
        ErrorRecord(1, 1, 0, "RapidOCR", QIN, LING, 10, "replace"),
        ErrorRecord(1, 1, 1, "RapidOCR", QIN, LING, 11, "replace"),
        ErrorRecord(1, 1, 2, "RapidOCR", QIN, LING, 12, "replace"),
        ErrorRecord(1, 1, 3, "RapidOCR", QIN, LING, 13, "replace"),
        ErrorRecord(1, 1, 4, "RapidOCR", QIN, LING, 14, "replace"),
        ErrorRecord(1, 1, 5, "RapidOCR", QIN, LING, 15, "replace"),  # QIN->LING x6
        ErrorRecord(2, 1, 0, "RapidOCR", ZHI, JIU, 20, "replace"),  # low freq x1
        ErrorRecord(3, 1, 0, "RapidOCR", MULTI, MULTI_C, 30, "replace"),  # multi-char noise
        ErrorRecord(4, 1, 0, "RapidOCR", None, ZHUBIAN, 40, "delete"),  # no pair
        ErrorRecord(5, 1, 0, "RapidOCR", ZhuangZhuang, None, 50, "insert"),  # no pair
    ])
    try:
        # default: single-char + min_count=5 -> only QIN->LING
        got = db.get_confusion_candidates(min_count=5)
        assert got == [{"wrong": QIN, "correct": LING, "count": 6}]
        # relax min_count=1 -> both single-char pairs appear
        got2 = db.get_confusion_candidates(min_count=1)
        wrongs = {g["wrong"] for g in got2}
        assert wrongs == {QIN, ZHI}
        # disable single-char filter -> multi-char noise also appears
        got3 = db.get_confusion_candidates(min_count=1, single_char_only=False)
        multi = [g for g in got3 if g["wrong"] == MULTI]
        assert multi and multi[0]["count"] == 1
    finally:
        db.close()


def _build_book_dir(tmp_path: Path, books: dict[str, int]) -> Path:
    """Build per-book dbs under tmp_path, each with N QIN->LING replace errors."""
    db_dir = tmp_path / "books"
    db_dir.mkdir()
    for code, n in books.items():
        db = BookDB(code, db_dir=str(db_dir))
        recs = [
            ErrorRecord(1, 1, i, "RapidOCR", QIN, LING, 100 + i, "replace")
            for i in range(n)
        ]
        db.save_error_records(recs)
        db.close()
    return db_dir


def test_feedback_driver_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys):
    """dry-run (default) only reports candidates, never touches learned_confusion.json."""
    db_dir = _build_book_dir(tmp_path, {"A": 6, "B": 3})  # QIN->LING global 9, 2 books
    learned = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", learned)
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", None)

    from feedback_canonical_errors import run

    summary = run(str(db_dir), min_count=5, apply=False)
    assert summary["candidates"] == 1
    assert summary["added"] == 0
    assert not learned.is_file()  # not written
    out = capsys.readouterr().out
    assert QIN in out and LING in out


def test_feedback_driver_apply_writes_and_takes_effect(tmp_path: Path, monkeypatch):
    """apply writes back, and load_confusion_set(reload) sees it immediately (loop closed)."""
    db_dir = _build_book_dir(tmp_path, {"A": 6, "B": 3})
    learned = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", learned)
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", None)

    from feedback_canonical_errors import run

    summary = run(str(db_dir), min_count=5, apply=True)
    assert summary["added"] == 1
    assert learned.is_file()
    data = json.loads(learned.read_text(encoding="utf-8"))
    assert data[0]["wrong"] == QIN and data[0]["correct"] == LING
    # downstream takes effect immediately
    assert load_confusion_set(reload=True).get(QIN) == LING


def test_feedback_driver_min_freq_filters(tmp_path: Path, monkeypatch):
    """Candidates below min_freq are not written back."""
    db_dir = _build_book_dir(tmp_path, {"A": 2})  # QIN->LING only 2 < 5
    learned = tmp_path / "learned_confusion.json"
    monkeypatch.setattr("kzocr.scheduler.cross_align._LEARNED_CONFUSION_PATH", learned)
    monkeypatch.setattr("kzocr.scheduler.cross_align._CONFUSION_CACHE", None)

    from feedback_canonical_errors import run

    summary = run(str(db_dir), min_count=5, apply=True)
    assert summary["candidates"] == 0
    assert summary["added"] == 0
    assert not learned.is_file()
