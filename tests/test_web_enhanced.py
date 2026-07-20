"""导出、批量、Web 面板增强测试。"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from kzocr.engine.types import GlyphVerdict
from kzocr.storage.db import BookDB
from kzocr.web.app import app

client = TestClient(app)


# ── JSON 导出测试 ──

def test_export_json():
    from kzocr.doc.export import export_json
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    db = BookDB("json-test", db_dir=td)
    db.init_page(0)
    db.update_ocr(0, status="success", char_count=100, latency_ms=500)
    db.update_verify(0, verdict="PASS", details="test details")
    db.close()
    result = export_json("json-test", db_path=td)
    data = json.loads(result)
    assert data["book_code"] == "json-test"
    assert data["total_pages"] >= 1
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)


# ── Web 面板增强测试 ──

@pytest.fixture(autouse=True)
def _setup():
    td = tempfile.mkdtemp()
    os.environ["KZOCR_DB_DIR"] = td
    db = BookDB("test-web2", db_dir=td)
    for i in range(2):
        db.init_page(i)
        db.update_ocr(i, status="success", char_count=100, latency_ms=200)
        db.update_verify(i, verdict="PASS")
    db.record_anomaly(0, GlyphVerdict(status="FAIL", confidence=1.0), ["TestDet"])
    db.close()
    yield
    for f in os.listdir(td):
        os.remove(os.path.join(td, f))
    os.rmdir(td)


def test_dashboard():
    resp = client.get("/book/test-web2/dashboard")
    assert resp.status_code == 200
    assert "看板" in resp.text or "延迟" in resp.text


def test_recipe_detail_not_found():
    resp = client.get("/book/test-web2/recipe/99.99")
    assert resp.status_code == 200


def test_search_empty():
    resp = client.get("/search")
    assert resp.status_code == 200


def test_search_with_query():
    resp = client.get("/search?q=test")
    assert resp.status_code == 200


def test_workspace():
    resp = client.get("/workspace/test-web2")
    assert resp.status_code == 200
