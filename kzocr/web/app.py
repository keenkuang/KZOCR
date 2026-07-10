"""KZOCR Web 管理面板。

基于 FastAPI + Jinja2。
设计借鉴 TCM-Modern-OCR Web UI（Next.js + shadcn/ui + Tailwind）。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kzocr.storage.db import BookDB

app = FastAPI(title="KZOCR Web Panel")

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
    books = _list_books()
    total_books = len(books)
    total_pages = sum(b["total_pages"] for b in books)
    total_success = sum(b["success_pages"] for b in books)
    total_anomalies = sum(b["anomaly_count"] for b in books)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "books": books,
        "total_books": total_books,
        "total_pages": total_pages,
        "total_success": total_success,
        "total_anomalies": total_anomalies,
    })


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
    return templates.TemplateResponse("book.html", {
        "request": request,
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
    return templates.TemplateResponse("anomalies.html", {
        "request": request,
        "book_code": book_code,
        "anomalies": items,
        "current_status": status,
    })


@app.get("/book/{book_code}/anomalies/{anomaly_id}/resolve")
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
    return templates.TemplateResponse("recipes.html", {
        "request": request,
        "book_code": book_code,
        "recipes": recipes,
    })
