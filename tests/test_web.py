"""Web 面板测试（FastAPI TestClient）。"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from kzocr.engine.types import GlyphVerdict
from kzocr.storage.db import BookDB
from kzocr.web.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _setup():
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    # 创建测试 DB
    db = BookDB("test-book-a", db_dir=td)
    for i in range(3):
        db.init_page(i)
        db.update_ocr(i, status="success", char_count=100 * (i + 1), latency_ms=500)
        db.update_verify(i, verdict="PASS" if i % 2 == 0 else "RARE")
        db.update_import(i, status="imported", count=1)
    db.record_anomaly(1, GlyphVerdict(status="FAIL", confidence=1.0, details="test anomaly"), ["TestDet"])
    db.write_benchmark("test-book-a", "mock", total_pages=3, success_pages=2, fail_pages=0, total_latency_ms=1500, total_elapsed_s=2.0)
    db.close()
    yield
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)


def test_index():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "test-book-a" in resp.text


def test_book_detail():
    resp = client.get("/book/test-book-a")
    assert resp.status_code == 200
    assert "test-book-a" in resp.text


def test_book_anomalies():
    resp = client.get("/book/test-book-a/anomalies")
    assert resp.status_code == 200
    assert "test anomaly" in resp.text or "FAIL" in resp.text


def test_resolve_anomaly():
    resp = client.post("/book/test-book-a/anomalies/1/resolve?resolution=fixed", follow_redirects=False)
    assert resp.status_code == 303
    # 确认已 resolved
    db = BookDB("test-book-a", db_dir=os.environ["KZOCR_DB_DIR"])
    pending = db.get_unresolved_anomalies("test-book-a")
    db.close()
    assert len(pending) == 0


def test_book_recipes():
    resp = client.get("/book/test-book-a/recipes")
    assert resp.status_code == 200


def test_no_books():
    td2 = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td2
    resp = client.get("/")
    assert resp.status_code == 200
    assert "暂无" in resp.text
    for f in os.listdir(td2):
        os.remove(os.path.join(td2, f))
    os.rmdir(td2)


# =============================================================================
# REST API 测试
# =============================================================================


def test_api_books_list():
    resp = client.get("/api/books")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(b["code"] == "test-book-a" for b in data)


def test_api_book_detail():
    resp = client.get("/api/books/test-book-a")
    assert resp.status_code == 200
    data = resp.json()
    assert data["book_code"] == "test-book-a"
    assert data["total_pages"] >= 2
    assert data["anomaly_count"] >= 1


def test_api_book_pages():
    resp = client.get("/api/books/test-book-a/pages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    assert data[0]["page_num"] == 0


def test_api_anomalies():
    resp = client.get("/api/books/test-book-a/anomalies?status=pending")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(a["verdict_status"] == "FAIL" for a in data)


def test_api_resolve():
    resp = client.post("/api/books/test-book-a/anomalies/1/resolve?resolution=fixed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # verify resolved
    db = BookDB("test-book-a", db_dir=os.environ["KZOCR_DB_DIR"])
    pending = db.get_unresolved_anomalies("test-book-a")
    db.close()
    assert len(pending) == 0


def test_api_recipes():
    resp = client.get("/api/books/test-book-a/recipes")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


# =============================================================================
# 跨引擎分歧页面 / REST + 形近字自学习
# =============================================================================


def test_book_divergences_page():
    from kzocr.scheduler.cross_align import Divergence

    db = BookDB("test-book-a", db_dir=os.environ["KZOCR_DB_DIR"])
    db.write_cross_divergences(
        0,
        [Divergence(page_no=0, div_type="replace", a_seg="三", b_seg="二",
                    a_context="黄【三】两", priority="high", engine_a="t1", engine_b="t3")],
        engine_a="t1", engine_b="t3",
    )
    db.close()
    resp = client.get("/book/test-book-a/divergences")
    assert resp.status_code == 200
    assert "三" in resp.text and "二" in resp.text


def test_api_divergences():
    from kzocr.scheduler.cross_align import Divergence

    db = BookDB("test-book-a", db_dir=os.environ["KZOCR_DB_DIR"])
    db.write_cross_divergences(
        0,
        [Divergence(page_no=0, div_type="replace", a_seg="三", b_seg="二", priority="high")],
        engine_a="t1", engine_b="t3",
    )
    db.close()
    resp = client.get("/api/books/test-book-a/divergences?priority=high")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) >= 1
    assert data[0]["a_seg"] == "三"


def test_api_add_confusion_self_learn():
    """POST /api/confusion 应持久化新混淆对，并同步内存缓存（内容动态、调用静态）。"""
    import kzocr.scheduler.cross_align as ca

    resp = client.post("/api/confusion", json={"wrong": "芩", "correct": "苓", "source": "test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    try:
        # 磁盘（reload）与内存缓存均应包含新学到的混淆
        assert ca.load_confusion_set(reload=True).get("芩") == "苓"
        assert ca.load_confusion_set().get("芩") == "苓"
    finally:
        # 还原全局状态，避免污染其它测试
        lp = ca._LEARNED_CONFUSION_PATH
        if lp.is_file():
            lp.unlink()
        ca._CONFUSION_CACHE = None
