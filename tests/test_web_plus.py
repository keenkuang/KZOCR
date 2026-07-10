"""健康检查 + Web 增强 + CLI 补全测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from kzocr.web.app import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "version" in data


def test_registrations_page():
    resp = client.get("/registrations")
    assert resp.status_code == 200


def test_quality_page():
    resp = client.get("/book/test-book-a/quality")
    assert resp.status_code == 200


def test_completion_bash():
    from kzocr.cli import build_parser
    import shtab
    parser = build_parser()
    script = shtab.complete(parser, shell="bash")
    assert "kzocr" in script
