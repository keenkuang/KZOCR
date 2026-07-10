"""校对工单 CLI 测试。"""

from __future__ import annotations

import os
import tempfile

import pytest

from kzocr.engine.types import GlyphVerdict
from kzocr.storage.db import BookDB


@pytest.fixture
def db_with_anomalies():
    """创建含异常记录的临时 DB。"""
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    db = BookDB("test-book", db_dir=td)
    db.init_page(0)
    db.init_page(1)
    db.record_anomaly(0, GlyphVerdict(status="FAIL", confidence=1.0, details="toxin_dose"), ["ToxinDoseDetector"])
    db.record_anomaly(1, GlyphVerdict(status="UNKNOWN", confidence=0.6, details="confusion"), ["ConfusionSetDetector"])
    db.close()
    yield td
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)


# ── DB 方法测试 ──

def test_get_unresolved_anomalies(db_with_anomalies):
    db = BookDB("test-book", db_dir=db_with_anomalies)
    items = db.get_unresolved_anomalies("test-book")
    assert len(items) == 2
    assert items[0]["verdict_status"] == "FAIL"
    assert items[1]["verdict_status"] == "UNKNOWN"
    db.close()


def test_resolve_anomaly(db_with_anomalies):
    db = BookDB("test-book", db_dir=db_with_anomalies)
    items = db.get_unresolved_anomalies("test-book")
    assert len(items) == 2
    db.resolve_anomaly(items[0]["id"], resolution="fixed", note="确认修正")
    remaining = db.get_unresolved_anomalies("test-book")
    assert len(remaining) == 1
    db.close()


# ── CLI 模拟测试 ──

def test_review_list_no_anomalies(monkeypatch):
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    db = BookDB("empty-book", db_dir=td)
    db.close()
    from kzocr.cli_review import cmd_review_list
    from argparse import Namespace
    args = Namespace(book_code="empty-book", limit=50)
    rc = cmd_review_list(args)
    assert rc == 0
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)


def test_review_show_resolve(monkeypatch, capsys):
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    db = BookDB("demo", db_dir=td)
    db.init_page(5)
    db.record_anomaly(5, GlyphVerdict(status="FAIL", confidence=1.0), ["TestDet"])
    db.close()
    from kzocr.cli_review import cmd_review_show, cmd_review_resolve
    from argparse import Namespace
    args = Namespace(book_code="demo", id=1)
    rc = cmd_review_show(args)
    assert rc == 0
    args2 = Namespace(book_code="demo", id=1, status="fixed", note="已修正")
    rc2 = cmd_review_resolve(args2)
    assert rc2 == 0
    db2 = BookDB("demo", db_dir=td)
    assert len(db2.get_unresolved_anomalies("demo")) == 0
    db2.close()
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)
