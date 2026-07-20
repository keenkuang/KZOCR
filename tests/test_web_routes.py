"""kzocr/web/app.py 路由单测补测（FastAPI TestClient 内存测试，零网络/零真实 DB）。

纠正记忆偏差：web/app.py 实际覆盖率仅 ~35%（此前误记 100%）。现有 test_web*.py 仅覆盖
/health、/register 等少数路由。本文件用临时目录 + 环境变量隔离，补全 prompts / engines /
registration / book（空库降级）/ monitor / pipeline 系列 handler 的内部逻辑（表单解析、
save/load/delete、404 分支、toggle、try/except 降级、模板渲染）。

W1 续补：覆盖此前仅测「空库降级」的 handler 的「带数据」分支，以及此前完全未测的
``/``、``/health``、``/api/confusion``、``/engines/status/all`（含配置）等路由。
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from kzocr.engine.types import GlyphVerdict
from kzocr.scheduler.cross_align import run_cross_align
from kzocr.storage.db import BookDB
from kzocr.web.app import app


def _db_dir() -> str:
    return os.environ["KZOCR_DB_DIR"]


def _populate_book(code: str = "bk1", db_dir: str | None = None) -> None:
    """向临时 BookDB 写入最小但真实的数据，驱动各 handler 的「带数据」分支。"""
    dbd = db_dir or _db_dir()
    db = BookDB(code, db_dir=dbd)
    for n in (0, 1, 2):
        db.init_page(n, char_count=10 + n)
        db.update_ocr(n, status="success", char_count=10 + n, latency_ms=100)
        db.update_verify(n, verdict="PASS", details=f"黄芪三钱，当归二钱 第{n}页")
    # 一条未决议异常（供 anomalies / resolve / workspace 分支）
    db.record_anomaly(
        1,
        GlyphVerdict(status="UNKNOWN", confidence=0.4, details="cross_divergence"),
        detector_chain=["CrossAlign"],
    )
    # benchmark 汇总（供 api_engines / _list_books / book_detail / dashboard 的 benchmark 分支）
    db.write_benchmark(
        code, engine="t1", total_pages=3, success_pages=2, fail_pages=1,
        total_latency_ms=300, total_elapsed_s=10,
    )
    # 跨引擎分歧（供 divergences 分支）
    divs = run_cross_align(1, "黄芪三钱，当归二钱", "黄芪二钱，当归三钱", confusion_set={})
    db.write_cross_divergences(1, divs, engine_a="t1", engine_b="t2")
    # 质检结果（供 quality 分支）
    db.save_quality_result("R1", "ok", confidence=0.9)
    db.close()


def _first_anomaly_id(code: str = "bk1", db_dir: str | None = None) -> int:
    dbd = db_dir or _db_dir()
    db = BookDB(code, db_dir=dbd)
    try:
        anoms = db.get_unresolved_anomalies(code, limit=999)
    finally:
        db.close()
    return anoms[0]["id"]


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


@pytest.fixture
def populated(dirs) -> None:
    _populate_book()


# ── 此前完全未测的路由：/、/health、/api/confusion ──
def test_index_with_data(client: TestClient, populated) -> None:
    assert client.get("/").status_code == 200


def test_health(client: TestClient, dirs) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["db_accessible"] is True


def test_api_confusion_post(client: TestClient, dirs, tmp_path, monkeypatch) -> None:
    import kzocr.scheduler.cross_align as _ca

    monkeypatch.setattr(_ca, "_LEARNED_CONFUSION_PATH", tmp_path / "learned.json")
    monkeypatch.setattr(_ca, "_CONFUSION_CACHE", None)
    r = client.post("/api/confusion", json={"wrong": "已", "correct": "己", "source": "test"})
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "noop")


def test_api_confusion_invalid_json(client: TestClient, dirs) -> None:
    r = client.post("/api/confusion", data="not-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["status"] == "error"


# ── book 带数据分支 ──
def test_book_detail_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1").status_code == 200


def test_book_anomalies_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/anomalies").status_code == 200


def test_book_anomalies_status_filter(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/anomalies", params={"status": "all"}).status_code == 200


def test_book_resolve_with_data(client: TestClient, populated) -> None:
    aid = _first_anomaly_id()
    r = client.post(f"/book/bk1/anomalies/{aid}/resolve", follow_redirects=False)
    assert r.status_code == 303


def test_book_recipes_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/recipes").status_code == 200


def test_book_divergences_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/divergences").status_code == 200


def test_book_quality_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/quality").status_code == 200


def test_book_dashboard_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/dashboard").status_code == 200


def test_book_recipe_detail_with_data(client: TestClient, populated) -> None:
    assert client.get("/book/bk1/recipe/1").status_code == 200


def test_workspace_with_data(client: TestClient, populated) -> None:
    assert client.get("/workspace/bk1").status_code == 200


def test_workspace_resolved(client: TestClient, populated) -> None:
    assert client.get("/workspace/bk1", params={"resolved": "yes"}).status_code == 200


# ── 看板带数据分支 ──
def test_monitor_page_with_data(client: TestClient, populated) -> None:
    assert client.get("/monitor").status_code == 200


def test_benchmark_page_with_data(client: TestClient, populated) -> None:
    assert client.get("/benchmark").status_code == 200


def test_monitor_api_with_data(client: TestClient, populated) -> None:
    r = client.get("/monitor/api")
    assert r.status_code == 200
    # benchmark 行存在但无引擎配置 → engines 仍空、total_engines=0
    assert r.json()["total_engines"] == 0


def test_engines_status_all_with_config(client: TestClient, populated) -> None:
    # 注册一个无效 base_url 引擎配置，触发「配置非空」分支（ftp:// 不联网，直接 offline）
    client.post("/engines/new", data={"name": "e1", "base_url": "ftp://bad"}, follow_redirects=False)
    r = client.get("/engines/status/all")
    assert r.status_code == 200
    assert "e1" in r.json()


# ── 搜索带数据分支 ──
def test_search_with_query_with_data(client: TestClient, populated) -> None:
    assert client.get("/search", params={"q": "黄芪"}).status_code == 200


# ── REST API 带数据分支 ──
def test_api_books_with_data(client: TestClient, populated) -> None:
    r = client.get("/api/books")
    assert r.status_code == 200
    assert any(b["code"] == "bk1" for b in r.json())


def test_api_book_detail_with_data(client: TestClient, populated) -> None:
    r = client.get("/api/books/bk1")
    assert r.status_code == 200
    assert r.json()["book_code"] == "bk1"


def test_api_book_pages_with_data(client: TestClient, populated) -> None:
    r = client.get("/api/books/bk1/pages")
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_api_anomalies_with_data(client: TestClient, populated) -> None:
    r = client.get("/api/books/bk1/anomalies")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_api_recipes_with_data(client: TestClient, populated) -> None:
    assert client.get("/api/books/bk1/recipes").status_code == 200


def test_api_divergences_with_data(client: TestClient, populated) -> None:
    r = client.get("/api/books/bk1/divergences")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_api_resolve_with_data(client: TestClient, populated) -> None:
    aid = _first_anomaly_id()
    r = client.post(f"/api/books/bk1/anomalies/{aid}/resolve")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_engines_with_data(client: TestClient, populated) -> None:
    r = client.get("/api/engines")
    assert r.status_code == 200
    assert any(e["engine"] == "t1" for e in r.json())


# ── 登记列表带数据分支 ──
def test_registrations_list_with_data(client: TestClient, populated) -> None:
    client.post("/register", data={"book_code": "bk2", "title": "新"}, follow_redirects=False)
    assert client.get("/registrations").status_code == 200


def test_register_edit_with_data(client: TestClient, populated) -> None:
    client.post("/register/bk1", data={"title": "书", "toc_json": "[]"}, follow_redirects=False)
    assert client.get("/register/bk1").status_code == 200
