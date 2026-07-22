"""BookDB canonical_char / engine_char_record / error_record 落库与统计测试。"""

from __future__ import annotations

import tempfile

from kzocr.storage.db import BookDB
from kzocr.scheduler.canonical import (
    ErrorRecord,
    build_canonical_chars,
)


def _tmp_db():
    d = tempfile.mkdtemp()
    return BookDB("testcanon", db_dir=d), d


def test_save_and_get_canonical_chars():
    db, d = _tmp_db()
    chars = build_canonical_chars(
        {"paddleocrv6": "甲丙", "rapidocr": "甲乙"},
        "甲丙", [[0, 0, 9, 9], [10, 0, 19, 9]],
        (1, 1, 1), primary_engine="paddleocrv6",
    )
    n = db.save_canonical_chars("testcanon", chars)
    assert n == 2
    got = db.get_canonical_chars(1)
    assert len(got) == 2
    assert got[1].char_text == "丙"
    # consensus=丙；paddleocrv6 读 丙 与 consensus 一致 → final=paddleocrv6
    assert got[1].final_engine == "paddleocrv6"
    assert len(got[1].engine_records) == 2
    # engine_char_record 落库验证
    engines = {r.engine: r.char_text for r in got[1].engine_records}
    assert engines == {"paddleocrv6": "丙", "rapidocr": "乙"}
    db.close()


def test_save_and_get_error_records():
    db, d = _tmp_db()
    recs = [
        ErrorRecord(1, 1, 0, "rapidocr", "丙", "乙", 10, "replace"),
        ErrorRecord(1, 1, 1, "rapidocr", "丁", "甲", 11, "replace"),
    ]
    assert db.save_error_records(recs) == 2
    got = db.get_error_records(page_no=1)
    assert len(got) == 2
    assert db.get_error_records(engine="rapidocr")[0]["wrong_char"] == "丙"
    db.close()


def test_get_error_stats():
    db, d = _tmp_db()
    # 造 2 个 canonical 字（每字 rapidocr 记录）→ rapidocr authored 2 字
    chars = build_canonical_chars(
        {"paddleocrv6": "甲乙", "rapidocr": "甲乙"},
        "甲乙", [[0, 0, 9, 9], [10, 0, 19, 9]],
        (1, 1, 1), primary_engine="paddleocrv6",
    )
    db.save_canonical_chars("testcanon", chars)
    # rapidocr 错 1 次（把 甲 读成 丙）
    db.save_error_records([
        ErrorRecord(1, 1, 0, "rapidocr", "丙", "甲", 10, "replace"),
    ])
    stats = db.get_error_stats()
    assert stats["total_errors"] == 1
    assert stats["engine_error_rates"]["rapidocr"]["errors"] == 1
    assert stats["engine_error_rates"]["rapidocr"]["chars"] == 2
    assert stats["engine_error_rates"]["rapidocr"]["error_rate"] == 0.5
    assert stats["confusion_top"][0]["wrong"] == "丙"
    assert stats["confusion_top"][0]["correct"] == "甲"
    db.close()


def test_old_db_migration_has_new_tables():
    """旧库（无三表）打开后应通过 CREATE TABLE IF NOT EXISTS 自动建表。"""
    db, d = _tmp_db()
    # 直接查表存在
    names = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"canonical_char", "engine_char_record", "error_record"} <= names
    db.close()
