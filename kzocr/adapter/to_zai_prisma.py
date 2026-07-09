"""适配器：把归一化 BookResult 写入 zai 校对台的 Prisma/SQLite 数据库。

zai 控制台（`tcm_ocr_zai`）完全自包含，数据模型在 `prisma/schema.prisma`。
本适配器以同名表/列（Prisma 对 SQLite 默认字段名即列名）直写其 `db/custom.db`，
**不修改 zai 源码**，从而让 zai 的人工校对工作台零改动即可校阅 kimi 引擎的产出。

若目标库不存在或表缺失，会按 schema 子集自动建表（CREATE TABLE IF NOT EXISTS），
使 KZOCR 可独立演示（无需先跑 `bun run db:push`）。

数据模型以 `kzocr/engine/types.py` 中的 dataclass 为准（只读，本文件不修改它）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from .. import config
from ..engine.types import BookResult


# 自动建表：列名对齐 prisma/schema.prisma（写库所需子集）
_SCHEMA_DDL = [
    """CREATE TABLE IF NOT EXISTS Book (
        bookCode TEXT PRIMARY KEY, title TEXT, author TEXT, publisher TEXT,
        pubYear INTEGER, pubEra TEXT, bookType TEXT, source TEXT,
        pageCount INTEGER, lineCount INTEGER, cerValue REAL, lineAccuracy REAL)""",
    """CREATE TABLE IF NOT EXISTS Page (
        pageNum INTEGER, bookCode TEXT, paragraphCount INTEGER, lineCount INTEGER)""",
    """CREATE TABLE IF NOT EXISTS Paragraph (
        id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT,
        seqInPage INTEGER, isFormulaParagraph INTEGER, verificationStatus TEXT)""",
    """CREATE TABLE IF NOT EXISTS Line (
        id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, seqInPara INTEGER,
        engineTexts TEXT, consensus TEXT, llmCorrected TEXT, glyphVerified TEXT,
        final TEXT, humanFinal TEXT, confidence REAL, auditSource TEXT,
        headingLevel INTEGER, disputed INTEGER, missingCharAlert TEXT,
        extraCharAlert TEXT, charLevelJson TEXT)""",
    """CREATE TABLE IF NOT EXISTS Proofread (
        id TEXT PRIMARY KEY, pageNum INTEGER, bookCode TEXT, lineId TEXT,
        originalText TEXT, correctedText TEXT, changeType TEXT, severity TEXT,
        notes TEXT, triggeredPattern TEXT)""",
    """CREATE TABLE IF NOT EXISTS Pattern (
        id TEXT PRIMARY KEY, correctName TEXT, ocrErrorPattern TEXT, patternType TEXT,
        isToxic INTEGER, severity TEXT, sourceBooks TEXT, evidenceCount INTEGER,
        libType INTEGER, entityType TEXT, meridianBelonging TEXT, bodyRegion TEXT,
        patternText TEXT, regex TEXT, example TEXT, lib TEXT)""",
    """CREATE TABLE IF NOT EXISTS Term (
        id TEXT PRIMARY KEY, termName TEXT, sublib TEXT, errorPattern TEXT,
        correctForm TEXT, scope TEXT, scopeScore INTEGER, confidence REAL,
        sourceBooks TEXT)""",
    """CREATE TABLE IF NOT EXISTS Formula (
        id TEXT PRIMARY KEY, formulaName TEXT, sourcePages TEXT, createdAt TEXT)""",
    """CREATE TABLE IF NOT EXISTS FormulaIngredient (
        id TEXT PRIMARY KEY, formulaId TEXT, herbName TEXT, herbCorrectedName TEXT,
        dosageValue TEXT, unit TEXT, roleInFormula TEXT, isToxic INTEGER)""",
]


def _uid() -> str:
    return "c" + uuid.uuid4().hex


def _resolve_db(db_path: Optional[Path], zai_path: Optional[Path]) -> Path:
    if db_path:
        return Path(db_path)
    if zai_path:
        return Path(zai_path)
    return Path(config.config.zai_db)


def push_book_to_zai(book: BookResult, db_path: Optional[Path] = None,
                     zai_path: Optional[Path] = None,
                     skip_prisma_marker: bool = False) -> dict:
    db = _resolve_db(db_path, zai_path)
    os.makedirs(db.parent, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        for ddl in _SCHEMA_DDL:
            cur.execute(ddl)

        # 幂等：同一 bookCode 先清旧数据（含 bookCode 的表）
        for t in ("Proofread", "Line", "Paragraph", "Page", "Book"):
            cur.execute(f"DELETE FROM {t} WHERE bookCode=?", (book.book_code,))

        # Pattern/Term/Formula/FormulaIngredient 无 bookCode 列，全量清（zai 单书模式）
        for t in ("FormulaIngredient", "Formula", "Pattern", "Term"):
            cur.execute(f"DELETE FROM {t}")

        total_lines = sum(len(para.lines) for p in book.pages for para in p.paragraphs)

        # Book
        cur.execute(
            "INSERT INTO Book (bookCode,title,author,publisher,pubYear,pubEra,bookType,"
            "source,pageCount,lineCount,cerValue,lineAccuracy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (book.book_code, book.title, book.author, book.publisher,
             book.pub_year, book.pub_era, book.book_type, book.engine_label,
             len(book.pages), total_lines, None, None),
        )

        counts = {"pages": 0, "paragraphs": 0, "lines": 0, "proofreads": 0,
                  "patterns": 0, "terms": 0, "formulas": 0, "ingredients": 0}

        # Page / Paragraph / Line / Proofread
        for p in book.pages:
            page_line_count = sum(len(para.lines) for para in p.paragraphs)
            cur.execute(
                "INSERT INTO Page (pageNum,bookCode,paragraphCount,lineCount) VALUES (?,?,?,?)",
                (p.page_num, book.book_code, len(p.paragraphs), page_line_count),
            )
            counts["pages"] += 1
            for para in p.paragraphs:
                para_id = f"{book.book_code}-P{p.page_num}-{para.sequence_in_page}"
                cur.execute(
                    "INSERT INTO Paragraph (id,pageNum,bookCode,seqInPage,isFormulaParagraph,verificationStatus) "
                    "VALUES (?,?,?,?,?,?)",
                    (para_id, p.page_num, book.book_code, para.sequence_in_page, 0, None),
                )
                counts["paragraphs"] += 1
                for ln in para.lines:
                    line_id = f"{book.book_code}-L{p.page_num}-{para.sequence_in_page}-{ln.sequence_in_paragraph}"
                    et = ln.engine_texts or {}
                    engine_texts_json = json.dumps(et, ensure_ascii=False)
                    cur.execute(
                        "INSERT INTO Line (id,pageNum,bookCode,seqInPara,engineTexts,consensus,"
                        "llmCorrected,glyphVerified,final,humanFinal,confidence,auditSource,"
                        "headingLevel,disputed,missingCharAlert,extraCharAlert,charLevelJson) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (line_id, p.page_num, book.book_code, ln.sequence_in_paragraph,
                         engine_texts_json, ln.consensus, ln.llm_corrected, ln.glyph_verified,
                         ln.final, ln.human_final, ln.confidence, book.engine_label,
                         None, int(ln.disputed), ln.missing_char_alert,
                         ln.extra_char_alert, ln.char_level_json),
                    )
                    counts["lines"] += 1
                    for pr in ln.proofreads:
                        cur.execute(
                            "INSERT INTO Proofread (id,pageNum,bookCode,lineId,originalText,"
                            "correctedText,changeType,severity,notes,triggeredPattern) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (_uid(), p.page_num, book.book_code, line_id,
                             pr.original_text, pr.corrected_text, pr.change_type,
                             pr.severity, pr.notes, pr.triggered_pattern),
                        )
                        counts["proofreads"] += 1

        # 三大范式库 → 统一 Pattern 表
        for h in book.herb_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,isToxic,severity,"
                "sourceBooks,evidenceCount,libType,entityType,meridianBelonging,bodyRegion,"
                "patternText,regex,example,lib) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), h.correct_name, h.ocr_error_pattern, h.pattern_type, int(h.is_toxic),
                 h.severity, h.source_books, h.evidence_count, 1, None, None, None,
                 None, None, None, "herb"),
            )
            counts["patterns"] += 1
        for m in book.meridian_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,isToxic,severity,"
                "sourceBooks,evidenceCount,libType,entityType,meridianBelonging,bodyRegion,"
                "patternText,regex,example,lib) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), m.correct_name, m.ocr_error_pattern, None, None,
                 m.severity, m.source_books, m.evidence_count, 2, m.entity_type,
                 m.meridian_belonging, m.body_region, None, None, None, "meridian"),
            )
            counts["patterns"] += 1
        for c in book.context_patterns:
            cur.execute(
                "INSERT INTO Pattern (id,correctName,ocrErrorPattern,patternType,isToxic,severity,"
                "sourceBooks,evidenceCount,libType,entityType,meridianBelonging,bodyRegion,"
                "patternText,regex,example,lib) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_uid(), None, None, c.pattern_type, None, None, c.source_books,
                 c.discovered_count, 3, None, None, None, c.pattern_text, c.regex,
                 c.example, "context"),
            )
            counts["patterns"] += 1

        # Term
        for t in book.terms:
            cur.execute(
                "INSERT INTO Term (id,termName,sublib,errorPattern,correctForm,scope,scopeScore,confidence,sourceBooks) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_uid(), t.term_name, t.sublib, t.error_pattern, t.correct_form,
                 t.scope, t.scope_score, t.confidence, None),
            )
            counts["terms"] += 1

        # Formula / FormulaIngredient
        for idx, f in enumerate(book.formulas):
            fid = f"F{idx}"
            cur.execute(
                "INSERT INTO Formula (id,formulaName,sourcePages,createdAt) VALUES (?,?,?,?)",
                (fid, f.formula_name, None, None),
            )
            counts["formulas"] += 1
            for ing in f.ingredients:
                cur.execute(
                    "INSERT INTO FormulaIngredient (id,formulaId,herbName,herbCorrectedName,"
                    "dosageValue,unit,roleInFormula,isToxic) VALUES (?,?,?,?,?,?,?,?)",
                    (_uid(), fid, ing.herb_name, None, ing.dosage_value,
                     ing.unit, ing.role_in_formula, int(ing.is_toxic)),
                )
                counts["ingredients"] += 1

        conn.commit()

        if not skip_prisma_marker:
            marker = Path(str(db) + ".zai_prisma_marker")
            marker.write_text(book.book_code, encoding="utf-8")

        return {"book_code": book.book_code, "db": str(db), "counts": counts}
    finally:
        conn.close()


def export_markdown(book: BookResult, out_path: Optional[Path] = None) -> Optional[str]:
    lines = [
        f"# {book.title}",
        "",
        f"> 来源：{book.publisher}（{book.pub_year}） | 引擎：{book.engine_label} | "
        f"book_code：{book.book_code}",
        "",
    ]
    for p in book.pages:
        lines.append(f"## 第 {p.page_num} 页")
        lines.append("")
        for para in p.paragraphs:
            for ln in para.lines:
                text = ln.human_final or ln.final or ln.consensus or ""
                lines.append(text)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 三大永久范式库（本批沉淀）")
    lines.append("")
    lines.append("### 药名 OCR 范式")
    for h in book.herb_patterns:
        lines.append(f"- {h.correct_name} ← {h.ocr_error_pattern}（{h.pattern_type}, {h.severity}）")
    lines.append("")
    lines.append("### 经络穴位 OCR 范式")
    for m in book.meridian_patterns:
        lines.append(f"- {m.correct_name} ← {m.ocr_error_pattern}（{m.entity_type}, {m.meridian_belonging}）")
    lines.append("")
    lines.append("### 语境范式")
    for c in book.context_patterns:
        lines.append(f"- {c.pattern_text}（{c.pattern_type}）")
    lines.append("")
    lines.append("## 术语")
    for t in book.terms:
        lines.append(f"- {t.term_name}（{t.sublib}）：{t.error_pattern} → {t.correct_form}")
    lines.append("")
    lines.append("## 方剂")
    for f in book.formulas:
        lines.append(f"### {f.formula_name}")
        for ing in f.ingredients:
            lines.append(f"- {ing.herb_name} {ing.dosage_value}{ing.unit or ''}（{ing.role_in_formula or ''}）")
        lines.append("")

    md = "\n".join(lines)
    if out_path:
        op = Path(out_path)
        os.makedirs(op.parent, exist_ok=True)
        op.write_text(md, encoding="utf-8")
        return str(op)
    return md
