"""Delivered proofread station: standalone FastAPI app (Route 1).

Usage:
    kzocr proofread --db path/to/custom.db [more.db ...]
or:
    python -m kzocr.proofread.app --db path/to/custom.db
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Form, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from kzocr import __version__
from kzocr.doc import validate_proofread_package
from kzocr.proofread.api import CustomDbProofread as _Db

logger = logging.getLogger("kzocr.proofread")

_PROOFREAD_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_PROOFREAD_DIR / "templates"))

# Package registry: stable pkg_id -> absolute db path.
# The "current package" is carried per-request via the kzocr_pkg cookie, so no
# mutable global "current" state lives here (safe for multiple tabs / concurrency).
_REGISTRY: dict[str, str] = {}
_DEFAULT_PKG: Optional[str] = None

COOKIE_NAME = "kzocr_pkg"


def _make_pkg_id(db_path: Path) -> str:
    """Stable pkg_id: filename stem by default; hash suffix on collision."""
    base = db_path.stem
    if base not in _REGISTRY:
        return base
    return f"{base}_{hashlib.sha1(str(db_path).encode()).hexdigest()[:6]}"


def register_package(db_path: str | Path) -> str:
    """Validate and register a package; return pkg_id; first one is default."""
    p = Path(db_path).resolve()
    validate_proofread_package(p)
    pkg_id = _make_pkg_id(p)
    _REGISTRY[pkg_id] = str(p)
    global _DEFAULT_PKG
    if _DEFAULT_PKG is None:
        _DEFAULT_PKG = pkg_id
    logger.info("Registered package %s -> %s", pkg_id, p)
    return pkg_id


def get_pkg_db(pkg_id: str) -> _Db:
    if pkg_id not in _REGISTRY:
        raise RuntimeError(f"Unknown package: {pkg_id}")
    return _Db(_REGISTRY[pkg_id])


def current_db(request: Request) -> _Db:
    """Resolve the current package's db from the request cookie."""
    pkg_id = request.cookies.get(COOKIE_NAME) or _DEFAULT_PKG
    if pkg_id is None:
        raise RuntimeError("No package registered yet")
    if pkg_id not in _REGISTRY:
        # Stale cookie (e.g. server restarted) -> fall back to default pkg.
        pkg_id = _DEFAULT_PKG
    if pkg_id is None:
        raise RuntimeError("No package registered yet")
    return get_pkg_db(pkg_id)


def _list_packages(request: Request) -> list[dict]:
    cur = request.cookies.get(COOKIE_NAME) or _DEFAULT_PKG
    out = []
    for pid, path in _REGISTRY.items():
        try:
            cnt = len(_Db(path).list_books())
        except Exception:
            cnt = 0
        out.append({"id": pid, "name": pid, "book_count": cnt, "current": pid == cur})
    return out


