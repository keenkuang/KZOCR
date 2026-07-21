"""交付式校对包·独立 FastAPI 应用（方案 B / Route 1）。

用法：
    kzocr proofread --db path/to/custom.db
或：
    python -m kzocr.proofread.app --db path/to/custom.db
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from kzocr import __version__
from kzocr.doc import validate_proofread_package
from kzocr.proofread.api import CustomDbProofread as _Db

logger = logging.getLogger("kzocr.proofread")

_PROOFREAD_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_PROOFREAD_DIR / "templates"))

# 全局单例：由 app_factory 初始化
_db: Optional[_Db] = None
_db_path: str = ""


def _get_db() -> _Db:
    """返回已初始化的 CustomDbProofread 实例。"""
    if _db is None:
        raise RuntimeError(
            "校对包未加载。启动方式：kzocr proofread --db <custom.db>"
        )
    return _db


def app_factory(db_path: str | Path) -> FastAPI:
    """创建以指定 custom.db 为数据源的 FastAPI 应用。"""
    global _db, _db_path
    p = Path(db_path).resolve()
    # 前置校验
    validate_proofread_package(p)
    _db = _Db(str(p))
    _db_path = str(p)
    logger.info("校对包加载成功：%s", p)

    app = FastAPI(
        title="KZOCR 交付式校对台",
        description="中医古籍 OCR 校对——面向校对人员的独立工作台",
        version=__version__,
    )

    # ── 模板注入 ────────────────────────────────────────
    @app.middleware("http")
    async def _add_globals(request: Request, call_next):
        request.state.db_path = _db_path
        return await call_next(request)

    # ── 首页：书籍列表 ──────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        books = _get_db().list_books()
        total = len(books)
        done = sum(b.proofread_count for b in books)
        all_lines = sum(b.line_count for b in books)
        return _TEMPLATES.TemplateResponse(request, "index.html", {
            "books": books,
            "total_books": total,
            "total_lines": all_lines,
            "proofread_lines": done,
        })

    # ── 书籍详情 / 行列表 ──────────────────────────────
    @app.get("/book/{book_code}", response_class=HTMLResponse)
    async def book_review(
        request: Request, book_code: str,
        page: int = Query(0, ge=0),
        status: str = Query("pending"),
    ) -> HTMLResponse:
        db = _get_db()
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

    # ── 保存行 ──────────────────────────────────────────
    @app.post("/book/{book_code}/line/{line_id}/save")
    async def save_line(
        book_code: str, line_id: str,
        human_final: str = Form(""),
    ) -> JSONResponse:
        ok = _get_db().save_human_final(book_code, line_id, human_final)
        return JSONResponse({"ok": ok, "line_id": line_id})

    # ── 导出 / 回导 ────────────────────────────────────
    @app.post("/book/{book_code}/export")
    async def export_book(
        book_code: str,
    ) -> JSONResponse:
        try:
            result = _get_db().export_import(book_code=book_code)
            return JSONResponse({
                "ok": True,
                "book_code": result["book_code"],
                "imported_lines": result["imported_lines"],
                "imported_proofreads": result["imported_proofreads"],
            })
        except Exception as exc:
            logger.error("回导失败：%s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return app


def main(argv: Optional[list[str]] = None) -> int:
    """命令行入口。"""
    ap = argparse.ArgumentParser(prog="kzocr proofread")
    ap.add_argument("--db", required=True, help="校对包路径（custom.db）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9090)
    args = ap.parse_args(argv)

    if not os.path.isfile(args.db):
        print(f"错误：校对包文件不存在：{args.db}", file=sys.stderr)
        return 1

    p = Path(args.db).resolve()
    try:
        validate_proofread_package(p)
    except Exception as exc:
        print(f"错误：校对包校验失败：{exc}", file=sys.stderr)
        return 1

    app = app_factory(p)
    logger.info(
        "校对工作台启动于 http://%s:%d  数据源：%s",
        args.host, args.port, p,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
