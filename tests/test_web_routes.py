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
from typing import Any

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


def test_register_submit_with_toc(client: TestClient, dirs) -> None:
    # toc_json 合法列表 → 正常创建并重定向到 /book/{code}（覆盖 toc_entries 非空路径）
    resp = client.post(
        "/register",
        data={"book_code": "bk_toc", "title": "方", "toc_json": '[{"title": "卷一"}]'},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/book/bk_toc"


def test_register_submit_invalid_toc(client: TestClient, dirs) -> None:
    # toc_json 非法 → 解析失败降级为空列表，仍正常重定向不抛（覆盖 1040-1041）
    resp = client.post(
        "/register",
        data={"book_code": "bk_bad", "title": "方", "toc_json": "not json at all"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/book/bk_bad"


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


def test_divergences_high_priority_group_filter(client: TestClient, dirs) -> None:
    """「高优先级」筛选须覆盖升级后的 P0/P1/high 分组，而非仅旧 'high'。

    回归：core 优先级已改为 P0/P1/normal，旧消费点（web 模板/路由筛选）
    若仍用 == 'high' 会漏掉 P0/P1 分歧。
    """
    from kzocr.scheduler.cross_align import Divergence

    db = BookDB("bk_prio", db_dir=_db_dir())
    db.write_cross_divergences(
        1,
        [
            Divergence(page_no=1, div_type="replace", a_seg="三", b_seg="二", priority="P0"),
            Divergence(page_no=1, div_type="replace", a_seg="甲", b_seg="申", priority="P1"),
            Divergence(page_no=1, div_type="replace", a_seg="x", b_seg="y", priority="high"),
            Divergence(page_no=1, div_type="replace", a_seg="ØNØ", b_seg="b", priority="normal"),
        ],
        engine_a="t1", engine_b="t2",
    )
    db.close()

    # HTML 路由：高优先级分组含 P0/P1/high（"三"），不含 normal（独特标记 "ØNØ"）
    r = client.get("/book/bk_prio/divergences?priority=high")
    assert r.status_code == 200
    assert "三" in r.text and "ØNØ" not in r.text

    # JSON 路由：高优先级分组三类齐全
    j = client.get("/api/books/bk_prio/divergences?priority=high").json()
    assert {d["priority"] for d in j} == {"P0", "P1", "high"}

    # 精确筛选：P0 仅返回 P0；normal 仅返回 normal
    jp0 = client.get("/api/books/bk_prio/divergences?priority=P0").json()
    assert {d["priority"] for d in jp0} == {"P0"}
    jn = client.get("/api/books/bk_prio/divergences?priority=normal").json()
    assert {d["priority"] for d in jn} == {"normal"}



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


# ── /api/engines/{name}/test（此前完全未测，覆盖 app.py 757-792 整段）──
def test_api_engine_test_unconfigured(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config", lambda n: None
    )
    r = client.get("/api/engines/ghost/test")
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_api_engine_test_local_no_port(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"host": "127.0.0.1", "port": 0},
    )
    r = client.get("/api/engines/e1/test")
    j = r.json()
    # 本地引擎：egress skip + 无 port/pid 检查 -> 全 skip -> status ok
    assert j["status"] == "ok"
    assert all(c["status"] == "skip" for c in j["checks"])


def test_api_engine_test_local_port_fail(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"host": "127.0.0.1", "port": 59999},
    )
    r = client.get("/api/engines/e1/test")
    j = r.json()
    assert any(c["name"] == "port" and c["status"] == "fail" for c in j["checks"])
    assert j["status"] == "degraded"


def test_api_engine_test_local_pid_ok(
    client: TestClient, dirs, monkeypatch, tmp_path
) -> None:
    pid = tmp_path / "pid.txt"
    pid.write_text("123")
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"host": "127.0.0.1", "port": 0, "pid_file": str(pid)},
    )
    r = client.get("/api/engines/e1/test")
    j = r.json()
    assert any(c["name"] == "pid_file" and c["status"] == "ok" for c in j["checks"])


def test_api_engine_test_cloud_no_base_url(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"requires_network": True, "base_url": ""},
    )
    r = client.get("/api/engines/e1/test")
    j = r.json()
    assert any(c["name"] == "egress" and c["status"] == "skip" for c in j["checks"])


def test_api_engine_test_cloud_ok(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"requires_network": True, "base_url": "https://open.bigmodel.cn"},
    )
    # 离线/无 DNS 环境下 _is_private_ip 会因解析失败拒绝公网域名，需放行以测 ok 分支
    monkeypatch.setattr("kzocr.security.egress._is_private_ip", lambda h: False)
    r = client.get("/api/engines/e1/test")
    j = r.json()
    assert any(c["name"] == "egress" and c["status"] == "ok" for c in j["checks"])


def test_api_engine_test_cloud_fail(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"requires_network": True, "base_url": "https://evil.example.com"},
    )
    r = client.get("/api/engines/e1/test")
    j = r.json()
    assert any(c["name"] == "egress" and c["status"] == "fail" for c in j["checks"])


