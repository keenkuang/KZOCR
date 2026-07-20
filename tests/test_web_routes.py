"""kzocr/web/app.py 路由单测补测（FastAPI TestClient 内存测试，零网络/零真实 DB）。

纠正记忆偏差：web/app.py 实际覆盖率仅 ~35%（此前误记 100%）。现有 test_web*.py 仅覆盖
/health、/register 等少数路由。本文件用临时目录 + 环境变量隔离，补全 prompts / engines /
registration / book（空库降级）/ monitor / pipeline 系列 handler 的内部逻辑（表单解析、
save/load/delete、404 分支、toggle、try/except 降级、模板渲染）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kzocr.web.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    prom = tmp_path / "prompts"
    eng = tmp_path / "engines"
    regs = tmp_path / "regs"
    dbd = tmp_path / "db"
    for d in (prom, eng, regs, dbd):
        d.mkdir()
    monkeypatch.setenv("KZOCR_PROMPT_DIR", str(prom))
    monkeypatch.setenv("KZOCR_ENGINE_CONFIG_DIR", str(eng))
    monkeypatch.setenv("KZOCR_DATA_DIR", str(regs))
    monkeypatch.setenv("KZOCR_DB_DIR", str(dbd))


# ── prompts ──
def test_prompts_page(client: TestClient, dirs) -> None:
    assert client.get("/prompts").status_code == 200


def test_prompt_edit_missing(client: TestClient, dirs) -> None:
    assert client.get("/prompts/nope").status_code == 200


def test_prompt_save_view_delete(client: TestClient, dirs) -> None:
    assert client.post("/prompts/p1", data={"text": "hi"}, follow_redirects=False).status_code == 303
    r = client.get("/prompts/p1")
    assert r.status_code == 200
    assert b"hi" in r.content
    assert client.get("/prompts/p1/delete", follow_redirects=False).status_code == 303


# ── engines ──
def test_engine_new(client: TestClient, dirs) -> None:
    assert client.post("/engines/new", data={"name": "e1", "base_url": "http://x"},
                        follow_redirects=False).status_code == 303


def test_engine_edit_existing(client: TestClient, dirs) -> None:
    client.post("/engines/new", data={"name": "e1", "base_url": "http://x"}, follow_redirects=False)
    assert client.get("/engines/e1/edit").status_code == 200


def test_engine_edit_missing_404(client: TestClient, dirs) -> None:
    assert client.get("/engines/ghost/edit").status_code == 404


def test_engine_save(client: TestClient, dirs) -> None:
    client.post("/engines/new", data={"name": "e1", "base_url": "http://x"}, follow_redirects=False)
    assert client.post("/engines/e1/save",
                       data={"enabled": "true", "base_url": "http://y", "workers": "3",
                             "rate_limit": "5", "batch_size": "10"},
                       follow_redirects=False).status_code == 303


def test_engine_toggle_existing(client: TestClient, dirs) -> None:
    client.post("/engines/new", data={"name": "e1", "base_url": "http://x"}, follow_redirects=False)
    r = client.post("/engines/e1/toggle")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_engine_toggle_missing(client: TestClient, dirs) -> None:
    assert client.post("/engines/ghost/toggle").status_code == 404


def test_engine_delete(client: TestClient, dirs) -> None:
    client.post("/engines/new", data={"name": "e1", "base_url": "http://x"}, follow_redirects=False)
    assert client.post("/engines/e1/delete", follow_redirects=False).status_code == 303


def test_engine_status_missing(client: TestClient, dirs) -> None:
    r = client.get("/engines/ghost/status")
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


def test_engine_status_invalid_url(client: TestClient, dirs) -> None:
    client.post("/engines/new", data={"name": "e1", "base_url": "ftp://bad"}, follow_redirects=False)
    r = client.get("/engines/e1/status")
    assert r.status_code == 200
    assert r.json()["status"] == "offline"


# ── registration ──
def test_register_update(client: TestClient, dirs) -> None:
    assert client.post("/register/bk1", data={"title": "书", "toc_json": "[]"},
                       follow_redirects=False).status_code == 303


def test_register_delete(client: TestClient, dirs) -> None:
    client.post("/register/bk1", data={"title": "书", "toc_json": "[]"}, follow_redirects=False)
    assert client.get("/register/bk1/delete", follow_redirects=False).status_code == 303


def test_register_form(client: TestClient, dirs) -> None:
    assert client.get("/register").status_code == 200


def test_register_submit(client: TestClient, dirs) -> None:
    assert client.post("/register", data={"book_code": "bk2", "title": "新"},
                       follow_redirects=False).status_code == 303


def test_register_submit_empty_code(client: TestClient, dirs) -> None:
    assert client.post("/register", data={"book_code": "", "title": "新"},
                       follow_redirects=False).status_code == 303


# ── book（空库建表 → 降级空数据，均返回 200/303）──
def test_book_detail_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1").status_code == 200


def test_book_anomalies_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1/anomalies").status_code == 200


def test_book_resolve_empty(client: TestClient, dirs) -> None:
    assert client.post("/book/bk1/anomalies/1/resolve", follow_redirects=False).status_code == 303


def test_book_recipes_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1/recipes").status_code == 200


def test_book_divergences_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1/divergences").status_code == 200


# ── monitor / pipeline ──
def test_monitor_api_empty(client: TestClient, dirs) -> None:
    r = client.get("/monitor/api")
    assert r.status_code == 200
    assert r.json()["total_engines"] == 0


def test_pipeline_form(client: TestClient, dirs) -> None:
    assert client.get("/pipeline").status_code == 200


def test_pipeline_run_missing_fields(client: TestClient, dirs) -> None:
    assert client.post("/pipeline", data={}, follow_redirects=False).status_code == 200


def test_pipeline_run_pdf_missing(client: TestClient, dirs) -> None:
    assert client.post("/pipeline", data={"book_code": "b", "pdf_path": "/no/such.pdf"},
                       follow_redirects=False).status_code == 200


# ── 页面 handler（空库/空配置降级 → 200）──
def test_monitor_page(client: TestClient, dirs) -> None:
    assert client.get("/monitor").status_code == 200


def test_benchmark_page(client: TestClient, dirs) -> None:
    assert client.get("/benchmark").status_code == 200


def test_registrations_list(client: TestClient, dirs) -> None:
    assert client.get("/registrations").status_code == 200


def test_register_edit_page(client: TestClient, dirs) -> None:
    assert client.get("/register/bk1").status_code == 200


def test_book_quality_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1/quality").status_code == 200


def test_book_dashboard_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1/dashboard").status_code == 200


def test_book_recipe_detail_empty(client: TestClient, dirs) -> None:
    assert client.get("/book/bk1/recipe/1").status_code == 200


def test_search_empty(client: TestClient, dirs) -> None:
    assert client.get("/search").status_code == 200


def test_search_with_query_empty_db(client: TestClient, dirs) -> None:
    assert client.get("/search", params={"q": "麻黄"}).status_code == 200


def test_workspace_empty(client: TestClient, dirs) -> None:
    assert client.get("/workspace/bk1").status_code == 200


def test_engines_status_all_empty(client: TestClient, dirs) -> None:
    r = client.get("/engines/status/all")
    assert r.status_code == 200
    assert r.json() == {}
