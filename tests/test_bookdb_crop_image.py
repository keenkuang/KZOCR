"""B7：BookDB 行级裁剪图路径存储测试。

覆盖：
- persist 时按 char_boxes 切图落盘，line.crop_img_path 写相对路径
- 开关 KZOCR_CROP_IMG=0 时不落图
- book.source_pdf 为空时不落图
- 旧库（无 source_pdf / crop_img_path 列）打开后自动 ALTER 补列且可读写
"""
import os
from types import SimpleNamespace

import numpy as np
import pytest

from kzocr.engine.types import BookResult
from kzocr.storage.db import BookDB


@pytest.fixture
def fake_render(monkeypatch):
    """让 crop_images 不依赖真实 PDF：
    - render_body_page 返回固定版心图
    - 真实 fitz.open 被 mock 为返回占位对象（render_body_page 已被 mock，不访问 doc 内容）
    """
    fake_img = np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(
        "kzocr.storage.crop_images.render_body_page",
        lambda doc, page_num: fake_img,
    )
    import fitz

    monkeypatch.setattr(fitz, "open", lambda p: object())


def _make_book(code="bk1", source_pdf="dummy.pdf"):
    pages = [
        SimpleNamespace(
            page_num=1,
            paragraphs=[],  # 走 elif p.char_boxes 分支（无段落，按逐行 char_boxes）
            text="",
            confidence=0.0,
            char_boxes=[
                [[10, 10, 30, 30], [35, 10, 55, 30]],  # 行 0
                [[10, 40, 30, 60]],                     # 行 1
            ],
        )
    ]
    return BookResult(book_code=code, title="t", source_pdf=source_pdf, pages=pages)


def test_crop_images_persist_writes_relative_path(tmp_path, fake_render):
    db = BookDB("bk1", db_dir=str(tmp_path))
    db.save_book_result(_make_book())
    db.close()

    db2 = BookDB("bk1", db_dir=str(tmp_path))
    book_row = db2._conn.execute("SELECT source_pdf FROM book").fetchone()
    assert book_row["source_pdf"] == "dummy.pdf"

    rows = db2._conn.execute(
        "SELECT line_seq, crop_img_path FROM line ORDER BY line_seq"
    ).fetchall()
    assert len(rows) == 2
    p0 = rows[0]["crop_img_path"]
    p1 = rows[1]["crop_img_path"]
    assert p0 == "bk1_crops/P1_L0_0.png"
    assert p1 == "bk1_crops/P1_L0_1.png"
    # 文件真实落在 db_dir 下
    assert os.path.exists(os.path.join(str(tmp_path), p0))
    assert os.path.exists(os.path.join(str(tmp_path), p1))
    db2.close()


def test_crop_images_disabled_by_env(tmp_path, fake_render, monkeypatch):
    monkeypatch.setenv("KZOCR_CROP_IMG", "0")
    db = BookDB("bk1", db_dir=str(tmp_path))
    db.save_book_result(_make_book())
    db.close()

    db2 = BookDB("bk1", db_dir=str(tmp_path))
    rows = db2._conn.execute("SELECT crop_img_path FROM line").fetchall()
    assert all(r["crop_img_path"] == "" for r in rows)
    # 关闭时不创建 crops 目录
    assert not os.path.exists(os.path.join(str(tmp_path), "bk1_crops"))
    db2.close()


def test_crop_images_no_source_pdf_no_crop(tmp_path, fake_render):
    db = BookDB("bk2", db_dir=str(tmp_path))
    db.save_book_result(_make_book(code="bk2", source_pdf=""))
    db.close()

    db2 = BookDB("bk2", db_dir=str(tmp_path))
    rows = db2._conn.execute("SELECT crop_img_path FROM line").fetchall()
    assert all(r["crop_img_path"] == "" for r in rows)
    db2.close()


def test_crop_images_old_db_migration(tmp_path):
    """旧库（无 source_pdf / crop_img_path 列）被 BookDB 打开后自动 ALTER 补列且可读写。"""
    import sqlite3

    db_path = os.path.join(str(tmp_path), "old.db")
    conn = sqlite3.connect(db_path)
    # 模拟真实历史旧库：含 author/publisher/pub_year，但无 B7 新增列 source_pdf
    conn.execute(
        "CREATE TABLE book (book_code TEXT PRIMARY KEY, title TEXT DEFAULT '', "
        "author TEXT DEFAULT '', publisher TEXT DEFAULT '', pub_year INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE line (id INTEGER PRIMARY KEY, page_num INTEGER, "
        "para_seq INTEGER DEFAULT 0, line_seq INTEGER, text TEXT DEFAULT '', "
        "char_boxes TEXT DEFAULT '[]', human_final TEXT DEFAULT '', "
        "UNIQUE (page_num, para_seq, line_seq))"
    )
    conn.commit()
    conn.close()

    # BookDB 打开 → create_schema 自动 ALTER 补列
    db = BookDB("old", db_dir=str(tmp_path))
    db.save_book("old", source_pdf="x.pdf")
    db._save_line(1, 0, 0, text="a", crop_img_path="rel.png")
    db.close()

    db2 = BookDB("old", db_dir=str(tmp_path))
    b = db2._conn.execute("SELECT source_pdf FROM book").fetchone()
    assert b["source_pdf"] == "x.pdf"
    line_row = db2._conn.execute("SELECT crop_img_path FROM line").fetchone()
    assert line_row["crop_img_path"] == "rel.png"
    db2.close()
