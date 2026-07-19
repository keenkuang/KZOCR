"""kzocr/web/app.py HTTP 接口测试（零网络，FastAPI TestClient）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# 必须在 import app 之前设环境变量阻断 BookDB 真实路径
import os
os.environ["KZOCR_DB_DIR"] = "/nonexistent/test_db"


from kzocr.web.app import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health_returns_json(self, client):
        """/health 返回 JSON 含 status 和 version。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "version" in data
        # db_dir 不存在，状态应为 degraded
        assert data["status"] in ("ok", "degraded")


class TestRegister:
    def test_register_form_returns_html(self, client):
        """GET /register 返回登记表单 HTML。"""
        resp = client.get("/register")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert b"register" in resp.content.lower() or b"form" in resp.content.lower()


class TestRoot:
    def test_index_returns_html_or_redirect(self, client):
        """GET / 返回首页 HTML。"""
        resp = client.get("/")
        assert resp.status_code in (200, 302)
        if resp.status_code == 200:
            assert resp.headers["content-type"].startswith("text/html")


class TestStaticFiles:
    def test_favicon_not_found(self, client):
        """不存在的静态文件返回 404。"""
        resp = client.get("/static/nonexistent.css")
        assert resp.status_code == 404


class TestPrompts:
    def test_prompts_page_returns_html(self, client):
        """GET /prompts 返回 prompt 管理页面。"""
        resp = client.get("/prompts")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")


class TestNotFound:
    def test_nonexistent_route_returns_404(self, client):
        """未注册的路由返回 404。"""
        resp = client.get("/this/route/does/not/exist")
        assert resp.status_code == 404


class TestEngineEndpoints:
    def test_engines_page(self, client):
        """GET /engines 返回引擎管理页面。"""
        resp = client.get("/engines")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_engine_delete_nonexistent(self, client):
        """POST 删除不存在的引擎应重定向。"""
        resp = client.post("/engines/nonexistent/delete", follow_redirects=False)
        assert resp.status_code == 303  # Redirect
        assert resp.headers["location"] == "/engines"
