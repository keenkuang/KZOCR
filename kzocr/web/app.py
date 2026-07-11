"""KZOCR Web 管理面板。

基于 FastAPI + Jinja2。
设计借鉴 TCM-Modern-OCR Web UI（Next.js + shadcn/ui + Tailwind）。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kzocr.storage.db import BookDB

app = FastAPI(
    title="KZOCR REST API",
    description="中医古籍 OCR 编排系统 API。管理书籍、方剂、校对异常、质检结果。",
    version="0.16.0",
    contact={"name": "KZOCR Team", "url": "https://github.com/keenkuang/KZOCR"},
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
if any(STATIC_DIR.iterdir()):
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _db_dir() -> str:
    return os.environ.get("KZOCR_DB_DIR", os.path.join(os.getcwd(), "db"))


def _list_books() -> list[dict]:
    dbd = _db_dir()
    if not os.path.isdir(dbd):
        return []
    books = []
    for f in sorted(os.listdir(dbd)):
        if f.endswith(".db"):
            code = f[:-3]
            db = BookDB(code, db_dir=dbd)
            try:
                progress = db.get_all_progress()
                _ = db.get_unresolved_anomalies(code, limit=1)
                # 从 benchmark 表中取总页数
                bench = db._conn.execute(
                    "SELECT total_pages, success_pages, fail_pages, engine, pages_per_min, created_at "
                    "FROM benchmark_results ORDER BY id DESC LIMIT 1"
                ).fetchone()
                books.append({
                    "code": code,
                    "total_pages": bench["total_pages"] if bench else len(progress),
                    "success_pages": bench["success_pages"] if bench else sum(1 for p in progress if p["ocr_status"] == "success"),
                    "fail_pages": bench["fail_pages"] if bench else sum(1 for p in progress if p["ocr_status"] == "failed"),
                    "anomaly_count": len(BookDB(code, db_dir=dbd).get_unresolved_anomalies(code, limit=999)),
                    "engine": bench["engine"] if bench else "",
                    "pages_per_min": bench["pages_per_min"] if bench else 0,
                    "updated_at": bench["created_at"] if bench else "",
                })
            except Exception:
                books.append({"code": code, "total_pages": 0, "success_pages": 0, "fail_pages": 0, "anomaly_count": 0, "engine": "", "pages_per_min": 0, "updated_at": ""})
            finally:
                db.close()
    return books


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from kzocr.engine.registration import list_registrations
    books = _list_books()
    regs = list_registrations()
    total_books = len(books)
    total_pages = sum(b["total_pages"] for b in books)
    total_success = sum(b["success_pages"] for b in books)
    total_anomalies = sum(b["anomaly_count"] for b in books)
    return templates.TemplateResponse(request, "index.html", {
        "books": books,
        "registrations": regs,
        "total_books": total_books,
        "total_pages": total_pages,
        "total_success": total_success,
        "total_anomalies": total_anomalies,
    })


@app.get("/engines", response_class=HTMLResponse)
async def engines_page(request: Request):
    """引擎管理页面（列表 + 新增模态框）。"""
    from kzocr.engine.engine_config import list_engine_configs
    configs = list_engine_configs()
    return templates.TemplateResponse(request, "engines.html", {"configs": configs})


@app.post("/engines/new")
async def engine_new(request: Request):
    """新增引擎。"""
    from kzocr.engine.engine_config import save_engine_config
    form = await request.form()
    name = form.get("name", "").strip()
    base_url = form.get("base_url", "").strip()
    if not name:
        return RedirectResponse(url="/engines", status_code=303)
    save_engine_config(name, {
        "base_url": base_url,
        "enabled": True,
        "workers": 2,
        "rate_limit": 5,
        "batch_size": 10,
        "adaptive": {"enabled": True, "min_workers": 1, "max_workers": 6},
    })
    return RedirectResponse(url="/engines", status_code=303)


@app.get("/engines/status/all")
async def all_engine_status():
    """批量检测所有引擎状态。"""
    from kzocr.engine.engine_config import list_engine_configs
    import asyncio, urllib.request, time, os
    from fastapi.responses import JSONResponse
    configs = list_engine_configs()
    async def _check_one(cfg):
        name = cfg["name"]
        base_url = cfg.get("base_url", "")
        api_key_env = cfg.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if not base_url or not base_url.startswith(("http://", "https://")):
            return name, {"status": "offline", "error": "无效 base_url"}
        def _sync():
            for url in [base_url.rstrip("/") + "/v1/models", base_url.rstrip("/")]:
                try:
                    req = urllib.request.Request(url, method="GET")
                    if api_key:
                        req.add_header("Authorization", f"Bearer {api_key}")
                    t0 = time.time()
                    resp = urllib.request.urlopen(req, timeout=8)
                    ms = int((time.time() - t0) * 1000)
                    return {"status": "online", "latency_ms": ms}
                except urllib.error.HTTPError as e:
                    ms = int((time.time() - t0) * 1000)
                    return {"status": "online", "latency_ms": ms}
                except Exception:
                    continue
            return {"status": "offline", "error": "无法连接"}
        return name, await asyncio.get_event_loop().run_in_executor(None, _sync)
    tasks = [_check_one(c) for c in configs]
    results_list = await asyncio.gather(*tasks)
    output = dict(results_list)
    return JSONResponse(output)


@app.get("/engines/{name}/edit", response_class=HTMLResponse)
async def engine_edit(request: Request, name: str):
    """引擎编辑页面。"""
    from kzocr.engine.engine_config import load_engine_config
    cfg = load_engine_config(name)
    if not cfg:
        return HTMLResponse(f"<h2>引擎 {name} 不存在</h2><a href='/engines'>返回</a>", status_code=404)
    return templates.TemplateResponse(request, "engine_edit.html", {"name": name, "cfg": cfg})


@app.post("/engines/{name}/save")
async def engine_save(name: str, request: Request):
    """保存引擎配置。"""
    from kzocr.engine.engine_config import save_engine_config, load_engine_config
    form = await request.form()
    cfg = load_engine_config(name) or {}
    cfg["enabled"] = form.get("enabled") == "true"
    cfg["model_name"] = form.get("model_name") or None
    cfg["base_url"] = form.get("base_url", "")
    cfg["workers"] = max(int(form.get("workers", 2)), 1)
    cfg["rate_limit"] = max(int(form.get("rate_limit", 5)), 1)
    cfg["batch_size"] = max(int(form.get("batch_size", 10)), 1)
    api_key_env = form.get("api_key_env", "").strip()
    if api_key_env:
        cfg["api_key_env"] = api_key_env
    elif "api_key_env" in cfg:
        del cfg["api_key_env"]
    if form.get("adaptive_enabled") == "true":
        cfg["adaptive"] = {
            "enabled": True,
            "min_workers": max(int(form.get("min_workers", 1)), 1),
            "max_workers": max(int(form.get("max_workers", 6)), 1),
        }
    else:
        cfg.pop("adaptive", None)
    prompt_override = form.get("prompt_override_book", "").strip()
    if prompt_override:
        cfg["prompt_overrides"] = {"book_context": prompt_override}
    elif "prompt_overrides" in cfg:
        del cfg["prompt_overrides"]
    save_engine_config(name, cfg)
    return RedirectResponse(url="/engines", status_code=303)


@app.post("/engines/{name}/delete")
async def engine_delete(name: str):
    """删除引擎。"""
    from kzocr.engine.engine_config import delete_engine_config
    delete_engine_config(name)
    return RedirectResponse(url="/engines", status_code=303)


@app.post("/engines/{name}/toggle")
async def engine_toggle(name: str):
    """切换引擎启用/禁用。"""
    from kzocr.engine.engine_config import load_engine_config, save_engine_config
    from fastapi.responses import JSONResponse
    cfg = load_engine_config(name)
    if not cfg:
        return JSONResponse({"error": f"引擎 {name} 不存在"}, status_code=404)
    cfg["enabled"] = not cfg.get("enabled", True)
    save_engine_config(name, cfg)
    return JSONResponse({"name": name, "enabled": cfg["enabled"]})


@app.get("/engines/{name}/status")
async def engine_status(name: str):
    """检测单个引擎连通性。"""
    from kzocr.engine.engine_config import load_engine_config
    import asyncio, urllib.request, time, os
    from fastapi.responses import JSONResponse
    cfg = load_engine_config(name)
    if not cfg:
        return JSONResponse({"status": "unknown", "error": "引擎不存在", "name": name})
    base_url = cfg.get("base_url", "")
    api_key_env = cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""

    async def _check():
        def _sync():
            if not base_url or not base_url.startswith(("http://", "https://")):
                return {"status": "offline", "error": f"无效的 base_url: {base_url}"}
            probe_urls = [
                base_url.rstrip("/") + "/v1/models",
                base_url.rstrip("/"),
            ]
            for url in probe_urls:
                try:
                    req = urllib.request.Request(url, method="GET")
                    if api_key:
                        req.add_header("Authorization", f"Bearer {api_key}")
                    t0 = time.time()
                    resp = urllib.request.urlopen(req, timeout=8)
                    ms = int((time.time() - t0) * 1000)
                    return {"status": "online", "latency_ms": ms, "code": resp.status}
                except urllib.error.HTTPError as e:
                    ms = int((time.time() - t0) * 1000)
                    # 401/403 = 服务器可达但需认证，标记为 auth_required
                    if e.code in (401, 403):
                        return {"status": "auth_required", "latency_ms": ms, "code": e.code, "note": f"HTTP {e.code}，需要认证"}
                    # 其他 4xx/5xx = 服务不可用
                    return {"status": "offline", "error": f"HTTP {e.code}", "latency_ms": ms, "code": e.code}
                except Exception as exc:
                    continue
            return {"status": "offline", "error": "无法连接"}
        return await asyncio.get_event_loop().run_in_executor(None, _sync)
    result = await _check()
    result["name"] = name
    return JSONResponse(result)


@app.get("/engines/status/all")
async def all_engine_status():
    """批量检测所有引擎状态。"""
    from kzocr.engine.engine_config import list_engine_configs
    import asyncio, urllib.request, time, os
    from fastapi.responses import JSONResponse
    configs = list_engine_configs()
    async def _check_one(cfg):
        name = cfg["name"]
        base_url = cfg.get("base_url", "")
        api_key_env = cfg.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if not base_url or not base_url.startswith(("http://", "https://")):
            return name, {"status": "offline", "error": "无效 base_url"}
        def _sync():
            for url in [base_url.rstrip("/") + "/v1/models", base_url.rstrip("/")]:
                try:
                    req = urllib.request.Request(url, method="GET")
                    if api_key:
                        req.add_header("Authorization", f"Bearer {api_key}")
                    t0 = time.time()
                    resp = urllib.request.urlopen(req, timeout=8)
                    ms = int((time.time() - t0) * 1000)
                    return {"status": "online", "latency_ms": ms}
                except urllib.error.HTTPError as e:
                    ms = int((time.time() - t0) * 1000)
                    return {"status": "online", "latency_ms": ms}
                except Exception:
                    continue
            return {"status": "offline", "error": "无法连接"}
        return name, await asyncio.get_event_loop().run_in_executor(None, _sync)
    tasks = [_check_one(c) for c in configs]
    results_list = await asyncio.gather(*tasks)
    output = dict(results_list)
    return JSONResponse(output)


# =============================================================================
# 监控看板 — 引擎运行状态
# =============================================================================


@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request):
    """实时监控：引擎状态卡 + 全局汇总。"""
    from kzocr.engine.engine_config import list_engine_configs
    import os, json
    configs = list_engine_configs()
    dbd = _db_dir()

    # 从 benchmark_results 收集各引擎性能汇总
    engine_stats: dict[str, dict] = {}
    for f in sorted(os.listdir(dbd)):
        if not f.endswith(".db"):
            continue
        code = f[:-3]
        db = BookDB(code, db_dir=dbd)
        try:
            rows = db._conn.execute(
                "SELECT engine, total_pages, success_pages, fail_pages, error_rate, "
                "total_latency_ms, latency_p95_ms, pages_per_min "
                "FROM benchmark_results ORDER BY id DESC"
            ).fetchall()
            for r in rows:
                eng = r["engine"]
                if not eng or eng == "none":
                    continue
                if eng not in engine_stats:
                    engine_stats[eng] = {"total_pages": 0, "success_pages": 0,
                                         "fail_pages": 0, "error_rates": [], "latencies": [],
                                         "p95s": [], "ppms": [], "count": 0}
                s = engine_stats[eng]
                s["total_pages"] += r["total_pages"] or 0
                s["success_pages"] += r["success_pages"] or 0
                s["fail_pages"] += r["fail_pages"] or 0
                s["count"] += 1
                if r["error_rate"] is not None:
                    s["error_rates"].append(r["error_rate"])
                if r["total_latency_ms"] and r["total_pages"]:
                    s["latencies"].append(r["total_latency_ms"] / max(r["total_pages"], 1))
                if r["latency_p95_ms"]:
                    s["p95s"].append(r["latency_p95_ms"])
                if r["pages_per_min"]:
                    s["ppms"].append(r["pages_per_min"])
        except Exception:
            pass
        finally:
            db.close()

    # 合并引擎配置信息
    engine_list = []
    for c in configs:
        name = c["name"]
        s = engine_stats.get(name, {})
        avg_err = sum(s.get("error_rates", [])) / max(len(s.get("error_rates", [])), 1)
        avg_p95 = sum(s.get("p95s", [])) / max(len(s.get("p95s", [])), 1)
        avg_ppm = sum(s.get("ppms", [])) / max(len(s.get("ppms", [])), 1)
        engine_list.append({
            "name": name,
            "enabled": c.get("enabled", True),
            "base_url": c.get("base_url", ""),
            "total_pages": s.get("total_pages", 0),
            "success_pages": s.get("success_pages", 0),
            "fail_pages": s.get("fail_pages", 0),
            "error_rate": round(avg_err * 100, 1),
            "avg_latency_ms": round(sum(s.get("latencies", [])) / max(len(s.get("latencies", [])), 1), 0),
            "p95_ms": round(avg_p95, 0),
            "pages_per_min": round(avg_ppm, 1),
            "runs": s.get("count", 0),
        })
    # 只从 configs 中拿引擎，不从 benchmark 新增
    return templates.TemplateResponse(request, "monitor.html", {"engines": engine_list})


# =============================================================================
# 基准测试 — 跨书 benchmark_results 聚合
# =============================================================================


@app.get("/benchmark", response_class=HTMLResponse)
async def benchmark_page(request: Request):
    """基准测试看板：引擎汇总卡片 + 明细表格。"""
    import os
    dbd = _db_dir()
    all_rows = []
    engine_agg: dict[str, dict] = {}

    for f in sorted(os.listdir(dbd)):
        if not f.endswith(".db"):
            continue
        code = f[:-3]
        db = BookDB(code, db_dir=dbd)
        try:
            rows = db._conn.execute(
                "SELECT book_code, engine, total_pages, success_pages, fail_pages, "
                "error_rate, total_latency_ms, latency_p50_ms, latency_p95_ms, "
                "pages_per_min, total_elapsed_s, created_at "
                "FROM benchmark_results ORDER BY id DESC LIMIT 50"
            ).fetchall()
            for r in rows:
                row = dict(r)
                all_rows.append(row)
                eng = row.get("engine", "")
                if not eng or eng == "none":
                    continue
                if eng not in engine_agg:
                    engine_agg[eng] = {"runs": 0, "total_pages": 0, "success_pages": 0,
                                       "fail_pages": 0, "total_latency": 0, "p95s": [], "ppms": []}
                a = engine_agg[eng]
                a["runs"] += 1
                a["total_pages"] += row.get("total_pages", 0) or 0
                a["success_pages"] += row.get("success_pages", 0) or 0
                a["fail_pages"] += row.get("fail_pages", 0) or 0
                a["total_latency"] += row.get("total_latency_ms", 0) or 0
                if row.get("latency_p95_ms"):
                    a["p95s"].append(row["latency_p95_ms"])
                if row.get("pages_per_min"):
                    a["ppms"].append(row["pages_per_min"])
        except Exception:
            pass
        finally:
            db.close()

    # 按created_at 降序排列，取前 200 条
    all_rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    all_rows = all_rows[:200]

    return templates.TemplateResponse(request, "benchmark.html", {
        "rows": all_rows,
        "summary": engine_agg,
        "total": len(all_rows),
    })


@app.get("/monitor/api")
async def monitor_api():
    """监控数据 JSON API"""
    from kzocr.engine.engine_config import list_engine_configs
    import os
    configs = list_engine_configs()
    dbd = _db_dir()
    engines = {}
    for c in configs:
        engines[c["name"]] = {"name": c["name"], "enabled": c.get("enabled", True),
                               "base_url": c.get("base_url", ""), "total_pages": 0,
                               "error_rate": 0, "latency_ms": 0}
    for f in sorted(os.listdir(dbd)):
        if not f.endswith(".db"):
            continue
        db = BookDB(f[:-3], db_dir=dbd)
        try:
            rows = db._conn.execute(
                "SELECT engine, total_pages, error_rate, total_latency_ms "
                "FROM benchmark_results ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for r in rows:
                eng = r["engine"]
                if eng in engines:
                    engines[eng]["total_pages"] += r["total_pages"] or 0
                    if r["error_rate"]:
                        engines[eng]["error_rate"] = max(engines[eng]["error_rate"], r["error_rate"])
                    engines[eng]["latency_ms"] = r["total_latency_ms"] or 0
        except Exception:
            pass
        finally:
            db.close()
    return {
        "engines": list(engines.values()),
        "total_engines": len(configs),
    }


# =============================================================================
# Prompt 管理
# =============================================================================


@app.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request):
    """Prompt 管理页面。首次访问时自动创建默认提示词。"""
    from kzocr.engine.prompt_manager import list_prompts, init_defaults
    init_defaults()
    prompts = list_prompts()
    return templates.TemplateResponse(request, "prompts.html", {"prompts": prompts})


@app.get("/prompts/{name}", response_class=HTMLResponse)
async def prompt_edit(request: Request, name: str):
    """编辑 prompt。"""
    from kzocr.engine.prompt_manager import load_prompt
    text = load_prompt(name) or ""
    return templates.TemplateResponse(request, "prompt_edit.html", {"name": name, "text": text})


@app.post("/prompts/{name}")
async def prompt_save(request: Request, name: str):
    """保存 prompt。"""
    from kzocr.engine.prompt_manager import save_prompt
    form = await request.form()
    save_prompt(name, form.get("text", ""))
    return RedirectResponse(url="/prompts", status_code=303)


@app.get("/prompts/{name}/delete")
async def prompt_delete(name: str):
    """删除 prompt。"""
    from kzocr.engine.prompt_manager import delete_prompt
    delete_prompt(name)
    return RedirectResponse(url="/prompts", status_code=303)


@app.post("/register/{book_code}")
async def register_update(request: Request, book_code: str):
    """更新已有登记。"""
    from kzocr.engine.registration import save_registration
    import json
    form = await request.form()
    toc_json = form.get("toc_json", "[]")
    try:
        toc_entries = json.loads(toc_json)
    except (json.JSONDecodeError, TypeError):
        toc_entries = []
    save_registration(
        book_code=book_code,
        title=form.get("title", ""),
        author=form.get("author", ""),
        publisher=form.get("publisher", ""),
        toc_entries=toc_entries,
    )
    return RedirectResponse(url="/registrations", status_code=303)


@app.get("/register/{book_code}/delete")
async def register_delete(book_code: str):
    """删除登记。"""
    from kzocr.engine.registration import _reg_path
    import os
    path = _reg_path(book_code)
    if os.path.isfile(path):
        os.remove(path)
    return RedirectResponse(url="/registrations", status_code=303)

@app.get("/book/{book_code}", response_class=HTMLResponse)
async def book_detail(request: Request, book_code: str):
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        anomalies = db.get_unresolved_anomalies(book_code, limit=999)
        bench = db._conn.execute(
            "SELECT * FROM benchmark_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        progress = []
        anomalies = []
        bench = None
    finally:
        db.close()
    return templates.TemplateResponse(request, "book.html", {
        "book_code": book_code,
        "progress": progress,
        "anomalies": anomalies,
        "benchmark": dict(bench) if bench else None,
    })


@app.get("/book/{book_code}/anomalies", response_class=HTMLResponse)
async def book_anomalies(request: Request, book_code: str, status: str = "pending"):
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        items = db.get_anomalies(status_filter=status)
    except Exception:
        items = []
    finally:
        db.close()
    return templates.TemplateResponse(request, "anomalies.html", {
        "book_code": book_code,
        "anomalies": items,
        "current_status": status,
    })


@app.post("/book/{book_code}/anomalies/{anomaly_id}/resolve")
async def resolve_anomaly(book_code: str, anomaly_id: int, resolution: str = "fixed", note: str = ""):
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        db.resolve_anomaly(anomaly_id, resolution=resolution, note=note)
    finally:
        db.close()
    return RedirectResponse(url=f"/book/{book_code}/anomalies", status_code=303)


@app.get("/book/{book_code}/recipes", response_class=HTMLResponse)
async def book_recipes(request: Request, book_code: str):
    from kzocr.analysis.recipe_parser import parse_recipes
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        pages_text = [p["verify_details"] for p in progress if p.get("verify_details")]
        if not pages_text:
            pages_text = [""] * len(progress)
        recipes = parse_recipes(pages_text)
    except Exception:
        recipes = []
    finally:
        db.close()
    return templates.TemplateResponse(request, "recipes.html", {
        "book_code": book_code,
        "recipes": recipes,
    })


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_form(request: Request, book_code: str = ""):
    """OCR 处理表单。"""
    from kzocr.engine.registration import list_registrations
    regs = list_registrations()
    return templates.TemplateResponse(request, "pipeline.html", {"registrations": regs, "book_code": book_code, "error": None})


@app.post("/pipeline")
async def pipeline_run(request: Request):
    """执行 OCR 处理。"""
    import os
    import logging
    from kzocr.config import load_config
    from kzocr.engine.run import run_engine
    form = await request.form()
    book_code = form.get("book_code", "")
    pdf_path = form.get("pdf_path", "")
    if not book_code or not pdf_path:
        return templates.TemplateResponse(request, "pipeline.html", {
            "registrations": [],
            "error": "请填写书籍编号和 PDF 路径",
        })
    if not os.path.isfile(pdf_path):
        return templates.TemplateResponse(request, "pipeline.html", {
            "registrations": [],
            "error": f"PDF 文件不存在：{pdf_path}",
        })
    try:
        cfg = load_config()
        run_engine(pdf_path, book_code=book_code, config=cfg)
        return RedirectResponse(url=f"/book/{book_code}", status_code=303)
    except Exception as exc:
        logging.getLogger("kzocr").error("Pipeline failed: %s", exc)
        return templates.TemplateResponse(request, "pipeline.html", {
            "registrations": [],
            "error": f"处理失败：{exc}",
        })


# =============================================================================
# 健康检查 + 系统信息
# =============================================================================


@app.get("/health")
async def health():
    """系统健康检查。"""
    dbd = _db_dir()
    db_ok = os.path.isdir(dbd)
    try:
        if db_ok:
            db = BookDB("_health_check", db_dir=dbd)
            db.close()
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.19.0",
        "db_dir": dbd,
        "db_accessible": db_ok,
    }


# =============================================================================
# Web 增强路由
# =============================================================================


@app.get("/registrations", response_class=HTMLResponse)
async def registrations_list(request: Request):
    """已登记书籍列表。"""
    from kzocr.engine.registration import list_registrations
    regs = list_registrations()
    return templates.TemplateResponse(request, "registrations.html", {"registrations": regs})


@app.get("/register/{book_code}", response_class=HTMLResponse)
async def register_edit(request: Request, book_code: str):
    """编辑已有登记。"""
    from kzocr.engine.registration import load_registration
    reg = load_registration(book_code) or {}
    return templates.TemplateResponse(request, "register.html", {"registration": reg, "edit": True})


@app.get("/book/{book_code}/quality", response_class=HTMLResponse)
async def book_quality(request: Request, book_code: str):
    """质检结果页面。"""
    from kzocr.storage.db import BookDB
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        results = db.get_quality_results()
    except Exception:
        results = []
    finally:
        db.close()
    return templates.TemplateResponse(request, "quality.html", {
        "book_code": book_code,
        "results": results,
    })


# =============================================================================
# REST API（JSON 端点）
# =============================================================================

api = APIRouter(prefix="/api")


@api.get("/engines")
async def api_engines():
    """返回所有引擎状态（基于 benchmark_results 和历史数据）。"""
    dbd = _db_dir()
    engines = {}
    for f in sorted(os.listdir(dbd)):
        if not f.endswith(".db"):
            continue
        code = f[:-3]
        db = BookDB(code, db_dir=dbd)
        try:
            rows = db._conn.execute(
                "SELECT engine, total_pages, success_pages, fail_pages, "
                "error_rate, total_latency_ms, pages_per_min, total_elapsed_s, created_at "
                "FROM benchmark_results ORDER BY id DESC LIMIT 5"
            ).fetchall()
            for r in rows:
                eng = r["engine"]
                if eng and eng != "none" and eng not in engines:
                    engines[eng] = dict(r)
        except Exception:
            pass
        finally:
            db.close()
    return list(engines.values())


@api.get("/engines/{name}/test")
async def api_engine_test(name: str):
    """测试引擎连通性。云端引擎检查 egress；本地引擎检查端口/进程。"""
    from kzocr.engine.engine_config import load_engine_config
    from kzocr.security.egress import validate_url
    import socket
    import os
    cfg = load_engine_config(name)
    if not cfg:
        return {"status": "error", "message": f"引擎 {name} 未配置"}
    result = {"engine": name, "checks": []}
    if cfg.get("requires_network"):
        base_url = cfg.get("base_url", "")
        if base_url:
            try:
                validate_url(base_url)
                result["checks"].append({"name": "egress", "status": "ok", "detail": f"域名校验通过: {base_url}"})
            except Exception as exc:
                result["checks"].append({"name": "egress", "status": "fail", "detail": str(exc)[:100]})
        else:
            result["checks"].append({"name": "egress", "status": "skip", "detail": "未配置 base_url"})
    else:
        result["checks"].append({"name": "egress", "status": "skip", "detail": "本地引擎"})
        # 本地引擎端口检查
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 0)
        if port:
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.close()
                result["checks"].append({"name": "port", "status": "ok", "detail": f"{host}:{port} 可连接"})
            except Exception as exc:
                result["checks"].append({"name": "port", "status": "fail", "detail": f"{host}:{port} {str(exc)[:60]}"})
        # 进程检查
        pid_file = cfg.get("pid_file", "")
        if pid_file and os.path.isfile(pid_file):
            result["checks"].append({"name": "pid_file", "status": "ok", "detail": f"PID 文件存在: {pid_file}"})
    result["status"] = "ok" if all(c["status"] in ("ok", "skip") for c in result["checks"]) else "degraded"
    return result


@api.get("/books")
async def api_books():
    """返回书籍列表（JSON）。"""
    return _list_books()


@api.get("/books/{book_code}")
async def api_book_detail(book_code: str):
    """返回单书详情（JSON）。"""
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        bench = db._conn.execute(
            "SELECT * FROM benchmark_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
        _ = db.get_unresolved_anomalies(book_code, limit=1)
        return {
            "book_code": book_code,
            "total_pages": len(progress),
            "success_pages": sum(1 for p in progress if p["ocr_status"] == "success"),
            "fail_pages": sum(1 for p in progress if p["ocr_status"] == "failed"),
            "anomaly_count": len(db.get_unresolved_anomalies(book_code, limit=999)),
            "benchmark": dict(bench) if bench else None,
        }
    finally:
        db.close()


@api.get("/books/{book_code}/pages")
async def api_book_pages(book_code: str):
    """返回逐页进度（JSON）。"""
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        return db.get_all_progress()
    finally:
        db.close()


@api.get("/books/{book_code}/anomalies")
async def api_anomalies(book_code: str, status: str = Query("pending", description="过滤状态")):
    """返回异常记录列表（JSON）。"""
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        return db.get_anomalies(status_filter=status)
    finally:
        db.close()


@api.post("/books/{book_code}/anomalies/{anomaly_id}/resolve")
async def api_resolve_anomaly(book_code: str, anomaly_id: int, resolution: str = "fixed", note: str = ""):
    """标记异常决议。"""
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        db.resolve_anomaly(anomaly_id, resolution=resolution, note=note)
        return {"status": "ok", "message": f"Anomaly #{anomaly_id} resolved as {resolution}"}
    finally:
        db.close()


@api.get("/books/{book_code}/recipes")
async def api_recipes(book_code: str):
    """返回方剂列表（JSON）。"""
    from kzocr.analysis.recipe_parser import parse_recipes
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        pages_text = [p["verify_details"] for p in progress if p.get("verify_details")]
        if not pages_text:
            pages_text = [""] * len(progress)
        recipes = parse_recipes(pages_text)
        return [
            {
                "recipe_no": r.recipe_no,
                "title": r.title,
                "start_page": r.start_page,
                "herbs": [{"name": h.herb_name, "dosage": h.dosage, "unit": h.unit} for h in r.herbs],
                "fields": {k: v for k, v in r.fields.items()},
            }
            for r in recipes
        ]
    finally:
        db.close()


# =============================================================================
# Web 面板增强路由
# =============================================================================


@app.get("/book/{book_code}/dashboard", response_class=HTMLResponse)
async def book_dashboard(request: Request, book_code: str):
    """引擎性能看板。"""
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        bench = db._conn.execute(
            "SELECT * FROM benchmark_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        progress = []
        bench = None
    finally:
        db.close()
    return templates.TemplateResponse(request, "dashboard.html", {
        "book_code": book_code,
        "progress": progress,
        "benchmark": dict(bench) if bench else None,
    })


@app.get("/book/{book_code}/recipe/{recipe_no}", response_class=HTMLResponse)
async def book_recipe_detail(request: Request, book_code: str, recipe_no: str):
    """方剂详情页。"""
    from kzocr.analysis.recipe_parser import parse_recipes
    from kzocr.analysis.quality import QualityChecker
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        pages_text = [p["verify_details"] or "" for p in progress if p.get("verify_details")]
        if not pages_text:
            pages_text = [""] * len(progress)
        recipes = parse_recipes(pages_text)
        recipe = next((r for r in recipes if r.recipe_no == recipe_no), None)
        qr = QualityChecker().check(recipe) if recipe else None
    except Exception:
        recipe = None
        qr = None
    finally:
        db.close()
    return templates.TemplateResponse(request, "recipe_detail.html", {
        "book_code": book_code,
        "recipe": recipe,
        "quality": qr,
    })


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    """全字段搜索。"""
    results: list[dict] = []
    if q:
        dbd = _db_dir()
        for f in sorted(os.listdir(dbd)):
            if not f.endswith(".db"):
                continue
            code = f[:-3]
            db = BookDB(code, db_dir=dbd)
            try:
                progress = db.get_all_progress()
                pages_text = [p["verify_details"] or "" for p in progress if p.get("verify_details")]
                if not pages_text:
                    continue
                from kzocr.analysis.recipe_parser import parse_recipes
                for r in parse_recipes(pages_text):
                    if q in r.title or any(q in h.herb_name for h in r.herbs) or any(q in v for v in r.fields.values()):
                        results.append({"book_code": code, "recipe_no": r.recipe_no, "title": r.title})
            except Exception:
                pass
            finally:
                db.close()
    return templates.TemplateResponse(request, "search.html", {
        "query": q,
        "results": results,
    })


@app.get("/workspace/{book_code}", response_class=HTMLResponse)
async def workspace(request: Request, book_code: str, resolved: str = "no"):
    """外包校对工作台。"""
    dbd = _db_dir()
    db = BookDB(book_code, db_dir=dbd)
    try:
        status_filter = "pending" if resolved == "no" else None
        anomalies = db.get_anomalies(status_filter=status_filter) if status_filter else db.get_all_progress()
    except Exception:
        anomalies = []
    finally:
        db.close()
    return templates.TemplateResponse(request, "workspace.html", {
        "book_code": book_code,
        "anomalies": anomalies,
    })


app.include_router(api)


@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    """书籍登记表单。"""
    return templates.TemplateResponse(request, "register.html", {})


@app.post("/register")
async def register_submit(request: Request):
    """提交书籍登记。"""
    from kzocr.engine.registration import save_registration
    import json
    form = await request.form()
    book_code = form.get("book_code", "")
    if not book_code:
        return RedirectResponse(url="/register", status_code=303)
    toc_json = form.get("toc_json", "[]")
    try:
        toc_entries = json.loads(toc_json)
    except (json.JSONDecodeError, TypeError):
        toc_entries = []
    save_registration(
        book_code=book_code,
        title=form.get("title", ""),
        author=form.get("author", ""),
        publisher=form.get("publisher", ""),
        toc_entries=toc_entries,
    )
    return RedirectResponse(url=f"/book/{book_code}", status_code=303)
