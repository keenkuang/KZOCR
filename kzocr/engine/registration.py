"""书籍登记管理：OCR 处理前填入书籍元数据和目录层级。"""

from __future__ import annotations

import json
import os
from typing import Optional

from kzocr.engine.types import TocEntry, TocTree


def _reg_dir() -> str:
    return os.environ.get(
        "KZOCR_DATA_DIR",
        os.path.join(os.environ.get("KZOCR_DB_DIR", "db"), "..", "registrations"),
    )


def _reg_path(book_code: str) -> str:
    d = _reg_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{book_code}.json")


def save_registration(
    book_code: str,
    title: str = "",
    author: str = "",
    publisher: str = "",
    toc_entries: Optional[list[dict]] = None,
) -> dict:
    """保存书籍登记信息。"""
    data = {
        "book_code": book_code,
        "title": title,
        "author": author,
        "publisher": publisher,
        "toc": {
            "max_depth": max((e.get("level", 1) for e in (toc_entries or [])), default=0),
            "entries": toc_entries or [],
        },
    }
    with open(_reg_path(book_code), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def load_registration(book_code: str) -> Optional[dict]:
    """加载书籍登记信息。"""
    path = _reg_path(book_code)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_registrations() -> list[dict]:
    """列出所有已登记书籍。"""
    d = _reg_dir()
    if not os.path.isdir(d):
        return []
    books = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            code = f[:-5]
            reg = load_registration(code)
            if reg:
                books.append(reg)
    return books


def registration_to_toc(reg: dict) -> Optional[TocTree]:
    """将 registration 中的 toc 转换为 TocTree 对象。"""
    toc_data = reg.get("toc")
    if not toc_data or not toc_data.get("entries"):
        return None

    def _build(e: dict) -> TocEntry:
        return TocEntry(
            level=e.get("level", 1),
            title=e.get("title", ""),
            page=e.get("page", 0),
            sub_entries=[_build(s) for s in e.get("sub_entries", [])],
            section_no=e.get("section_no", ""),
        )

    return TocTree(
        max_depth=toc_data.get("max_depth", 0),
        entries=[_build(e) for e in toc_data["entries"]],
    )