def app_factory(*db_paths: str | Path) -> FastAPI:
    """Create a FastAPI app backed by one or more custom.db packages."""
    global _DEFAULT_PKG
    _REGISTRY.clear()
    _DEFAULT_PKG = None
    for dp in db_paths:
        register_package(dp)
    if not _REGISTRY:
        raise RuntimeError("At least one package required: kzocr proofread --db <custom.db>")

    app = FastAPI(
        title="KZOCR delivered proofread station",
        description="TCM ancient-book OCR proofreading - standalone station for proofreaders",
        version=__version__,
    )

    @app.middleware("http")
    async def _add_globals(request: Request, call_next):
        request.state.db_path = _REGISTRY.get(
            request.cookies.get(COOKIE_NAME) or _DEFAULT_PKG or ""
        )
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        db = current_db(request)
        books = db.list_books()
        total = len(books)
        done = sum(b.proofread_count for b in books)
        all_lines = sum(b.line_count for b in books)
        return _TEMPLATES.TemplateResponse(request, "index.html", {
            "packages": _list_packages(request),
            "books": books,
            "total_books": total,
            "total_lines": all_lines,
            "proofread_lines": done,
        })

    @app.get("/book/{book_code}", response_class=HTMLResponse)
    async def book_review(
        request: Request, book_code: str,
        page: int = Query(0, ge=0),
        status: str = Query("pending"),
    ) -> HTMLResponse:
        db = current_db(request)
        pages = db.get_pages(book_code)
        current_page = page if page > 0 else (pages[0] if pages else 0)
        lines = db.list_lines(book_code, page=current_page, status=status)
        info = db.get_book_info(book_code)
        total_pending = db.count_lines(book_code, status="pending")
        total_done = db.count_lines(book_code, status="done")
        page_pending = db.get_page_line_count(book_code, current_page, status="pending")
        page_done = db.get_page_line_count(book_code, current_page, status="done")
        return _TEMPLATES.TemplateResponse(request, "review.html", {
            "book_code": book_code,
            "book_title": info.title if info else book_code,
            "pages": pages,
            "current_page": current_page,
            "lines": lines,
            "status": status,
            "total_pending": total_pending,
            "total_done": total_done,
            "page_pending": page_pending,
            "page_done": page_done,
            "total_lines": info.line_count if info else 0,
            "proofread_lines": info.proofread_count if info else 0,
        })

    @app.post("/book/{book_code}/line/{line_id}/save")
    async def save_line(
        book_code: str, line_id: str,
        human_final: str = Form(""),
        request: Request = None,
    ) -> JSONResponse:
        ok = current_db(request).save_human_final(book_code, line_id, human_final)
        return JSONResponse({"ok": ok, "line_id": line_id})

    @app.post("/book/{book_code}/export")
    async def export_book(book_code: str, request: Request = None) -> JSONResponse:
        try:
            result = current_db(request).export_import(book_code=book_code)
            return JSONResponse({
                "ok": True,
                "book_code": result["book_code"],
                "imported_lines": result["imported_lines"],
                "imported_proofreads": result["imported_proofreads"],
            })
        except Exception as exc:
            logger.error("Export failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/book/{book_code}/line/{line_id}/audit")
    async def line_audit(book_code: str, line_id: str, request: Request = None) -> JSONResponse:
        rows = current_db(request).get_line_proofreads(book_code, line_id)
        return JSONResponse({"ok": True, "rows": rows})

    @app.get("/book/{book_code}/import-audit")
    async def import_audit(book_code: str, request: Request = None) -> JSONResponse:
        rows = current_db(request).get_import_audit(book_code)
        return JSONResponse({"ok": True, "rows": rows})

    # ── Package management ──
    @app.get("/packages")
    async def list_packages_route(request: Request) -> JSONResponse:
        return JSONResponse({
            "packages": _list_packages(request),
            "current": request.cookies.get(COOKIE_NAME) or _DEFAULT_PKG,
        })

    @app.post("/packages/switch")
    async def switch_package(pkg_id: str = Form(...)) -> RedirectResponse:
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE_NAME, pkg_id)
        return resp

    @app.post("/packages/open")
    async def open_package(path: str = Form(...)) -> RedirectResponse:
        try:
            pkg_id = register_package(path)
        except Exception as exc:
            logger.warning("Rejecting invalid package open %s: %s", path, exc)
            return RedirectResponse("/", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE_NAME, pkg_id)
        return resp

    _STATIC_DIR = _PROOFREAD_DIR / "static"
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="kzocr proofread")
    ap.add_argument("--db", nargs="*", default=[],
                    help="One or more proofread packages (custom.db)")
    ap.add_argument("--books-dir", default="",
                    help="Directory to scan for *.db packages")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9090)
    args = ap.parse_args(argv)

    db_paths = list(args.db)
    if args.books_dir:
        for f in sorted(Path(args.books_dir).glob("*.db")):
            db_paths.append(str(f))
    if not db_paths:
        print("Error: no package given (use --db or --books-dir)", file=sys.stderr)
        return 1

    valid = []
    for dp in db_paths:
        if not os.path.isfile(dp):
            print(f"Error: package not found: {dp}", file=sys.stderr)
            continue
        try:
            validate_proofread_package(Path(dp).resolve())
            valid.append(dp)
        except Exception as exc:
            print(f"Warning: skip invalid package {dp}: {exc}", file=sys.stderr)
    if not valid:
        return 1

    app = app_factory(*valid)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
