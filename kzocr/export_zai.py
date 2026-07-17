"""从 zai 控制台数据库导出最终校正文档（人工终校后）。

读取 Book → Page → Line（优先 humanFinal，否则 final/consensus），并附三大永久范式库
沉淀（统一 Pattern 表），生成 Markdown。表结构对齐 `adapter/to_zai_prisma.py` 的写入端。
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from kzocr.config import load_config


def export_book_markdown(book_code: str, db_path: Optional[str] = None) -> str:
    db = db_path or load_config().zai_db
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, publisher, pubYear FROM Book WHERE bookCode=?", (book_code,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"未找到书籍：{book_code}")
        title, publisher, pub_year = row

        lines_out: list[str] = [
            f"# {title}",
            "",
            f"> 来源：{publisher}（{pub_year}） | 导出：KZOCR",
            "",
        ]

        pages = cur.execute(
            "SELECT pageNum FROM Page WHERE bookCode=? ORDER BY pageNum", (book_code,)
        ).fetchall()
        for (page_num,) in pages:
            lines_out.append(f"## 第 {page_num} 页")
            lines_out.append("")
            for human, final, consensus in cur.execute(
                "SELECT humanFinal, final, consensus FROM Line "
                "WHERE bookCode=? AND pageNum=? ORDER BY paraSeq, seqInPara",
                (book_code, page_num),
            ).fetchall():
                text = human or final or consensus or ""
                if text:
                    lines_out.append(text)
            lines_out.append("")

        # 三大永久范式库沉淀（本库全局，统一 Pattern 表）
        lines_out += ["---", "", "## 三大永久范式库（沉淀）", ""]
        lines_out.append("### 药名 OCR 范式")
        for r in cur.execute(
            "SELECT correctName, ocrErrorPattern, severity FROM Pattern "
            "WHERE libType=1 ORDER BY correctName"
        ).fetchall():
            lines_out.append(f"- {r[0]} ← {r[1]}（{r[2]}）")
        lines_out.append("")
        lines_out.append("### 经络穴位 OCR 范式")
        for r in cur.execute(
            "SELECT correctName, ocrErrorPattern, meridianBelonging FROM Pattern "
            "WHERE libType=2 ORDER BY correctName"
        ).fetchall():
            lines_out.append(f"- {r[0]} ← {r[1]}（{r[2]}）")
        lines_out.append("")
        return "\n".join(lines_out)
    finally:
        conn.close()


def export_json(book_code: str, db_path: Optional[str] = None) -> str:
    """导出结构化 JSON，含 recipes/herbs/modifications/quality_issues。

    从 BookDB（`$KZOCR_DB_DIR/{book_code}.db`）读取逐页进度和配方解析结果。
    """
    db_path = db_path or ""
    from kzocr.analysis.recipe_parser import parse_recipes
    from kzocr.analysis.quality import QualityChecker
    from kzocr.storage.db import BookDB

    dbd = db_path or os.environ.get("KZOCR_DB_DIR", "db")
    db = BookDB(book_code, db_dir=dbd)
    try:
        progress = db.get_all_progress()
        pages_text = [p["verify_details"] or "" for p in progress if p.get("verify_details")]
        if not pages_text:
            pages_text = [""] * len(progress)
        recipes = parse_recipes(pages_text)
        checker = QualityChecker()
        export_data: dict[str, Any] = {
            "book_code": book_code,
            "total_pages": len(progress),
            "success_pages": sum(1 for p in progress if p["ocr_status"] == "success"),
            "fail_pages": sum(1 for p in progress if p["ocr_status"] == "failed"),
            "recipes": [],
        }
        for r in recipes:
            qr = checker.check(r)
            export_data["recipes"].append({
                "recipe_no": r.recipe_no,
                "title": r.title,
                "start_page": r.start_page,
                "fields": r.fields,
                "herbs": [
                    {"name": h.herb_name, "dosage": h.dosage, "unit": h.unit,
                     "preparation": h.preparation, "dosage_group": h.dosage_group}
                    for h in r.herbs
                ],
                "modifications": [
                    {"condition": m.condition, "action": m.action, "content": m.content}
                    for m in r.modifications
                ],
                "quality": {
                    "status": qr.status,
                    "confidence": qr.confidence,
                    "issues": [
                        {"field": i.field, "type": i.issue_type, "severity": i.severity, "detail": i.detail}
                        for i in qr.issues
                    ],
                },
            })
        return json.dumps(export_data, ensure_ascii=False, indent=2)
    finally:
        db.close()