# ── pipeline_run 成功路径（此前仅测缺字段/pdf 缺失两个 error 分支）──
def test_pipeline_run_success(client: TestClient, dirs, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(pdf_path: str, book_code: str | None = None, *args, **kwargs) -> None:
        captured["book_code"] = book_code

    monkeypatch.setattr("kzocr.engine.run.run_engine", fake_run)
    monkeypatch.setattr("os.path.isfile", lambda p: True)
    r = client.post(
        "/pipeline", data={"book_code": "b1", "pdf_path": "/x.pdf"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/book/b1"
    assert captured["book_code"] == "b1"


# ── register_update 非法 toc_json 降级（502-503 的 json.loads 异常分支）──
def test_register_update_invalid_toc(client: TestClient, dirs) -> None:
    r = client.post(
        "/register/bk2", data={"title": "书", "toc_json": "not json at all"},
        follow_redirects=False,
    )
    assert r.status_code == 303


# ── engine_save 的 adaptive / prompt_override / api_key_env 分支（185-202）──
def test_engine_save_adaptive(client: TestClient, dirs) -> None:
    from kzocr.engine.engine_config import load_engine_config

    r = client.post(
        "/engines/e1/save",
        data={
            "enabled": "true", "base_url": "http://y", "workers": "3",
            "rate_limit": "5", "batch_size": "10",
            "adaptive_enabled": "true", "min_workers": "2", "max_workers": "8",
            "prompt_override_book": "某书上下文",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    cfg = load_engine_config("e1")
    assert cfg["adaptive"] == {"enabled": True, "min_workers": 2, "max_workers": 8}
    assert cfg["prompt_overrides"] == {"book_context": "某书上下文"}


def test_engine_save_api_key_set_and_clear(client: TestClient, dirs) -> None:
    from kzocr.engine.engine_config import load_engine_config

    client.post(
        "/engines/e1/save",
        data={
            "enabled": "true", "base_url": "http://y", "workers": "3",
            "rate_limit": "5", "batch_size": "10", "api_key_env": "MY_KEY",
        },
        follow_redirects=False,
    )
    assert load_engine_config("e1")["api_key_env"] == "MY_KEY"
    # 再次保存不带 api_key_env：engine_save 走 del 分支，但 save_engine_config 会用
    # _DEFAULT_CONFIG 的 api_key_env="" 填回，清理后值为空串（语义上等同未设置）
    client.post(
        "/engines/e1/save",
        data={
            "enabled": "true", "base_url": "http://y", "workers": "3",
            "rate_limit": "5", "batch_size": "10",
        },
        follow_redirects=False,
    )
    assert load_engine_config("e1")["api_key_env"] == ""


# ── engine_status / all_engine_status 的连通性分支（mock urllib，零真实网络）──
def _fake_urlopen_online(req: Any, timeout: int = 8) -> Any:
    class _Resp:
        status = 200

    return _Resp()


def test_engine_status_invalid_base_url(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"base_url": "ftp://x"},
    )
    r = client.get("/engines/e1/status")
    assert r.json()["status"] == "offline"


def test_engine_status_online(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"base_url": "https://api.example.com"},
    )
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_online)
    r = client.get("/engines/e1/status")
    assert r.json()["status"] == "online"


def test_engine_status_auth_required(client: TestClient, dirs, monkeypatch) -> None:
    import urllib.error

    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"base_url": "https://api.example.com"},
    )

    def _raise(req: Any, timeout: int = 8) -> Any:
        raise urllib.error.HTTPError("url", 401, "unauth", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    r = client.get("/engines/e1/status")
    assert r.json()["status"] == "auth_required"


def test_engine_status_offline_http_error(
    client: TestClient, dirs, monkeypatch
) -> None:
    import urllib.error

    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"base_url": "https://api.example.com"},
    )

    def _raise(req: Any, timeout: int = 8) -> Any:
        raise urllib.error.HTTPError("url", 500, "boom", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    r = client.get("/engines/e1/status")
    assert r.json()["status"] == "offline"


def test_all_engine_status_online(client: TestClient, dirs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.list_engine_configs",
        lambda: [{"name": "e1", "base_url": "https://api.example.com"}],
    )
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_online)
    r = client.get("/engines/status/all")
    assert r.status_code == 200
    assert r.json()["e1"]["status"] == "online"


# ── 失败降级路径（真实错误处理分支）──
def test_pipeline_run_failure(client: TestClient, dirs, monkeypatch) -> None:
    def fake_run(pdf_path: str, book_code: str | None = None, *args, **kwargs) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("kzocr.engine.run.run_engine", fake_run)
    monkeypatch.setattr("os.path.isfile", lambda p: True)
    r = client.post("/pipeline", data={"book_code": "b1", "pdf_path": "/x.pdf"})
    assert r.status_code == 200
    assert "处理失败" in r.text


def test_engine_status_generic_exception(
    client: TestClient, dirs, monkeypatch
) -> None:
    monkeypatch.setattr(
        "kzocr.engine.engine_config.load_engine_config",
        lambda n: {"base_url": "https://api.example.com"},
    )

    def _raise(req: Any, timeout: int = 8) -> Any:
        raise OSError("net down")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    r = client.get("/engines/e1/status")
    assert r.json()["status"] == "offline"
