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
    """引擎状态页面。"""
    dbd = _db_dir()
    engine_list: list[dict] = []
    for f in sorted(os.listdir(dbd)):
        if not f.endswith(".db"):
            continue
        code = f[:-3]
        db = BookDB(code, db_dir=dbd)
        try:
            rows = db._conn.execute(
                "SELECT engine, total_pages, success_pages, fail_pages, "
                "error_rate, total_latency_ms, pages_per_min, total_elapsed_s, created_at "
                "FROM benchmark_results ORDER BY id DESC"
            ).fetchall()
            for r in rows:
                eng_name = r["engine"]
                if eng_name and eng_name != "none":
                    existing = next((e for e in engine_list if e["name"] == eng_name), None)
                    if not existing:
                        engine_list.append({
                            "name": eng_name,
                            "total_pages": r["total_pages"],
                            "success_pages": r["success_pages"],
                            "fail_pages": r["fail_pages"],
                            "error_rate": r["error_rate"],
                            "latency_avg_ms": round(r["total_latency_ms"] / max(r["total_pages"], 1), 1),
                            "pages_per_min": r["pages_per_min"],
                            "last_seen": r["created_at"],
                        })
        except Exception:
            pass
        finally:
            db.close()
    return templates.TemplateResponse(request, "engines.html", {"engines": engine_list})


@app.get("/engines/config", response_class=HTMLResponse)
async def engines_config_page(request: Request):
    """引擎配置管理页面。"""
    from kzocr.engine.engine_config import list_engine_configs
    configs = list_engine_configs()
    # 补充来自 benchmark 的状态数据
    dbd = _db_dir()
    for c in configs:
        c["status"] = "HEALTHY"
        c["error_rate"] = 0.0
        c["pages_count"] = 0
        for f in sorted(os.listdir(dbd)):
            if not f.endswith(".db"):
                continue
            code = f[:-3]
            try:
                db = BookDB(code, db_dir=dbd)
                row = db._conn.execute(
                    "SELECT error_rate, total_pages FROM benchmark_results "
                    "WHERE engine=? AND error_rate IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (c["name"],),
                ).fetchone()
                if row:
                    c["error_rate"] = row["error_rate"]
                    c["pages_count"] = row["total_pages"]
                    c["status"] = "DEGRADED" if row["error_rate"] > 0.1 else "HEALTHY"
                db.close()
            except Exception:
                pass
    return templates.TemplateResponse(request, "engine_config.html", {
        "configs": configs,
        "tiers": [1, 2, 3],
        "statuses": ["HEALTHY", "DEGRADED", "UNAVAILABLE"],
    })


@app.post("/engines/config/{name}")
async def engines_config_save(request: Request, name: str):
    """保存引擎配置。新增时 name='new'，从表单读取实际名称。"""
    from kzocr.engine.engine_config import save_engine_config
    form = await request.form()
    actual_name = form.get("name", name) if name == "new" else name
    save_engine_config(actual_name, {
        "label": form.get("label", actual_name),
        "tier": int(form.get("tier", 1)),
        "requires_network": form.get("requires_network") == "true",
        "batch_capable": form.get("batch_capable") == "true",
        "enabled": form.get("enabled") == "true",
        "backoff_threshold_ms": int(form.get("backoff_threshold_ms", 30000)),
        "backoff_fail_rate": float(form.get("backoff_fail_rate", 0.5)),
        "max_workers": int(form.get("max_workers", 3)),
    })
    return RedirectResponse(url="/engines/config", status_code=303)


@app.get("/engines/config/{name}/delete")
async def engines_config_delete(name: str):
    """删除引擎配置。"""
    from kzocr.engine.engine_config import delete_engine_config
    delete_engine_config(name)
    return RedirectResponse(url="/engines/config", status_code=303)


@app.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request):
    """Prompt 管理页面。"""
    from kzocr.engine.prompt_manager import list_prompts
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
    """测试引擎连通性。尝试 validate_url 校验。"""
    from kzocr.engine.engine_config import load_engine_config
    from kzocr.security.egress import validate_url
    cfg = load_engine_config(name)
    if not cfg:
        return {"status": "error", "message": f"引擎 {name} 未配置"}
    result = {"engine": name, "checks": []}
    if cfg.get("requires_network"):
        base_url = cfg.get("base_url", "")
        if base_url:
            try:
                validate_url(base_url)
                result["checks"].append({"name": "egress", "status": "ok", "detail": f"{base_url} 通过"})
            except Exception as exc:
                result["checks"].append({"name": "egress", "status": "fail", "detail": str(exc)})
        else:
            result["checks"].append({"name": "egress", "status": "skip", "detail": "无 base_url 配置"})
    else:
        result["checks"].append({"name": "egress", "status": "skip", "detail": "本地引擎，无需校验"})
    result["status"] = "ok" if all(c["status"] == "ok" or c["status"] == "skip" for c in result["checks"]) else "degraded"
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
