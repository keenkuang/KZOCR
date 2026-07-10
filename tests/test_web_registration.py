"""Web 书籍登记测试。"""

from __future__ import annotations

import json
import os
import tempfile

from fastapi.testclient import TestClient

from kzocr.web.app import app

client = TestClient(app)


def test_register_form_get():
    """GET /register → 200。"""
    resp = client.get("/register")
    assert resp.status_code == 200


def test_register_submit():
    """POST /register with data → 302 重定向。"""
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DATA_DIR"] = td
    try:
        toc = [{"level": 1, "title": "内科秘验方", "page": 1},
               {"level": 3, "title": "§1 治感冒秘方", "page": 1}]
        resp = client.post("/register", data={
            "book_code": "REG-TEST",
            "title": "测试书籍",
            "author": "佚名",
            "publisher": "中医出版社",
            "toc_json": json.dumps(toc),
        }, follow_redirects=False)
        assert resp.status_code == 303
    finally:
        for f in os.listdir(td):
            os.remove(os.path.join(td, f))
        os.rmdir(td)


def test_registration_file_created():
    """提交后 JSON 文件存在且内容正确。"""
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DATA_DIR"] = td
    try:
        client.post("/register", data={
            "book_code": "FILE-TEST",
            "title": "文件测试",
            "toc_json": json.dumps([{"level": 1, "title": "章一", "page": 1}]),
        })
        path = os.path.join(td, "FILE-TEST.json")
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
            assert data["book_code"] == "FILE-TEST"
            assert len(data["toc"]["entries"]) == 1
    finally:
        for f in os.listdir(td):
            os.remove(os.path.join(td, f))
        os.rmdir(td)


def test_registration_toc_loaded():
    """注册的 TOC 在 enrich 时被采纳。"""
    from kzocr.engine.registration import save_registration
    from kzocr.engine.types import BookResult, PageResult
    from kzocr.engine.toc import enrich_book_result
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DATA_DIR"] = td
    try:
        save_registration("TOC-REG", toc_entries=[
            {"level": 1, "title": "章一", "page": 1},
            {"level": 3, "title": "§1 节一", "page": 1},
        ])
        book = BookResult(book_code="TOC-REG", title="TOC测试",
                          pages=[PageResult(page_num=0, text="正文")])
        enrich_book_result(book)
        assert book.toc is not None
        assert len(book.toc.entries) >= 1
        assert book.toc.entries[0].title == "章一"
    finally:
        for f in os.listdir(td):
            os.remove(os.path.join(td, f))
        os.rmdir(td)
